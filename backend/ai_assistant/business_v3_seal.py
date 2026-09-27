"""Internal mixed-v3 seal of fully replayed, audit-bound immutable sources.

No public finish route, report, model, or file generator imports this module.
"""
from __future__ import annotations

import json

from business_analysis import evidence_seal_v3 as contract
from business_analysis.contracts import AnalysisContractError

from . import business_evidence_store as store, business_evidence_v3 as plan
from . import business_v3_catalog as catalog, business_v3_tool_receipts as receipts, models as m
from .policy import AiError, authorize_owner, canonical, cas, digest, identifier, integer, mutation


def _reject(message="v3来源尚未形成可封存的完整审计证据"):
    raise AiError(message, "conflict", 409)


def _receipt_chain(run_id, source):
    rows = list(m.AiBusinessSourceToolReceipt.objects.filter(run_id=run_id, source_id=source["id"])
        .order_by("sequence").values("sequence", "audit_id", "invocation_id", "response_digest")[:2000])
    if len(rows) != source["page_count"]:
        _reject("v3来源收据数与目录事实页数不一致")
    chain = digest({"schemaVersion": "business-receipt-chain-v1", "sourceKey": source["source_key"]})
    for sequence, row in enumerate(rows, 1):
        if row["sequence"] != sequence:
            _reject("v3来源收据序号不连续")
        chain = digest([chain, sequence, row["audit_id"], row["invocation_id"], row["response_digest"]])
    return chain


def _source_proof(row, source, query, principal, *, allow_sealed=False):
    receipt = receipts.require_complete(row.id, source["source_key"], principal,
        allow_sealed=allow_sealed)
    if receipt["receiptCount"] != source["page_count"] or not source["finished"]:
        _reject()
    if source["domain"] == "finance":
        from . import business_finance_collection_v3 as owning
        replay = owning.inspect(row.id, source["source_key"], principal, allow_sealed=allow_sealed)
        complete = replay["completeSource"]
        if complete is None:
            _reject("财报自然月来源尚未完整")
        ref, revision = complete["sourceRef"], complete["sourceRevision"]
        coverage = contract.finance_months(query, complete)
    else:
        from . import business_daily_collection_v3 as owning
        replay = owning.inspect(row.id, source["source_key"], principal, allow_sealed=allow_sealed)
        if not replay["reconciliation"]["reconciled"]:
            _reject("日来源尚未完成控制汇总")
        checkpoint = json.loads(source["checkpoint_json"])
        ref = replay["reconciliation"]["sourceRef"]
        revision = checkpoint["metadata"]["sourceRevision"]
        pages = (json.loads(value) for value in m.AiBusinessEvidenceChunk.objects.filter(
            run_id=row.id, source_key=source["source_key"]).order_by("sequence")
            .values_list("payload_json", flat=True).iterator(chunk_size=10))
        coverage = contract.date_observations(query, pages,
            snapshot=source["domain"] == "netshop" and query["dataset"] == "master")
    if (replay["sourceId"] != source["id"] or replay["pageCount"] != source["page_count"]
            or replay["rowCount"] != source["row_count"] or replay["storedBytes"] != source["stored_bytes"]
            or not replay["finished"]):
        _reject("拥有方重放结果与目录计数不一致")
    return {"sourceKey": source["source_key"], "ordinal": source["ordinal"],
        "domain": source["domain"], "queryDigest": source["query_digest"],
        "sourceVersion": source["version"], "checkpointDigest": digest(source["checkpoint_json"]),
        "pageCount": source["page_count"], "rowCount": source["row_count"],
        "storedBytes": source["stored_bytes"], "sourceRef": ref,
        "sourceRevision": revision, "receiptCount": receipt["receiptCount"],
        "receiptChainDigest": _receipt_chain(row.id, source), "coverage": coverage}


def finish(run_id, expected_version, principal):
    """Seal only after complete owner replay; recheck exact physical CAS under lock."""
    identifier(run_id)
    integer(expected_version, "expectedVersion")
    row, built, sources, actor = catalog.load(run_id, principal)
    cas(row, expected_version)
    if not sources or any(not item["finished"] or item["page_count"] < 1 for item in sources):
        _reject("v3至少一个财报或日来源尚未完成")
    queries = {entry["key"]: entry["query"] for entry in built["entries"]}
    try:
        proofs = [_source_proof(row, item, queries[item["source_key"]], principal) for item in sources]
        sealed = contract.make(run_id=row.id, evidence_version=row.version + 1,
            plan_digest=digest(row.plan_json), catalog_digest=built["header"]["catalogDigest"],
            sources=proofs, stored_bytes=row.stored_bytes)
    except (AnalysisContractError, KeyError, TypeError, ValueError, RecursionError) as error:
        raise AiError("v3封存来源覆盖或收据形状无效", "conflict", 409) from error
    catalog.unchanged(row, actor, sources, principal)
    saved = canonical(sealed)
    with mutation(principal):
        if plan._actor(principal) != actor:
            raise AiError("v3封存期间账号权限变化", "access_denied", 403)
        live = authorize_owner(m.AiBusinessEvidenceRun.objects.select_for_update().get(pk=row.id), principal)
        cas(live, expected_version)
        if (live.status != "collecting" or live.collection_status != "manual"
                or live.state_json != "{}" or live.stored_bytes != row.stored_bytes
                or live.plan_json != row.plan_json or live.request_digest != row.request_digest):
            _reject("v3父任务封存 CAS 失败")
        locked = list(m.AiBusinessEvidenceSource.objects.select_for_update().filter(run_id=row.id)
            .order_by("ordinal").values(*sources[0].keys())[:49])
        if locked != sources:
            raise AiError("v3来源检查点已变化", "version_conflict", 409)
        # Chunks and audit rows are immutable. Receipt insert locks both the
        # parent and source; these hashes therefore cannot change after this
        # exact parent+directory lock has been acquired.
        for source, proof in zip(locked, proofs):
            if _receipt_chain(row.id, source) != proof["receiptChainDigest"]:
                raise AiError("v3来源收据链封存 CAS 失败", "version_conflict", 409)
        if store.progress(live)["pageCount"] != sealed["pageCount"]:
            _reject("v3父任务事实页数变化")
        store.check_quota(principal, len(saved.encode("utf-8")) - len(live.state_json.encode("utf-8")),
                          64 * 1024 * 1024)
        live.status = "sealed"
        live.state_json = saved
        live.version += 1
        live.save(update_fields=["status", "state_json", "version"])
        if plan._actor(principal) != actor:
            raise AiError("v3封存落地期间账号权限变化", "access_denied", 403)
    return {"runId": run_id, "status": "sealed", "version": sealed["evidenceVersion"],
            "seal": sealed, "reportGenerationSupported": False}


def verify(run_id, principal):
    """Re-read a sealed parent and every immutable source/receipt after transition."""
    row, built, sources, actor = catalog.load(run_id, principal, allow_sealed=True)
    if row.status != "sealed" or any(not source["finished"] for source in sources):
        _reject("v3父任务尚未封存或来源状态变化")
    queries = {entry["key"]: entry["query"] for entry in built["entries"]}
    try:
        proofs = [_source_proof(row, source, queries[source["source_key"]], principal,
                    allow_sealed=True) for source in sources]
        expected = contract.make(run_id=row.id, evidence_version=row.version,
            plan_digest=digest(row.plan_json), catalog_digest=built["header"]["catalogDigest"],
            sources=proofs, stored_bytes=row.stored_bytes)
    except (AnalysisContractError, KeyError, TypeError, ValueError, RecursionError) as error:
        raise AiError("v3封存来源无法从不可变事实重建", "conflict", 409) from error
    if row.state_json != canonical(expected):
        _reject("v3封存摘要与真实来源或收据链不一致")
    catalog.unchanged(row, actor, sources, principal)
    return {"runId": row.id, "status": "sealed", "version": row.version,
            "seal": expected, "receiptBound": True,
            "sourceAuthorityVerified": False, "reportGenerationSupported": False}
