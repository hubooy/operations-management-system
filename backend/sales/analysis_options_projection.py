"""Explicit ERP identity-index rebuild; current facts are never queried by GET."""
from dataclasses import dataclass
from datetime import date
import hashlib
import json
import uuid
from django.conf import settings
from django.db import connection, transaction
from django.db.models import Count, F, Max, Min
from django.db.models.functions import Collate, Length
from access_control.models import AppUser
from . import analysis_options_contract as contract
from .models import SalesAnalysisOption, SalesAnalysisOptionsState, SalesDataRevision, SalesOrderLine, SalesWriteAuthority
from .authority_lock import acquire_sales_write_authority_shared_lock
from .cutover_attestation import require_valid_cutover_attestation, SalesCutoverAttestationError
from .runtime_guard import validate_writer_runtime_state, WriterRuntimeGuardError

MAX_IDENTITIES = 10_000
MAX_DIRECTORY_BYTES = 16 * 1024 * 1024
_TOKEN = object()
FIELDS = ("platform", "shop", "channel")


class OptionsError(ValueError):
    def __init__(self, message, *, code="invalid_request", status=400):
        super().__init__(message)
        self.code, self.status = code, status


def current_principal(principal):
    if principal.role != "admin" or principal.scope is not None:
        raise OptionsError("仅当前无范围管理员可以读取ERP来源", code="access_denied", status=403)
    value = AppUser.objects.filter(email=principal.email.lower()).values("email", "role", "scope", "status", "version").first()
    if not value or value["role"] != "admin" or value["scope"] is not None or value["status"] != "active":
        raise OptionsError("实际账号或权限已变化", code="access_denied", status=403)
    return value


def revision_snapshot():
    rows = dict(SalesDataRevision.objects.filter(domain__in=("sales", "erp")).values_list("domain", "revision"))
    if set(rows) != {"sales", "erp"} or any(type(v) is not int or not 0 <= v <= contract.MAX_SAFE_INTEGER for v in rows.values()):
        raise OptionsError("ERP来源修订未就绪", code="options_not_ready", status=503)
    return f'{rows["sales"]}:{rows["erp"]}'


def _authority():
    value = SalesWriteAuthority.objects.filter(id=1).values("status", "authority_epoch", "cutover_id").first()
    if not value or value["status"] != "active" or not settings.SALES_WRITE_AUTHORITY_EPOCH or not settings.SALES_WRITE_CUTOVER_ID or str(value["authority_epoch"]) != settings.SALES_WRITE_AUTHORITY_EPOCH or value["cutover_id"] != settings.SALES_WRITE_CUTOVER_ID:
        raise OptionsError("销售写入权威未就绪或已变化", code="options_not_ready", status=503)
    try:
        receipt = require_valid_cutover_attestation(cutover_id=value["cutover_id"])
    except SalesCutoverAttestationError as error:
        raise OptionsError("销售切换回执无效", code="options_not_ready", status=503) from error
    return {"epoch": str(value["authority_epoch"]), "cutoverId": value["cutover_id"], "attestationDigest": contract.digest(receipt)}


def _invalid(error=None):
    return OptionsError("ERP来源目录无效或超出容量，须完整重建", code="options_not_ready", status=503)


def _entries(values):
    if type(values) is not list or len(values) > MAX_IDENTITIES:
        raise _invalid()
    result, previous, size = [], None, 2
    try:
        for raw in values:
            item = contract.normalize_group(raw)
            key = tuple(item["identity"][field] for field in FIELDS)
            if previous is not None and key <= previous:
                raise _invalid()
            # Retain exact raw+key group only after fixed-shape scalar validation.
            row = {field: raw[field] for field in sorted(contract.GROUP_FIELDS)}
            size += len(contract.canonical(row).encode()) + bool(result)
            if size > MAX_DIRECTORY_BYTES:
                raise _invalid()
            result.append(row); previous = key
    except contract.OptionsContractError as error:
        raise _invalid(error) from error
    return result


def row_entry(row):
    try:
        raw = {"platform": row["platform"], "shop_name": row["shop"], "channel": row["channel"],
            "platform_key": row["platform"], "shop_key": row["shop"], "channel_key": row["channel"],
            "firstDate": row["first_date"], "lastDate": row["last_date"], "rowCount": row["row_count"]}
        contract.normalize_group(raw)
        if contract.canonical(raw) != row["entry_json"] or contract.digest(raw) != row["entry_digest"]:
            raise _invalid()
        return raw
    except (KeyError, TypeError, contract.OptionsContractError) as error:
        raise _invalid(error) from error


def state_snapshot(revision):
    value = SalesAnalysisOptionsState.objects.filter(id=1).values("status", "generation", "directory_digest", "identity_count", "stored_bytes", "source_sales_revision", "reason").first()
    if not value or value["status"] != "ready" or value["source_sales_revision"] != int(revision.split(":")[0]):
        raise OptionsError("ERP来源目录未初始化或销售数据已变化，请受控重建", code="options_not_ready", status=503)
    if (len(value["generation"]) != 32 or any(c not in "0123456789abcdef" for c in value["generation"])
            or len(value["directory_digest"]) != 64 or any(c not in "0123456789abcdef" for c in value["directory_digest"])
            or not 0 <= value["identity_count"] <= MAX_IDENTITIES or not 2 <= value["stored_bytes"] <= MAX_DIRECTORY_BYTES or value["reason"]):
        raise _invalid()
    return value


@dataclass(frozen=True)
class PreparedProjection:
    _token: object
    _raw: str
    _digest: str


def prepare_rebuild(principal):
    if connection.in_atomic_block:
        raise OptionsError("ERP目录准备必须在最外层事务之外", code="conflict", status=409)
    actor, revision, authority = current_principal(principal), revision_snapshot(), _authority()
    try:
        validate_writer_runtime_state(cutover_id=authority["cutoverId"])
    except WriterRuntimeGuardError as error:
        raise OptionsError("销售运行时来源未就绪", code="options_not_ready", status=503) from error
    collation = "C" if connection.vendor == "postgresql" else "BINARY"
    query = SalesOrderLine.objects.filter(is_business_row=True).annotate(
        raw_platform=Collate("platform", collation), raw_shop=Collate("shop_name", collation), raw_channel=Collate("channel", collation),
        n_platform=Length("platform"), n_shop=Length("shop_name"), n_channel=Length("channel"))
    query = query.filter(raw_platform=F("platform_key"), raw_shop=F("shop_key"), raw_channel=F("channel_key"),
        n_platform__range=(1,200), n_shop__range=(1,200), n_channel__range=(1,200))
    query = query.values("raw_platform", "raw_shop", "raw_channel").annotate(firstDate=Min("business_date"), lastDate=Max("business_date"), rowCount=Count("id")).order_by("raw_platform", "raw_shop", "raw_channel")
    groups, size = [], 2
    for value in query[:MAX_IDENTITIES+1].iterator(chunk_size=100):
        if len(groups) >= MAX_IDENTITIES:
            raise _invalid()
        if type(value["firstDate"]) is not date or type(value["lastDate"]) is not date:
            raise _invalid()
        row = {"platform": value["raw_platform"], "shop_name": value["raw_shop"], "channel": value["raw_channel"],
            "platform_key": value["raw_platform"], "shop_key": value["raw_shop"], "channel_key": value["raw_channel"],
            "firstDate": value["firstDate"].isoformat(), "lastDate": value["lastDate"].isoformat(), "rowCount": value["rowCount"]}
        try:
            contract.normalize_group(row)
        except contract.OptionsContractError as error:
            raise _invalid(error) from error
        size += len(contract.canonical(row).encode()) + bool(groups)
        if size > MAX_DIRECTORY_BYTES: raise _invalid()
        groups.append(row)
    if actor != current_principal(principal) or revision != revision_snapshot() or authority != _authority():
        raise OptionsError("ERP目录准备期间来源或权限变化", code="options_revision_changed", status=409)
    raw = contract.canonical({"actor": actor, "revision": revision, "authority": authority, "entries": groups})
    return PreparedProjection(_TOKEN, raw, hashlib.sha256(raw.encode()).hexdigest())


def publish_rebuild(prepared, principal):
    if type(prepared) is not PreparedProjection or prepared._token is not _TOKEN or type(prepared._raw) is not str or len(prepared._raw) > MAX_DIRECTORY_BYTES+8192:
        raise OptionsError("不能从JSON恢复ERP目录准备", code="conflict", status=409)
    encoded = prepared._raw.encode()
    if len(encoded) > MAX_DIRECTORY_BYTES+8192 or hashlib.sha256(encoded).hexdigest() != prepared._digest:
        raise _invalid()
    value = json.loads(prepared._raw)
    entries = _entries(value["entries"])
    with transaction.atomic():
        acquire_sales_write_authority_shared_lock()
        if value["authority"] != _authority():
            raise OptionsError("销售权威已变化", code="conflict", status=409)
        SalesDataRevision.objects.select_for_update().get(domain="sales")
        if value["revision"] != revision_snapshot() or value["actor"] != current_principal(principal):
            raise OptionsError("来源或权限已变化，目录未发布", code="options_revision_changed", status=409)
        state = SalesAnalysisOptionsState.objects.select_for_update().get(id=1)
        rows = [SalesAnalysisOption(platform=g["platform"], shop=g["shop_name"], channel=g["channel"], first_date=g["firstDate"], last_date=g["lastDate"], row_count=g["rowCount"], entry_json=contract.canonical(g), entry_digest=contract.digest(g)) for g in entries]
        SalesAnalysisOption.objects.all().delete()
        SalesAnalysisOption.objects.bulk_create(rows, batch_size=100)
        state.status, state.reason = "ready", ""
        state.generation, state.directory_digest = uuid.uuid4().hex, contract.digest(entries)
        state.identity_count, state.stored_bytes = len(rows), len(contract.canonical(entries).encode())
        state.source_sales_revision = int(value["revision"].split(":")[0])
        state.save()
        if value["actor"] != current_principal(principal):
            raise OptionsError("发布期间账号已变化", code="access_denied", status=403)
    return {"generation": state.generation, "directoryDigest": state.directory_digest, "identityCount": len(rows), "coverageVerified": False}
