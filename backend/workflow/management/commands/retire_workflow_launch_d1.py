from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from workflow.models import WorkflowMigrationRun

from .migrate_workflow_launch_from_d1 import GENERATION_VERSION, _counts, _sha, _source_snapshot
from .workflow_write_authority import CUTOVER_ID_RE, RUN_ID_RE, _assert_postgres_quiet


RETIREMENT_VERSION = "workflow-launch-domain-retirement-receipt-v1"
SMOKE_VERSION = "workflow-launch-system-test-receipt-v1"
R2_EVIDENCE_VERSION = "workflow-launch-r2-retirement-evidence-v1"
HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_RECEIPT_BYTES = 64 * 1024
R2_PATTERNS = ("workflow-launch/%", "workflow-launch-%", "new-product/%", "new-product-%")
REQUIRED_SMOKE_CHECKS = (
    "djangoReader",
    "djangoWriterNegative",
    "publicLaunchProjects",
    "publicLaunchProjectDetail",
    "publicLegacyLaunchListEmpty",
    "publicLegacyLaunchCreateRejected",
    "globalSearchConsumer",
    "aiConsumer",
    "legacyD1Rejected",
    "legacyR2Rejected",
    "otherWorkflowDomainsPreserved",
)
TOMBSTONE_VIEW = "workflow_launch_write_authority"
RETIREMENT_GUARDS = {
    "workflow_launch_retired_records_insert_guard",
    "workflow_launch_retired_records_update_guard",
    "workflow_launch_retired_records_delete_guard",
}


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


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


def _load_smoke_receipt(path: Path, *, cutover_id: str, run_id: str, source_digest: str) -> tuple[dict[str, object], str]:
    value, payload = _load_json_file(path, "新品终态系统测试 receipt")
    checks = value.get("checks")
    if (
        set(value) != {"version", "status", "cutoverId", "migrationRunId", "sourceDigest", "workerBuildSha256", "checks", "recordedAt"}
        or value.get("version") != SMOKE_VERSION
        or value.get("status") != "passed"
        or value.get("cutoverId") != cutover_id
        or value.get("migrationRunId") != run_id
        or value.get("sourceDigest") != source_digest
        or not HEX_64_RE.fullmatch(str(value.get("workerBuildSha256") or ""))
        or not isinstance(checks, dict)
        or set(checks) != set(REQUIRED_SMOKE_CHECKS)
        or any(checks.get(name) != "passed" for name in REQUIRED_SMOKE_CHECKS)
    ):
        raise CommandError("系统测试 receipt 未证明新品完整垂直链路通过。")
    _recent_timestamp(value.get("recordedAt"), "新品终态系统测试 receipt")
    return value, _sha256(payload)


def _load_r2_evidence(path: Path) -> tuple[dict[str, object], str]:
    value, payload = _load_json_file(path, "新品 R2 退役证据")
    patterns = value.get("patterns")
    if (
        set(value) != {"version", "status", "patterns", "totalObjectCount", "matchesDigest", "sourcePathSha256", "recordedAt"}
        or value.get("version") != R2_EVIDENCE_VERSION
        or value.get("status") != "passed"
        or not isinstance(patterns, dict)
        or set(patterns) != set(R2_PATTERNS)
        or not isinstance(value.get("totalObjectCount"), int)
        or int(value["totalObjectCount"]) < 0
        or not HEX_64_RE.fullmatch(str(value.get("matchesDigest") or ""))
        or not HEX_64_RE.fullmatch(str(value.get("sourcePathSha256") or ""))
    ):
        raise CommandError("新品 R2 退役证据结构无效。")
    for pattern in R2_PATTERNS:
        counts = patterns.get(pattern)
        if not isinstance(counts, dict) or set(counts) != {
            "objectCount", "objectBytes", "multipartUploadCount", "multipartPartCount"
        } or any(not isinstance(counts.get(key), int) or counts[key] != 0 for key in counts):
            raise CommandError("新品 R2 退役证据未证明候选命名空间为空。")
    if value["matchesDigest"] != _hash_json(patterns):
        raise CommandError("新品 R2 退役证据摘要不一致。")
    _recent_timestamp(value.get("recordedAt"), "新品 R2 退役证据")
    return value, _sha256(payload)


def _historically_verified_run(
    source_digest: str,
    counts: dict[str, int],
    approved_run_id: str,
) -> WorkflowMigrationRun:
    """Validate the immutable migration receipt without re-hashing mutable live data."""
    expected_run_id = f"workflow-{source_digest[:32]}"
    run = WorkflowMigrationRun.objects.filter(id=approved_run_id).first()
    if (
        run is None
        or approved_run_id != expected_run_id
        or run.mode != "apply"
        or run.status != "verified"
        or run.approved_run_id != expected_run_id
        or run.source_snapshot_digest != source_digest
        or run.target_snapshot_digest != source_digest
        or run.source_counts != counts
        or run.target_counts != counts
        or run.completed_at is None
        or run.manifest.get("version") != GENERATION_VERSION
        or run.manifest.get("sourceDigest") != source_digest
    ):
        raise CommandError("D1 新品事实没有匹配的历史已复验迁移凭证。")
    return run


def _migration_path() -> Path:
    path = (Path(settings.BASE_DIR).parent / "drizzle" / "0104_workflow_launch_domain_retirement.sql").resolve()
    if not path.is_file():
        raise CommandError("缺少受控 D1 新品退役迁移。")
    return path


def _open(path: Path, *, writable: bool) -> sqlite3.Connection:
    if writable:
        connection = sqlite3.connect(path, timeout=30, isolation_level=None)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
    else:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        connection.execute("BEGIN")
    connection.row_factory = sqlite3.Row
    return connection


def _rows(source: sqlite3.Connection, query: str, parameters=()) -> list[dict[str, object]]:
    return [dict(row) for row in source.execute(query, parameters).fetchall()]


def _preserved_workflow(source: sqlite3.Connection) -> dict[str, object]:
    sections = {
        "records": _rows(source, "SELECT * FROM workflow_operation_records WHERE record_type<>'launch' ORDER BY id"),
        "activities": _rows(source, "SELECT a.* FROM workflow_operation_activities a WHERE NOT EXISTS (SELECT 1 FROM workflow_operation_records r WHERE r.id=a.record_id AND r.record_type='launch') ORDER BY a.id"),
        "receipts": _rows(source, "SELECT * FROM domain_retirement_receipts WHERE domain<>'workflow-launch' ORDER BY domain")
        if source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='domain_retirement_receipts'").fetchone() else [],
    }
    counts = {key: len(rows) for key, rows in sections.items()}
    digests = {key: _hash_json(rows) for key, rows in sections.items()}
    return {"counts": counts, "digests": digests, "digest": _hash_json({"counts": counts, "digests": digests})}


def _completed_receipt(source: sqlite3.Connection) -> dict[str, object] | None:
    if source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='domain_retirement_receipts'").fetchone() is None:
        return None
    row = source.execute("SELECT * FROM domain_retirement_receipts WHERE domain='workflow-launch'").fetchone()
    return dict(row) if row is not None else None


def _verify_retired(source: sqlite3.Connection, receipt: dict[str, object]) -> None:
    if receipt.get("version") != RETIREMENT_VERSION or receipt.get("status") != "completed" or not receipt.get("completed_at"):
        raise CommandError("D1 新品退役 receipt 无效。")
    view = source.execute("SELECT sql FROM sqlite_master WHERE type='view' AND name=?", (TOMBSTONE_VIEW,)).fetchone()
    if view is None or "workflow-launch-domain-retired-v1" not in str(view[0] or ""):
        raise CommandError("D1 新品 authority tombstone 不完整。")
    if int(source.execute(f'SELECT COUNT(*) FROM "{TOMBSTONE_VIEW}"').fetchone()[0]) != 0:
        raise CommandError("D1 新品 authority tombstone 非空。")
    guards = {str(row[0]) for row in source.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'workflow_launch_retired_%_guard'")}
    if guards != RETIREMENT_GUARDS:
        raise CommandError("D1 新品永久 guard 集合不完整。")
    if int(source.execute("SELECT COUNT(*) FROM workflow_operation_records WHERE record_type='launch'").fetchone()[0]):
        raise CommandError("D1 仍含旧新品记录。")
    if int(source.execute("SELECT COUNT(*) FROM workflow_operation_activities a WHERE NOT EXISTS (SELECT 1 FROM workflow_operation_records r WHERE r.id=a.record_id)").fetchone()[0]):
        raise CommandError("D1 退役后存在无主运营活动。")


def _statements(payload: bytes) -> list[str]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CommandError("D1 新品退役迁移不是 UTF-8。") from error
    statements = [item.strip() for item in text.split("--> statement-breakpoint") if item.strip()]
    if len(statements) < 20 or not statements[0].startswith("-- Operator-only terminal retirement"):
        raise CommandError("D1 新品退役迁移结构不符合契约。")
    return statements


def _write_audit(path: Path, payload: dict[str, object]) -> None:
    path = path.expanduser().resolve()
    if path.exists():
        raise CommandError("新品退役审计输出已存在，拒绝覆盖。")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class Command(BaseCommand):
    help = "Plan or execute terminal D1/R2 retirement for workflow launch after PostgreSQL cutover."

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
        if settings.DJANGO_ENVIRONMENT == "production" and settings.DJANGO_PROCESS_ROLE != "migration_writer":
            raise CommandError("生产 D1 新品退役只能由 migration_writer 进程角色操作。")
        source_path = Path(str(options["source"])).expanduser().resolve()
        smoke_path = Path(str(options["smoke_receipt"])).expanduser().resolve()
        r2_path = Path(str(options["r2_evidence"])).expanduser().resolve()
        cutover_id = str(options["cutover_id"] or "").strip()
        run_id = str(options["approved_run_id"] or "").strip()
        apply = bool(options["apply"])
        approved_plan_id = str(options["approved_plan_id"] or "").strip()
        audit_output = str(options["audit_output"] or "").strip()
        if not source_path.is_file() or source_path.is_symlink():
            raise CommandError("D1 新品源必须是普通文件。")
        if not CUTOVER_ID_RE.fullmatch(cutover_id) or not RUN_ID_RE.fullmatch(run_id):
            raise CommandError("cutover-id 或 approved-run-id 无效。")
        if apply != bool(approved_plan_id) or (approved_plan_id and not HEX_64_RE.fullmatch(approved_plan_id)):
            raise CommandError("--apply 必须且只能与有效 --approved-plan-id 同时使用。")
        if audit_output and not apply:
            raise CommandError("audit-output 只允许在 apply 时使用。")

        source = _open(source_path, writable=apply)
        try:
            completed = _completed_receipt(source)
            if completed is not None:
                _verify_retired(source, completed)
                if completed["cutover_id"] != cutover_id or approved_plan_id and completed["plan_id"] != approved_plan_id:
                    raise CommandError("既有 D1 新品退役 receipt 与本次请求冲突。")
                source.rollback()
                result = {"status": "duplicate" if apply else "retired", "version": completed["version"], "cutoverId": completed["cutover_id"], "planId": completed["plan_id"], "auditId": completed["audit_id"]}
                if audit_output:
                    _write_audit(Path(audit_output), result)
                self.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
                return

            snapshot = _source_snapshot(source_path, require_sealed=False)
            source_digest = _sha(snapshot)
            counts = _counts(snapshot)
            authority = source.execute("SELECT owner,epoch,cutover_id FROM workflow_launch_write_authority WHERE id=1").fetchone()
            if authority is None or authority["owner"] != "postgresql" or authority["cutover_id"] != cutover_id:
                raise CommandError("D1 新品 authority 不属于本次已激活 cutover。")
            with transaction.atomic():
                target = _assert_postgres_quiet()
                run = _historically_verified_run(source_digest, counts, run_id)
                if run.id != run_id or target.status != "postgres" or target.cutover_id != cutover_id or target.migration_verify_run_id != run.id:
                    raise CommandError("PostgreSQL 新品 authority 不属于本次完成迁移。")
                target_epoch = str(target.authority_epoch)
            smoke, smoke_sha = _load_smoke_receipt(smoke_path, cutover_id=cutover_id, run_id=run_id, source_digest=source_digest)
            _r2, r2_sha = _load_r2_evidence(r2_path)
            preserved = _preserved_workflow(source)
            migration = _migration_path().read_bytes()
            migration_sha = _sha256(migration)
            attestation_sha = _hash_json({"cutoverId": cutover_id, "migrationRunId": run_id, "sourceDigest": source_digest, "postgresAuthorityEpoch": target_epoch, "r2EvidenceSha256": r2_sha})
            preflight_sha = _hash_json({"d1Owner": authority["owner"], "d1Epoch": authority["epoch"], "counts": counts, "preserved": preserved, "r2EvidenceSha256": r2_sha})
            audit_id = _hash_json({"version": RETIREMENT_VERSION, "cutoverId": cutover_id, "sourceDigest": source_digest, "migrationSha256": migration_sha, "smokeReceiptSha256": smoke_sha, "r2EvidenceSha256": r2_sha, "preflightEvidenceSha256": preflight_sha})
            plan_fields = {"version": RETIREMENT_VERSION, "cutoverId": cutover_id, "approvedRunId": run_id, "sourceDigest": source_digest, "attestationSha256": attestation_sha, "smokeReceiptSha256": smoke_sha, "preflightEvidenceSha256": preflight_sha, "migrationSha256": migration_sha, "auditId": audit_id, "preservedEvidenceSha256": preserved["digest"], "workerBuildSha256": smoke["workerBuildSha256"], "r2EvidenceSha256": r2_sha}
            plan = {**plan_fields, "planId": _hash_json(plan_fields), "counts": counts, "preserved": preserved}
            if not apply:
                source.rollback()
                self.stdout.write(json.dumps({**plan, "status": "planned"}, ensure_ascii=False, sort_keys=True))
                return
            if plan["planId"] != approved_plan_id:
                raise CommandError("approved-plan-id 与当前退役前状态不一致。")

            statements = _statements(migration)
            for statement in statements[:4]:
                source.execute(statement)
            source.execute(
                "INSERT INTO domain_retirement_receipts (domain,version,status,cutover_id,plan_id,attestation_sha256,smoke_receipt_sha256,preflight_evidence_sha256,migration_sha256,audit_id,preserved_evidence_sha256,created_at,completed_at) VALUES ('workflow-launch',?,'approved',?,?,?,?,?,?,?,?,?,NULL)",
                (RETIREMENT_VERSION, cutover_id, approved_plan_id, attestation_sha, smoke_sha, preflight_sha, migration_sha, audit_id, preserved["digest"], timezone.now().isoformat()),
            )
            for statement in statements[4:]:
                source.execute(statement)
            receipt = _completed_receipt(source)
            if receipt is None:
                raise CommandError("D1 新品退役未生成完成 receipt。")
            _verify_retired(source, receipt)
            if _preserved_workflow(source) != preserved:
                raise CommandError("D1 新品退役改变了其他运营事务状态。")
            source.commit()
            result = {"status": "retired", "version": RETIREMENT_VERSION, "cutoverId": cutover_id, "approvedRunId": run_id, "planId": approved_plan_id, "auditId": audit_id, "sourceDigest": source_digest, "migrationSha256": migration_sha, "r2EvidenceSha256": r2_sha, "preserved": preserved, "completedAt": receipt["completed_at"]}
            if audit_output:
                _write_audit(Path(audit_output), result)
            self.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True))
        except sqlite3.DatabaseError as error:
            source.rollback()
            raise CommandError("D1 新品退役事务失败。") from error
        finally:
            source.close()
