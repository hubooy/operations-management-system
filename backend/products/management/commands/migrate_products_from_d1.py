from __future__ import annotations

from datetime import timezone as datetime_timezone
from decimal import Decimal, ROUND_FLOOR
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

from products.import_service import SCOPE, SCOPE_KEY, _content_hash, _state_token
from products.models import (
    ProductDataRevision,
    ProductImportAttempt,
    ProductImportFingerprint,
    ProductImportScopeHead,
    ProductInventoryProjection,
    ProductInventoryProjectionControl,
    ProductMigrationRun,
    ProductRawUploadSession,
    ProductShippingRate,
    ProductShippingRateImportBatch,
    ProductWriteAuthority,
    ProductWriteRequestReceipt,
)


DOMAIN = "product-shipping-rates"
GENERATION_VERSION = "products-d1-to-postgres-v1"
HEX_RE = re.compile(r"^[0-9a-f]{64}$")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: object) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _projection_digest(batch_id: str, snapshot_date: str, rows: list[dict[str, object]]) -> str:
    body = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(
        f"product-inventory-projection-v1\n{batch_id}\n{snapshot_date}\n{body}".encode()
    ).hexdigest()


def _parse_json(value: object, fallback: object) -> object:
    if not isinstance(value, str):
        return fallback
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return fallback
    return parsed


def _timestamp(value: object):
    parsed = parse_datetime(str(value or ""))
    if parsed is None:
        raise CommandError("D1 商品经营时间戳无效")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed.astimezone(datetime_timezone.utc)


def _ppt(value: object) -> int:
    try:
        scaled = Decimal(str(value)) * Decimal(10**12)
    except Exception as error:
        raise CommandError("D1 SKU 快递费率包含无效数值") from error
    result = int((scaled + Decimal("0.5")).to_integral_value(rounding=ROUND_FLOOR))
    if abs(result) > 10**15:
        raise CommandError("D1 SKU 快递费率超出商品域安全数值范围")
    return result


def _table(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT type FROM sqlite_master WHERE name=? AND type='table'", (name,)
    ).fetchone()
    return row is not None


def _rows(connection: sqlite3.Connection, sql: str, parameters: tuple = ()) -> list[dict[str, object]]:
    return [dict(row) for row in connection.execute(sql, parameters).fetchall()]


def _source_snapshot(source: Path) -> dict[str, object]:
    uri = f"file:{source.as_posix()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as error:
        raise CommandError("无法以只读方式打开 D1 SQLite 快照") from error
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        for name in ("product_shipping_rate_import_batches", "product_shipping_rates"):
            if not _table(connection, name):
                raise CommandError(f"D1 快照缺少商品经营表 {name}，或该域已经退役")
        batches = _rows(
            connection,
            "SELECT * FROM product_shipping_rate_import_batches ORDER BY created_at,id",
        )
        if any(str(row.get("status")) == "processing" for row in batches):
            raise CommandError("D1 仍有 processing 商品快递费率批次")
        raw_rates = _rows(
            connection,
            "SELECT product_code,shipping_rate,source_row_number,last_import_batch_id "
            "FROM product_shipping_rates ORDER BY product_code",
        )
        batch_ids = {str(row["id"]) for row in batches}
        if any(str(row["last_import_batch_id"]) not in batch_ids for row in raw_rates):
            raise CommandError("D1 快递费率存在无法解析的批次所有权")
        owners = {str(row["last_import_batch_id"]) for row in raw_rates}
        if len(owners) > 1:
            raise CommandError("D1 当前快递费率不是单一原子批次所有")
        current_batch_id = next(iter(owners), "")
        if current_batch_id:
            current_source_batch = next(
                (row for row in batches if str(row["id"]) == current_batch_id),
                None,
            )
            if (
                current_source_batch is None
                or str(current_source_batch.get("status")) != "completed"
                or int(current_source_batch.get("row_count") or 0) != len(raw_rates)
            ):
                raise CommandError("D1 当前快递费率批次状态或行数与事实不一致")
        elif any(
            str(row.get("status")) == "completed" and int(row.get("row_count") or 0) > 0
            for row in batches
        ):
            raise CommandError("D1 存在非空已完成快递费率批次但没有当前事实")
        rates = [
            {
                "productCode": str(row["product_code"]).strip(),
                "shippingRatePpt": _ppt(row["shipping_rate"]),
                "sourceRowNumber": int(row["source_row_number"]),
                "lastImportBatchId": str(row["last_import_batch_id"]),
            }
            for row in raw_rates
        ]
        if any(not row["productCode"] for row in rates) or len({row["productCode"] for row in rates}) != len(rates):
            raise CommandError("D1 快递费率规格代码为空或重复")
        canonical_content_hash = _content_hash(rates) if rates else ""

        prepared_batches: list[dict[str, object]] = []
        for row in batches:
            totals = _parse_json(row.get("totals_json"), {})
            totals = totals if isinstance(totals, dict) else {}
            content_hash = str(row.get("content_hash") or "")
            if str(row["id"]) == current_batch_id:
                totals = {
                    **totals,
                    "legacyContentHash": content_hash,
                    "canonicalFormatVersion": GENERATION_VERSION,
                }
                content_hash = canonical_content_hash
            prepared_batches.append(
                {
                    "id": str(row["id"]),
                    "source": str(row.get("source") or "sku_cumulative"),
                    "fileName": str(row.get("file_name") or ""),
                    "fileSizeBytes": int(row.get("file_size_bytes") or 0),
                    "fileHash": str(row.get("file_hash") or ""),
                    "rawFileHash": str(row.get("raw_file_hash") or ""),
                    "contentHash": content_hash,
                    "sheetName": str(row.get("sheet_name") or ""),
                    "actor": str(row.get("actor") or ""),
                    "status": str(row.get("status") or ""),
                    "sourceRowCount": int(row.get("source_row_count") or 0),
                    "rowCount": int(row.get("row_count") or 0),
                    "insertedCount": int(row.get("inserted_count") or 0),
                    "updatedCount": int(row.get("updated_count") or 0),
                    "duplicateCount": int(row.get("duplicate_count") or 0),
                    "warningCount": int(row.get("warning_count") or 0),
                    "warnings": _parse_json(row.get("warnings_json"), []),
                    "totals": totals,
                    "createdAt": _timestamp(row.get("created_at")).isoformat(),
                    "completedAt": str(row["completed_at"]) if row.get("completed_at") else None,
                }
            )

        fingerprints: list[dict[str, object]] = []
        attempts: list[dict[str, object]] = []
        heads: list[dict[str, object]] = []
        if _table(connection, "import_content_fingerprints"):
            fingerprints = _rows(
                connection,
                "SELECT * FROM import_content_fingerprints WHERE domain=? ORDER BY sequence",
                (DOMAIN,),
            )
        if _table(connection, "import_content_attempts"):
            attempts = _rows(
                connection,
                "SELECT * FROM import_content_attempts WHERE domain=? ORDER BY sequence",
                (DOMAIN,),
            )
            if any(str(row.get("outcome")) == "processing" for row in attempts):
                raise CommandError("D1 仍有 processing 商品快递费率尝试")
        if _table(connection, "import_scope_heads"):
            heads = _rows(
                connection,
                "SELECT * FROM import_scope_heads WHERE domain=? ORDER BY scope_key",
                (DOMAIN,),
            )
            if any(str(row.get("status")) != "ready" or str(row.get("owner_token") or "") for row in heads):
                raise CommandError("D1 商品快递费率范围仍被写入所有者占用")
        if len(heads) > 1 or (heads and str(heads[0]["scope_key"]) != SCOPE_KEY):
            raise CommandError("D1 商品快递费率范围键与当前契约不一致")
        if _table(connection, "inventory_import_uploads") and _table(
            connection, "inventory_import_upload_chunks"
        ):
            product_upload_chunks = int(
                connection.execute(
                    "SELECT COUNT(*) FROM inventory_import_upload_chunks c "
                    "JOIN inventory_import_uploads u ON u.id=c.upload_id "
                    "WHERE u.fingerprint LIKE 'sku-shipping-rates:%'"
                ).fetchone()[0]
            )
            if product_upload_chunks:
                raise CommandError(
                    "D1 商品经营旧分片对象键必须在 authority prepare 前受控清理"
                )
        if heads:
            state_token = str(heads[0].get("state_token") or "")
            generation = int(heads[0].get("generation") or 0)
        elif current_batch_id:
            state_token = _state_token("0" * 64, current_batch_id, canonical_content_hash, len(rates))
            generation = 1
        else:
            state_token = "0" * 64
            generation = 0
        if not HEX_RE.fullmatch(state_token):
            raise CommandError("D1 商品快递费率范围状态令牌无效")

        prepared_fingerprints: list[dict[str, object]] = []
        for row in fingerprints:
            content_hash = str(row.get("content_hash") or "")
            if str(row.get("batch_id")) == current_batch_id:
                content_hash = canonical_content_hash
            prepared_fingerprints.append(
                {
                    "batchId": str(row.get("batch_id") or ""),
                    "scopeKey": str(row.get("scope_key") or SCOPE_KEY),
                    "scope": _parse_json(row.get("scope_json"), SCOPE),
                    "importHash": str(row.get("import_hash") or ""),
                    "contentHash": content_hash,
                    "rawFileHash": str(row.get("raw_file_hash") or ""),
                    "rowCount": int(row.get("row_count") or 0),
                    "publishedStateToken": state_token
                    if str(row.get("batch_id")) == current_batch_id
                    else _sha({"legacyFingerprint": int(row.get("sequence") or 0)}),
                    "status": str(row.get("status") or "completed"),
                    "publicationSequence": row.get("publication_sequence"),
                    "createdAt": _timestamp(row.get("created_at")).isoformat(),
                }
            )
        if current_batch_id and not any(row["batchId"] == current_batch_id for row in prepared_fingerprints):
            current = next(row for row in prepared_batches if row["id"] == current_batch_id)
            prepared_fingerprints.append(
                {
                    "batchId": current_batch_id,
                    "scopeKey": SCOPE_KEY,
                    "scope": SCOPE,
                    "importHash": current["fileHash"],
                    "contentHash": canonical_content_hash,
                    "rawFileHash": current["rawFileHash"],
                    "rowCount": len(rates),
                    "publishedStateToken": state_token,
                    "status": "completed",
                    "publicationSequence": generation,
                    "createdAt": current["createdAt"],
                }
            )

        prepared_attempts = [
            {
                "id": str(row.get("attempt_id") or f"legacy-{row.get('sequence') or uuid.uuid4().hex}"),
                "batchId": str(row.get("batch_id") or ""),
                "scopeKey": str(row.get("scope_key") or ""),
                "scope": _parse_json(row.get("scope_json"), {}),
                "rawFileHash": str(row.get("raw_file_hash") or ""),
                "contentHash": canonical_content_hash
                if str(row.get("batch_id")) == current_batch_id
                else str(row.get("content_hash") or ""),
                "rowCount": int(row.get("row_count") or 0),
                "outcome": str(row.get("outcome") or "failed"),
                "errorCode": str(row.get("error_code") or "")[:64],
                "actor": str(row.get("actor") or ""),
                "metadata": {
                    "fileName": str(row.get("file_name") or ""),
                    "fileSizeBytes": int(row.get("file_size_bytes") or 0),
                    "warnings": _parse_json(row.get("warnings_json"), []),
                    "legacyImportHash": str(row.get("import_hash") or ""),
                    "recoveredFromAttemptId": str(row.get("recovered_from_attempt_id") or ""),
                },
                "createdAt": _timestamp(row.get("created_at")).isoformat(),
                "completedAt": _timestamp(row.get("updated_at") or row.get("created_at")).isoformat(),
            }
            for row in attempts
        ]

        inventory_batch = None
        inventory: list[dict[str, object]] = []
        if _table(connection, "inventory_import_batches") and _table(connection, "inventory_stock_lines"):
            row = connection.execute(
                "SELECT id,snapshot_date FROM inventory_import_batches WHERE status='completed' "
                "ORDER BY snapshot_date DESC,rowid DESC LIMIT 1"
            ).fetchone()
            if row is not None:
                inventory_batch = {"id": str(row["id"]), "snapshotDate": str(row["snapshot_date"])}
                inventory = _rows(
                    connection,
                    "SELECT TRIM(product_code) AS product_code,MAX(NULLIF(TRIM(brand),'')) AS brand,"
                    "COALESCE(SUM(CASE WHEN available_quantity>0 THEN available_quantity ELSE 0 END),0) AS available_quantity,"
                    "COALESCE(SUM(CASE WHEN unit_cost_cents>0 AND available_quantity>0 "
                    "THEN available_quantity*unit_cost_cents ELSE 0 END),0) AS known_stock_value_cents,"
                    "COALESCE(SUM(CASE WHEN unit_cost_cents>0 AND available_quantity>0 "
                    "THEN available_quantity ELSE 0 END),0) AS priced_available_quantity "
                    "FROM inventory_stock_lines WHERE batch_id=? AND TRIM(warehouse)<>'刷刷仓' "
                    "AND TRIM(product_code)<>'' GROUP BY TRIM(product_code) ORDER BY TRIM(product_code)",
                    (inventory_batch["id"],),
                )
                inventory = [
                    {
                        "productCode": str(row["product_code"]),
                        "brand": str(row.get("brand") or ""),
                        "availableQuantity": int(row.get("available_quantity") or 0),
                        "knownStockValueCents": int(row.get("known_stock_value_cents") or 0),
                        "pricedAvailableQuantity": int(row.get("priced_available_quantity") or 0),
                    }
                    for row in inventory
                ]
        if len(inventory) > 20_000:
            raise CommandError("D1 最新库存投影超过商品域 20,000 规格上限")
        inventory_revision = (
            _projection_digest(inventory_batch["id"], inventory_batch["snapshotDate"], inventory)
            if inventory_batch
            else ""
        )
        snapshot = {
            "version": GENERATION_VERSION,
            "batches": prepared_batches,
            "rates": rates,
            "fingerprints": prepared_fingerprints,
            "attempts": prepared_attempts,
            "scopeHead": {
                "scopeKey": SCOPE_KEY,
                "stateToken": state_token,
                "status": "ready",
                "currentBatchId": current_batch_id,
                "generation": generation,
            },
            "inventoryBatch": inventory_batch,
            "inventoryRevision": inventory_revision,
            "inventory": inventory,
        }
        snapshot["fingerprints"] = sorted(
            snapshot["fingerprints"], key=lambda row: (row["createdAt"], row["batchId"])
        )
        snapshot["attempts"] = sorted(
            snapshot["attempts"], key=lambda row: (row["createdAt"], row["id"])
        )
        connection.rollback()
        return snapshot
    finally:
        connection.close()


def _target_snapshot() -> dict[str, object]:
    batches = [
        {
            "id": row.id,
            "source": row.source,
            "fileName": row.file_name,
            "fileSizeBytes": int(row.file_size_bytes),
            "fileHash": row.file_hash,
            "rawFileHash": row.raw_file_hash,
            "contentHash": row.content_hash,
            "sheetName": row.sheet_name,
            "actor": row.actor_email,
            "status": row.status,
            "sourceRowCount": int(row.source_row_count),
            "rowCount": int(row.row_count),
            "insertedCount": int(row.inserted_count),
            "updatedCount": int(row.updated_count),
            "duplicateCount": int(row.duplicate_count),
            "warningCount": int(row.warning_count),
            "warnings": row.warnings_json,
            "totals": row.totals_json,
            "createdAt": row.created_at,
            "completedAt": row.completed_at,
        }
        for row in ProductShippingRateImportBatch.objects.order_by("created_at", "id")
    ]
    rates = [
        {
            "productCode": row.product_code,
            "shippingRatePpt": int(Decimal(row.shipping_rate) * Decimal(10**12)),
            "sourceRowNumber": int(row.source_row_number),
            "lastImportBatchId": row.last_import_batch_id,
        }
        for row in ProductShippingRate.objects.order_by("product_code")
    ]
    fingerprints = [
        {
            "batchId": row.batch_id,
            "scopeKey": row.scope_key,
            "scope": row.scope_json,
            "importHash": row.import_hash,
            "contentHash": row.content_hash,
            "rawFileHash": row.raw_file_hash,
            "rowCount": int(row.row_count),
            "publishedStateToken": row.published_state_token,
            "status": row.status,
            "publicationSequence": row.publication_sequence,
            "createdAt": row.created_at.isoformat(),
        }
        for row in ProductImportFingerprint.objects.order_by("created_at", "id")
    ]
    attempts = [
        {
            "id": str(row.id),
            "batchId": row.batch_id,
            "scopeKey": row.scope_key,
            "scope": row.scope_json,
            "rawFileHash": row.raw_file_hash,
            "contentHash": row.content_hash,
            "rowCount": int(row.row_count),
            "outcome": row.outcome,
            "errorCode": row.error_code,
            "actor": row.actor_email,
            "metadata": row.metadata,
            "createdAt": row.created_at.isoformat(),
            "completedAt": row.completed_at.isoformat() if row.completed_at else "",
        }
        for row in ProductImportAttempt.objects.order_by("created_at", "id")
    ]
    head = ProductImportScopeHead.objects.get(scope_key=SCOPE_KEY)
    control = ProductInventoryProjectionControl.objects.get(id=1)
    inventory = [
        {
            "productCode": row.product_code,
            "brand": row.brand,
            "availableQuantity": int(row.available_quantity),
            "knownStockValueCents": int(row.known_stock_value_cents),
            "pricedAvailableQuantity": int(row.priced_available_quantity),
        }
        for row in ProductInventoryProjection.objects.filter(
            projection_revision=control.active_revision
        ).order_by("product_code")
    ] if control.active_revision else []
    return {
        "version": GENERATION_VERSION,
        "batches": batches,
        "rates": rates,
        "fingerprints": fingerprints,
        "attempts": attempts,
        "scopeHead": {
            "scopeKey": head.scope_key,
            "stateToken": head.state_token,
            "status": head.status,
            "currentBatchId": head.current_batch_id,
            "generation": int(head.generation),
        },
        "inventoryBatch": {
            "id": control.active_source_batch_id,
            "snapshotDate": control.active_snapshot_date,
        } if control.active_revision else None,
        "inventoryRevision": control.active_revision,
        "inventory": inventory,
    }


def _counts(snapshot: dict[str, object]) -> dict[str, int]:
    return {
        key: len(snapshot[key])
        for key in ("batches", "rates", "fingerprints", "attempts", "inventory")
    }


class Command(BaseCommand):
    help = "Plan, apply, or verify the product-operations migration from a read-only D1 SQLite snapshot."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True)
        parser.add_argument("--mode", choices=("plan", "apply", "verify"), required=True)
        parser.add_argument("--approve-run-id")
        parser.add_argument("--verify-run-id")

    def handle(self, *args, **options):
        if (
            settings.DJANGO_ENVIRONMENT == "production"
            and settings.DJANGO_PROCESS_ROLE != "migration_writer"
        ):
            raise CommandError("生产商品经营迁移只能由 migration_writer 进程角色操作")
        source = Path(str(options["source"])).resolve(strict=True)
        if not source.is_file():
            raise CommandError("D1 source 必须是现有 SQLite 文件")
        source_path_digest = hashlib.sha256(str(source).lower().encode()).hexdigest()
        snapshot = _source_snapshot(source)
        source_digest = _sha(snapshot)
        counts = _counts(snapshot)
        mode = str(options["mode"])
        if mode == "plan":
            run_id = f"products-plan-{uuid.uuid4().hex}"
            ProductMigrationRun.objects.create(
                id=run_id,
                mode="plan",
                status="planned",
                source_path_digest=source_path_digest,
                source_snapshot_digest=source_digest,
                source_counts=counts,
                manifest={"version": GENERATION_VERSION, "sourceDigest": source_digest},
            )
            self.stdout.write(_json({"ok": True, "mode": mode, "runId": run_id, "sourceDigest": source_digest, "counts": counts}))
            return
        if mode == "verify":
            run_id = str(options.get("verify_run_id") or "")
            run = ProductMigrationRun.objects.filter(
                id=run_id,
                mode="apply",
                status__in=["applied", "verified"],
            ).first()
            if run is None:
                raise CommandError("verify-run-id 不是已应用的商品经营迁移")
            target = _target_snapshot()
            target_digest = _sha(target)
            if (
                source_path_digest != run.source_path_digest
                or source_digest != run.source_snapshot_digest
                or target_digest != run.target_snapshot_digest
                or _counts(target) != run.target_counts
            ):
                raise CommandError("商品经营迁移复验摘要不一致")
            with transaction.atomic():
                authority = ProductWriteAuthority.objects.select_for_update().get(id=1)
                if authority.status != "d1":
                    raise CommandError("商品经营 PostgreSQL 已激活写权，禁止改写迁移凭据")
                run = ProductMigrationRun.objects.select_for_update().get(id=run_id)
                run.status = "verified"
                run.completed_at = timezone.now()
                run.save(update_fields=["status", "completed_at"])
                authority.migration_verify_run_id = run_id
                authority.save(update_fields=["migration_verify_run_id", "updated_at"])
            self.stdout.write(_json({"ok": True, "mode": mode, "runId": run.id, "targetDigest": target_digest, "counts": _counts(target)}))
            return

        approved_id = str(options.get("approve_run_id") or "")
        approved = ProductMigrationRun.objects.filter(id=approved_id, status="planned", mode="plan").first()
        if approved is None:
            raise CommandError("apply 必须提供仍有效的 --approve-run-id")
        if approved.source_path_digest != source_path_digest or approved.source_snapshot_digest != source_digest:
            raise CommandError("D1 快照在 plan 后已变化，拒绝应用")
        authority = ProductWriteAuthority.objects.get(id=1)
        if authority.status != "d1":
            raise CommandError("商品经营 PostgreSQL 已激活写权，禁止重新迁移")
        if any(
            model.objects.exists()
            for model in (
                ProductShippingRateImportBatch,
                ProductShippingRate,
                ProductImportAttempt,
                ProductImportFingerprint,
                ProductInventoryProjection,
                ProductRawUploadSession,
                ProductWriteRequestReceipt,
            )
        ):
            raise CommandError("商品经营目标库不是空镜像，拒绝覆盖")
        head = ProductImportScopeHead.objects.get(scope_key=SCOPE_KEY)
        control = ProductInventoryProjectionControl.objects.get(id=1)
        revision = ProductDataRevision.objects.get(domain="products")
        if (
            head.state_token != "0" * 64
            or head.status != "ready"
            or head.owner_token
            or head.current_batch_id
            or int(head.generation) != 0
            or control.active_revision
            or int(control.active_total) != 0
            or control.syncing_revision
            or int(control.syncing_total) != 0
            or int(control.syncing_offset) != 0
            or control.syncing_owner
            or control.owner_token_hash
            or int(revision.revision) != 0
            or bool(revision.source_digest)
            or bool(authority.migration_verify_run_id)
        ):
            raise CommandError("商品经营目标控制状态不是全新镜像，拒绝覆盖")
        run_id = f"products-apply-{uuid.uuid4().hex}"
        with transaction.atomic():
            ProductMigrationRun.objects.create(
                id=run_id,
                mode="apply",
                status="applying",
                source_path_digest=source_path_digest,
                source_snapshot_digest=source_digest,
                source_counts=counts,
                approved_run_id=approved_id,
                manifest={"version": GENERATION_VERSION, "sourceDigest": source_digest},
            )
            ProductShippingRateImportBatch.objects.bulk_create(
                [
                    ProductShippingRateImportBatch(
                        id=row["id"], source=row["source"], file_name=row["fileName"],
                        file_size_bytes=row["fileSizeBytes"], file_hash=row["fileHash"],
                        raw_file_hash=row["rawFileHash"], content_hash=row["contentHash"],
                        scope_key=SCOPE_KEY, published_state_token=snapshot["scopeHead"]["stateToken"],
                        sheet_name=row["sheetName"], actor_email=row["actor"], status=row["status"],
                        source_row_count=row["sourceRowCount"], row_count=row["rowCount"],
                        inserted_count=row["insertedCount"], updated_count=row["updatedCount"],
                        duplicate_count=row["duplicateCount"], warning_count=row["warningCount"],
                        warnings_json=row["warnings"], totals_json=row["totals"],
                        created_at=row["createdAt"], completed_at=row["completedAt"],
                        migration_generation=run_id,
                    )
                    for row in snapshot["batches"]
                ],
                batch_size=500,
            )
            ProductShippingRate.objects.bulk_create(
                [
                    ProductShippingRate(
                        product_code=row["productCode"],
                        shipping_rate=Decimal(row["shippingRatePpt"]) / Decimal(10**12),
                        source_row_number=row["sourceRowNumber"],
                        last_import_batch_id=row["lastImportBatchId"],
                        migration_generation=run_id,
                    )
                    for row in snapshot["rates"]
                ],
                batch_size=500,
            )
            ProductImportFingerprint.objects.bulk_create(
                [
                    ProductImportFingerprint(
                        batch_id=row["batchId"], scope_key=row["scopeKey"], scope_json=row["scope"],
                        import_hash=row["importHash"], content_hash=row["contentHash"],
                        raw_file_hash=row["rawFileHash"], row_count=row["rowCount"],
                        published_state_token=row["publishedStateToken"], status=row["status"],
                        publication_sequence=row["publicationSequence"], created_at=_timestamp(row["createdAt"]),
                    )
                    for row in snapshot["fingerprints"]
                ],
                batch_size=500,
            )
            ProductImportAttempt.objects.bulk_create(
                [
                    ProductImportAttempt(
                        id=row["id"], batch_id=row["batchId"], scope_key=row["scopeKey"],
                        scope_json=row["scope"], raw_file_hash=row["rawFileHash"],
                        content_hash=row["contentHash"], row_count=row["rowCount"],
                        outcome=row["outcome"], error_code=row["errorCode"], actor_email=row["actor"],
                        metadata=row["metadata"], created_at=_timestamp(row["createdAt"]),
                        completed_at=_timestamp(row["completedAt"]),
                    )
                    for row in snapshot["attempts"]
                ],
                batch_size=500,
            )
            head = ProductImportScopeHead.objects.select_for_update().get(scope_key=SCOPE_KEY)
            head.state_token = snapshot["scopeHead"]["stateToken"]
            head.status = "ready"
            head.owner_token = ""
            head.current_batch_id = snapshot["scopeHead"]["currentBatchId"]
            head.generation = snapshot["scopeHead"]["generation"]
            head.owner_started_at = None
            head.heartbeat_at = None
            head.save()
            control = ProductInventoryProjectionControl.objects.select_for_update().get(id=1)
            control.active_revision = snapshot["inventoryRevision"]
            control.active_total = len(snapshot["inventory"])
            control.active_source_batch_id = (snapshot["inventoryBatch"] or {}).get("id", "")
            control.active_snapshot_date = (snapshot["inventoryBatch"] or {}).get("snapshotDate", "")
            control.save()
            ProductInventoryProjection.objects.bulk_create(
                [
                    ProductInventoryProjection(
                        projection_revision=snapshot["inventoryRevision"],
                        product_code=row["productCode"], brand=row["brand"],
                        available_quantity=row["availableQuantity"],
                        known_stock_value_cents=row["knownStockValueCents"],
                        priced_available_quantity=row["pricedAvailableQuantity"],
                        source_batch_id=control.active_source_batch_id,
                        snapshot_date=control.active_snapshot_date,
                    )
                    for row in snapshot["inventory"]
                ],
                batch_size=500,
            )
            revision = ProductDataRevision.objects.select_for_update().get(domain="products")
            revision.revision = 1
            revision.source_digest = source_digest
            revision.save()
            target = _target_snapshot()
            target_digest = _sha(target)
            if target_digest != source_digest:
                raise CommandError("商品经营迁移落库摘要回查不一致")
            run = ProductMigrationRun.objects.get(id=run_id)
            run.status = "applied"
            run.target_snapshot_digest = target_digest
            run.target_counts = _counts(target)
            run.completed_at = timezone.now()
            run.save()
        self.stdout.write(_json({"ok": True, "mode": mode, "runId": run_id, "approvedRunId": approved_id, "targetDigest": source_digest, "counts": counts}))
