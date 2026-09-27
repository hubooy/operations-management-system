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

from netshop.models import NetshopDataRevision

from .migrate_netshop_from_d1 import (
    _canonical_bytes,
    _open_source,
    _rows,
    _snapshot,
)
from .netshop_write_authority import (
    CUTOVER_ID_RE,
    _assert_postgres_quiet,
    _open_writable,
    _source_receipt,
    _verified_migration,
)


RETIREMENT_VERSION = "netshop-domain-retirement-receipt-v1"
SMOKE_VERSION = "netshop-system-test-receipt-v1"
HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
REVISION_RE = re.compile(r"^\d+:[0-9a-f]{12}$")
MAX_RECEIPT_BYTES = 64 * 1024
REQUIRED_SMOKE_CHECKS = (
    "djangoReader",
    "djangoWriterNegative",
    "publicOverview",
    "publicProducts",
    "publicPromotion",
    "marketProjection",
    "customerServiceProjection",
    "globalSearchProjection",
    "legacyD1Rejected",
)
TOMBSTONE_VIEWS = (
    "netshop_asset_upload_chunks",
    "netshop_asset_upload_results",
    "netshop_asset_uploads",
    "netshop_rows",
    "netshop_import_batches",
    "netshop_product_daily_revisions",
    "netshop_product_daily_scope_revisions",
    "netshop_promotion_product_daily",
    "netshop_promotion_shop_daily",
    "netshop_promotion_aggregate_state",
    "netshop_promotion_aggregate_manifest",
    "netshop_promotion_aggregate_control",
    "netshop_promotion_scope_revisions",
    "netshop_schema_migrations",
    "netshop_write_authority",
)
RETIREMENT_GUARDS = tuple(
    f"netshop_retired_{table}_{operation}_guard"
    for table in ("fingerprints", "attempts", "scope_heads")
    for operation in ("insert", "update", "delete")
)
RECEIPT_KEYS = {
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


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _hash_json(value: object) -> str:
    return _sha256(_canonical_bytes(value))


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key: {key}")
        result[key] = value
    return result


def _load_smoke_receipt(
    path: Path,
    *,
    cutover_id: str,
    run_id: str,
    source_digest: str,
) -> tuple[dict[str, object], str]:
    if not path.is_file() or path.is_symlink():
        raise CommandError("系统测试 receipt 必须是普通文件。")
    payload = path.read_bytes()
    if not payload or len(payload) > MAX_RECEIPT_BYTES:
        raise CommandError("系统测试 receipt 为空或超过大小上限。")
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise CommandError("系统测试 receipt 不是严格 UTF-8 JSON。") from error
    if not isinstance(value, dict) or set(value) != RECEIPT_KEYS:
        raise CommandError("系统测试 receipt 字段集合不符合契约。")
    checks = value.get("checks")
    if (
        value.get("version") != SMOKE_VERSION
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
        raise CommandError("系统测试 receipt 未证明完整网店垂直链路通过。")
    recorded_at = parse_datetime(str(value.get("recordedAt") or ""))
    now = timezone.now()
    if (
        recorded_at is None
        or timezone.is_naive(recorded_at)
        or recorded_at < now - timedelta(minutes=30)
        or recorded_at > now + timedelta(minutes=2)
    ):
        raise CommandError("系统测试 receipt 已过期或时间无效。")
    return value, _sha256(payload)


def _migration_path() -> Path:
    path = (Path(settings.BASE_DIR).parent / "drizzle" / "0096_netshop_domain_retirement.sql").resolve()
    if not path.is_file():
        raise CommandError("缺少受控 D1 网店退役迁移。")
    return path


def _market_projection_receipt(source: sqlite3.Connection) -> dict[str, object]:
    required = {"market_netshop_projection", "market_netshop_projection_control"}
    present = {
        str(row[0])
        for row in source.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?,?)",
            tuple(sorted(required)),
        )
    }
    if present != required:
        raise CommandError("D1 市场网店投影尚未安装。")
    control = source.execute(
        "SELECT active_revision,active_total,syncing_revision,owner_token "
        "FROM market_netshop_projection_control WHERE id=1"
    ).fetchone()
    revision = NetshopDataRevision.objects.filter(domain="netshop").first()
    if (
        control is None
        or not REVISION_RE.fullmatch(str(control["active_revision"] or ""))
        or int(control["active_total"] or 0) <= 0
        or str(control["syncing_revision"] or "")
        or str(control["owner_token"] or "")
        or revision is None
        or str(control["active_revision"])
        != f"{revision.revision}:{revision.source_digest[:12]}"
    ):
        raise CommandError("D1 市场网店投影不是当前 PostgreSQL 的静默完整版本。")
    active_revision = str(control["active_revision"])
    sections = {
        "market_projection": _rows(
            source,
            "SELECT projection_key,kind,source,dataset,platform,shop_name,business_date,"
            "sku_id,spu_id,product_code,transaction_amount_cents,brand "
            "FROM market_netshop_projection WHERE projection_revision=? "
            "ORDER BY projection_key COLLATE BINARY",
            (active_revision,),
        )
    }
    counts, digests, digest = _snapshot(sections)
    if counts["market_projection"] != int(control["active_total"]):
        raise CommandError("D1 市场网店投影行数回查不一致。")
    return {
        "revision": active_revision,
        "total": counts["market_projection"],
        "digest": digest,
        "sectionDigest": digests["market_projection"],
    }


def _shared_receipt(source: sqlite3.Connection) -> dict[str, object]:
    sections = {
        "fingerprints_other": _rows(
            source,
            "SELECT * FROM import_content_fingerprints WHERE domain<>'netshop' ORDER BY sequence",
        ),
        "attempts_other": _rows(
            source,
            "SELECT * FROM import_content_attempts WHERE domain<>'netshop' ORDER BY sequence",
        ),
        "heads_other": _rows(
            source,
            "SELECT * FROM import_scope_heads WHERE domain<>'netshop' ORDER BY domain,scope_key",
        ),
    }
    counts, digests, digest = _snapshot(sections)
    return {"counts": counts, "digests": digests, "digest": digest}


def _build_plan(
    source: sqlite3.Connection,
    *,
    cutover_id: str,
    approved_run_id: str,
    smoke_path: Path,
) -> dict[str, object]:
    counts, digests, source_digest, run_id = _source_receipt(
        source, allowed_owners=frozenset({"postgresql"})
    )
    if run_id != approved_run_id:
        raise CommandError("approved-run-id 与终态 D1 网店快照不一致。")
    d1_authority = source.execute(
        "SELECT owner,epoch,cutover_id FROM netshop_write_authority WHERE id=1"
    ).fetchone()
    if d1_authority is None or str(d1_authority["cutover_id"]) != cutover_id:
        raise CommandError("D1 网店 authority 不属于本次 cutover。")
    with transaction.atomic():
        target = _assert_postgres_quiet()
        _verified_migration(
            run_id=run_id,
            source_counts=counts,
            source_digests=digests,
            source_digest=source_digest,
        )
        if (
            target.status != "postgres"
            or target.cutover_id != cutover_id
            or target.migration_verify_run_id != run_id
            or target.authority_epoch is None
        ):
            raise CommandError("PostgreSQL 网店 authority 不属于本次完成迁移。")
        target_epoch = str(target.authority_epoch)

    smoke, smoke_sha = _load_smoke_receipt(
        smoke_path,
        cutover_id=cutover_id,
        run_id=run_id,
        source_digest=source_digest,
    )
    market = _market_projection_receipt(source)
    shared = _shared_receipt(source)
    migration = _migration_path().read_bytes()
    migration_sha = _sha256(migration)
    preserved_sha = _hash_json(
        {"sourceDigest": source_digest, "counts": counts, "digests": digests}
    )
    attestation_sha = _hash_json(
        {
            "cutoverId": cutover_id,
            "migrationRunId": run_id,
            "sourceDigest": source_digest,
            "postgresAuthorityEpoch": target_epoch,
            "market": market,
        }
    )
    preflight = {
        "d1Owner": str(d1_authority["owner"]),
        "d1Epoch": int(d1_authority["epoch"]),
        "cutoverId": cutover_id,
        "postgresAuthorityEpoch": target_epoch,
        "migrationRunId": run_id,
        "sourceDigest": source_digest,
        "market": market,
        "shared": shared,
    }
    preflight_sha = _hash_json(preflight)
    audit_id = _hash_json(
        {
            "version": RETIREMENT_VERSION,
            "cutoverId": cutover_id,
            "sourceDigest": source_digest,
            "migrationSha256": migration_sha,
            "smokeReceiptSha256": smoke_sha,
            "preflightEvidenceSha256": preflight_sha,
        }
    )
    plan_fields = {
        "version": RETIREMENT_VERSION,
        "cutoverId": cutover_id,
        "approvedRunId": run_id,
        "sourceDigest": source_digest,
        "attestationSha256": attestation_sha,
        "smokeReceiptSha256": smoke_sha,
        "preflightEvidenceSha256": preflight_sha,
        "migrationSha256": migration_sha,
        "auditId": audit_id,
        "preservedEvidenceSha256": preserved_sha,
        "workerBuildSha256": smoke["workerBuildSha256"],
    }
    return {
        **plan_fields,
        "planId": _hash_json(plan_fields),
        "counts": counts,
        "digests": digests,
        "market": market,
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
        "FROM domain_retirement_receipts WHERE domain='netshop'"
    ).fetchone()
    return dict(row) if row is not None else None


def _verify_retired(source: sqlite3.Connection, receipt: dict[str, object]) -> None:
    if (
        receipt.get("version") != RETIREMENT_VERSION
        or receipt.get("status") != "completed"
        or not receipt.get("completed_at")
        or not HEX_64_RE.fullmatch(str(receipt.get("plan_id") or ""))
    ):
        raise CommandError("D1 网店退役 receipt 无效。")
    views = {
        str(row[0]): str(row[1] or "")
        for row in source.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='view' AND name LIKE 'netshop_%'"
        )
    }
    if set(views) != set(TOMBSTONE_VIEWS) or any(
        "netshop-domain-retired-v1" not in sql for sql in views.values()
    ):
        raise CommandError("D1 网店退役 tombstone view 集合不完整。")
    for view in TOMBSTONE_VIEWS:
        if int(source.execute(f'SELECT COUNT(*) FROM "{view}"').fetchone()[0]) != 0:
            raise CommandError("D1 网店退役 tombstone view 非空。")
    guards = {
        str(row[0])
        for row in source.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'netshop_retired_%'"
        )
    }
    if guards != set(RETIREMENT_GUARDS):
        raise CommandError("D1 网店退役共享表 guard 集合不完整。")
    for table in ("import_content_fingerprints", "import_content_attempts", "import_scope_heads"):
        count = int(
            source.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE domain=?', ("netshop",)
            ).fetchone()[0]
        )
        if count:
            raise CommandError("D1 共享导入表仍含网店域行。")


def _statements(payload: bytes) -> list[str]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CommandError("D1 网店退役迁移不是 UTF-8。") from error
    statements = [item.strip() for item in text.split("--> statement-breakpoint") if item.strip()]
    if len(statements) < 20 or not statements[0].startswith("-- Operator-only terminal retirement"):
        raise CommandError("D1 网店退役迁移结构不符合契约。")
    return statements


def _write_audit(path: Path, payload: dict[str, object]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
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
    help = "Plan or execute terminal D1 retirement after the netshop PostgreSQL cutover."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--source", required=True)
        parser.add_argument("--cutover-id", required=True)
        parser.add_argument("--approved-run-id", required=True)
        parser.add_argument("--smoke-receipt", required=True)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--approved-plan-id", default="")
        parser.add_argument("--audit-output", default="")

    def handle(self, *args: Any, **options: Any) -> None:
        if (
            settings.DJANGO_ENVIRONMENT == "production"
            and settings.DJANGO_PROCESS_ROLE != "migration_writer"
        ):
            raise CommandError("生产 D1 网店退役只能由 migration_writer 进程角色操作。")
        source_input = Path(str(options["source"])).expanduser()
        smoke_input = Path(str(options["smoke_receipt"])).expanduser()
        cutover_id = str(options["cutover_id"] or "").strip()
        run_id = str(options["approved_run_id"] or "").strip()
        apply = bool(options["apply"])
        approved_plan_id = str(options["approved_plan_id"] or "").strip()
        audit_output = str(options["audit_output"] or "").strip()
        # Check the operator-supplied leaves before canonicalization.  Calling
        # ``resolve()`` first erases the fact that a leaf is a symlink and
        # would let a mutable link retarget the destructive retirement step.
        if not source_input.is_file() or source_input.is_symlink():
            raise CommandError("D1 网店源必须是普通文件。")
        if not smoke_input.is_file() or smoke_input.is_symlink():
            raise CommandError("系统测试 receipt 必须是普通文件。")
        source_path = source_input.resolve()
        smoke_path = smoke_input.resolve()
        if not CUTOVER_ID_RE.fullmatch(cutover_id) or not re.fullmatch(r"netshop-[0-9a-f]{24}", run_id):
            raise CommandError("cutover-id 或 approved-run-id 无效。")
        if apply != bool(approved_plan_id):
            raise CommandError("--apply 必须且只能与 --approved-plan-id 同时使用。")
        if approved_plan_id and not HEX_64_RE.fullmatch(approved_plan_id):
            raise CommandError("approved-plan-id 无效。")
        if audit_output and not apply:
            raise CommandError("audit-output 只允许在 apply 时使用。")

        source = _open_writable(source_path) if apply else _open_source(source_path)
        try:
            completed = _completed_receipt(source)
            if completed is not None:
                _verify_retired(source, completed)
                if (
                    completed["cutover_id"] != cutover_id
                    or completed["plan_id"] != (approved_plan_id or completed["plan_id"])
                ):
                    raise CommandError("既有 D1 网店退役 receipt 与本次请求冲突。")
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
                cutover_id=cutover_id,
                approved_run_id=run_id,
                smoke_path=smoke_path,
            )
            if not apply:
                source.rollback()
                self.stdout.write(
                    json.dumps({**plan, "status": "planned"}, ensure_ascii=False, sort_keys=True)
                )
                return
            if plan["planId"] != approved_plan_id:
                raise CommandError("approved-plan-id 与当前退役前状态不一致。")

            migration = _migration_path().read_bytes()
            statements = _statements(migration)
            for statement in statements[:4]:
                source.execute(statement)
            created_at = timezone.now().isoformat().replace("+00:00", "Z")
            source.execute(
                "INSERT INTO domain_retirement_receipts (domain,version,status,cutover_id,plan_id,"
                "attestation_sha256,smoke_receipt_sha256,preflight_evidence_sha256,migration_sha256,"
                "audit_id,preserved_evidence_sha256,created_at,completed_at) "
                "VALUES ('netshop',?,'approved',?,?,?,?,?,?,?,?,?,NULL)",
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
                raise CommandError("D1 网店退役未生成完成 receipt。")
            _verify_retired(source, receipt)
            if _market_projection_receipt(source) != plan["market"]:
                raise CommandError("D1 网店退役改变了市场投影。")
            if _shared_receipt(source) != plan["shared"]:
                raise CommandError("D1 网店退役改变了其他域共享导入状态。")
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
                "market": plan["market"],
                "shared": plan["shared"],
                "completedAt": receipt["completed_at"],
            }
            if audit_output:
                _write_audit(Path(audit_output), result)
            self.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True))
        except sqlite3.DatabaseError as error:
            source.rollback()
            raise CommandError("D1 网店退役事务失败。") from error
        finally:
            source.close()
