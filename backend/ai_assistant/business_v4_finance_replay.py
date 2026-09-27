"""Read-only full replay of one completed v4 monthly finance source.

Each immutable page is bound to its successful internal signed-tool receipt and
the complete request argument digest. This does not seal a parent or establish
an independent upstream digital signature.
"""
from __future__ import annotations

from itertools import zip_longest
import json
import re
import time

from business_analysis import evidence_v4, finance_collection_state_v4 as verifier
from business_analysis.contracts import AnalysisContractError
from business_analysis.partitioned import Checkpoint

from . import business_evidence_v3 as actor_service, models as m
from . import business_v4_finance_collection as collector
from .policy import AiError, authorize_owner, canonical, digest, identifier

SCHEMA = "business-v4-finance-complete-replay-candidate-v1"
MAX_REPLAY_SECONDS = 600
MAX_PROOF_BYTES = 38_000


def _need(ok, message="v4财报完整来源重放失败"):
    if not ok:
        raise AiError(message, "conflict", 409)


def _loaded(run_id, source_key, principal):
    actor = actor_service._actor(principal)
    parent = m.AiBusinessV4Run.objects.filter(pk=identifier(run_id)).first()
    if parent is None:
        raise AiError("v4财报父任务不存在", "not_found", 404)
    authorize_owner(parent, principal)
    source = m.AiBusinessV4Source.objects.filter(run_id=parent.id,
        source_key=identifier(source_key)).first()
    if source is None or source.domain != "finance":
        raise AiError("v4财报来源不存在", "not_found", 404)
    try:
        query = collector._plan(parent, source)
        raw = source.checkpoint_json
        _need(type(raw) is str and len(raw.encode("utf-8")) <=
            collector.MAX_CHECKPOINT_BYTES,
            "v4财报检查点超过固定容量，禁止解析")
        saved = json.loads(raw)
        state = verifier._state(saved["financeState"], query)
        directory = list(m.AiBusinessV4Source.objects.filter(run_id=parent.id)
            .order_by("ordinal").values("source_key", "ordinal", "domain",
                "temporal_role", "query_json", "query_digest",
                "source_identity_digest", "page_count", "row_count",
                "stored_bytes")[:49])
        entries = json.loads(parent.plan_json)["sourcePlans"]
        if (parent.status != "collecting" or parent.collection_status != "manual"
                or parent.owner_email != actor["email"] or parent.scope_json != "null"
                or parent.version != parent.page_count + 1
                or source.version != source.page_count + 1 or not source.finished
                or not 1 <= source.page_count <= evidence_v4.MAX_SOURCE_PAGES
                or not 0 <= source.row_count <= 100_000
                or not 0 < source.stored_bytes <= evidence_v4.MAX_SOURCE_BYTES
                or re.fullmatch(r"[a-f0-9]{64}", source.source_ref or "") is None
                or type(source.source_revision) is not str
                or not 1 <= len(source.source_revision) <= 128
                or type(saved) is not dict or set(saved) != {"schemaVersion",
                    "sourceRef", "sourceRevision", "lastChunkDigest", "pageCount",
                    "rowCount", "storedBytes", "finished", "financeState"}
                or raw != canonical(saved) or saved["schemaVersion"] != collector.CHECKPOINT_SCHEMA
                or saved["sourceRef"] != source.source_ref
                or saved["sourceRevision"] != source.source_revision
                or saved["pageCount"] != source.page_count
                or saved["rowCount"] != source.row_count
                or saved["storedBytes"] != source.stored_bytes
                or saved["finished"] is not True or state["finished"] is not True
                or state["pageCount"] != source.page_count
                or state["rowsRead"] != source.row_count
                or state["storedBytes"] != source.stored_bytes
                or state["sourceRef"] != source.source_ref
                or state["sourceRevision"] != source.source_revision
                or len(directory) != len(entries)
                or any((row["source_key"], row["ordinal"], row["domain"],
                    row["temporal_role"], row["query_json"], row["query_digest"],
                    row["source_identity_digest"]) !=
                    (item["sourceKey"], item["ordinal"], item["domain"],
                    item["temporalRole"], canonical(item["query"]),
                    item["queryDigest"], item["sourceIdentityDigest"])
                    for row, item in zip(directory, entries))
                or sum(item["page_count"] for item in directory) != parent.page_count
                or sum(item["row_count"] for item in directory) != parent.row_count
                or sum(item["stored_bytes"] for item in directory) != parent.stored_bytes):
            _need(False, "v4财报完成来源或父目录计数不一致")
    except (AnalysisContractError, KeyError, TypeError, ValueError,
            AttributeError, UnicodeError, RecursionError) as error:
        raise AiError("v4财报父目录、自然月检查点不能重建", "conflict", 409) from error
    fixed_parent = {key: getattr(parent, key) for key in ("version", "status",
        "collection_status", "plan_json", "plan_digest", "run_identity_digest",
        "page_count", "row_count", "stored_bytes")}
    fixed_source = {key: getattr(source, key) for key in ("version", "finished",
        "query_json", "query_digest", "checkpoint_json", "page_count",
        "row_count", "stored_bytes", "source_ref", "source_revision")}
    return actor, parent, source, query, saved, fixed_parent, fixed_source


def _final_fence(actor, parent, source, fixed_parent, fixed_source, principal):
    if actor_service._actor(principal) != actor:
        raise AiError("v4财报重放期间账号权限变化", "access_denied", 403)
    current_parent = m.AiBusinessV4Run.objects.filter(pk=parent.id).values(
        *fixed_parent).first()
    current_source = m.AiBusinessV4Source.objects.filter(pk=source.id,
        run_id=parent.id).values(*fixed_source).first()
    _need(current_parent == fixed_parent and current_source == fixed_source,
        "v4财报重放期间父任务或来源版本变化")
    if actor_service._actor(principal) != actor:
        raise AiError("v4财报重放完成时账号权限变化", "access_denied", 403)


def inspect(run_id, source_key, principal, *, checkpoint=None):
    """Replay every real page and receipt; return one non-authorizing proof."""
    deadline = time.monotonic() + MAX_REPLAY_SECONDS
    check = Checkpoint.wrap(checkpoint)
    actor, parent, source, query, saved, fixed_parent, fixed_source = (
        _loaded(run_id, source_key, principal))
    _need(not m.AiBusinessSourceToolReceipt.objects.filter(audit_id__in=
        m.AiBusinessV4ToolReceipt.objects.filter(run_id=parent.id,
            source_id=source.id).values("audit_id")).exists(),
        "v4财报审计不得复用旧v3来源收据")
    chunks = m.AiBusinessV4Chunk.objects.filter(run_id=parent.id,
        source_id=source.id).order_by("sequence").iterator(chunk_size=16)
    receipts = m.AiBusinessV4ToolReceipt.objects.filter(run_id=parent.id,
        source_id=source.id).select_related("audit").order_by("sequence").iterator(chunk_size=16)
    state, pages, rows, size, last_digest, receipt_chain = (
        None, 0, 0, 0, None, digest([]))
    marker = object()
    try:
        for chunk, receipt in zip_longest(chunks, receipts, fillvalue=marker):
            pages += 1
            _need(time.monotonic() <= deadline, "v4财报完整重放超过时间边界")
            if check is not None and pages % 20 == 1:
                check({"stage": "v4_finance_replay", "phase": "page", "sequence": pages})
            if (chunk is marker or receipt is marker or pages > source.page_count
                    or chunk.sequence != pages or receipt.sequence != pages
                    or chunk.run_id != parent.id or chunk.source_id != source.id
                    or receipt.run_id != parent.id or receipt.source_id != source.id
                    or receipt.chunk_id != chunk.id or receipt.actor_email != actor["email"]
                    or receipt.tool_name != collector.TOOL
                    or receipt.surface != "business_collection"
                    or receipt.audit_id is None or receipt.audit.actor_email != actor["email"]
                    or receipt.audit.actor_role != "admin"
                    or receipt.audit.surface != "business_collection"
                    or receipt.audit.tool_name != collector.TOOL
                    or receipt.audit.request_id != receipt.request_id
                    or receipt.audit.invocation_id != receipt.invocation_id
                    or receipt.audit.status != "succeeded"
                    or receipt.audit.error_code is not None
                    or receipt.audit.created_at > chunk.created_at
                    or chunk.created_at > receipt.created_at):
                _need(False, "v4财报页序或成功工具审计收据不完整")
            expected = ({"query": query, "offset": 0, "afterId": 0} if state is None
                else verifier.next_arguments(state, trusted_query=query))
            _need(receipt.audit.arguments_json == canonical({
                "argumentsDigest": digest(expected)}),
                "v4财报工具请求不属于精确自然月续页")
            raw = chunk.payload_json
            encoded_size = len(raw.encode("utf-8"))
            size += encoded_size
            _need(type(raw) is str and encoded_size <= verifier.PAGE_BYTES
                and size <= evidence_v4.MAX_SOURCE_BYTES
                and digest(raw) == chunk.payload_digest
                and chunk.source_ref == source.source_ref
                and chunk.source_revision == source.source_revision
                and receipt.response_digest == chunk.payload_digest
                and receipt.audit.response_digest == chunk.payload_digest
                and receipt.payload_bytes == encoded_size,
                "v4财报事实字节与审计响应摘要不一致")
            page = json.loads(raw)
            _need(raw == canonical(page), "v4财报事实块不是唯一规范JSON")
            state = verifier.consume(state, page, trusted_query=query)
            _need(chunk.row_count == len(page["rows"]),
                "v4财报块真实行数与来源页不同")
            rows += chunk.row_count
            last_digest = chunk.payload_digest
            receipt_chain = digest([receipt_chain, pages, chunk.payload_digest,
                receipt.audit_id, receipt.invocation_id,
                chunk.source_ref, chunk.source_revision])
        _need(state is not None and state["finished"] is True
            and pages == source.page_count and rows == source.row_count
            and size == source.stored_bytes
            and last_digest == saved["lastChunkDigest"]
            and canonical(state) == canonical(saved["financeState"]),
            "v4财报终页、自然月控制状态或检查点与事实不同")
        complete = verifier.result(state, trusted_query=query)
    except (AnalysisContractError, KeyError, ValueError, TypeError,
            AttributeError, UnicodeError, RecursionError) as error:
        if check is not None: check.raise_if_failed()
        raise AiError("v4财报完整页链、批次或原值核验失败", "conflict", 409) from error
    if check is not None:
        check({"stage": "v4_finance_replay", "phase": "complete"})
    _need(time.monotonic() <= deadline, "v4财报完整重放超过时间边界")
    _final_fence(actor, parent, source, fixed_parent, fixed_source, principal)
    proof = {"schemaVersion": SCHEMA, "runId": parent.id,
        "sourceId": source.id, "sourceKey": source.source_key,
        "sourceVersion": source.version, "runVersion": parent.version,
        "queryDigest": source.query_digest, "sourceRef": source.source_ref,
        "sourceRevision": source.source_revision,
        "pageCount": pages, "rowCount": rows, "storedBytes": size,
        "monthlyContext": complete, "receiptChainDigest": receipt_chain,
        "fullSourceReplayVerified": True, "internalToolAuditBound": True,
        "requestOffsetAuditVerified": True,
        "upstreamSignatureVerified": False, "sealed": False,
        "persistentEvidenceVerified": False, "reportGenerationSupported": False,
        "dailyProrationAllowed": False, "inferSkuProfit": False,
        "sumWithErpB2bAdsAllowed": False}
    proof["proofDigest"] = digest(proof)
    _need(len(canonical(proof).encode("utf-8")) <= MAX_PROOF_BYTES,
        "v4财报完整来源证明超过固定响应容量")
    _need(time.monotonic() <= deadline, "v4财报证明返回前超过时间边界")
    return proof
