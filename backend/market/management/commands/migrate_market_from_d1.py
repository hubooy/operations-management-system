from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass, field as dataclass_field
from datetime import timezone as datetime_timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from django.conf import settings
from django.core.management.color import no_style
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, models, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from market.import_service import (
    ROW_KEYS,
    _combined_scope_key,
    _content_hash,
    _lock_identity,
    _scope_identity,
    _scope_key,
)
from market.models import (
    MarketAnnotationCloudRun,
    MarketAnnotationCommitReceipt,
    MarketAnnotationConcurrencySetting,
    MarketAnnotationItem,
    MarketAnnotationJob,
    MarketAnnotationLocalAgent,
    MarketAnnotationPromptAudit,
    MarketAnnotationPromptVersion,
    MarketAnnotationValidationResult,
    MarketAnnotationValidationRun,
    MarketAnnotationValidationSample,
    MarketBrandRecognitionJob,
    MarketBrandSeed,
    MarketBrandSuggestion,
    MarketDataRevision,
    MarketDownloadConfig,
    MarketDownloadTask,
    MarketImageCache,
    MarketImageCacheJob,
    MarketImageCacheJobItem,
    MarketImportAttempt,
    MarketImportBatch,
    MarketImportFingerprint,
    MarketImportScopeHead,
    MarketMasterAuditLog,
    MarketMasterIdentity,
    MarketMasterMappingRule,
    MarketMigrationRun,
    MarketNetshopProjection,
    MarketNetshopProjectionControl,
    MarketPriceBandItem,
    MarketPriceBandVersion,
    MarketPriceSnapshot,
    MarketRankingEntry,
    MarketSkuAnnotation,
    MarketSkuGmvTotal,
    MarketSubcategoryTaxonomy,
    MarketWriteAuthority,
)


FORMAT_VERSION = "market-d1-migration-v1"
MAX_ROWS_PER_SECTION = 2_000_000
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
SOURCE_AUTHORITY_TABLE = "market_write_authority"


def _canonical(value: object) -> object:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(_canonical(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json(value: object, expected: type[list] | type[dict]) -> list | dict:
    if isinstance(value, expected):
        return value
    try:
        parsed = json.loads(str(value or "[]" if expected is list else value or "{}"))
    except json.JSONDecodeError as error:
        raise CommandError("D1 市场迁移材料包含无效 JSON。") from error
    if not isinstance(parsed, expected):
        raise CommandError("D1 市场迁移材料 JSON 类型不符合契约。")
    return parsed


def _nullable_object(value: object) -> dict:
    if value is None or (isinstance(value, str) and value.strip().lower() in {"", "null"}):
        return {}
    return _json(value, dict)  # type: ignore[return-value]


def _datetime(value: object, *, nullable: bool) -> object:
    if value in {None, ""} and nullable:
        return None
    parsed = parse_datetime(str(value or ""))
    if parsed is None:
        raise CommandError("D1 市场迁移材料包含无效时间。")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, datetime_timezone.utc)
    return parsed


def _open_source(path: Path) -> sqlite3.Connection:
    source = sqlite3.connect(f"file:{path.as_posix()}?mode=ro&immutable=1", uri=True, timeout=30)
    source.row_factory = sqlite3.Row
    source.execute("PRAGMA query_only=ON")
    source.execute("BEGIN")
    return source


def _tables(source: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(source: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in source.execute(f'PRAGMA table_info("{table}")')}


def _rows(
    source: sqlite3.Connection,
    table: str,
    *,
    where: str = "",
    parameters: tuple[object, ...] = (),
) -> list[dict[str, object]]:
    query = f'SELECT * FROM "{table}"'
    if where:
        query += f" WHERE {where}"
    return [dict(row) for row in source.execute(query, parameters)]


def _source_authority(
    source: sqlite3.Connection,
    *,
    apply: bool,
    allowed_owners: set[str] | frozenset[str] | None = None,
) -> dict[str, object]:
    row = source.execute(
        f"SELECT id,owner,epoch,cutover_id,updated_at FROM {SOURCE_AUTHORITY_TABLE} WHERE id=1"
    ).fetchone()
    allowed = set(allowed_owners) if allowed_owners is not None else {"pending"} if apply else {"d1", "pending"}
    if row is None or str(row["owner"]) not in allowed or int(row["epoch"]) < 1:
        raise CommandError("D1 市场写 authority 不在本操作允许状态。")
    return dict(row)


def _validate_source(
    source: sqlite3.Connection,
    *,
    apply: bool,
    allowed_owners: set[str] | frozenset[str] | None = None,
) -> dict[str, object]:
    if [str(row[0]) for row in source.execute("PRAGMA quick_check")] != ["ok"]:
        raise CommandError("D1 市场封存快照未通过 SQLite quick_check。")
    required = {spec.source_table for spec in SPECS} | {
        SOURCE_AUTHORITY_TABLE,
        "import_content_fingerprints",
        "import_content_attempts",
        "import_scope_heads",
        "market_import_range_claims",
        "market_import_staging_rows",
        "market_download_staging_rows",
        "market_image_cache_claims",
        "market_netshop_projection_control",
    }
    missing = required - _tables(source)
    if missing:
        raise CommandError(f"D1 源缺少必需市场表：{', '.join(sorted(missing))}。")
    if source.execute("SELECT COUNT(*) FROM market_ranking_entries").fetchone()[0] > MAX_ROWS_PER_SECTION:
        raise CommandError("D1 市场事实超过受控迁移上限。")
    quiet_checks = (
        ("SELECT COUNT(*) FROM market_import_batches WHERE status='processing'", "存在处理中市场批次"),
        ("SELECT COUNT(*) FROM market_import_range_claims", "存在市场导入范围 claim"),
        ("SELECT COUNT(*) FROM market_import_staging_rows", "存在市场导入 staging 行"),
        ("SELECT COUNT(*) FROM market_download_staging_rows", "存在市场下载 staging 行"),
        ("SELECT COUNT(*) FROM market_image_cache_claims", "存在市场图片 claim"),
        ("SELECT COUNT(*) FROM market_annotation_items WHERE status IN ('claimed','inferencing')", "存在执行中的标注项"),
        ("SELECT COUNT(*) FROM market_annotation_validation_results WHERE COALESCE(claim_token_hash,'')<>''", "存在执行中的验证项"),
        ("SELECT COUNT(*) FROM market_annotation_cloud_runs WHERE COALESCE(lease_token_hash,'')<>''", "存在云标注租约"),
        ("SELECT COUNT(*) FROM market_brand_recognition_jobs WHERE COALESCE(lease_token,'')<>''", "存在品牌识别租约"),
        ("SELECT COUNT(*) FROM market_image_cache_jobs WHERE COALESCE(lease_token,'')<>''", "存在图片任务租约"),
        ("SELECT COUNT(*) FROM market_download_tasks WHERE COALESCE(execution_token,'')<>''", "存在下载执行租约"),
        ("SELECT COUNT(*) FROM market_download_tasks WHERE status IN ('downloading','validating','importing','running')", "存在执行中的下载任务"),
        ("SELECT COUNT(*) FROM import_content_attempts WHERE domain='market' AND outcome='processing'", "存在处理中市场导入尝试"),
        ("SELECT COUNT(*) FROM import_scope_heads WHERE domain='market' AND (status<>'ready' OR COALESCE(owner_token,'')<>'')", "市场导入 scope head 未静默"),
    )
    for sql, message in quiet_checks:
        if int(source.execute(sql).fetchone()[0]):
            raise CommandError(f"D1 {message}，拒绝迁移。")
    control = source.execute(
        "SELECT active_revision,active_total,syncing_revision,owner_token FROM market_netshop_projection_control WHERE id=1"
    ).fetchone()
    if control is None or str(control["syncing_revision"] or "") or str(control["owner_token"] or ""):
        raise CommandError("D1 市场网店投影不是静默激活状态。")
    actual_projection = int(
        source.execute(
            "SELECT COUNT(*) FROM market_netshop_projection WHERE projection_revision=?",
            (str(control["active_revision"] or ""),),
        ).fetchone()[0]
    )
    if actual_projection != int(control["active_total"] or 0):
        raise CommandError("D1 市场网店投影激活行数不一致。")
    return _source_authority(source, apply=apply, allowed_owners=allowed_owners)


def _business_row(row: dict[str, object]) -> dict[str, object]:
    mapping = {
        "naturalKey": "natural_key",
        "sourceRowNumber": "source_row_number",
        "periodStart": "period_start",
        "periodEnd": "period_end",
        "category": "category",
        "scope": "scope",
        "priceBandFilter": "price_band_filter",
        "rankingDimension": "ranking_dimension",
        "operationMode": "source_operation_mode",
        "subcategory": "source_subcategory",
        "rank": "rank",
        "skuCode": "sku_code",
        "productName": "product_name",
        "brand": "source_brand",
        "priceCents": "price_cents",
        "priceLowCents": "price_low_cents",
        "priceHighCents": "price_high_cents",
        "priceEstimated": "price_estimated",
        "priceRaw": "price_raw",
        "gmvCents": "gmv_cents",
        "gmvLowCents": "gmv_low_cents",
        "gmvHighCents": "gmv_high_cents",
        "gmvRaw": "gmv_raw",
        "quantity": "quantity",
        "quantityLow": "quantity_low",
        "quantityHigh": "quantity_high",
        "quantityRaw": "quantity_raw",
        "pageViews": "page_views",
        "pageViewsRaw": "page_views_raw",
        "visitors": "visitors",
        "visitorsLow": "visitors_low",
        "visitorsHigh": "visitors_high",
        "visitorsRaw": "visitors_raw",
        "conversionBps": "conversion_bps",
        "conversionLowBps": "conversion_low_bps",
        "conversionHighBps": "conversion_high_bps",
        "conversionRaw": "conversion_raw",
        "cartCustomers": "cart_customers",
        "cartCustomersRaw": "cart_customers_raw",
        "searchClicks": "search_clicks",
        "searchClicksRaw": "search_clicks_raw",
        "imageUrl": "image_url",
        "productUrl": "product_url",
        "raw": "raw_json",
    }
    result = {key: row.get(column) for key, column in mapping.items()}
    result["operationMode"] = row.get("source_operation_mode") or row.get("operation_mode") or "未知"
    result["subcategory"] = row.get("source_subcategory") or row.get("subcategory") or ""
    result["brand"] = row.get("source_brand") or row.get("brand") or ""
    result["priceEstimated"] = bool(result["priceEstimated"])
    result["raw"] = _json(result["raw"], dict)
    if set(result) != ROW_KEYS:
        raise CommandError("D1 市场事实无法转换为当前规范化行契约。")
    return result


def _build_context(source: sqlite3.Connection) -> dict[str, object]:
    fingerprints = {
        str(row["batch_id"]): dict(row)
        for row in source.execute(
            "SELECT * FROM import_content_fingerprints WHERE domain='market' ORDER BY sequence"
        )
    }
    attempts = [
        dict(row)
        for row in source.execute(
            "SELECT * FROM import_content_attempts WHERE domain='market' ORDER BY sequence"
        )
    ]
    attempts_by_batch: dict[str, list[dict[str, object]]] = {}
    for item in attempts:
        attempts_by_batch.setdefault(str(item.get("batch_id") or ""), []).append(item)
    ranking_rows = _rows(source, "market_ranking_entries")
    rows_by_batch: dict[str, list[dict[str, object]]] = {}
    for item in ranking_rows:
        rows_by_batch.setdefault(str(item["last_import_batch_id"]), []).append(item)
    batches: dict[str, dict[str, object]] = {}
    for batch in _rows(source, "market_import_batches"):
        batch_id = str(batch["id"])
        owned = rows_by_batch.get(batch_id, [])
        fingerprint = fingerprints.get(batch_id, {})
        use_current = bool(owned) and len(owned) == int(batch.get("row_count") or 0)
        if use_current:
            normalized = [_business_row(row) for row in owned]
            ranges_by_key = {
                json.dumps(_scope_identity(row), ensure_ascii=False, sort_keys=True): _scope_identity(row)
                for row in normalized
            }
            ranges = [ranges_by_key[key] for key in sorted(ranges_by_key)]
            scope = {"sourceType": str(batch["source_type"]), "ranges": ranges}
            content_hash = _content_hash(normalized, scope)
            combined_scope_key = _combined_scope_key(ranges)
        else:
            scope = _json(fingerprint.get("scope_json", "{}"), dict)
            content_hash = str(fingerprint.get("content_hash") or "")
            if not HEX64_RE.fullmatch(content_hash):
                content_hash = _digest_text(f"legacy-market-batch\n{batch_id}")
            combined_scope_key = str(fingerprint.get("scope_key") or _digest_text(f"legacy-market-scope\n{batch_id}"))
        raw_hash = str(batch.get("file_hash") or fingerprint.get("raw_file_hash") or "").lower()
        if not HEX64_RE.fullmatch(raw_hash):
            raise CommandError(f"D1 市场批次 {batch_id} 缺少有效原文件 SHA-256。")
        state_token = _digest_text(
            json.dumps(
                {"batchId": batch_id, "contentHash": content_hash, "scope": scope},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        latest_attempt = attempts_by_batch.get(batch_id, [])[-1] if attempts_by_batch.get(batch_id) else {}
        batches[batch_id] = {
            "scope": scope,
            "scopeKey": combined_scope_key,
            "contentHash": content_hash,
            "rawFileHash": raw_hash,
            "stateToken": state_token,
            "publicationSequence": fingerprint.get("publication_sequence"),
            "actor": str(latest_attempt.get("actor") or "migration@teruisi.internal"),
            "fingerprintCreatedAt": fingerprint.get("created_at") or batch.get("created_at"),
        }
    item_by_id = {str(row["id"]): row for row in _rows(source, "market_annotation_items")}
    identities_by_category_sku: dict[tuple[str, str], list[dict[str, object]]] = {}
    for row in _rows(source, "market_master_identities"):
        identities_by_category_sku.setdefault((str(row["category"]), str(row["sku_code"])), []).append(row)
    cache_hash_by_url = {
        str(row["source_url"]): str(row["content_sha256"] or "")
        for row in _rows(source, "market_image_cache")
        if HEX64_RE.fullmatch(str(row["content_sha256"] or ""))
    }
    snapshots = _rows(source, "market_price_snapshots")

    def exact_identity(row: dict[str, object]) -> dict[str, str]:
        item = item_by_id.get(str(row.get("source_job_item_id") or ""), {})
        category = str(item.get("category") or row.get("category") or "")
        sku_code = str(item.get("sku_code") or row.get("sku_code") or "")
        scope = str(item.get("scope") or "")
        dimension = str(item.get("ranking_dimension") or "")
        image_hash = str(item.get("image_content_sha256") or "")
        candidates = identities_by_category_sku.get((category, sku_code), [])
        if not scope or dimension not in {"SKU", "SPU"}:
            if len(candidates) != 1:
                raise CommandError(f"旧市场标注 {category}/{sku_code} 无法唯一映射到完整业务身份。")
            scope = str(candidates[0]["scope"])
            dimension = str(candidates[0]["ranking_dimension"])
        if not HEX64_RE.fullmatch(image_hash):
            image_hash = cache_hash_by_url.get(str(row.get("image_url") or ""), "")
        if not HEX64_RE.fullmatch(image_hash):
            values = {
                str(snapshot["image_content_sha256"] or "")
                for snapshot in snapshots
                if str(snapshot["category"]) == category
                and str(snapshot["scope"]) == scope
                and str(snapshot["sku_code"]) == sku_code
                and str(snapshot["ranking_dimension"]) == dimension
                and HEX64_RE.fullmatch(str(snapshot["image_content_sha256"] or ""))
            }
            if len(values) == 1:
                image_hash = values.pop()
        if not HEX64_RE.fullmatch(image_hash):
            raise CommandError(f"旧市场标注 {category}/{scope}/{dimension}/{sku_code} 缺少可验证图片哈希。")
        return {
            "category": category,
            "scope": scope,
            "sku_code": sku_code,
            "ranking_dimension": dimension,
            "image_content_sha256": image_hash,
        }

    annotations = {str(row["id"]): exact_identity(row) for row in _rows(source, "market_sku_annotations")}
    control = dict(
        source.execute("SELECT * FROM market_netshop_projection_control WHERE id=1").fetchone()
    )
    return {
        "fingerprints": fingerprints,
        "attempts": attempts,
        "rankingRows": ranking_rows,
        "rowsByBatch": rows_by_batch,
        "batches": batches,
        "exactIdentity": exact_identity,
        "annotationIdentities": annotations,
        "projectionControl": control,
        "normalizations": {
            "marketMasterAuditBeforeNullToEmptyObject": int(
                source.execute(
                    "SELECT COUNT(*) FROM market_master_audit_logs "
                    "WHERE LOWER(TRIM(before_json))='null'"
                ).fetchone()[0]
            ),
        },
    }


RecordBuilder = Callable[[sqlite3.Connection, dict[str, object]], list[dict[str, object]]]


@dataclass(frozen=True)
class Spec:
    name: str
    source_table: str
    model: type[models.Model]
    aliases: dict[str, str] = dataclass_field(default_factory=dict)
    exclude_fields: frozenset[str] = frozenset({"migration_generation"})
    include_auto_id: bool = False
    where: str = ""
    parameters: tuple[object, ...] = ()
    overrides: dict[str, object | Callable[[dict[str, object], dict[str, object]], object]] = dataclass_field(default_factory=dict)
    builder: RecordBuilder | None = None


def _fields(spec: Spec) -> list[models.Field]:
    values: list[models.Field] = []
    for item in spec.model._meta.concrete_fields:
        if item.name in spec.exclude_fields:
            continue
        if isinstance(item, models.AutoField) and not spec.include_auto_id:
            continue
        values.append(item)
    return values


def _convert(field: models.Field, value: object) -> object:
    if value is None:
        return None
    if isinstance(field, models.JSONField):
        return _json(value, list if isinstance(field.get_default(), list) else dict)
    if isinstance(field, models.DateTimeField):
        return _datetime(value, nullable=field.null)
    if isinstance(field, models.BooleanField):
        return bool(value)
    if isinstance(field, models.UUIDField):
        try:
            return uuid.UUID(str(value))
        except ValueError as error:
            raise CommandError(f"D1 市场迁移 UUID 字段 {field.name} 无效。") from error
    if isinstance(field, (models.IntegerField, models.BigIntegerField)):
        return int(value)
    if isinstance(field, (models.CharField, models.TextField)):
        return str(value)
    return value


def _record(
    source_row: dict[str, object],
    spec: Spec,
    context: dict[str, object],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for model_field in _fields(spec):
        name = model_field.name
        if name in spec.overrides:
            override = spec.overrides[name]
            raw = override(source_row, context) if callable(override) else override
        else:
            column = spec.aliases.get(name, name)
            if column in source_row:
                raw = source_row[column]
            elif model_field.has_default():
                raw = model_field.get_default()
            elif model_field.null:
                raw = None
            else:
                raise CommandError(f"D1 表 {spec.source_table} 缺少必需字段 {column}。")
        try:
            result[name] = _convert(model_field, raw)
        except CommandError as error:
            source_column = spec.aliases.get(name, name)
            raise CommandError(
                f"D1 表 {spec.source_table} 字段 {source_column} 不符合市场迁移契约。"
            ) from error
    return result


def _batch_records(source: sqlite3.Connection, context: dict[str, object]) -> list[dict[str, object]]:
    spec = SPEC_BY_NAME["market_import_batches"]
    return [_record(row, spec, context) for row in _rows(source, spec.source_table)]


def _attempt_records(source: sqlite3.Connection, context: dict[str, object]) -> list[dict[str, object]]:
    result = []
    for row in context["attempts"]:  # type: ignore[index]
        result.append(
            {
                "batch_id": str(row.get("batch_id") or ""),
                "scope_key": str(row.get("scope_key") or ""),
                "raw_file_hash": str(row.get("raw_file_hash") or ""),
                "content_hash": str(row.get("content_hash") or ""),
                "outcome": str(row.get("outcome") or "failed"),
                "error_code": str(row.get("error_code") or ""),
                "actor_email": str(row.get("actor") or ""),
                "metadata": {
                    "legacyAttemptId": str(row.get("attempt_id") or ""),
                    "scope": _json(row.get("scope_json", "{}"), dict),
                    "fileName": str(row.get("file_name") or ""),
                    "fileSizeBytes": int(row.get("file_size_bytes") or 0),
                    "warnings": _json(row.get("warnings_json", "[]"), list),
                    "recoveredFromAttemptId": str(row.get("recovered_from_attempt_id") or ""),
                },
                "created_at": _datetime(row.get("created_at"), nullable=False),
                "completed_at": _datetime(row.get("updated_at"), nullable=True),
            }
        )
    return result


def _fingerprint_records(source: sqlite3.Connection, context: dict[str, object]) -> list[dict[str, object]]:
    batches: dict[str, dict[str, object]] = context["batches"]  # type: ignore[assignment]
    source_batches = {str(row["id"]): row for row in _rows(source, "market_import_batches")}
    result = []
    for batch_id, meta in batches.items():
        batch = source_batches[batch_id]
        result.append(
            {
                "batch_id": batch_id,
                "scope_key": meta["scopeKey"],
                "scope_json": meta["scope"],
                "import_hash": meta["rawFileHash"],
                "content_hash": meta["contentHash"],
                "raw_file_hash": meta["rawFileHash"],
                "row_count": int(batch.get("row_count") or 0),
                "published_state_token": meta["stateToken"],
                "status": "completed",
                "publication_sequence": meta["publicationSequence"],
                "created_at": _datetime(meta["fingerprintCreatedAt"], nullable=False),
            }
        )
    return result


def _scope_head_records(source: sqlite3.Connection, context: dict[str, object]) -> list[dict[str, object]]:
    batches: dict[str, dict[str, object]] = context["batches"]  # type: ignore[assignment]
    latest: dict[str, tuple[str, str, object]] = {}
    for row in context["rankingRows"]:  # type: ignore[index]
        normalized = _business_row(row)
        lock = _lock_identity(_scope_identity(normalized))
        key = _scope_key(lock)
        batch_id = str(row["last_import_batch_id"])
        candidate = (str(row.get("updated_at") or ""), batch_id, lock)
        if key not in latest or candidate[0] > latest[key][0]:
            latest[key] = candidate
    return [
        {
            "scope_key": key,
            "state_token": batches[batch_id]["stateToken"],
            "status": "ready",
            "owner_token": "",
            "current_batch_id": batch_id,
            "generation": 1,
            "owner_started_at": None,
            "heartbeat_at": None,
            "updated_at": _datetime(updated_at, nullable=False),
        }
        for key, (updated_at, batch_id, _lock) in latest.items()
    ]


def _annotation_overrides(row: dict[str, object], context: dict[str, object]) -> dict[str, str]:
    return context["exactIdentity"](row)  # type: ignore[operator]


def _validation_identity(row: dict[str, object], context: dict[str, object]) -> dict[str, str]:
    annotation = context["annotationIdentities"].get(str(row.get("source_annotation_id") or ""))  # type: ignore[union-attr]
    return annotation or _annotation_overrides(row, context)


SPECS = [
    Spec(
        "market_import_batches",
        "market_import_batches",
        MarketImportBatch,
        aliases={"raw_file_hash": "file_hash"},
        overrides={
            "content_hash": lambda row, ctx: ctx["batches"][str(row["id"])]["contentHash"],
            "scope_json": lambda row, ctx: ctx["batches"][str(row["id"])]["scope"],
            "published_state_token": lambda row, ctx: ctx["batches"][str(row["id"])]["stateToken"],
            "actor_email": lambda row, ctx: ctx["batches"][str(row["id"])]["actor"],
        },
        builder=_batch_records,
    ),
    Spec("market_ranking_entries", "market_ranking_entries", MarketRankingEntry, include_auto_id=True),
    Spec("market_master_identities", "market_master_identities", MarketMasterIdentity),
    Spec("market_sku_gmv_totals", "market_sku_gmv_totals", MarketSkuGmvTotal),
    Spec("market_price_snapshots", "market_price_snapshots", MarketPriceSnapshot),
    Spec("market_import_scope_heads", "import_scope_heads", MarketImportScopeHead, builder=_scope_head_records),
    Spec(
        "market_import_attempts",
        "import_content_attempts",
        MarketImportAttempt,
        exclude_fields=frozenset({"id", "migration_generation"}),
        builder=_attempt_records,
    ),
    Spec("market_import_fingerprints", "import_content_fingerprints", MarketImportFingerprint, builder=_fingerprint_records),
    Spec("market_image_cache", "market_image_cache", MarketImageCache),
    Spec(
        "market_image_cache_jobs",
        "market_image_cache_jobs",
        MarketImageCacheJob,
        aliases={"lease_token_hash": "lease_token"},
        overrides={
            "status": lambda row, _ctx: "queued" if row.get("status") == "running" else row.get("status"),
            "lease_expires_at": None,
        },
    ),
    Spec(
        "market_image_cache_job_items",
        "market_image_cache_job_items",
        MarketImageCacheJobItem,
        overrides={
            "status": lambda row, _ctx: "ready" if row.get("status") == "completed" else row.get("status"),
        },
    ),
    Spec("market_price_band_versions", "market_price_band_versions", MarketPriceBandVersion),
    Spec("market_price_band_items", "market_price_band_items", MarketPriceBandItem),
    Spec("market_master_mapping_rules", "market_master_mapping_rules", MarketMasterMappingRule),
    Spec("market_subcategory_taxonomy", "market_subcategory_taxonomy", MarketSubcategoryTaxonomy),
    Spec("market_brand_suggestions", "market_brand_suggestions", MarketBrandSuggestion),
    Spec(
        "market_brand_recognition_jobs",
        "market_brand_recognition_jobs",
        MarketBrandRecognitionJob,
        aliases={"lease_token_hash": "lease_token"},
        overrides={
            "status": lambda row, _ctx: "queued" if row.get("status") == "running" else row.get("status"),
            "lease_expires_at": None,
        },
    ),
    Spec("market_brand_seeds", "market_brand_seeds", MarketBrandSeed),
    Spec("market_download_configs", "market_download_configs", MarketDownloadConfig),
    Spec("market_download_tasks", "market_download_tasks", MarketDownloadTask, aliases={"execution_token_hash": "execution_token"}),
    Spec(
        "market_master_audit_logs",
        "market_master_audit_logs",
        MarketMasterAuditLog,
        exclude_fields=frozenset({"id", "migration_generation"}),
        overrides={
            "before_json": lambda row, _ctx: _nullable_object(row.get("before_json")),
        },
    ),
    Spec("market_annotation_prompt_versions", "market_annotation_prompt_versions", MarketAnnotationPromptVersion),
    Spec("market_annotation_jobs", "market_annotation_jobs", MarketAnnotationJob),
    Spec("market_annotation_items", "market_annotation_items", MarketAnnotationItem),
    Spec(
        "market_sku_annotations",
        "market_sku_annotations",
        MarketSkuAnnotation,
        overrides={
            "scope": lambda row, ctx: _annotation_overrides(row, ctx)["scope"],
            "ranking_dimension": lambda row, ctx: _annotation_overrides(row, ctx)["ranking_dimension"],
            "image_content_sha256": lambda row, ctx: _annotation_overrides(row, ctx)["image_content_sha256"],
        },
    ),
    Spec("market_annotation_commit_receipts", "market_annotation_commit_receipts", MarketAnnotationCommitReceipt),
    Spec(
        "market_annotation_validation_samples",
        "market_annotation_validation_samples",
        MarketAnnotationValidationSample,
        overrides={
            "scope": lambda row, ctx: _validation_identity(row, ctx)["scope"],
            "ranking_dimension": lambda row, ctx: _validation_identity(row, ctx)["ranking_dimension"],
            "image_content_sha256": lambda row, ctx: _validation_identity(row, ctx)["image_content_sha256"],
        },
    ),
    Spec("market_annotation_validation_runs", "market_annotation_validation_runs", MarketAnnotationValidationRun),
    Spec(
        "market_annotation_validation_results",
        "market_annotation_validation_results",
        MarketAnnotationValidationResult,
        overrides={
            "updated_at": lambda row, _ctx: row.get("updated_at") or row.get("created_at"),
        },
    ),
    Spec("market_annotation_prompt_audits", "market_annotation_prompt_audits", MarketAnnotationPromptAudit),
    Spec("market_annotation_local_agents", "market_annotation_local_agents", MarketAnnotationLocalAgent),
    Spec("market_annotation_concurrency_settings", "market_annotation_concurrency_settings", MarketAnnotationConcurrencySetting),
    Spec("market_annotation_cloud_runs", "market_annotation_cloud_runs", MarketAnnotationCloudRun),
    Spec(
        "market_netshop_projection",
        "market_netshop_projection",
        MarketNetshopProjection,
        where="projection_revision=(SELECT active_revision FROM market_netshop_projection_control WHERE id=1)",
    ),
]
SPEC_BY_NAME = {spec.name: spec for spec in SPECS}


def _source_records(source: sqlite3.Connection, spec: Spec, context: dict[str, object]) -> list[dict[str, object]]:
    if spec.builder:
        values = spec.builder(source, context)
    else:
        values = [
            _record(row, spec, context)
            for row in _rows(source, spec.source_table, where=spec.where, parameters=spec.parameters)
        ]
    if len(values) > MAX_ROWS_PER_SECTION:
        raise CommandError(f"市场迁移节 {spec.name} 超过受控上限。")
    return values


def _target_records(spec: Spec) -> list[dict[str, object]]:
    names = [item.name for item in _fields(spec)]
    return list(spec.model.objects.values(*names))


def _control_source(context: dict[str, object]) -> dict[str, object]:
    control: dict[str, object] = context["projectionControl"]  # type: ignore[assignment]
    return {
        "activeRevision": str(control.get("active_revision") or ""),
        "activeTotal": int(control.get("active_total") or 0),
    }


def _control_target() -> dict[str, object]:
    control = MarketNetshopProjectionControl.objects.get(id=1)
    return {"activeRevision": control.active_revision, "activeTotal": int(control.active_total)}


def _section(values: Iterable[dict[str, object]]) -> tuple[int, str]:
    digests = sorted(hashlib.sha256(_canonical_bytes(value)).hexdigest() for value in values)
    payload = f"{len(digests)}\n{''.join(digests)}"
    return len(digests), _digest_text(payload)


def _snapshot(
    sections: Iterable[tuple[str, Iterable[dict[str, object]]]],
) -> tuple[dict[str, int], dict[str, str], str]:
    counts: dict[str, int] = {}
    digests: dict[str, str] = {}
    for name, values in sections:
        count, digest = _section(values)
        counts[name] = count
        digests[name] = digest
    combined = hashlib.sha256(_canonical_bytes({"counts": counts, "digests": digests})).hexdigest()
    return counts, digests, combined


def _source_sections(source: sqlite3.Connection, context: dict[str, object]):
    for spec in SPECS:
        yield spec.name, _source_records(source, spec, context)
    yield "market_netshop_projection_control", [_control_source(context)]


def _target_sections():
    for spec in SPECS:
        yield spec.name, _target_records(spec)
    yield "market_netshop_projection_control", [_control_target()]


def _bulk(spec: Spec, records: list[dict[str, object]], generation: str) -> None:
    if not records:
        return
    has_generation = any(item.name == "migration_generation" for item in spec.model._meta.concrete_fields)
    objects = []
    timestamp_names = [
        item.name
        for item in _fields(spec)
        if isinstance(item, models.DateTimeField) and (item.auto_now or item.auto_now_add)
    ]
    timestamp_values: list[dict[str, object]] = []
    for record in records:
        values = dict(record)
        if has_generation:
            values["migration_generation"] = generation
        objects.append(spec.model(**values))
        timestamp_values.append({name: record[name] for name in timestamp_names})
    spec.model.objects.bulk_create(objects, batch_size=500)
    if timestamp_names:
        for item, original in zip(objects, timestamp_values, strict=True):
            for name, value in original.items():
                setattr(item, name, value)
        spec.model.objects.bulk_update(objects, timestamp_names, batch_size=500)


def _target_empty() -> bool:
    return all(not spec.model.objects.exists() for spec in SPECS)


def _apply(
    source: sqlite3.Connection,
    context: dict[str, object],
    *,
    source_digest: str,
    run_id: str,
) -> dict[str, object]:
    generation = run_id.removeprefix("market-")
    for spec in SPECS:
        _bulk(spec, _source_records(source, spec, context), generation)
    sequence_sql = connection.ops.sequence_reset_sql(
        no_style(),
        list(dict.fromkeys(spec.model for spec in SPECS)),
    )
    if sequence_sql:
        with connection.cursor() as cursor:
            for statement in sequence_sql:
                cursor.execute(statement)
    control_data = _control_source(context)
    control = MarketNetshopProjectionControl.objects.select_for_update().get(id=1)
    control.active_revision = str(control_data["activeRevision"])
    control.active_total = int(control_data["activeTotal"])
    control.syncing_revision = ""
    control.syncing_total = 0
    control.syncing_offset = 0
    control.syncing_owner = ""
    control.owner_token_hash = ""
    control.lease_expires_at = None
    control.save()
    publications = [
        int(value["publication_sequence"])
        for value in _target_records(SPEC_BY_NAME["market_import_fingerprints"])
        if value.get("publication_sequence") is not None
    ]
    MarketDataRevision.objects.update_or_create(
        domain="market",
        defaults={
            "revision": max([1, len(context["batches"]), *publications]),  # type: ignore[arg-type]
            "source_digest": source_digest,
        },
    )
    # D1 has no complete options-directory generation. Restoring business rows
    # must not retain a previously initialized PostgreSQL-only directory.
    from market.models import MarketAnalysisOption, MarketAnalysisOptionsState
    MarketAnalysisOption.objects.all().delete()
    MarketAnalysisOptionsState.objects.update_or_create(id=1, defaults={
        "status": "not_ready", "generation": "", "directory_digest": "", "identity_count": 0,
        "stored_bytes": 0, "source_revision": "", "reason": "restored_requires_initialization"})
    authority = MarketWriteAuthority.objects.select_for_update().get(id=1)
    authority.migration_verify_run_id = run_id
    authority.save(update_fields=["migration_verify_run_id", "updated_at"])
    return {"migrationGeneration": generation}


class Command(BaseCommand):
    help = "Plan, apply, or verify the D1 to PostgreSQL market migration."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--source", required=True)
        modes = parser.add_mutually_exclusive_group()
        modes.add_argument("--apply", action="store_true")
        modes.add_argument("--verify-only", action="store_true")
        parser.add_argument("--approved-run-id", default="")

    def handle(self, *args: Any, **options: Any) -> None:
        if settings.DJANGO_ENVIRONMENT == "production" and settings.DJANGO_PROCESS_ROLE != "migration_writer":
            raise CommandError("生产市场迁移只能由 migration_writer 进程角色操作。")
        source_path = Path(str(options["source"])).expanduser().resolve()
        if not source_path.is_file() or source_path.suffix.lower() not in {".sqlite", ".sqlite3", ".db"}:
            raise CommandError("--source 必须指向封存 SQLite 快照文件。")
        if any(Path(str(source_path) + suffix).exists() for suffix in ("-wal", "-shm")):
            raise CommandError("迁移源旁存在 WAL/SHM；请先生成封存快照。")
        apply = bool(options["apply"])
        verify_only = bool(options["verify_only"])
        approved = str(options.get("approved_run_id") or "").strip()
        source = _open_source(source_path)
        try:
            authority = _validate_source(
                source,
                apply=apply,
                allowed_owners=(
                    frozenset({"d1", "pending", "postgresql"})
                    if verify_only
                    else None
                ),
            )
            context = _build_context(source)
            counts, digests, source_digest = _snapshot(_source_sections(source, context))
            run_id = f"market-{source_digest[:24]}"
            manifest = {
                "version": FORMAT_VERSION,
                "runId": run_id,
                "sourceDigest": source_digest,
                "counts": counts,
                "digests": digests,
                "authority": authority,
                "normalizations": context["normalizations"],
            }
            if not apply and not verify_only:
                self.stdout.write(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
                return
            if approved != run_id:
                raise CommandError("--approved-run-id 与当前封存市场快照计划不一致。")
            if verify_only:
                run = MarketMigrationRun.objects.filter(id=run_id, status="completed").first()
                if run is None or run.source_snapshot_digest != source_digest or run.completed_at is None:
                    raise CommandError("PostgreSQL 缺少对应的已完成市场迁移记录。")
                target_counts, target_digests, target_digest = _snapshot(_target_sections())
                if target_counts != counts or target_digests != digests or target_digest != source_digest:
                    raise CommandError("D1 与 PostgreSQL 市场迁移回查不一致。")
                self.stdout.write(json.dumps({**manifest, "status": "verified", "targetDigest": target_digest}, ensure_ascii=False, sort_keys=True))
                return
            existing = MarketMigrationRun.objects.filter(id=run_id, status="completed").first()
            if existing:
                target_counts, target_digests, target_digest = _snapshot(_target_sections())
                if target_counts != counts or target_digests != digests or target_digest != source_digest:
                    raise CommandError("既有市场迁移记录与当前 PostgreSQL 数据不一致。")
                self.stdout.write(json.dumps({**manifest, "status": "duplicate", "targetDigest": target_digest}, ensure_ascii=False, sort_keys=True))
                return
            target_authority = MarketWriteAuthority.objects.filter(id=1).first()
            if target_authority is None or target_authority.status != "d1":
                raise CommandError("PostgreSQL 市场 authority 必须保持 d1 预切换状态。")
            if not _target_empty():
                raise CommandError("PostgreSQL 市场目标不是空白镜像，拒绝覆盖。")
            path_digest = _digest_text(os.path.normcase(str(source_path)))
            with transaction.atomic():
                audit = _apply(
                    source,
                    context,
                    source_digest=source_digest,
                    run_id=run_id,
                )
                target_counts, target_digests, target_digest = _snapshot(_target_sections())
                if target_counts != counts or target_digests != digests or target_digest != source_digest:
                    raise CommandError("市场迁移事务内逐节回查不一致。")
                run = MarketMigrationRun.objects.create(
                    id=run_id,
                    mode="apply",
                    status="completed",
                    source_path_digest=path_digest,
                    source_snapshot_digest=source_digest,
                    target_snapshot_digest=target_digest,
                    source_counts=counts,
                    target_counts=target_counts,
                    approved_run_id=approved,
                    manifest={**manifest, **audit},
                )
                run.completed_at = timezone.now()
                run.save(update_fields=["completed_at"])
            self.stdout.write(json.dumps({**manifest, **audit, "status": "applied", "targetDigest": source_digest}, ensure_ascii=False, sort_keys=True))
        finally:
            source.rollback()
            source.close()
