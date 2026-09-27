from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .inventory_write_authority import (
    CUTOVER_ID_RE,
    _assert_postgres_quiet,
    _open,
    _verified_run,
)
from .migrate_inventory_from_d1 import _counts, _exclusions, _sha, _source_snapshot


RETIREMENT_VERSION = "inventory-domain-retirement-receipt-v1"
SMOKE_VERSION = "inventory-system-test-receipt-v1"
R2_EVIDENCE_VERSION = "inventory-r2-retirement-evidence-v1"
HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
RUN_ID_RE = re.compile(r"^inventory-apply-[0-9a-f]{32}$")
MAX_RECEIPT_BYTES = 64 * 1024
REQUIRED_SMOKE_CHECKS = (
    "djangoReader",
    "djangoWriterNegative",
    "publicOverview",
    "publicAgeAnalysis",
    "publicInboundMonitor",
    "publicReplenishment",
    "publicSettings",
    "publicDirectImport",
    "publicChunkUpload",
    "productsProjection",
    "systemCostConsumer",
    "aiConsumer",
    "globalSearchConsumer",
    "legacyD1Rejected",
    "legacyR2Rejected",
    "otherDomainsPreserved",
)
TOMBSTONE_VIEWS = (
    "erp_inventory_age_lines",
    "inventory_age_metrics",
    "inventory_import_batches",
    "inventory_stock_lines",
    "inventory_write_authority",
    "replenishment_plan_items",
)
RETIREMENT_GUARDS = tuple(
    f"inventory_retired_{surface}_{operation}_guard"
    for surface in (
        "age_batches",
        "settings",
        "fingerprints",
        "attempts",
        "heads",
        "uploads",
        "upload_chunks",
        "upload_results",
    )
    for operation in ("insert", "update", "delete")
)
SMOKE_RECEIPT_KEYS = {
    "version",
    "status",
    "cutoverId",
    "migrationRunId",
    "sourceDigest",
    "targetDigest",
    "workerBuildSha256",
    "checks",
    "recordedAt",
}
R2_EVIDENCE_KEYS = {
    "version",
    "status",
    "prefix",
    "objectCount",
    "objectBytes",
    "multipartUploadCount",
    "multipartPartCount",
    "objectsDigest",
    "sourcePathSha256",
    "recordedAt",
}
AGE_SCOPE_KEYS = (
    "ce499a195aa16f0f763b11768a0c897ff8f51beb6d4c3a35e6f5dcbb8795055d",
    "c8d8ffcac2953c3a5b5e4cec882a9553048c2d95564642441939ae6bb007b8a4",
)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _hash_json(value: object) -> str:
    return _sha256(_canonical_bytes(value))


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key: {key}")
        result[key] = value
    return result


def _load_json_file(path: Path, label: str) -> tuple[dict[str, object], bytes]:
    if not path.is_file() or path.is_symlink():
        raise CommandError(f"{label}必须是普通文件。")
    payload = path.read_bytes()
    if not payload or len(payload) > MAX_RECEIPT_BYTES:
        raise CommandError(f"{label}为空或超过大小上限。")
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise CommandError(f"{label}不是严格 UTF-8 JSON。") from error
    if not isinstance(value, dict):
        raise CommandError(f"{label}不是 JSON 对象。")
    return value, payload


def _recent_timestamp(value: object, label: str) -> None:
    recorded_at = parse_datetime(str(value or ""))
    now = timezone.now()
    if (
        recorded_at is None
        or timezone.is_naive(recorded_at)
        or recorded_at < now - timedelta(minutes=30)
        or recorded_at > now + timedelta(minutes=2)
    ):
        raise CommandError(f"{label}已过期或时间无效。")


def _load_smoke_receipt(
    path: Path, *, cutover_id: str, run_id: str, source_digest: str
) -> tuple[dict[str, object], str]:
    value, payload = _load_json_file(path, "系统测试 receipt")
    checks = value.get("checks")
    if (
        set(value) != SMOKE_RECEIPT_KEYS
        or value.get("version") != SMOKE_VERSION
        or value.get("status") != "passed"
        or value.get("cutoverId") != cutover_id
        or value.get("migrationRunId") != run_id
        or value.get("sourceDigest") != source_digest
        or value.get("targetDigest") != source_digest
        or not HEX_64_RE.fullmatch(str(value.get("workerBuildSha256") or ""))
        or not isinstance(checks, dict)
        or set(checks) != set(REQUIRED_SMOKE_CHECKS)
        or any(checks.get(name) != "passed" for name in REQUIRED_SMOKE_CHECKS)
    ):
        raise CommandError("系统测试 receipt 未证明完整库存垂直链路通过。")
    _recent_timestamp(value.get("recordedAt"), "系统测试 receipt")
    return value, _sha256(payload)


def _load_r2_evidence(path: Path) -> tuple[dict[str, object], str]:
    value, payload = _load_json_file(path, "库存 R2 退役证据")
    if (
        set(value) != R2_EVIDENCE_KEYS
        or value.get("version") != R2_EVIDENCE_VERSION
        or value.get("status") != "passed"
        or value.get("prefix") != "inventory-upload/"
        or value.get("objectCount") != 0
        or value.get("objectBytes") != 0
        or value.get("multipartUploadCount") != 0
        or value.get("multipartPartCount") != 0
        or not HEX_64_RE.fullmatch(str(value.get("objectsDigest") or ""))
        or not HEX_64_RE.fullmatch(str(value.get("sourcePathSha256") or ""))
    ):
        raise CommandError("库存 R2 退役证据未证明库存前缀为空。")
    _recent_timestamp(value.get("recordedAt"), "库存 R2 退役证据")
    return value, _sha256(payload)


def _migration_path() -> Path:
    path = (
        Path(settings.BASE_DIR).parent
        / "drizzle"
        / "0102_inventory_domain_retirement.sql"
    ).resolve()
    if not path.is_file():
        raise CommandError("缺少受控 D1 库存退役迁移。")
    return path


def _query_rows(source: sqlite3.Connection, query: str, parameters=()) -> list[dict[str, object]]:
    return [dict(row) for row in source.execute(query, parameters).fetchall()]


def _shared_receipt(source: sqlite3.Connection) -> dict[str, object]:
    age_placeholders = ",".join("?" for _ in AGE_SCOPE_KEYS)
    sections = {
        "fingerprints_other": _query_rows(
            source,
            "SELECT * FROM import_content_fingerprints WHERE NOT "
            "(domain='inventory-stock' OR (domain='erp-reference' AND "
            "json_extract(scope_json,'$.source')='inventory_age')) ORDER BY sequence",
        ),
        "attempts_other": _query_rows(
            source,
            "SELECT * FROM import_content_attempts WHERE NOT "
            "(domain='inventory-stock' OR (domain='erp-reference' AND "
            "json_extract(scope_json,'$.source')='inventory_age')) ORDER BY sequence",
        ),
        "heads_other": _query_rows(
            source,
            "SELECT * FROM import_scope_heads h WHERE NOT "
            "(domain='inventory-stock' OR (domain='erp-reference' AND "
            f"(scope_key IN ({age_placeholders}) OR COALESCE(current_batch_id,'') LIKE 'inventory_age:%' "
            "OR EXISTS (SELECT 1 FROM erp_reference_import_batches b "
            "WHERE b.id=h.current_batch_id AND b.source_key='inventory_age')))) "
            "ORDER BY domain,scope_key",
            AGE_SCOPE_KEYS,
        ),
        "uploads_other": _query_rows(
            source,
            "SELECT * FROM inventory_import_uploads WHERE NOT "
            "(fingerprint LIKE 'inventory-v1:%' OR fingerprint LIKE 'erp:inventory_age:%') "
            "ORDER BY id",
        ),
        "upload_chunks_other": _query_rows(
            source,
            "SELECT c.* FROM inventory_import_upload_chunks c "
            "JOIN inventory_import_uploads u ON u.id=c.upload_id WHERE NOT "
            "(u.fingerprint LIKE 'inventory-v1:%' OR u.fingerprint LIKE 'erp:inventory_age:%') "
            "ORDER BY c.upload_id,c.chunk_index",
        ),
        "upload_results_other": _query_rows(
            source,
            "SELECT r.* FROM inventory_import_upload_results r "
            "JOIN inventory_import_uploads u ON u.id=r.upload_id WHERE NOT "
            "(u.fingerprint LIKE 'inventory-v1:%' OR u.fingerprint LIKE 'erp:inventory_age:%') "
            "ORDER BY r.upload_id",
        ),
        "erp_batches_other": _query_rows(
            source,
            "SELECT * FROM erp_reference_import_batches WHERE source_key<>'inventory_age' ORDER BY id",
        ),
        "settings_other": _query_rows(
            source,
            "SELECT * FROM system_settings WHERE key<>'operating' ORDER BY key",
        ),
        "retirement_receipts_other": _query_rows(
            source,
            "SELECT * FROM domain_retirement_receipts WHERE domain<>'inventory' ORDER BY domain",
        ) if source.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='domain_retirement_receipts'"
        ).fetchone() else [],
    }
    counts = {name: len(rows) for name, rows in sections.items()}
    digests = {name: _hash_json(rows) for name, rows in sections.items()}
    return {
        "counts": counts,
        "digests": digests,
        "digest": _hash_json({"counts": counts, "digests": digests}),
    }


def _inventory_upload_chunk_count(source: sqlite3.Connection) -> int:
    return int(source.execute(
        "SELECT COUNT(*) FROM inventory_import_upload_chunks c "
        "JOIN inventory_import_uploads u ON u.id=c.upload_id WHERE "
        "u.fingerprint LIKE 'inventory-v1:%' OR u.fingerprint LIKE 'erp:inventory_age:%'"
    ).fetchone()[0])


def _build_plan(
    source: sqlite3.Connection,
    source_path: Path,
    *,
    cutover_id: str,
    approved_run_id: str,
    smoke_path: Path,
    r2_evidence_path: Path,
) -> dict[str, object]:
    snapshot = _source_snapshot(source_path)
    source_digest = _sha(snapshot)
    counts = _counts(snapshot)
    exclusions = _exclusions(snapshot)
    d1_authority = source.execute(
        "SELECT owner,epoch,cutover_id FROM inventory_write_authority WHERE id=1"
    ).fetchone()
    if (
        d1_authority is None
        or str(d1_authority["owner"]) != "postgresql"
        or str(d1_authority["cutover_id"]) != cutover_id
    ):
        raise CommandError("D1 库存 authority 不属于本次已激活 cutover。")
    inventory_chunks = _inventory_upload_chunk_count(source)
    if inventory_chunks:
        raise CommandError("D1 库存旧分片对象键尚未在切换前受控清理。")
    with transaction.atomic():
        target = _assert_postgres_quiet()
        run = _verified_run(source_path, source_digest, counts, exclusions)
        if run.id != approved_run_id:
            raise CommandError("approved-run-id 与终态 D1 库存快照不一致。")
        if (
            target.status != "postgres"
            or target.cutover_id != cutover_id
            or target.migration_verify_run_id != run.id
            or target.authority_epoch is None
        ):
            raise CommandError("PostgreSQL 库存 authority 不属于本次完成迁移。")
        target_epoch = str(target.authority_epoch)

    smoke, smoke_sha = _load_smoke_receipt(
        smoke_path,
        cutover_id=cutover_id,
        run_id=run.id,
        source_digest=source_digest,
    )
    _r2_evidence, r2_evidence_sha = _load_r2_evidence(r2_evidence_path)
    shared = _shared_receipt(source)
    migration = _migration_path().read_bytes()
    migration_sha = _sha256(migration)
    preserved_sha = _hash_json({
        "sourceDigest": source_digest,
        "counts": counts,
        "exclusions": exclusions,
        "r2EvidenceSha256": r2_evidence_sha,
    })
    attestation_sha = _hash_json({
        "cutoverId": cutover_id,
        "migrationRunId": run.id,
        "sourceDigest": source_digest,
        "postgresAuthorityEpoch": target_epoch,
        "r2EvidenceSha256": r2_evidence_sha,
    })
    preflight = {
        "d1Owner": str(d1_authority["owner"]),
        "d1Epoch": int(d1_authority["epoch"]),
        "cutoverId": cutover_id,
        "postgresAuthorityEpoch": target_epoch,
        "migrationRunId": run.id,
        "sourceDigest": source_digest,
        "inventoryUploadChunks": inventory_chunks,
        "r2EvidenceSha256": r2_evidence_sha,
        "shared": shared,
    }
    preflight_sha = _hash_json(preflight)
    audit_id = _hash_json({
        "version": RETIREMENT_VERSION,
        "cutoverId": cutover_id,
        "sourceDigest": source_digest,
        "migrationSha256": migration_sha,
        "smokeReceiptSha256": smoke_sha,
        "r2EvidenceSha256": r2_evidence_sha,
        "preflightEvidenceSha256": preflight_sha,
    })
    plan_fields = {
        "version": RETIREMENT_VERSION,
        "cutoverId": cutover_id,
        "approvedRunId": run.id,
        "sourceDigest": source_digest,
        "attestationSha256": attestation_sha,
        "smokeReceiptSha256": smoke_sha,
        "preflightEvidenceSha256": preflight_sha,
        "migrationSha256": migration_sha,
        "auditId": audit_id,
        "preservedEvidenceSha256": preserved_sha,
        "workerBuildSha256": smoke["workerBuildSha256"],
        "r2EvidenceSha256": r2_evidence_sha,
    }
    return {
        **plan_fields,
        "planId": _hash_json(plan_fields),
        "counts": counts,
        "exclusions": exclusions,
        "shared": shared,
    }


def _completed_receipt(source: sqlite3.Connection) -> dict[str, object] | None:
    present = source.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='domain_retirement_receipts'"
    ).fetchone()
    if present is None:
        return None
    row = source.execute(
        "SELECT domain,version,status,cutover_id,plan_id,attestation_sha256,"
        "smoke_receipt_sha256,preflight_evidence_sha256,migration_sha256,audit_id,"
        "preserved_evidence_sha256,created_at,completed_at "
        "FROM domain_retirement_receipts WHERE domain='inventory'"
    ).fetchone()
    return dict(row) if row is not None else None


def _verify_retired(source: sqlite3.Connection, receipt: dict[str, object]) -> None:
    if (
        receipt.get("version") != RETIREMENT_VERSION
        or receipt.get("status") != "completed"
        or not receipt.get("completed_at")
        or not HEX_64_RE.fullmatch(str(receipt.get("plan_id") or ""))
    ):
        raise CommandError("D1 库存退役 receipt 无效。")
    views = {
        str(row[0]): str(row[1] or "")
        for row in source.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='view'"
        )
        if str(row[0]) in TOMBSTONE_VIEWS
    }
    if set(views) != set(TOMBSTONE_VIEWS) or any(
        "inventory-domain-retired-v1" not in sql for sql in views.values()
    ):
        raise CommandError("D1 库存退役 tombstone view 集合不完整。")
    for view in TOMBSTONE_VIEWS:
        if int(source.execute(f'SELECT COUNT(*) FROM "{view}"').fetchone()[0]) != 0:
            raise CommandError("D1 库存退役 tombstone view 非空。")
    guards = {
        str(row[0]) for row in source.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' "
            "AND name LIKE 'inventory_retired_%_guard'"
        )
    }
    if guards != set(RETIREMENT_GUARDS):
        raise CommandError("D1 库存退役共享表 guard 集合不完整。")
    if _query_rows(
        source,
        "SELECT * FROM import_content_fingerprints WHERE domain='inventory-stock' "
        "OR (domain='erp-reference' AND json_extract(scope_json,'$.source')='inventory_age')",
    ) or _query_rows(
        source,
        "SELECT * FROM import_content_attempts WHERE domain='inventory-stock' "
        "OR (domain='erp-reference' AND json_extract(scope_json,'$.source')='inventory_age')",
    ):
        raise CommandError("D1 共享导入表仍含库存域行。")
    if int(source.execute(
        "SELECT COUNT(*) FROM inventory_import_uploads WHERE "
        "fingerprint LIKE 'inventory-v1:%' OR fingerprint LIKE 'erp:inventory_age:%'"
    ).fetchone()[0]):
        raise CommandError("D1 共享上传表仍含库存域行。")
    if int(source.execute(
        "SELECT COUNT(*) FROM erp_reference_import_batches WHERE source_key='inventory_age'"
    ).fetchone()[0]) or int(source.execute(
        "SELECT COUNT(*) FROM system_settings WHERE key='operating'"
    ).fetchone()[0]):
        raise CommandError("D1 共享 ERP/设置表仍含库存域行。")


def _statements(payload: bytes) -> list[str]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CommandError("D1 库存退役迁移不是 UTF-8。") from error
    statements = [
        item.strip() for item in text.split("--> statement-breakpoint") if item.strip()
    ]
    if len(statements) < 70 or not statements[0].startswith(
        "-- Operator-only terminal retirement"
    ):
        raise CommandError("D1 库存退役迁移结构不符合契约。")
    return statements


def _write_audit(path: Path, payload: dict[str, object]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ) + "\n"
    handle, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Command(BaseCommand):
    help = "Plan or execute terminal D1/R2 retirement after inventory PostgreSQL cutover."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--source", required=True)
        parser.add_argument("--cutover-id", required=True)
        parser.add_argument("--approved-run-id", required=True)
        parser.add_argument("--smoke-receipt", required=True)
        parser.add_argument("--r2-evidence", required=True)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--approved-plan-id", default="")
        parser.add_argument("--audit-output", default="")

    def handle(self, *args: Any, **options: Any) -> None:
        if (
            settings.DJANGO_ENVIRONMENT == "production"
            and settings.DJANGO_PROCESS_ROLE != "migration_writer"
        ):
            raise CommandError("生产 D1 库存退役只能由 migration_writer 进程角色操作。")
        source_input = Path(str(options["source"])).expanduser()
        smoke_input = Path(str(options["smoke_receipt"])).expanduser()
        r2_input = Path(str(options["r2_evidence"])).expanduser()
        cutover_id = str(options["cutover_id"] or "").strip()
        run_id = str(options["approved_run_id"] or "").strip()
        apply = bool(options["apply"])
        approved_plan_id = str(options["approved_plan_id"] or "").strip()
        audit_output = str(options["audit_output"] or "").strip()
        for path, label in (
            (source_input, "D1 库存源"),
            (smoke_input, "系统测试 receipt"),
            (r2_input, "库存 R2 退役证据"),
        ):
            if not path.is_file() or path.is_symlink():
                raise CommandError(f"{label}必须是普通文件。")
        source_path = source_input.resolve()
        smoke_path = smoke_input.resolve()
        r2_path = r2_input.resolve()
        if not CUTOVER_ID_RE.fullmatch(cutover_id) or not RUN_ID_RE.fullmatch(run_id):
            raise CommandError("cutover-id 或 approved-run-id 无效。")
        if apply != bool(approved_plan_id):
            raise CommandError("--apply 必须且只能与 --approved-plan-id 同时使用。")
        if approved_plan_id and not HEX_64_RE.fullmatch(approved_plan_id):
            raise CommandError("approved-plan-id 无效。")
        if audit_output and not apply:
            raise CommandError("audit-output 只允许在 apply 时使用。")

        source = _open(source_path, writable=apply)
        try:
            completed = _completed_receipt(source)
            if completed is not None:
                _verify_retired(source, completed)
                if (
                    completed["cutover_id"] != cutover_id
                    or completed["plan_id"] != (approved_plan_id or completed["plan_id"])
                ):
                    raise CommandError("既有 D1 库存退役 receipt 与本次请求冲突。")
                source.rollback()
                result = {
                    "status": "duplicate" if apply else "retired",
                    "version": completed["version"],
                    "cutoverId": completed["cutover_id"],
                    "planId": completed["plan_id"],
                    "auditId": completed["audit_id"],
                }
                if audit_output:
                    _write_audit(Path(audit_output), result)
                self.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
                return

            plan = _build_plan(
                source,
                source_path,
                cutover_id=cutover_id,
                approved_run_id=run_id,
                smoke_path=smoke_path,
                r2_evidence_path=r2_path,
            )
            if not apply:
                source.rollback()
                self.stdout.write(json.dumps({**plan, "status": "planned"}, ensure_ascii=False, sort_keys=True))
                return
            if plan["planId"] != approved_plan_id:
                raise CommandError("approved-plan-id 与当前退役前状态不一致。")

            migration = _migration_path().read_bytes()
            statements = _statements(migration)
            for statement in statements[:4]:
                source.execute(statement)
            created_at = timezone.now().isoformat().replace("+00:00", "Z")
            source.execute(
                "INSERT INTO domain_retirement_receipts "
                "(domain,version,status,cutover_id,plan_id,attestation_sha256,"
                "smoke_receipt_sha256,preflight_evidence_sha256,migration_sha256,"
                "audit_id,preserved_evidence_sha256,created_at,completed_at) "
                "VALUES ('inventory',?,'approved',?,?,?,?,?,?,?,?,?,NULL)",
                (
                    RETIREMENT_VERSION,
                    cutover_id,
                    approved_plan_id,
                    plan["attestationSha256"],
                    plan["smokeReceiptSha256"],
                    plan["preflightEvidenceSha256"],
                    plan["migrationSha256"],
                    plan["auditId"],
                    plan["preservedEvidenceSha256"],
                    created_at,
                ),
            )
            for statement in statements[4:]:
                source.execute(statement)
            receipt = _completed_receipt(source)
            if receipt is None:
                raise CommandError("D1 库存退役未生成完成 receipt。")
            _verify_retired(source, receipt)
            if _shared_receipt(source) != plan["shared"]:
                raise CommandError("D1 库存退役改变了其他域共享状态。")
            source.commit()
            result = {
                "status": "retired",
                "version": RETIREMENT_VERSION,
                "cutoverId": cutover_id,
                "approvedRunId": run_id,
                "planId": approved_plan_id,
                "auditId": plan["auditId"],
                "sourceDigest": plan["sourceDigest"],
                "migrationSha256": plan["migrationSha256"],
                "r2EvidenceSha256": plan["r2EvidenceSha256"],
                "shared": plan["shared"],
                "completedAt": receipt["completed_at"],
            }
            if audit_output:
                _write_audit(Path(audit_output), result)
            self.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True))
        except sqlite3.DatabaseError as error:
            source.rollback()
            raise CommandError("D1 库存退役事务失败。") from error
        finally:
            source.close()
