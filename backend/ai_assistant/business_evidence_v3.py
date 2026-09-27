"""Internal owner-bound v3 finance plan directory; collection remains disabled.

No public route imports this module. The 0028 PostgreSQL gate permits only the
initial zero-fact directory, and this adapter never advances it or seals it.
"""
from __future__ import annotations

import json

from access_control.models import AppUser
from business_analysis import evidence_v3
from business_analysis.contracts import AnalysisContractError

from . import business_evidence_store as store, models as m
from .policy import AiError, authorize_owner, canonical, current_principal, digest, fields, identifier, integer, mutation, uid

MAX_BYTES = 64 * 1024 * 1024


def _actor(principal):
    current_principal(principal, admin=True)
    if principal.role != "admin" or principal.scope is not None:
        raise AiError("v3来源计划仅允许当前无范围管理员", "access_denied", 403)
    actor = AppUser.objects.filter(email=principal.email.lower()).values(
        "email", "role", "status", "scope", "version").first()
    if (not actor or actor["role"] != "admin" or actor["status"] != "active"
            or actor["scope"] is not None or type(actor["version"]) is not int
            or actor["version"] < 1):
        raise AiError("实际账号或数据权限已变化", "access_denied", 403)
    return actor


def _identity(built):
    return digest({"schemaVersion": "business-evidence-create-v3",
                   "header": built["header"], "collectionMode": "manual"})


def _source_records(row):
    records = list(m.AiBusinessEvidenceSource.objects.filter(run_id=row.id).order_by("ordinal")
                   .values("source_key", "ordinal", "domain", "query_json", "query_digest",
                           "version", "checkpoint_run_version", "checkpoint_json", "page_count",
                           "stored_bytes", "row_count", "finished")[:49])
    if (not 2 <= len(records) <= evidence_v3.MAX_SOURCES
            or any(record["version"] != 1 or record["checkpoint_run_version"] != 1
                   or record["checkpoint_json"] != "{}" or record["page_count"] != 0
                   or record["stored_bytes"] != 0 or record["row_count"] != 0
                   or record["finished"] for record in records)):
        raise AiError("v3来源目录不处于未采集的初始状态", "conflict", 409)
    return records


def _loaded(run_id, principal):
    actor = _actor(principal)
    row = m.AiBusinessEvidenceRun.objects.filter(pk=identifier(run_id)).first()
    if row is None:
        raise AiError("v3来源任务不存在", "not_found", 404)
    authorize_owner(row, principal)
    if (row.status != "collecting" or row.version != 1 or row.collection_status != "manual"
            or row.scope_json != "null" or row.state_json != "{}" or row.stored_bytes != 0
            or m.AiBusinessEvidenceChunk.objects.filter(run_id=row.id).exists()):
        raise AiError("v3来源任务不处于未采集的初始状态", "conflict", 409)
    records = _source_records(row)
    try:
        header = json.loads(row.plan_json)
        if type(header) is not dict or header.get("schemaVersion") != evidence_v3.HEADER_SCHEMA:
            raise AnalysisContractError("v3计划版本不符")
        sources = [{"key": record["source_key"], "domain": record["domain"],
                    "query": json.loads(record["query_json"])} for record in records]
        built = evidence_v3.build_catalog(sources, analysis_request=header["analysisRequest"])
        evidence_v3.validate_header(header, sources, analysis_request=header["analysisRequest"])
        if (row.plan_json != canonical(built["header"]) or row.request_digest != _identity(built)
                or [(record["source_key"], record["ordinal"], record["domain"],
                     record["query_json"], record["query_digest"]) for record in records]
                   != [(entry["key"], entry["ordinal"], entry["domain"],
                        canonical(entry["query"]), entry["queryDigest"]) for entry in built["entries"]]):
            raise AnalysisContractError("v3持久目录、顺序或摘要不一致")
    except (AnalysisContractError, ValueError, TypeError, KeyError, RecursionError) as error:
        raise AiError("v3持久来源目录未通过可信重建", "conflict", 409) from error
    _unchanged(row, actor, records, principal)
    return row, built, sources, actor, records


def _unchanged(row, actor, records, principal):
    if _actor(principal) != actor:
        raise AiError("v3目录读取期间账号权限变化", "access_denied", 403)
    current = m.AiBusinessEvidenceRun.objects.filter(pk=row.id).values(
        "version", "status", "collection_status", "plan_json", "request_digest",
        "state_json", "stored_bytes").first()
    expected = {key: getattr(row, key) for key in (
        "version", "status", "collection_status", "plan_json", "request_digest",
        "state_json", "stored_bytes")}
    if current != expected or _source_records(row) != records:
        raise AiError("v3目录读取期间持久身份或版本变化", "version_conflict", 409)
    if _actor(principal) != actor:
        raise AiError("v3目录读取期间账号权限变化", "access_denied", 403)


def _mapping(row, built):
    return {"id": row.id, "status": row.status, "version": row.version,
            "collectionStatus": row.collection_status, "storedBytes": row.stored_bytes,
            "sourceCount": len(built["entries"]), "catalogDigest": built["header"]["catalogDigest"],
            "planDigest": built["planDigest"], "createdAt": row.created_at.isoformat(),
            "reportGenerationSupported": False, "sourceAuthorityVerified": False,
            "persistentEvidenceVerified": False, "modelAnalysisCompleted": False}


def create(body, principal):
    """Atomically stage one exact zero-fact plan; no scheduled collection."""
    _actor(principal)
    fields(body, {"schemaVersion", "clientRequestId", "sources", "analysisRequest", "expectedPrincipalKey"},
           {"schemaVersion", "clientRequestId", "sources", "analysisRequest"})
    if body["schemaVersion"] != evidence_v3.HEADER_SCHEMA:
        raise AiError("v3来源计划版本无效")
    from .business_evidence import principal_key
    if "expectedPrincipalKey" in body and body["expectedPrincipalKey"] != principal_key(principal):
        raise AiError("当前账号已变化，请重新确认分析范围", "access_denied", 403)
    client = identifier(body["clientRequestId"])
    try:
        built = evidence_v3.build_catalog(body["sources"], analysis_request=body["analysisRequest"])
    except (AnalysisContractError, ValueError, TypeError, KeyError) as error:
        raise AiError("v3来源计划未通过完整范围与容量校验", "invalid_request", 400) from error
    from market.analysis import validate as validate_market
    from market.errors import MarketApiError
    from netshop.analysis import SOURCES
    for entry in built["entries"]:
        query = entry["query"]
        if entry["domain"] == "netshop" and query["platform"] not in SOURCES.get(query["dataset"], {}):
            raise AiError("网店来源组合无效")
        if entry["domain"] == "market":
            try:
                validate_market({"operation": "analysis_records", **query})
            except MarketApiError as error:
                raise AiError("市场来源组合无效") from error
    identity = _identity(built)
    with mutation(principal):
        _actor(principal)
        old = m.AiBusinessEvidenceRun.objects.filter(owner_email=principal.email.lower(), client_request_id=client).first()
        if old is not None:
            authorize_owner(old, principal)
            if old.request_digest != identity:
                raise AiError("请求标识对应的来源范围已变化", "conflict", 409)
            actual, verified, _, _, _ = _loaded(old.id, principal)
            return {"item": _mapping(actual, verified), "replayed": True}
        if m.AiBusinessEvidenceRun.objects.filter(owner_email=principal.email.lower(), status="collecting").count() >= 4:
            raise AiError("未完成证据任务已达到上限", "rate_limited", 429)
        if m.AiBusinessEvidenceRun.objects.count() >= 10000:
            raise AiError("证据任务存储容量已满", "rate_limited", 429)
        new_bytes = len(canonical(built["header"]).encode("utf-8")) + 2 + sum(
            len(canonical(entry["query"]).encode("utf-8")) + 2 for entry in built["entries"])
        store.check_quota(principal, new_bytes, MAX_BYTES)
        row = m.AiBusinessEvidenceRun.objects.create(id=uid("evidence"), owner_email=principal.email.lower(),
            client_request_id=client, request_digest=identity, plan_json=canonical(built["header"]),
            collection_status="manual")
        for entry in built["entries"]:
            m.AiBusinessEvidenceSource.objects.create(id=uid("source"), run=row,
                source_key=entry["key"], ordinal=entry["ordinal"], domain=entry["domain"],
                query_json=canonical(entry["query"]), query_digest=entry["queryDigest"])
        _actor(principal)
        return {"item": _mapping(row, built), "replayed": False}


def detail(run_id, principal):
    row, built, _, actor, records = _loaded(run_id, principal)
    result = {"item": _mapping(row, built), "plan": built["header"]}
    _unchanged(row, actor, records, principal)
    return result


def directory(run_id, *, offset=0, limit=10, principal):
    integer(offset, "offset", lo=0, hi=47)
    integer(limit, "limit", lo=1, hi=20)
    row, built, sources, actor, records = _loaded(run_id, principal)
    try:
        page = evidence_v3.directory_page(sources, run_id=row.id, evidence_version=row.version,
            offset=offset, limit=limit, analysis_request=built["header"]["analysisRequest"])
    except AnalysisContractError as error:
        raise AiError("v3来源目录页超出可信范围", "conflict", 409) from error
    _unchanged(row, actor, records, principal)
    return page
