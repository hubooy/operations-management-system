from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable


CANONICAL_FORMAT_VERSION = "sales-projection-v4"
RETIREMENT_AUDIT_VERSION = "sales-d1-retirement-v4"
RETIREMENT_TOMBSTONE_VIEWS = [
    "sales_import_upload_chunks",
    "sales_import_uploads",
    "sales_order_lines",
    "sales_import_batches",
    "sales_overview_response_cache",
    "sales_overview_cache_state",
    "sales_projection_outbox",
    "sales_projection_source_state",
    "sales_write_authority",
]
SHARED_IMPORT_RETIREMENT_GUARDS = [
    "sales_retired_fingerprints_insert_guard",
    "sales_retired_fingerprints_update_guard",
    "sales_retired_fingerprints_delete_guard",
    "sales_retired_attempts_insert_guard",
    "sales_retired_attempts_update_guard",
    "sales_retired_attempts_delete_guard",
    "sales_retired_scope_heads_insert_guard",
    "sales_retired_scope_heads_update_guard",
    "sales_retired_scope_heads_delete_guard",
]
HEX64 = re.compile(r"^[0-9a-f]{64}$")
RUN_ID = re.compile(r"^[0-9a-f]{32,64}$")
REVISION = re.compile(r"^\d+:\d+$")


class GateError(RuntimeError):
    """A controlled rejection that never includes source rows or credentials."""


def _reject_float(_: str) -> float:
    raise GateError("证据 JSON 不允许浮点数")


def _reject_constant(_: str) -> None:
    raise GateError("证据 JSON 不允许非有限数")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GateError("证据 JSON 包含重复字段")
        result[key] = value
    return result


def _read_strict_json(path: Path, label: str) -> Any:
    try:
        raw = path.read_text(encoding="utf-8")
        return json.loads(
            raw,
            object_pairs_hook=_strict_object,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except GateError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise GateError(f"{label} 不是可读取的严格 JSON") from error


def _exact_object(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise GateError(f"{label} 字段集合无效")
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validate_hex64(value: Any, label: str) -> str:
    if not isinstance(value, str) or HEX64.fullmatch(value) is None:
        raise GateError(f"{label} 不是小写 SHA-256")
    return value


def _validate_snapshot(value: Any, label: str) -> dict[str, Any]:
    snapshot = _exact_object(
        value,
        {"canonicalFormatVersion", "sourceRevision", "sourceCounts", "sourceDigests"},
        label,
    )
    if snapshot["canonicalFormatVersion"] != CANONICAL_FORMAT_VERSION:
        raise GateError(f"{label} canonical format 不是 v4")
    if not isinstance(snapshot["sourceRevision"], str) or REVISION.fullmatch(
        snapshot["sourceRevision"]
    ) is None:
        raise GateError(f"{label} revision 无效")
    counts = snapshot["sourceCounts"]
    digests = snapshot["sourceDigests"]
    if not isinstance(counts, dict) or not isinstance(digests, dict) or not counts:
        raise GateError(f"{label} counts/digests 缺失")
    if set(counts) != set(digests):
        raise GateError(f"{label} counts/digests 字段集合不一致")
    for key in sorted(counts):
        if not isinstance(key, str) or re.fullmatch(r"[a-z0-9_]+", key) is None:
            raise GateError(f"{label} digest key 无效")
        count = counts[key]
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise GateError(f"{label} count 无效")
        _validate_hex64(digests[key], f"{label} digest")
    return snapshot


def _snapshot_from_dry_run(value: Any) -> dict[str, Any]:
    dry_run = _exact_object(
        value,
        {
            "status",
            "runId",
            "canonicalFormatVersion",
            "sourceCounts",
            "sourceDigests",
            "sourceRevision",
        },
        "rehearsal sales_snapshot_dry_run result",
    )
    if dry_run["status"] != "dry_run_completed" or not isinstance(
        dry_run["runId"], str
    ) or RUN_ID.fullmatch(dry_run["runId"]) is None:
        raise GateError("rehearsal dry-run 终态或 runId 无效")
    return _validate_snapshot(
        {
            "canonicalFormatVersion": dry_run["canonicalFormatVersion"],
            "sourceRevision": dry_run["sourceRevision"],
            "sourceCounts": dry_run["sourceCounts"],
            "sourceDigests": dry_run["sourceDigests"],
        },
        "rehearsal canonical snapshot",
    )


def _rehearsal_snapshot(
    state_path: Path,
    *,
    rehearsal_id: str,
    rehearsal_root: Path,
) -> dict[str, Any]:
    if re.fullmatch(r"[0-9a-f]{12}", rehearsal_id) is None:
        raise GateError("rehearsalId 无效")
    state = _exact_object(
        _read_strict_json(state_path, "rehearsal cutover state"),
        {
            "version",
            "cutoverId",
            "sourcePathDigest",
            "createdAt",
            "updatedAt",
            "status",
            "steps",
        },
        "rehearsal cutover state",
    )
    if (
        state["version"] != "sales-local-cutover-v1"
        or state["cutoverId"] != f"rehearsal-{rehearsal_id}"
        or state["status"] != "completed"
        or not isinstance(state["sourcePathDigest"], str)
        or HEX64.fullmatch(state["sourcePathDigest"]) is None
        or not isinstance(state["steps"], list)
    ):
        raise GateError("rehearsal cutover state 身份或终态无效")
    if state_path.parent != rehearsal_root / "audit" / "cutover":
        raise GateError("rehearsal cutover state 不在唯一 audit/cutover 目录")
    names: set[str] = set()
    dry_results: list[Any] = []
    for raw_step in state["steps"]:
        step = _exact_object(raw_step, {"name", "completedAt", "result"}, "rehearsal step")
        name = step["name"]
        if not isinstance(name, str) or not name or name in names or not isinstance(
            step["result"], dict
        ):
            raise GateError("rehearsal step 缺失、重复或无效")
        names.add(name)
        if name == "sales_snapshot_dry_run":
            dry_results.append(step["result"])
    if len(dry_results) != 1:
        raise GateError("rehearsal 必须且只能包含一个 sales_snapshot_dry_run")
    return _snapshot_from_dry_run(dry_results[0])


def _compare_snapshot_evidence(
    *,
    live: Any,
    backup: Any,
    state_path: Path,
    rehearsal_id: str,
    rehearsal_root: Path,
) -> dict[str, Any]:
    live_snapshot = _validate_snapshot(live, "live D1 canonical snapshot")
    backup_snapshot = _validate_snapshot(backup, "backup D1 canonical snapshot")
    rehearsal_snapshot = _rehearsal_snapshot(
        state_path,
        rehearsal_id=rehearsal_id,
        rehearsal_root=rehearsal_root,
    )
    if backup_snapshot != rehearsal_snapshot:
        raise GateError("备份 D1 canonical snapshot 与演练 dry-run 证据不一致")
    if live_snapshot != rehearsal_snapshot:
        raise GateError("实时 D1 canonical snapshot 已偏离成功演练材料")
    snapshot_sha256 = _sha256_text(_canonical_json(live_snapshot))
    return {
        "status": "verified",
        "canonicalFormatVersion": CANONICAL_FORMAT_VERSION,
        "sourceRevision": live_snapshot["sourceRevision"],
        "snapshotSha256": snapshot_sha256,
        "digestKeyCount": len(live_snapshot["sourceDigests"]),
    }


def _load_snapshot_module(backend_dir: Path) -> tuple[Callable[..., Any], Callable[..., Any]]:
    if not backend_dir.is_absolute() or not (backend_dir / "manage.py").is_file():
        raise GateError("Django backend 目录无效")
    sys.path.insert(0, str(backend_dir))
    os.environ["DJANGO_SETTINGS_MODULE"] = "teruisi_backend.settings"
    os.environ["TERUISI_DJANGO_ENVIRONMENT"] = "test"
    os.environ["DJANGO_DEBUG"] = "true"
    os.environ["TERUISI_DJANGO_PROCESS_ROLE"] = "development"
    os.environ["TERUISI_DJANGO_EXPECT_READ_ONLY"] = "false"
    os.environ["TERUISI_DJANGO_SQLITE_PATH"] = ":memory:"
    os.environ.pop("TERUISI_DJANGO_DATABASE_URL", None)
    os.environ.pop("TERUISI_DJANGO_ERP_DATABASE_URL", None)
    os.environ.pop("TERUISI_DJANGO_INTERNAL_SECRET", None)
    try:
        import django

        django.setup()
        from sales.management.commands.migrate_sales_from_d1 import (
            CANONICAL_FORMAT_VERSION as deployed_format,
            _complete_source_snapshot,
            _open_source,
        )
    except Exception as error:
        raise GateError("无法加载部署内 v4 canonical snapshot 实现") from error
    if deployed_format != CANONICAL_FORMAT_VERSION:
        raise GateError("部署内 canonical snapshot 版本不是 v4")
    return _open_source, _complete_source_snapshot


def _capture_once(
    source: Path,
    open_source: Callable[..., Any],
    complete_source_snapshot: Callable[..., Any],
) -> dict[str, Any]:
    connection = open_source(source)
    try:
        revision, counts, digests, _, _ = complete_source_snapshot(connection, 2_000)
    finally:
        connection.rollback()
        connection.close()
    return _validate_snapshot(
        {
            "canonicalFormatVersion": CANONICAL_FORMAT_VERSION,
            "sourceRevision": f"{revision[0]}:{revision[1]}",
            "sourceCounts": counts,
            "sourceDigests": digests,
        },
        "captured D1 canonical snapshot",
    )


def _capture_stable(
    source: Path,
    open_source: Callable[..., Any],
    complete_source_snapshot: Callable[..., Any],
) -> dict[str, Any]:
    if not source.is_absolute() or source.suffix.lower() != ".sqlite" or not source.is_file():
        raise GateError("canonical snapshot 源必须是绝对普通 SQLite 文件")
    if source.is_symlink():
        raise GateError("canonical snapshot 源不得是符号链接")
    canonical_source = source.resolve(strict=True)
    first = _capture_once(canonical_source, open_source, complete_source_snapshot)
    second = _capture_once(canonical_source, open_source, complete_source_snapshot)
    if first != second:
        raise GateError("D1 canonical snapshot 在连续只读采样期间变化")
    return first


def _validate_retirement_audit(args: argparse.Namespace) -> dict[str, Any]:
    audit_path = Path(args.audit).resolve(strict=True)
    if re.fullmatch(r"[0-9a-f]{12}", args.rehearsal_id) is None:
        raise GateError("rehearsalId 无效")
    audit = _exact_object(
        _read_strict_json(audit_path, "rehearsal retirement audit"),
        {
            "version",
            "auditId",
            "cutoverId",
            "sourcePathSha256",
            "auditOutputPathSha256",
            "approvedPlanId",
            "sourceCoreEvidenceSha256",
            "recordedAt",
            "attestation",
            "smokeReceipt",
            "postgresqlPreflight",
            "migration",
            "authority",
            "retiredEvidence",
            "preservedEvidence",
            "result",
        },
        "rehearsal retirement audit",
    )
    expected_cutover = f"rehearsal-{args.rehearsal_id}"
    expected_audit = _validate_hex64(args.audit_id, "expected retirement auditId")
    expected_preserved = _validate_hex64(
        args.preserved_evidence_sha256,
        "expected preservedEvidenceSha256",
    )
    expected_attestation = _validate_hex64(
        args.attestation_payload_sha256,
        "expected attestation payload SHA-256",
    )
    expected_smoke = _validate_hex64(args.smoke_receipt_sha256, "expected smoke receipt SHA-256")
    core = dict(audit)
    core.pop("auditId")
    computed_audit = _sha256_text(_canonical_json(core))
    result = _exact_object(
        audit.get("result"),
        {
            "retiredTablesAbsent",
            "retirementTombstoneViewsPresent",
            "retiredTriggersAbsent",
            "sharedSalesRowsDeleted",
            "sharedImportRetirementGuardsPresent",
            "preservedEvidenceSha256",
        },
        "rehearsal retirement audit result",
    )
    attestation = audit.get("attestation")
    smoke = audit.get("smokeReceipt")
    preflight = audit.get("postgresqlPreflight")
    authority = audit.get("authority")
    if not all(isinstance(item, dict) for item in (attestation, smoke, preflight, authority)):
        raise GateError("rehearsal retirement audit 关键证据缺失")
    preserved_sha = _sha256_text(_canonical_json(audit.get("preservedEvidence")))
    if (
        audit.get("version") != RETIREMENT_AUDIT_VERSION
        or result.get("retiredTablesAbsent") != RETIREMENT_TOMBSTONE_VIEWS
        or result.get("retirementTombstoneViewsPresent") != RETIREMENT_TOMBSTONE_VIEWS
        or result.get("sharedImportRetirementGuardsPresent") != SHARED_IMPORT_RETIREMENT_GUARDS
        or audit.get("cutoverId") != expected_cutover
        or audit.get("auditOutputPathSha256") != _sha256_text(str(audit_path))
        or audit.get("auditId") != expected_audit
        or computed_audit != expected_audit
        or result.get("preservedEvidenceSha256") != expected_preserved
        or preserved_sha != expected_preserved
        or attestation.get("payloadSha256") != expected_attestation
        or smoke.get("fileSha256") != expected_smoke
        or authority.get("cutoverId") != expected_cutover
        or preflight.get("status") != "verified"
        or preflight.get("cutoverId") != expected_cutover
        or preflight.get("planId") != audit.get("approvedPlanId")
        or preflight.get("attestationPayloadSha256") != expected_attestation
        or preflight.get("smokeReceiptSha256") != expected_smoke
    ):
        raise GateError("rehearsal retirement audit 与 result 终态证据不一致")
    return {
        "status": "verified",
        "cutoverId": expected_cutover,
        "auditId": expected_audit,
        "preservedEvidenceSha256": expected_preserved,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    subparsers = parser.add_subparsers(dest="mode", required=True)
    live = subparsers.add_parser("verify-live", add_help=False)
    live.add_argument("--backend-dir", required=True)
    live.add_argument("--source", required=True)
    live.add_argument("--backup-source", required=True)
    live.add_argument("--rehearsal-state", required=True)
    live.add_argument("--rehearsal-root", required=True)
    live.add_argument("--rehearsal-id", required=True)
    expected = subparsers.add_parser("verify-expected-live", add_help=False)
    expected.add_argument("--backend-dir", required=True)
    expected.add_argument("--source", required=True)
    expected.add_argument("--expected-snapshot-sha256", required=True)
    evidence = subparsers.add_parser("verify-evidence", add_help=False)
    evidence.add_argument("--live-snapshot", required=True)
    evidence.add_argument("--backup-snapshot", required=True)
    evidence.add_argument("--rehearsal-state", required=True)
    evidence.add_argument("--rehearsal-root", required=True)
    evidence.add_argument("--rehearsal-id", required=True)
    audit = subparsers.add_parser("verify-retirement-audit", add_help=False)
    audit.add_argument("--audit", required=True)
    audit.add_argument("--rehearsal-id", required=True)
    audit.add_argument("--audit-id", required=True)
    audit.add_argument("--preserved-evidence-sha256", required=True)
    audit.add_argument("--attestation-payload-sha256", required=True)
    audit.add_argument("--smoke-receipt-sha256", required=True)
    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    args = _parser().parse_args()
    try:
        if args.mode == "verify-live":
            backend_dir = Path(args.backend_dir).resolve(strict=True)
            rehearsal_root = Path(args.rehearsal_root).resolve(strict=True)
            state_path = Path(args.rehearsal_state).resolve(strict=True)
            open_source, complete_source_snapshot = _load_snapshot_module(backend_dir)
            backup = _capture_stable(
                Path(args.backup_source),
                open_source,
                complete_source_snapshot,
            )
            # Capture the mutable live source last so the second stable read is
            # as close as possible to the formal mutation boundary.
            live = _capture_stable(
                Path(args.source),
                open_source,
                complete_source_snapshot,
            )
            result = _compare_snapshot_evidence(
                live=live,
                backup=backup,
                state_path=state_path,
                rehearsal_id=args.rehearsal_id,
                rehearsal_root=rehearsal_root,
            )
        elif args.mode == "verify-expected-live":
            backend_dir = Path(args.backend_dir).resolve(strict=True)
            open_source, complete_source_snapshot = _load_snapshot_module(backend_dir)
            live = _capture_stable(
                Path(args.source),
                open_source,
                complete_source_snapshot,
            )
            expected_sha256 = _validate_hex64(
                args.expected_snapshot_sha256,
                "expected canonical snapshot SHA-256",
            )
            actual_sha256 = _sha256_text(_canonical_json(live))
            if actual_sha256 != expected_sha256:
                raise GateError("写锁内实时 D1 canonical snapshot 已偏离正式批准材料")
            result = {
                "status": "verified",
                "canonicalFormatVersion": CANONICAL_FORMAT_VERSION,
                "sourceRevision": live["sourceRevision"],
                "snapshotSha256": actual_sha256,
                "digestKeyCount": len(live["sourceDigests"]),
            }
        elif args.mode == "verify-evidence":
            rehearsal_root = Path(args.rehearsal_root).resolve(strict=True)
            result = _compare_snapshot_evidence(
                live=_read_strict_json(Path(args.live_snapshot), "live snapshot fixture"),
                backup=_read_strict_json(Path(args.backup_snapshot), "backup snapshot fixture"),
                state_path=Path(args.rehearsal_state).resolve(strict=True),
                rehearsal_id=args.rehearsal_id,
                rehearsal_root=rehearsal_root,
            )
        else:
            result = _validate_retirement_audit(args)
        sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
        return 0
    except GateError as error:
        sys.stderr.write(str(error) + "\n")
        return 1
    except Exception:
        sys.stderr.write("销售 canonical evidence 门禁发生非预期失败\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
