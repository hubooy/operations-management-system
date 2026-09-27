"""Default-closed owning read of one v4 followed-SKU relation window.

Three distinct plan sources are bound, but only the explicitly selected
window is replayed and grouped. This grants no seal, Agent, or report right.
"""
from contextlib import contextmanager
import json
import re
import time

from business_analysis import promotion_attributed_sku_relation_v4 as relation
from business_analysis.contracts import AnalysisContractError

from . import business_v4_promotion_capacity_owning as capacity_owner
from . import business_v4_promotion_replay as replay
from .policy import AiError, canonical, digest, fields, identifier, integer


SCHEMA = "business-v4-attributed-sku-owning-candidate-v1"
BINDING_SCHEMA = "business-v4-attributed-sku-three-window-binding-v1"
WINDOWS = ("current", "previous", "yearAgo")
MAX_RESPONSE_BYTES = 38_000
MAX_WINDOW_SECONDS = 1_500
MAX_ROWS = relation.MAX_RESULT_GROUPS


def _need(value, message="v4跟单SKU拥有方绑定不一致"):
    if not value:
        raise AiError(message, "conflict", 409)


def _sources(run_id, source_keys, principal):
    _need(type(source_keys) is dict and set(source_keys) == set(WINDOWS)
        and all(type(source_keys[name]) is str for name in WINDOWS),
        "v4跟单SKU须显式选择三种窗口来源")
    keys = {name: identifier(source_keys[name]) for name in WINDOWS}
    _need(len(set(keys.values())) == 3, "v4跟单SKU三窗口不得复用同一来源")
    loaded = {name: replay._loaded(identifier(run_id), keys[name], principal)
        for name in WINDOWS}
    actor, parent, _, current_query, _, fixed_parent, _ = loaded["current"]
    identities = []
    for name in WINDOWS:
        item = loaded[name]
        member_actor, member_parent, source, query, _, member_fixed, _ = item
        _need(member_actor == actor and member_parent.id == parent.id
            and member_fixed == fixed_parent and query["window"] == name
            and {key:value for key,value in query.items() if key != "window"} ==
                {key:value for key,value in current_query.items()
                    if key != "window"},
            "v4跟单SKU三窗口不属于同一固定店铺与原始日期计划")
        identities.append((source.id, source.source_ref,
            source.source_identity_digest))
    _need(len(set(identities)) == 3
        and len({item[0] for item in identities}) == 3
        and len({item[1] for item in identities}) == 3,
        "v4跟单SKU三窗口来源身份或事实引用重复")
    return loaded


def _final_fence(loaded, principal):
    first = loaded["current"]
    actor, parent = first[:2]
    for name in WINDOWS:
        _, _, source, _, _, fixed_parent, fixed_source = loaded[name]
        replay._final_fence(actor, parent, source, fixed_parent, fixed_source,
            principal)
        latest = replay._loaded(parent.id, source.source_key, principal)
        _need(latest[0] == actor and latest[5] == fixed_parent
            and latest[6] == fixed_source,
            "v4跟单SKU读取后计划目录或来源修订变化")


def _pages(parent, source, query, actor, proof, checkpoint, deadline):
    for packed in capacity_owner._receipt_pages(parent, source, query,
            actor, proof, checkpoint, deadline):
        raw = packed["rawPage"]
        try:
            _need(type(raw) is bytes and len(raw) <= 131_072)
            page = json.loads(raw)
            _need(canonical(page).encode("utf-8") == raw,
                "v4跟单SKU持久页并非规范原文字节")
        except (ValueError, TypeError, UnicodeError, RecursionError) as error:
            raise AiError("v4跟单SKU持久页不能规范解析", "conflict", 409) from error
        yield page


@contextmanager
def table(run_id, source_keys, window, view, principal, *, enabled=False,
          checkpoint=None):
    _need(enabled is True, "v4跟单SKU拥有方读取默认关闭")
    _need(type(window) is str and window in WINDOWS
        and type(view) is str and view in relation.VIEWS,
        "v4跟单SKU窗口或视图无效")
    started = time.monotonic()
    loaded = _sources(run_id, source_keys, principal)
    actor, parent, source, query, _, _, _ = loaded[window]
    proof = replay.inspect(parent.id, source.source_key, principal,
        checkpoint=checkpoint)
    _need(time.monotonic()-started < MAX_WINDOW_SECONDS
        and proof["runId"] == parent.id
        and proof["runVersion"] == parent.version
        and proof["sourceId"] == source.id
        and proof["sourceKey"] == source.source_key
        and proof["sourceVersion"] == source.version
        and proof["sourceRef"] == source.source_ref
        and proof["sourceRevision"] == source.source_revision
        and proof["queryDigest"] == source.query_digest
        and proof["internalToolAuditBound"] is True
        and proof["requestCursorAuditVerified"] is True
        and proof["payloadCursorChainVerified"] is True
        and proof["upstreamSignatureVerified"] is False
        and proof["sealed"] is False
        and proof["proofDigest"] == digest({key:value for key,value in
            proof.items() if key != "proofDigest"}),
        "v4跟单SKU完整重放证明未绑定所选持久来源")
    selected = {"key":source.source_key, "domain":source.domain,
        "query":query}
    try:
        with relation.table(selected, _pages(parent, source, query, actor,
                proof, checkpoint, started + MAX_WINDOW_SECONDS), proof,
                view=view, checkpoint=checkpoint) as opened:
            _need(time.monotonic()-started < MAX_WINDOW_SECONDS,
                "v4跟单SKU双遍读取超过时间边界")
            _final_fence(loaded, principal)
            triple = {name:{"sourceKey":loaded[name][2].source_key,
                "sourceId":loaded[name][2].id,
                "sourceVersion":loaded[name][2].version,
                "sourceRef":loaded[name][2].source_ref,
                "sourceRevision":loaded[name][2].source_revision,
                "queryDigest":loaded[name][2].query_digest,
                "sourceIdentityDigest":loaded[name][2].source_identity_digest}
                for name in WINDOWS}
            binding = {"schemaVersion": BINDING_SCHEMA,
                "runId":parent.id, "runVersion":parent.version,
                "planDigest":parent.plan_digest, "shop":query["shop"],
                "startDate":query["startDate"], "endDate":query["endDate"],
                "sourceKeys":{name:triple[name]["sourceKey"] for name in WINDOWS},
                "sourceDirectory":triple, "selectedWindow":window,
                "selectedSourceReplayProofDigest":proof["proofDigest"],
                "selectedSourceReceiptChainDigest":proof["receiptChainDigest"],
                "relationAlgorithmVersion":relation.ALGORITHM,
                "relationTableBindingDigest":opened.header()["tableBindingDigest"],
                "otherWindowsReplayed":False,
                "sameSourceAmountsAdded":False,
                "sourceAuthorityVerified":False,
                "agentReadPersisted":False,
                "registeredRenderer":False}
            _need(time.monotonic()-started < MAX_WINDOW_SECONDS)
            try:
                yield opened, json.loads(canonical(binding))
            finally:
                _final_fence(loaded, principal)
                _need(time.monotonic()-started < MAX_WINDOW_SECONDS,
                    "v4跟单SKU返回前超时")
    except (AnalysisContractError, KeyError, TypeError, ValueError,
            UnicodeError, OverflowError) as error:
        raise AiError("v4跟单SKU未通过完整持久页、审计或分组核验",
            "conflict", 409) from error


def _response(binding, key, value):
    result = {"schemaVersion":SCHEMA, "binding":binding,
        "bindingDigest":digest(binding), "authorityVerified":False,
        "agentReadPersisted":False, key:value}
    result["responseDigest"] = digest(result)
    return result


def _page_response(binding, page):
    while True:
        result = _response(binding, "table", page)
        if len(canonical(result).encode("utf-8")) <= MAX_RESPONSE_BYTES:
            return result
        if len(page["rows"]) <= 1:
            raise AiError("v4跟单SKU完整行或绑定超过响应容量",
                "payload_too_large", 413)
        page["rows"].pop()
        pagination = page["pagination"]
        pagination["returned"] = len(page["rows"])
        end = pagination["offset"] + len(page["rows"])
        pagination["nextOffset"] = end if end < pagination["total"] else None
        page["pageDigest"] = digest({key:value for key,value in page.items()
            if key != "pageDigest"})


def page(run_id, params, principal, *, enabled=False, checkpoint=None):
    _need(enabled is True, "v4跟单SKU拥有方读取默认关闭")
    fields(params, {"sourceKeys", "window", "view", "offset", "limit"},
        {"sourceKeys", "window", "view"})
    offset = integer(params.get("offset", 0), "offset", 0, MAX_ROWS)
    integer(params.get("limit", 20), "limit", 20, 20)
    with table(run_id, params["sourceKeys"], params["window"],
            params["view"], principal, enabled=True,
            checkpoint=checkpoint) as (opened, binding):
        return _page_response(binding, opened.page(offset, 20))


def read_row(run_id, source_keys, window, view, row_index, row_id,
             principal, *, enabled=False, checkpoint=None):
    _need(enabled is True, "v4跟单SKU拥有方读取默认关闭")
    row_index = integer(row_index, "rowIndex", 0, MAX_ROWS-1)
    if type(row_id) is not str or re.fullmatch(r"[a-f0-9]{64}", row_id) is None:
        raise AiError("v4跟单SKU精确行身份无效", "invalid_request", 400)
    with table(run_id, source_keys, window, view, principal, enabled=True,
            checkpoint=checkpoint) as (opened, binding):
        result = _response(binding, "row", opened.read_row(row_index,row_id))
        if len(canonical(result).encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise AiError("v4跟单SKU精确行超过响应容量",
                "payload_too_large", 413)
        return result
