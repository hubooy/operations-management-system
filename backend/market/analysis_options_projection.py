"""Owning historical source index. Explicit rebuild; no request-time batch scan."""
from dataclasses import dataclass
import hashlib
import json
import uuid

from django.db import connection, transaction
from django.db.models import Count, Func, Sum, TextField
from django.db.models.functions import Cast
from access_control.models import AppUser
from . import analysis_options_contract as contract
from .errors import MarketApiError
from .models import MarketAnalysisOption, MarketAnalysisOptionsState, MarketDataRevision, MarketImportBatch
from .revisions import assert_write_authority, canonical_json, revision_value

MAX_REBUILD_BATCHES = 10_000
MAX_REBUILD_BYTES = 64 * 1024 * 1024
MAX_SCOPE_BYTES = 16 * 1024 * 1024
_TOKEN = object()
FIELDS = ("category", "scope", "ranking_dimension", "price_band_filter")


def current_principal(principal):
    if principal.role != "admin" or principal.scope is not None:
        raise MarketApiError("来源选项仅向当前无范围限制管理员开放", code="access_denied", status=403)
    user = AppUser.objects.filter(email=principal.email.lower()).values("email", "role", "status", "scope", "version").first()
    if not user or user["status"] != "active" or user["role"] != "admin" or user["scope"] is not None:
        raise MarketApiError("账号或数据权限已变化", code="access_denied", status=403)
    return {"email": user["email"], "role": user["role"], "scope": None, "version": user["version"]}


def _hash(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def search_value(identity):
    # Query text forbids control characters, so a newline separator cannot create
    # an accidental match across two original fields. Unicode casefold is fixed
    # here; PostgreSQL locale-dependent upper()/icontains is not used.
    return "\n".join(identity[key].casefold() for key in contract.IDENTITY_FIELDS)


def _error(error):
    return MarketApiError("来源目录元数据无效或超出容量，须受控重建", code="options_not_ready", status=503)


def row_entry(row):
    # Bounded native columns, NOT a potentially corrupt arbitrarily nested JSON.
    try:
        item = contract._entry({"identity": {"platform": "京东", "category": row["category"], "scope": row["scope"],
            "rankingDimension": row["ranking_dimension"], "priceBandFilter": row["price_band_filter"]},
            "firstDate": row["first_date"], "lastDate": row["last_date"]})
        raw = canonical_json(item)
        if row["entry_json"] != raw or row["entry_digest"] != _hash(raw) or row["search_casefold"] != search_value(item["identity"]):
            raise contract.OptionsContractError("目录行摘要或投影不一致")
        return item
    except (contract.OptionsContractError, KeyError, TypeError) as error:
        raise _error(error) from error


def state_snapshot():
    value = MarketAnalysisOptionsState.objects.filter(id=1).values("status", "generation", "directory_digest",
        "identity_count", "stored_bytes", "source_revision", "reason").first()
    if not value or value["status"] != "ready":
        raise MarketApiError("市场来源目录尚未完整初始化或已失效", code="options_not_ready", status=503)
    try:
        contract._hex(value["directory_digest"])
        if (type(value["generation"]) is not str or len(value["generation"]) != 32
                or any(c not in "0123456789abcdef" for c in value["generation"])
                or type(value["identity_count"]) is not int or not 0 <= value["identity_count"] <= contract.MAX_IDENTITIES
                or type(value["stored_bytes"]) is not int or not 2 <= value["stored_bytes"] <= contract.MAX_DIRECTORY_BYTES
                or not value["source_revision"] or value["reason"]):
            raise contract.OptionsContractError("目录状态无效")
    except contract.OptionsContractError as error:
        raise _error(error) from error
    return value


@dataclass(frozen=True)
class PreparedProjection:
    _token: object
    _raw: str
    _digest: str


def prepare_rebuild(principal):
    """Read all bounded completed batch metadata outside any SQL write lock."""
    if connection.in_atomic_block:
        raise MarketApiError("目录准备须在事务外完成", code="invalid_request", status=409)
    actor, before = current_principal(principal), revision_value()
    entries, last, count, total = [], None, 0, 0
    batch_chain = _hash("[]")
    # Fetch one scope only after its DB-computed byte count passes the bound.
    while True:
        query = MarketImportBatch.objects.filter(status="completed")
        if last is not None:
            query = query.filter(id__gt=last)
        row = query.order_by("id").annotate(scope_bytes=Func(Cast("scope_json", TextField()),
            function="octet_length" if connection.vendor == "postgresql" else "length")).values(
                "id", "scope_bytes", "source_type", "row_count", "completed_at").first()
        if row is None:
            break
        count += 1
        if count > MAX_REBUILD_BATCHES or type(row["scope_bytes"]) is not int or not 1 <= row["scope_bytes"] <= MAX_SCOPE_BYTES:
            raise MarketApiError("历史范围数量或字节超过初始化容量", code="options_not_ready", status=503)
        total += row["scope_bytes"]
        if total > MAX_REBUILD_BYTES:
            raise MarketApiError("历史范围总字节超过初始化容量", code="options_not_ready", status=503)
        raw = MarketImportBatch.objects.filter(id=row["id"], status="completed").values_list("scope_json", flat=True).first()
        try:
            if (type(raw) is not dict or raw.get("sourceType") != row["source_type"]
                    or type(row["row_count"]) is not int or not 1 <= row["row_count"] <= 5000 or row["completed_at"] is None):
                raise contract.OptionsContractError("历史成功批次元数据不完整")
            result = contract.normalize_completed_scope(raw, status="completed")
            if result["rangeCount"] > row["row_count"]:
                raise contract.OptionsContractError("范围数大于成功行数")
            entries = contract.merge_entries(entries, result["entries"])
        except contract.OptionsContractError as error:
            raise _error(error) from error
        batch_chain = _hash(batch_chain + ":" + canonical_json({"id": row["id"], "scope": raw, "rowCount": row["row_count"]}))
        last = row["id"]
    if before != revision_value() or actor != current_principal(principal):
        raise MarketApiError("初始化期间来源或权限发生变化", code="options_revision_changed", status=409)
    raw = canonical_json({"actor": actor, "revision": before, "entries": entries, "batchCount": count,
                          "scopeBytes": total, "batchDigest": batch_chain})
    return PreparedProjection(_TOKEN, raw, _hash(raw))


def _save_entries(entries, state, revision):
    # Metadata-only, at most 10000 rows/16MiB. Entire replacement and state commit
    # share the caller's existing revision lock and transaction.
    digest = contract.directory_digest(entries)
    size = len(canonical_json(entries).encode("utf-8"))
    rows = []
    for item in entries:
        identity = item["identity"]
        raw = canonical_json(item)
        rows.append(MarketAnalysisOption(category=identity["category"], scope=identity["scope"],
            ranking_dimension=identity["rankingDimension"], price_band_filter=identity["priceBandFilter"],
            first_date=item["firstDate"], last_date=item["lastDate"], entry_json=raw, entry_digest=_hash(raw),
            search_casefold=search_value(identity)))
    MarketAnalysisOption.objects.all().delete()
    MarketAnalysisOption.objects.bulk_create(rows, batch_size=100)
    state.status, state.reason = "ready", ""
    state.generation, state.directory_digest = uuid.uuid4().hex, digest
    state.identity_count, state.stored_bytes, state.source_revision = len(entries), size, revision
    state.save()


def publish_rebuild(prepared, principal):
    if type(prepared) is not PreparedProjection or prepared._token is not _TOKEN or _hash(prepared._raw) != prepared._digest:
        raise MarketApiError("不能从外部JSON恢复目录准备结果", code="invalid_request", status=409)
    if len(prepared._raw.encode("utf-8")) > contract.MAX_DIRECTORY_BYTES + 8192:
        raise MarketApiError("目录准备载荷超限", code="options_not_ready", status=503)
    value = json.loads(prepared._raw)
    entries = contract._entries(value["entries"])
    with transaction.atomic():
        assert_write_authority()
        MarketDataRevision.objects.select_for_update().get(domain="market")
        if current_principal(principal) != value["actor"] or revision_value() != value["revision"]:
            raise MarketApiError("来源或权限已变化，初始化未发布", code="options_revision_changed", status=409)
        state = MarketAnalysisOptionsState.objects.select_for_update().get(id=1)
        _save_entries(entries, state, value["revision"])
        if current_principal(principal) != value["actor"]:
            raise MarketApiError("初始化期间权限已变化", code="access_denied", status=403)
    return {"generation": state.generation, "directoryDigest": state.directory_digest,
            "identityCount": len(entries), "batchCount": value["batchCount"], "coverageVerified": False}


def synchronize_import(scope, revision):
    """Called ONLY inside successful import transaction, after bump_revision.

    Existing legitimate import semantics remain: bad/new oversized metadata blocks
    the directory atomically, never silently leaves it ready or discards a source.
    """
    if not connection.in_atomic_block:
        raise MarketApiError("目录同步必须属于实际导入事务", code="invalid_request", status=409)
    state = MarketAnalysisOptionsState.objects.select_for_update().get(id=1)
    if state.status != "ready":
        return  # A new import cannot certify all historical batches were scanned.
    try:
        snapshot = state_snapshot()
        stats = MarketAnalysisOption.objects.aggregate(count=Count("id"), bytes=Sum(Func("entry_json",
            function="octet_length" if connection.vendor == "postgresql" else "length")))
        if stats["count"] > contract.MAX_IDENTITIES or (stats["bytes"] or 0) + max(0, stats["count"] - 1) + 2 > contract.MAX_DIRECTORY_BYTES:
            raise contract.OptionsContractError("原目录超出容量")
        values = list(MarketAnalysisOption.objects.order_by(*FIELDS).values()[:contract.MAX_IDENTITIES + 1])
        entries = [row_entry(row) for row in values]
        # Python C/BINARY sort is explicit; default database collation may differ.
        entries.sort(key=lambda item: contract._key(item["identity"]))
        if (len(entries) != snapshot["identity_count"] or contract.directory_digest(entries) != snapshot["directory_digest"]
                or len(canonical_json(entries).encode("utf-8")) != snapshot["stored_bytes"]):
            raise contract.OptionsContractError("原目录不完整")
        added = contract.normalize_completed_scope(scope, status="completed")["entries"]
        result = contract.merge_entries(entries, added)
    except (contract.OptionsContractError, MarketApiError):
        state.status, state.reason, state.generation = "blocked", "invalid_or_over_capacity", uuid.uuid4().hex
        state.save()
        return
    _save_entries(result, state, revision)
