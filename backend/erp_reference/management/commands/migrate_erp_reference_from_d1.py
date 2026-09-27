from __future__ import annotations

from datetime import timezone as datetime_timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from erp_reference.import_service import (
    IMPORT_VERSION,
    SOURCE_LABELS,
    SOURCE_SCOPES,
    SOURCE_SCOPE_KEYS,
    combined_database_digest,
    content_hash,
)
from erp_reference.models import (
    ErpComboItem,
    ErpProductMaster,
    ErpReferenceImportAttempt,
    ErpReferenceImportBatch,
    ErpReferenceImportFingerprint,
    ErpReferenceImportScopeHead,
    ErpReferenceMigrationRun,
    ErpReferenceRawUploadSession,
    ErpReferenceWriteAuthority,
    ErpReferenceWriteRequestReceipt,
)
from sales.models import SalesDataRevision


GENERATION_VERSION = "erp-reference-d1-to-postgres-v1"
DOMAIN = "erp-reference"
SOURCES = ("products", "combos")
HEX_64 = re.compile(r"^[0-9a-f]{64}$")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _hex(value: object, seed: object) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if HEX_64.fullmatch(candidate) else _sha(seed)


def _parse_json(value: object, fallback: object) -> object:
    if not isinstance(value, str):
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _timestamp(value: object):
    parsed = parse_datetime(str(value or ""))
    if parsed is None:
        raise CommandError("D1 ERP 时间戳无效")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed.astimezone(datetime_timezone.utc)


def _optional_timestamp(value: object):
    return _timestamp(value) if value else None


def _table(source: sqlite3.Connection, name: str) -> bool:
    return source.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _rows(
    source: sqlite3.Connection, sql: str, parameters: tuple[object, ...] = ()
) -> list[dict[str, object]]:
    return [dict(row) for row in source.execute(sql, parameters).fetchall()]


def _integer(value: object, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool):
        raise CommandError(f"D1 ERP {label}不是有效整数")
    try:
        result = int(value or 0)
    except (TypeError, ValueError) as error:
        raise CommandError(f"D1 ERP {label}不是有效整数") from error
    if result < minimum:
        raise CommandError(f"D1 ERP {label}小于安全下限")
    return result


def _source_for_scope(value: object) -> str:
    parsed = _parse_json(value, {})
    if not isinstance(parsed, dict):
        return ""
    source = str(parsed.get("source") or "")
    return source if source in SOURCES else ""


def _uuid(value: object, namespace: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"teruisi:{DOMAIN}:{namespace}:{value}"))


def _source_snapshot(path: Path) -> dict[str, object]:
    try:
        source = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as error:
        raise CommandError("无法以只读方式打开 D1 ERP 快照") from error
    source.row_factory = sqlite3.Row
    try:
        source.execute("PRAGMA query_only=ON")
        source.execute("BEGIN")
        for name in (
            "erp_reference_import_batches", "erp_product_master", "erp_combo_items",
            "erp_product_projection_state", "import_content_fingerprints",
            "import_content_attempts", "import_scope_heads",
        ):
            if not _table(source, name):
                raise CommandError(f"D1 快照缺少 ERP 迁移表 {name}，或 ERP 域已经退役")
        raw_batches = _rows(
            source,
            "SELECT * FROM erp_reference_import_batches "
            "WHERE source_key IN ('products','combos') ORDER BY created_at,id",
        )
        raw_products = _rows(source, "SELECT * FROM erp_product_master ORDER BY product_code")
        raw_combos = _rows(source, "SELECT * FROM erp_combo_items ORDER BY parent_code,child_code")
        raw_fingerprints = _rows(
            source,
            "SELECT * FROM import_content_fingerprints WHERE domain=? ORDER BY sequence",
            (DOMAIN,),
        )
        raw_attempts = _rows(
            source,
            "SELECT * FROM import_content_attempts WHERE domain=? ORDER BY sequence",
            (DOMAIN,),
        )
        raw_heads = _rows(
            source,
            "SELECT * FROM import_scope_heads WHERE domain=? ORDER BY scope_key",
            (DOMAIN,),
        )
        projection = source.execute(
            "SELECT erp_revision,row_count,content_hash,source_batch_id "
            "FROM erp_product_projection_state WHERE id=1"
        ).fetchone()
        source.rollback()
    except sqlite3.DatabaseError as error:
        source.rollback()
        raise CommandError("读取 D1 ERP 快照失败") from error
    finally:
        source.close()

    if projection is None:
        raise CommandError("D1 ERP 快照缺少货品 revision 水位")
    if any(str(row.get("status") or "") == "processing" for row in raw_batches):
        raise CommandError("D1 ERP 仍有 processing 导入批次")
    if any(str(row.get("status") or "") == "processing" for row in raw_fingerprints):
        raise CommandError("D1 ERP 仍有 processing 内容指纹")
    if any(str(row.get("outcome") or "") == "processing" for row in raw_attempts):
        raise CommandError("D1 ERP 仍有 processing 导入尝试")
    if any(
        _source_for_scope(row.get("scope_json")) in SOURCES
        and (str(row.get("status") or "") != "ready" or str(row.get("owner_token") or ""))
        for row in raw_heads
    ):
        raise CommandError("D1 ERP 导入范围仍被写入所有者占用")

    products = [{
        "productCode": str(row.get("product_code") or "").strip(),
        "productName": str(row.get("product_name") or "").strip(),
        "brand": str(row.get("brand") or "").strip(),
        "specification": str(row.get("specification") or "").strip(),
        "barcode": str(row.get("barcode") or "").strip(),
        "category": str(row.get("category") or "").strip(),
        "supplier": str(row.get("supplier") or "").strip(),
        "productStatus": str(row.get("product_status") or "").strip(),
        "sourceRowNumber": _integer(row.get("source_row_number"), "货品源行", minimum=1),
        "lastImportBatchId": str(row.get("last_import_batch_id") or ""),
        "createdAt": str(row.get("created_at") or ""),
        "updatedAt": str(row.get("updated_at") or ""),
    } for row in raw_products]
    combos = [{
        "parentCode": str(row.get("parent_code") or "").strip(),
        "parentName": str(row.get("parent_name") or "").strip(),
        "childCode": str(row.get("child_code") or "").strip(),
        "childName": str(row.get("child_name") or "").strip(),
        "childQuantityMilli": _integer(row.get("child_quantity_milli"), "组合装数量", minimum=1),
        "sourceRowNumber": _integer(row.get("source_row_number"), "组合装源行", minimum=1),
        "lastImportBatchId": str(row.get("last_import_batch_id") or ""),
    } for row in raw_combos]
    product_codes = [str(row["productCode"]) for row in products]
    combo_keys = [(str(row["parentCode"]), str(row["childCode"])) for row in combos]
    if not products or any(not value for value in product_codes) or len(product_codes) != len(set(product_codes)):
        raise CommandError("D1 ERP 货品事实为空、缺少编码或业务身份重复")
    if any(not left or not right for left, right in combo_keys) or len(combo_keys) != len(set(combo_keys)):
        raise CommandError("D1 ERP 组合装业务身份无效或重复")

    owners: dict[str, str] = {}
    for source_key, rows in (("products", products), ("combos", combos)):
        batch_ids = {str(row["lastImportBatchId"]) for row in rows}
        if len(batch_ids) > 1 or (rows and (not batch_ids or not next(iter(batch_ids)))):
            raise CommandError(f"D1 ERP {source_key} 当前事实不是单一原子批次所有")
        owners[source_key] = next(iter(batch_ids), "")

    batches: list[dict[str, object]] = []
    batch_ids: set[str] = set()
    for raw in raw_batches:
        source_key = str(raw.get("source_key") or "")
        batch_id = str(raw.get("id") or "")
        if source_key not in SOURCES or not batch_id or batch_id in batch_ids:
            raise CommandError("D1 ERP 批次身份无效或重复")
        batch_ids.add(batch_id)
        totals = _parse_json(raw.get("totals_json"), {})
        totals = totals if isinstance(totals, dict) else {}
        current_rows = products if source_key == "products" else combos
        business_rows = [
            {key: value for key, value in row.items() if key not in {"lastImportBatchId", "createdAt", "updatedAt"}}
            for row in current_rows
        ]
        canonical_current = content_hash(source_key, business_rows) if owners[source_key] == batch_id else ""
        content = canonical_current or _hex(totals.get("contentHash"), {"batch": batch_id, "kind": "content"})
        raw_hash = _hex(totals.get("rawFileHash"), {"batch": batch_id, "kind": "raw"})
        file_hash = _hex(raw.get("file_hash"), {"batch": batch_id, "kind": "import"})
        warnings = _parse_json(raw.get("warnings_json"), [])
        batches.append({
            "id": batch_id, "sourceKey": source_key,
            "sourceLabel": str(raw.get("source_label") or SOURCE_LABELS[source_key]),
            "fileName": str(raw.get("file_name") or "")[:2000],
            "fileSizeBytes": _integer(raw.get("file_size_bytes"), "批次文件大小"),
            "fileHash": file_hash, "rawFileHash": raw_hash, "contentHash": content,
            "scopeKey": SOURCE_SCOPE_KEYS[source_key],
            "sheetName": str(raw.get("sheet_name") or "")[:255],
            "status": str(raw.get("status") or "completed"),
            "rowCount": _integer(raw.get("row_count"), "批次行数"),
            "insertedCount": _integer(raw.get("inserted_count"), "批次新增数"),
            "updatedCount": _integer(raw.get("updated_count"), "批次更新数"),
            "excludedCount": _integer(raw.get("excluded_count"), "批次排除数"),
            "warningCount": _integer(raw.get("warning_count"), "批次告警数"),
            "warnings": warnings if isinstance(warnings, list) else [],
            "totals": {**totals, "rawFileHash": raw_hash, "contentHash": content,
                       "canonicalFormatVersion": IMPORT_VERSION},
            "actor": "migration@local", "createdAt": _timestamp(raw.get("created_at")).isoformat(),
            "completedAt": _optional_timestamp(raw.get("completed_at")).isoformat()
            if raw.get("completed_at") else None,
        })
    for source_key in SOURCES:
        owner = owners[source_key]
        if owner and owner not in batch_ids:
            raise CommandError(f"D1 ERP {source_key} 当前事实引用了孤立批次")
        if not owner:
            empty_digest = content_hash(source_key, [])
            owner = f"{source_key}:{_sha({'source': source_key, 'empty': True})}"
            owners[source_key] = owner
            now = "1970-01-01T00:00:00+00:00"
            batches.append({
                "id": owner, "sourceKey": source_key, "sourceLabel": SOURCE_LABELS[source_key],
                "fileName": "migration-empty-baseline.xlsx", "fileSizeBytes": 0,
                "fileHash": owner.split(":", 1)[1], "rawFileHash": _sha({"source": source_key, "emptyRaw": True}),
                "contentHash": empty_digest, "scopeKey": SOURCE_SCOPE_KEYS[source_key],
                "sheetName": "migration", "status": "completed", "rowCount": 0,
                "insertedCount": 0, "updatedCount": 0, "excludedCount": 0,
                "warningCount": 0, "warnings": [],
                "totals": {"rawFileHash": _sha({"source": source_key, "emptyRaw": True}),
                           "contentHash": empty_digest, "canonicalFormatVersion": IMPORT_VERSION},
                "actor": "migration@local", "createdAt": now, "completedAt": now,
            })
            batch_ids.add(owner)

    source_heads = {
        source_key: row for row in raw_heads
        if (source_key := _source_for_scope(row.get("scope_json"))) in SOURCES
    }
    heads: list[dict[str, object]] = []
    for source_key in SOURCES:
        raw = source_heads.get(source_key, {})
        current_batch_id = owners[source_key]
        raw_current = str(raw.get("current_batch_id") or "")
        if raw_current and raw_current != current_batch_id:
            raise CommandError(f"D1 ERP {source_key} 范围头与事实所有权不一致")
        state_token = _hex(
            raw.get("state_token"),
            {"source": source_key, "batch": current_batch_id,
             "content": next(row["contentHash"] for row in batches if row["id"] == current_batch_id)},
        )
        heads.append({
            "sourceKey": source_key, "scopeKey": SOURCE_SCOPE_KEYS[source_key],
            "stateToken": state_token, "currentBatchId": current_batch_id,
            "generation": max(1, _integer(raw.get("generation"), "范围代次")),
        })

    fingerprints: list[dict[str, object]] = []
    seen_fingerprint_batches: set[str] = set()
    seen_imports: set[tuple[str, str]] = set()
    for raw in raw_fingerprints:
        source_key = _source_for_scope(raw.get("scope_json"))
        batch_id = str(raw.get("batch_id") or "")
        if source_key not in SOURCES or batch_id not in batch_ids:
            continue
        import_hash = _hex(raw.get("import_hash"), {"batch": batch_id, "kind": "fingerprint-import"})
        identity = (source_key, import_hash)
        if batch_id in seen_fingerprint_batches or identity in seen_imports:
            raise CommandError("D1 ERP 内容指纹身份重复")
        seen_fingerprint_batches.add(batch_id)
        seen_imports.add(identity)
        fingerprints.append({
            "batchId": batch_id, "sourceKey": source_key,
            "scopeKey": SOURCE_SCOPE_KEYS[source_key], "scope": SOURCE_SCOPES[source_key],
            "importHash": import_hash,
            "rawFileHash": _hex(raw.get("raw_file_hash"), {"batch": batch_id, "kind": "fingerprint-raw"}),
            "contentHash": _hex(raw.get("content_hash"), {"batch": batch_id, "kind": "fingerprint-content"}),
            "rowCount": _integer(raw.get("row_count"), "指纹行数"),
            "publishedStateToken": next(row["stateToken"] for row in heads if row["sourceKey"] == source_key),
            "outcome": "imported", "createdAt": _timestamp(raw.get("created_at")).isoformat(),
        })
    for batch in batches:
        if batch["id"] in seen_fingerprint_batches:
            continue
        source_key = str(batch["sourceKey"])
        import_hash = str(batch["fileHash"])
        if (source_key, import_hash) in seen_imports:
            import_hash = _sha({"batch": batch["id"], "kind": "migration-import"})
        seen_imports.add((source_key, import_hash))
        fingerprints.append({
            "batchId": batch["id"], "sourceKey": source_key,
            "scopeKey": SOURCE_SCOPE_KEYS[source_key], "scope": SOURCE_SCOPES[source_key],
            "importHash": import_hash, "rawFileHash": batch["rawFileHash"],
            "contentHash": batch["contentHash"], "rowCount": batch["rowCount"],
            "publishedStateToken": next(row["stateToken"] for row in heads if row["sourceKey"] == source_key),
            "outcome": "imported", "createdAt": batch["createdAt"],
        })

    attempts: list[dict[str, object]] = []
    seen_attempts: set[str] = set()
    for raw in raw_attempts:
        source_key = _source_for_scope(raw.get("scope_json"))
        if source_key not in SOURCES:
            continue
        attempt_id = _uuid(raw.get("attempt_id"), "attempt")
        if attempt_id in seen_attempts:
            raise CommandError("D1 ERP 导入尝试身份重复")
        seen_attempts.add(attempt_id)
        warnings = _parse_json(raw.get("warnings_json"), [])
        attempts.append({
            "id": attempt_id, "batchId": str(raw.get("batch_id") or ""),
            "sourceKey": source_key, "scopeKey": SOURCE_SCOPE_KEYS[source_key],
            "scope": SOURCE_SCOPES[source_key],
            "importHash": _hex(raw.get("import_hash"), {"attempt": attempt_id, "kind": "import"}),
            "rawFileHash": _hex(raw.get("raw_file_hash"), {"attempt": attempt_id, "kind": "raw"}),
            "contentHash": _hex(raw.get("content_hash"), {"attempt": attempt_id, "kind": "content"}),
            "rowCount": _integer(raw.get("row_count"), "尝试行数"),
            "fileName": str(raw.get("file_name") or "")[:2000],
            "fileSizeBytes": _integer(raw.get("file_size_bytes"), "尝试文件大小"),
            "actor": str(raw.get("actor") or "migration@local")[:320],
            "warnings": warnings if isinstance(warnings, list) else [],
            "outcome": str(raw.get("outcome") or "failed")[:32],
            "errorCode": str(raw.get("error_code") or "")[:80],
            "createdAt": _timestamp(raw.get("created_at")).isoformat(),
        })

    revision = _integer(projection[0], "ERP revision", minimum=1)
    if _integer(projection[1], "ERP 投影行数") != len(products):
        raise CommandError("D1 ERP 投影行数与货品事实不一致")
    return {
        "products": products, "combos": combos,
        "batches": sorted(batches, key=lambda row: (str(row["createdAt"]), str(row["id"]))),
        "fingerprints": sorted(fingerprints, key=lambda row: (str(row["createdAt"]), str(row["batchId"]))),
        "attempts": sorted(attempts, key=lambda row: (str(row["createdAt"]), str(row["id"]))),
        "heads": sorted(heads, key=lambda row: str(row["sourceKey"])), "revision": revision,
    }


def _target_snapshot() -> dict[str, object]:
    products = [{
        "productCode": row.product_code, "productName": row.product_name, "brand": row.brand,
        "specification": row.specification, "barcode": row.barcode, "category": row.category,
        "supplier": row.supplier, "productStatus": row.product_status,
        "sourceRowNumber": int(row.source_row_number), "lastImportBatchId": row.last_import_batch_id,
        "createdAt": row.created_at, "updatedAt": row.updated_at,
    } for row in ErpProductMaster.objects.order_by("product_code")]
    combos = [{
        "parentCode": row.parent_code, "parentName": row.parent_name,
        "childCode": row.child_code, "childName": row.child_name,
        "childQuantityMilli": int(row.child_quantity_milli),
        "sourceRowNumber": int(row.source_row_number), "lastImportBatchId": row.last_import_batch_id,
    } for row in ErpComboItem.objects.order_by("parent_code", "child_code")]
    batches = [{
        "id": row.id, "sourceKey": row.source_key, "sourceLabel": row.source_label,
        "fileName": row.file_name, "fileSizeBytes": int(row.file_size_bytes),
        "fileHash": row.file_hash, "rawFileHash": row.raw_file_hash,
        "contentHash": row.content_hash, "scopeKey": row.scope_key, "sheetName": row.sheet_name,
        "status": row.status, "rowCount": int(row.row_count),
        "insertedCount": int(row.inserted_count), "updatedCount": int(row.updated_count),
        "excludedCount": int(row.excluded_count), "warningCount": int(row.warning_count),
        "warnings": row.warnings_json, "totals": row.totals_json, "actor": row.actor_email,
        "createdAt": row.created_at.astimezone(datetime_timezone.utc).isoformat(),
        "completedAt": row.completed_at.astimezone(datetime_timezone.utc).isoformat()
        if row.completed_at else None,
    } for row in ErpReferenceImportBatch.objects.order_by("created_at", "id")]
    fingerprints = [{
        "batchId": row.batch_id, "sourceKey": row.source_key, "scopeKey": row.scope_key,
        "scope": row.scope_json, "importHash": row.import_hash,
        "rawFileHash": row.raw_file_hash, "contentHash": row.content_hash,
        "rowCount": int(row.row_count), "publishedStateToken": row.published_state_token,
        "outcome": row.outcome, "createdAt": row.created_at.astimezone(datetime_timezone.utc).isoformat(),
    } for row in ErpReferenceImportFingerprint.objects.order_by("created_at", "batch_id")]
    attempts = [{
        "id": str(row.id), "batchId": row.batch_id, "sourceKey": row.source_key,
        "scopeKey": row.scope_key, "scope": row.scope_json, "importHash": row.import_hash,
        "rawFileHash": row.raw_file_hash, "contentHash": row.content_hash,
        "rowCount": int(row.row_count), "fileName": row.file_name,
        "fileSizeBytes": int(row.file_size_bytes), "actor": row.actor_email,
        "warnings": row.warnings_json, "outcome": row.outcome, "errorCode": row.error_code,
        "createdAt": row.created_at.astimezone(datetime_timezone.utc).isoformat(),
    } for row in ErpReferenceImportAttempt.objects.order_by("created_at", "id")]
    heads = [{
        "sourceKey": row.source_key, "scopeKey": row.scope_key,
        "stateToken": row.state_token, "currentBatchId": row.current_batch_id,
        "generation": int(row.generation),
    } for row in ErpReferenceImportScopeHead.objects.order_by("source_key")]
    revision = SalesDataRevision.objects.get(domain="erp")
    return {
        "products": products, "combos": combos, "batches": batches,
        "fingerprints": fingerprints, "attempts": attempts, "heads": heads,
        "revision": int(revision.revision),
    }


def _counts(snapshot: dict[str, object]) -> dict[str, int]:
    return {key: len(snapshot[key]) for key in ("products", "combos", "batches", "fingerprints", "attempts", "heads")}


class Command(BaseCommand):
    help = "Plan, apply, or verify the ERP reference migration from a read-only D1 snapshot."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--source", required=True)
        parser.add_argument("--mode", choices=("plan", "apply", "verify"), required=True)
        parser.add_argument("--approve-run-id", default="")
        parser.add_argument("--verify-run-id", default="")

    def handle(self, *args, **options) -> None:
        if settings.DJANGO_ENVIRONMENT == "production" and settings.DJANGO_PROCESS_ROLE != "migration_writer":
            raise CommandError("生产 ERP 迁移只能由 migration_writer 进程角色操作")
        raw_source = Path(str(options["source"])).expanduser()
        if not raw_source.is_file() or raw_source.is_symlink() or raw_source.suffix.lower() not in {".sqlite", ".sqlite3"}:
            raise CommandError("D1 ERP 源必须是普通 SQLite 文件")
        source = raw_source.resolve()
        snapshot = _source_snapshot(source)
        source_digest = _sha(snapshot)
        source_counts = _counts(snapshot)
        source_path_digest = hashlib.sha256(str(source).lower().encode()).hexdigest()
        mode = str(options["mode"])
        manifest = {"version": GENERATION_VERSION, "sourceDigest": source_digest}
        if mode == "plan":
            run_id = f"erp-reference-plan-{uuid.uuid4().hex}"
            ErpReferenceMigrationRun.objects.create(
                id=run_id, mode="plan", status="planned", source_path_digest=source_path_digest,
                source_snapshot_digest=source_digest, source_counts=source_counts, manifest=manifest,
            )
            self.stdout.write(_json({"ok": True, "mode": mode, "runId": run_id,
                                     "sourceDigest": source_digest, "counts": source_counts}))
            return
        if mode == "verify":
            run_id = str(options.get("verify_run_id") or "")
            run = ErpReferenceMigrationRun.objects.filter(
                id=run_id, mode="apply", status__in=["applied", "verified"],
                source_snapshot_digest=source_digest,
            ).first()
            if run is None or run.manifest.get("version") != GENERATION_VERSION:
                raise CommandError("ERP verify-run-id 未绑定当前 D1 快照")
            target = _target_snapshot()
            target_digest = _sha(target)
            if target_digest != source_digest or _counts(target) != source_counts:
                raise CommandError("ERP 迁移目标与当前 D1 快照摘要不一致")
            with transaction.atomic():
                run = ErpReferenceMigrationRun.objects.select_for_update().get(id=run_id)
                run.status = "verified"
                run.target_snapshot_digest = target_digest
                run.target_counts = _counts(target)
                run.completed_at = timezone.now()
                run.save()
                authority = ErpReferenceWriteAuthority.objects.select_for_update().get(id=1)
                if authority.status != "d1":
                    raise CommandError("PostgreSQL ERP authority 已激活，不能重绑迁移")
                authority.migration_verify_run_id = run_id
                authority.save(update_fields=["migration_verify_run_id", "updated_at"])
            self.stdout.write(_json({"ok": True, "mode": mode, "runId": run_id,
                                     "targetDigest": target_digest, "counts": source_counts}))
            return

        approved_id = str(options.get("approve_run_id") or "")
        approved = ErpReferenceMigrationRun.objects.filter(
            id=approved_id, mode="plan", status="planned",
            source_snapshot_digest=source_digest, source_counts=source_counts,
        ).first()
        if approved is None or approved.manifest.get("version") != GENERATION_VERSION:
            raise CommandError("ERP apply 必须绑定当前快照的精确 plan run")
        authority = ErpReferenceWriteAuthority.objects.get(id=1)
        revision = SalesDataRevision.objects.get(domain="erp")
        existing_products = list(ErpProductMaster.objects.order_by("product_code").values_list("product_code", flat=True))
        source_products = [str(row["productCode"]) for row in snapshot["products"]]
        target_busy = (
            ErpReferenceImportBatch.objects.exists()
            or ErpReferenceImportFingerprint.objects.exists()
            or ErpReferenceImportAttempt.objects.exists()
            or ErpComboItem.objects.exists()
            or ErpReferenceRawUploadSession.objects.exists()
            or ErpReferenceWriteRequestReceipt.objects.exists()
        )
        current_target = _target_snapshot()
        target_products_match = (
            current_target["products"] == snapshot["products"]
            if existing_products else True
        )
        if (
            authority.status != "d1" or authority.migration_verify_run_id
            or target_busy or not target_products_match
            or (existing_products and existing_products != source_products)
        ):
            raise CommandError("ERP 迁移目标不是全新控制面或既有 bridge 货品投影与 D1 不一致")
        run_id = f"erp-reference-{uuid.uuid4().hex}"
        with transaction.atomic():
            ErpReferenceMigrationRun.objects.create(
                id=run_id, mode="apply", status="applying", source_path_digest=source_path_digest,
                source_snapshot_digest=source_digest, source_counts=source_counts,
                approved_run_id=approved_id, manifest=manifest,
            )
            if not existing_products:
                ErpProductMaster.objects.bulk_create([
                    ErpProductMaster(
                        product_code=row["productCode"], product_name=row["productName"],
                        brand=row["brand"], specification=row["specification"], barcode=row["barcode"],
                        category=row["category"], supplier=row["supplier"],
                        product_status=row["productStatus"], source_row_number=row["sourceRowNumber"],
                        last_import_batch_id=row["lastImportBatchId"], created_at=row["createdAt"],
                        updated_at=row["updatedAt"], migration_generation=run_id,
                    ) for row in snapshot["products"]
                ], batch_size=500)
            else:
                ErpProductMaster.objects.all().update(migration_generation=run_id)
            ErpComboItem.objects.bulk_create([
                ErpComboItem(
                    parent_code=row["parentCode"], parent_name=row["parentName"],
                    child_code=row["childCode"], child_name=row["childName"],
                    child_quantity_milli=row["childQuantityMilli"],
                    source_row_number=row["sourceRowNumber"], last_import_batch_id=row["lastImportBatchId"],
                    migration_generation=run_id,
                ) for row in snapshot["combos"]
            ], batch_size=500)
            ErpReferenceImportBatch.objects.bulk_create([
                ErpReferenceImportBatch(
                    id=row["id"], source_key=row["sourceKey"], source_label=row["sourceLabel"],
                    file_name=row["fileName"], file_size_bytes=row["fileSizeBytes"],
                    file_hash=row["fileHash"], raw_file_hash=row["rawFileHash"],
                    content_hash=row["contentHash"], scope_key=row["scopeKey"],
                    published_state_token=next(head["stateToken"] for head in snapshot["heads"] if head["sourceKey"] == row["sourceKey"]),
                    sheet_name=row["sheetName"], status=row["status"], row_count=row["rowCount"],
                    inserted_count=row["insertedCount"], updated_count=row["updatedCount"],
                    excluded_count=row["excludedCount"], warning_count=row["warningCount"],
                    warnings_json=row["warnings"], totals_json=row["totals"], actor_email=row["actor"],
                    created_at=_timestamp(row["createdAt"]), completed_at=_optional_timestamp(row["completedAt"]),
                    migration_generation=run_id,
                ) for row in snapshot["batches"]
            ], batch_size=500)
            ErpReferenceImportFingerprint.objects.bulk_create([
                ErpReferenceImportFingerprint(
                    batch_id=row["batchId"], source_key=row["sourceKey"], scope_key=row["scopeKey"],
                    scope_json=row["scope"], import_hash=row["importHash"],
                    raw_file_hash=row["rawFileHash"], content_hash=row["contentHash"],
                    row_count=row["rowCount"], published_state_token=row["publishedStateToken"],
                    outcome=row["outcome"], created_at=_timestamp(row["createdAt"]),
                    migration_generation=run_id,
                ) for row in snapshot["fingerprints"]
            ], batch_size=500)
            ErpReferenceImportAttempt.objects.bulk_create([
                ErpReferenceImportAttempt(
                    id=row["id"], batch_id=row["batchId"], source_key=row["sourceKey"],
                    scope_key=row["scopeKey"], scope_json=row["scope"], import_hash=row["importHash"],
                    raw_file_hash=row["rawFileHash"], content_hash=row["contentHash"],
                    row_count=row["rowCount"], file_name=row["fileName"],
                    file_size_bytes=row["fileSizeBytes"], actor_email=row["actor"],
                    warnings_json=row["warnings"], outcome=row["outcome"], error_code=row["errorCode"],
                    created_at=_timestamp(row["createdAt"]), migration_generation=run_id,
                ) for row in snapshot["attempts"]
            ], batch_size=500)
            for row in snapshot["heads"]:
                head = ErpReferenceImportScopeHead.objects.select_for_update().get(
                    scope_key=row["scopeKey"], source_key=row["sourceKey"]
                )
                head.state_token = row["stateToken"]
                head.status = "ready"
                head.owner_token = ""
                head.current_batch_id = row["currentBatchId"]
                head.generation = row["generation"]
                head.owner_started_at = None
                head.heartbeat_at = None
                head.save()
            revision = SalesDataRevision.objects.select_for_update().get(domain="erp")
            revision.revision = snapshot["revision"]
            revision.source_digest = combined_database_digest()
            revision.save()
            target = _target_snapshot()
            target_digest = _sha(target)
            if target_digest != source_digest or _counts(target) != source_counts:
                raise CommandError("ERP 迁移落库摘要回查不一致")
            run = ErpReferenceMigrationRun.objects.get(id=run_id)
            run.status = "applied"
            run.target_snapshot_digest = target_digest
            run.target_counts = _counts(target)
            run.completed_at = timezone.now()
            run.save()
        self.stdout.write(_json({"ok": True, "mode": mode, "runId": run_id,
                                 "approvedRunId": approved_id, "targetDigest": source_digest,
                                 "counts": source_counts}))
