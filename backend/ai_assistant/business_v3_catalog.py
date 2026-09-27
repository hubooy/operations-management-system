"""Current owner-bound v3 directory reconstruction with mixed source progress.

This is not a seal. It reconstructs immutable identities while the parent stays
collecting/manual, regardless of which individual source is still in progress.
"""
from __future__ import annotations

import json

from business_analysis import evidence_seal_v3, evidence_v3
from business_analysis.contracts import AnalysisContractError
from . import business_evidence_v3 as plan, models as m
from .policy import AiError, authorize_owner, canonical, digest, identifier

MAX_DATA_BYTES = 64 * 1024 * 1024 - 38_000
MAX_DATA_PAGES = 1_999


def _reject(message="v3来源目录未通过持久身份与进度核验"):
    raise AiError(message, "conflict", 409)


def records(row):
    return list(m.AiBusinessEvidenceSource.objects.filter(run_id=row.id).order_by("ordinal").values(
        "id", "source_key", "ordinal", "domain", "query_json", "query_digest", "version",
        "checkpoint_run_version", "checkpoint_json", "page_count", "stored_bytes", "row_count", "finished")[:49])


def unchanged(row, actor, original, principal):
    if plan._actor(principal) != actor:
        raise AiError("v3目录读取期间账号权限变化", "access_denied", 403)
    live = m.AiBusinessEvidenceRun.objects.filter(pk=row.id).values("version", "status", "collection_status",
        "plan_json", "request_digest", "state_json", "stored_bytes").first()
    if (live != {key: getattr(row, key) for key in ("version", "status", "collection_status",
            "plan_json", "request_digest", "state_json", "stored_bytes")}
            or records(row) != original):
        raise AiError("v3目录读取期间父任务或来源版本变化", "version_conflict", 409)
    if plan._actor(principal) != actor:
        raise AiError("v3目录读取期间账号权限变化", "access_denied", 403)


def load(run_id, principal, *, allow_sealed=False):
    actor = plan._actor(principal)
    row = m.AiBusinessEvidenceRun.objects.filter(pk=identifier(run_id)).first()
    if row is None:
        raise AiError("v3证据任务不存在", "not_found", 404)
    authorize_owner(row, principal)
    sealed = allow_sealed and row.status == "sealed"
    if (row.status != ("sealed" if sealed else "collecting") or row.collection_status != "manual"
            or row.scope_json != "null" or row.version < 1 or row.stored_bytes > MAX_DATA_BYTES):
        _reject("v3父任务不是允许读取的手工来源目录")
    if sealed:
        try:
            state = json.loads(row.state_json)
            base = {key: value for key, value in state.items() if key != "sealedDigest"}
            if (type(state) is not dict or state.get("schemaVersion") != evidence_seal_v3.SCHEMA
                    or row.state_json != canonical(state) or state.get("runId") != row.id
                    or state.get("evidenceVersion") != row.version
                    or state.get("sealedDigest") != digest(base)):
                _reject("v3封存状态摘要或父身份无效")
        except (ValueError, TypeError, AttributeError) as error:
            raise AiError("v3封存状态不可解码", "conflict", 409) from error
    elif row.state_json != "{}":
        _reject("v3未封存父任务含封存状态")
    current = records(row)
    try:
        header = json.loads(row.plan_json)
        if type(header) is not dict or header.get("schemaVersion") != evidence_v3.HEADER_SCHEMA:
            raise AnalysisContractError("父任务不是v3协议")
        sources = [{"key": item["source_key"], "domain": item["domain"],
                    "query": json.loads(item["query_json"])} for item in current]
        built = evidence_v3.build_catalog(sources, analysis_request=header["analysisRequest"])
        evidence_v3.validate_header(header, sources, analysis_request=header["analysisRequest"])
        if (len(current) != len(built["entries"]) or row.plan_json != canonical(built["header"])
                or row.request_digest != plan._identity(built)):
            raise AnalysisContractError("v3父计划或目录摘要不匹配")
        for actual, expected in zip(current, built["entries"]):
            if (actual["source_key"], actual["ordinal"], actual["domain"], actual["query_json"], actual["query_digest"]) != (
                    expected["key"], expected["ordinal"], expected["domain"], canonical(expected["query"]),
                    expected["queryDigest"]):
                raise AnalysisContractError("v3来源精确身份、查询或顺序变化")
            if (actual["version"] != actual["page_count"] + 1
                    or actual["checkpoint_run_version"] > row.version
                    or (actual["page_count"] == 0 and (actual["checkpoint_json"] != "{}"
                        or actual["stored_bytes"] != 0 or actual["row_count"] != 0 or actual["finished"]))):
                raise AnalysisContractError("v3来源检查点计数或父版本变化")
        if (sum(item["page_count"] for item in current) > MAX_DATA_PAGES
                or sum(item["stored_bytes"] for item in current) != row.stored_bytes):
            raise AnalysisContractError("v3来源事实页与父字节不一致")
        # The physical table has no portable length transform in the ORM; one
        # bounded SQL aggregation checks all chunks without loading payloads.
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("""SELECT source_key,count(*),coalesce(sum(octet_length(payload_json)),0),
                min(sequence),max(sequence) FROM ai_business_evidence_chunks WHERE run_id=%s GROUP BY source_key""",
                [row.id])
            chunks = {key: (count, size, first, last) for key, count, size, first, last in cursor.fetchall()}
        if set(chunks) - {item["source_key"] for item in current}:
            raise AnalysisContractError("v3出现目录外事实块")
        for item in current:
            actual = chunks.get(item["source_key"], (0, 0, None, None))
            if (actual[0] != item["page_count"] or actual[1] != item["stored_bytes"]
                    or actual[0] and (actual[2], actual[3]) != (1, actual[0])):
                raise AnalysisContractError("v3来源块序列或字节数与检查点不一致")
    except (AnalysisContractError, ValueError, TypeError, KeyError, RecursionError) as error:
        raise AiError("v3实际目录或块计数未通过可信重建", "conflict", 409) from error
    unchanged(row, actor, current, principal)
    return row, built, current, actor
