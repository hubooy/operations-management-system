"""Exact successful central-tool audit to immutable v3 source chunk binding.

The audit proves a trusted Worker tool invocation produced these page bytes
within the existing internal HMAC runtime boundary. It is not a separate
digital signature by the upstream finance/JD/ERP source.
"""
from __future__ import annotations

import json

from . import business_v3_catalog as catalog, models as m
from .policy import AiError, canonical, digest

FINANCE_TOOL = "get_business_finance_source_page"
INITIAL_DAILY_TOOL = "get_business_source_page"
DAILY_CONTINUATION = {"sales": "get_business_sales_continuation_page",
    "netshop": "get_business_netshop_continuation_page",
    "market": "get_business_market_continuation_page"}


def expected_tool(domain, sequence):
    if domain == "finance": return FINANCE_TOOL
    if domain in DAILY_CONTINUATION:
        return INITIAL_DAILY_TOOL if sequence == 1 else DAILY_CONTINUATION[domain]
    raise AiError("v3来源域无对应签名工具", "conflict", 409)


def _reject(message="v3来源页缺少唯一成功的签名工具审计"):
    raise AiError(message, "conflict", 409)


def audit_for_page(principal, *, request_id, tool_name, encoded):
    """Resolve exactly one real successful audit; never accept an audit DTO."""
    page_digest = digest(encoded)  # Policy.digest(str) hashes raw canonical UTF-8.
    records = list(m.AiToolAuditLogs.objects.filter(request_id=request_id,
        actor_email=principal.email.lower(), actor_role="admin", surface="business_collection",
        tool_name=tool_name, status="succeeded", response_digest=page_digest,
        error_code__isnull=True).values("id", "invocation_id", "created_at")[:2])
    if len(records) != 1:
        _reject()
    return records[0]


def bind_page(*, parent, source, chunk, principal, request_id, tool_name, page, encoded, audit):
    """Insert one receipt before advancing source/parent counters in the CAS txn."""
    if (source.run_id != parent.id or chunk.run_id != parent.id or chunk.source_key != source.source_key
            or chunk.sequence != source.page_count + 1 or tool_name != expected_tool(source.domain, chunk.sequence)
            or encoded != canonical(page) or chunk.payload_json != encoded
            or digest(encoded) != chunk.payload_digest or audit.get("id") is None):
        _reject("v3来源页或工具审计不属于当前待提交块")
    return m.AiBusinessSourceToolReceipt.objects.create(chunk=chunk, audit_id=audit["id"],
        run=parent, source=source, sequence=chunk.sequence, request_id=request_id,
        invocation_id=audit["invocation_id"], actor_email=principal.email.lower(),
        tool_name=tool_name, surface="business_collection", response_digest=chunk.payload_digest,
        payload_bytes=len(encoded.encode("utf-8")), source_ref=page["sourceRef"],
        source_revision=page["sourceRevision"])


def require_complete(run_id, source_key, principal, *, allow_sealed=False):
    """Reject every old/direct v3 chunk without a bound immutable audit."""
    row, built, sources, actor = catalog.load(run_id, principal, allow_sealed=allow_sealed)
    source = next((item for item in sources if item["source_key"] == source_key), None)
    if source is None:
        raise AiError("v3来源不存在", "not_found", 404)
    if not source["finished"] or source["page_count"] < 1:
        _reject("v3来源尚未形成可核验的完整收据链")
    chunks = list(m.AiBusinessEvidenceChunk.objects.filter(run_id=row.id, source_key=source_key)
                  .order_by("sequence").values("id", "sequence", "payload_json", "payload_digest")[:2000])
    receipts = list(m.AiBusinessSourceToolReceipt.objects.filter(run_id=row.id, source_id=source["id"])
                    .select_related("audit").order_by("sequence")[:2000])
    if len(chunks) != source["page_count"] or len(receipts) != len(chunks):
        _reject("v3来源包含未绑定签名工具回执的旧事实块")
    for position, (chunk, receipt) in enumerate(zip(chunks, receipts), 1):
        audit = receipt.audit
        raw = chunk["payload_json"]
        try:
            page = json.loads(raw)
        except (ValueError, TypeError, RecursionError) as error:
            raise AiError("v3收据对应事实块不可解码", "conflict", 409) from error
        if (chunk["sequence"] != position or receipt.sequence != position
                or receipt.chunk_id != chunk["id"] or receipt.run_id != row.id
                or receipt.source_id != source["id"] or receipt.actor_email != row.owner_email
                or receipt.surface != "business_collection"
                or receipt.tool_name != expected_tool(source["domain"], position)
                or receipt.tool_name != audit.tool_name or receipt.request_id != audit.request_id
                or receipt.invocation_id != audit.invocation_id
                or audit.actor_email != row.owner_email or audit.actor_role != "admin"
                or audit.surface != "business_collection" or audit.status != "succeeded"
                or audit.error_code is not None or receipt.created_at < audit.created_at
                or raw != canonical(page) or digest(raw) != chunk["payload_digest"]
                or receipt.response_digest != chunk["payload_digest"]
                or audit.response_digest != chunk["payload_digest"]
                or receipt.payload_bytes != len(raw.encode("utf-8"))
                or receipt.source_ref != page.get("sourceRef")
                or receipt.source_revision != page.get("sourceRevision")):
            _reject("v3签名工具回执与真实不可变事实块不一致")
    # A signed page can be genuine yet belong to a different source with the
    # same platform/window. Only the owning full replay checks every filter,
    # row identity, source revision and checkpoint against this directory query.
    if source["domain"] == "finance":
        from . import business_finance_collection_v3 as owning
    else:
        from . import business_daily_collection_v3 as owning
    replayed = owning.inspect(row.id, source_key, principal, allow_sealed=allow_sealed)
    if (replayed["sourceId"] != source["id"] or replayed["pageCount"] != len(chunks)
            or replayed["storedBytes"] != source["stored_bytes"] or not replayed["finished"]):
        _reject("v3签名收据对应事实页未通过拥有方来源身份重建")
    catalog.unchanged(row, actor, sources, principal)
    return {"runId": row.id, "sourceKey": source_key, "pageCount": len(chunks),
            "receiptCount": len(receipts), "auditBound": True,
            "sourceAuthorityVerified": False, "persistentEvidenceVerified": False}
