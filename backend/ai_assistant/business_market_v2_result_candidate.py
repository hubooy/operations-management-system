"""Pure, closed five-Agent result checks over proposed persisted read snapshots.

The caller must later load every receipt and tool result from the protected
database and re-read the owning source. Caller JSON cannot confer read,
numeric-citation, model, human-review, or publication authority here.
"""
import re

from business_analysis import market_numeric_claims
from business_analysis.contracts import AnalysisContractError, digest
from business_analysis.promotion_views import _copy
from . import business_market_v2_execution_plan_contract as plan
from . import business_market_v2_execution_snapshot_contract as execution
from . import business_market_v2_read_receipt_contract as reads
from . import business_promotion_market_runtime_v2_contract as runtime
from . import business_promotion_runtime_contract as promotion
from . import business_screening_runtime_contract as screening


SCHEMA = "business-market-v2-five-agent-result-candidate-v1"
_ID = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")
_FIELDS = {"role", "jobId", "answer", "reads", "numericClaims"}
_READ_FIELDS = {"receipt", "provider", "result"}
_PROVIDER_FIELDS = {"jobId", "dispatchId", "state", "calls",
    "callId", "toolDispatchId", "toolName", "arguments"}
_CLAIM_FIELDS = {"toolDispatchId", "reference", "value"}
_REQUIRED = {
    "commerce": {execution.TOOL_ORDER[0], execution.TOOL_ORDER[1]},
    "promotion": {execution.TOOL_ORDER[0], execution.TOOL_ORDER[3]},
    "market_b2b": {execution.TOOL_ORDER[0], execution.TOOL_ORDER[4]},
    "independent_review": {execution.TOOL_ORDER[0], execution.TOOL_ORDER[1],
        execution.TOOL_ORDER[4]},
    "report": {execution.TOOL_ORDER[0], execution.TOOL_ORDER[2],
        execution.TOOL_ORDER[4]},
}
_MARKET = execution.TOOL_ORDER[4]
_BUDGET = execution.TOOL_ORDER[2]


def _need(ok, message="市场五Agent结果候选缺少同任务回执或数值证据"):
    if not ok:
        raise AnalysisContractError(message)


def _read(item, role, job_id, root, seen_dispatches):
    _need(type(item) is dict and set(item) == _READ_FIELDS)
    receipt = reads.receipt(item["receipt"])
    provider = item["provider"]
    _need(type(provider) is dict and set(provider) == _PROVIDER_FIELDS
        and provider["jobId"] == job_id
        and provider["dispatchId"] == receipt["providerDispatchId"]
        and provider["state"] == "succeeded"
        and provider["callId"] == receipt["providerCallId"]
        and provider["toolDispatchId"] == receipt["toolDispatchId"]
        and provider["toolName"] == receipt["toolName"]
        and type(provider["arguments"]) is dict
        and type(provider["calls"]) is list
        and 1 <= len(provider["calls"]) <= 40
        and sum(type(call) is dict
            and call == {"id": provider["callId"],
                "name": provider["toolName"],
                "arguments": provider["arguments"]}
            for call in provider["calls"]) == 1)
    args = provider["arguments"]
    _need(args.get("reportId") == root["admittedReportId"]
        and (args.get("role") == role if receipt["toolName"] != _MARKET
            else args.get("marketContextDigest") == root["marketContextDigest"]
            and args.get("mode") == receipt["mode"]))
    result = item["result"]
    _need(type(result) is dict and set(result) ==
        {"ok", "toolName", "auditStatus", "data"}
        and result["ok"] is True and result["auditStatus"] == "recorded"
        and result["toolName"] == receipt["toolName"]
        and receipt["jobId"] == job_id and receipt["role"] == role
        and receipt["executionReportId"] == root["executionReportId"]
        and receipt["admittedReportId"] == root["admittedReportId"]
        and receipt["ownerEmail"] == root["ownerEmail"]
        and receipt["contextProofDigest"] == root["contextProofDigest"]
        and receipt["toolResultDigest"] == digest(result)
        and receipt["toolDispatchId"] not in seen_dispatches)
    seen_dispatches.add(receipt["toolDispatchId"])
    data = result["data"]
    _need(type(data) is dict)
    _need(data.get("resultDigest") == digest({key: value
        for key, value in data.items() if key != "resultDigest"}))
    if receipt["toolName"] == _MARKET:
        _need(role in runtime.MARKET_ROLES
            and receipt["mode"] in {"summary", "page", "row"}
            and data.get("role") == role
            and data.get("reportId") == root["admittedReportId"]
            and data.get("marketManifestDigest") == root["manifestDigest"]
            and data.get("jobIdClaim") == job_id
            and data.get("providerDispatchIdClaim") == receipt["providerDispatchId"]
            and data.get("providerCallIdClaim") == receipt["providerCallId"]
            and data.get("mode") == receipt["mode"]
            and data.get("serverFullMarketMaterialVerified") is True
            and type(data.get("payload")) is dict)
        if receipt["mode"] == "summary":
            coverage = data["payload"].get("observationCoverage")
            tables = data["payload"].get("tables")
            _need(coverage == {"currentDatePresent": True,
                "baselineDatePresent": True, "bothDatesPresent": True}
                and data["payload"].get("marketAndOwnSalesAdditive") is False
                and type(tables) is list and len(tables) == 3
                and {table.get("view") for table in tables if type(table) is dict}
                    == {"price_band_summary", "price_band_members", "rank_entry_exit"}
                and all(type(table.get("rowCount")) is int
                    and table["rowCount"] >= 0
                    and type(table.get("sourceTableDigest")) is str
                    and re.fullmatch(r"[0-9a-f]{64}", table["sourceTableDigest"])
                    for table in tables))
        elif receipt["mode"] == "page":
            pagination = data["payload"].get("pagination")
            rows = data["payload"].get("rows")
            _need(args.get("view") in runtime.TOOL_VIEWS
                and type(args.get("offset")) is int
                and type(args.get("limit")) is int
                and args["limit"] == runtime.PAGE_SIZE
                and type(pagination) is dict
                and pagination.get("offset") == args["offset"]
                and type(rows) is list and len(rows) <= runtime.PAGE_SIZE
                and all(type(row) is dict
                    and row.get("rowIndex") == args["offset"] + index
                    for index, row in enumerate(rows)))
        else:
            row = data["payload"].get("row")
            _need(args.get("view") in runtime.TOOL_VIEWS
                and type(args.get("rowIndex")) is int
                and type(args.get("rowId")) is str
                and type(row) is dict
                and (row.get("rowIndex"), row.get("rowId")) ==
                    (args["rowIndex"], args["rowId"]))
    else:
        _need(receipt["mode"] == "read"
            and data.get("toolName") == receipt["toolName"]
            and data.get("reportId") == root["admittedReportId"]
            and data.get("roleClaim") == role
            and data.get("status") in {"available", "unavailable_no_fixed_budget"})
        _need(data.get("sourceResultDigest") ==
            (digest(data.get("payload")) if data["status"] == "available" else None))
        if receipt["toolName"] == _BUDGET:
            _need(role in screening.BUDGET_NODES)
            _need(data["status"] == ("available" if root["withBudget"]
                else "unavailable_no_fixed_budget"))
            if not root["withBudget"]:
                _need(data.get("payload") is None)
        elif receipt["toolName"] == execution.TOOL_ORDER[3]:
            _need(role in promotion.PROMOTION_ROLES)
        else:
            _need(data["status"] == "available")
    return receipt, data


def _claim(raw, role, job_id, by_dispatch, summary):
    _need(type(raw) is dict and set(raw) == _CLAIM_FIELDS
        and type(raw["toolDispatchId"]) is str
        and _ID.fullmatch(raw["toolDispatchId"]) is not None
        and type(raw["value"]) is int)
    ref = market_numeric_claims.reference(raw["reference"])
    _need((ref["role"], ref["jobId"]) == (role, job_id))
    source = by_dispatch.get(raw["toolDispatchId"])
    _need(source is not None and source[0]["toolName"] == _MARKET
        and source[0]["mode"] in {"page", "row"}
        and summary is not None)
    receipt, data = source
    payload = data["payload"]
    table_view = ("price_band_summary" if ref["view"] == "price_band"
        else "rank_entry_exit")
    table = next((item for item in summary["payload"]["tables"]
        if item["view"] == table_view), None)
    _need(payload.get("tableBindingDigest") == ref["tableBindingDigest"]
        and table is not None
        and table["sourceTableDigest"] == ref["tableBindingDigest"]
        and type(data.get("citationBases")) is list)
    base = {key: value for key, value in ref.items()
        if key not in {"metric", "field"}}
    _need(base in data["citationBases"])
    rows = payload.get("rows") if receipt["mode"] == "page" else [payload.get("row")]
    _need(type(rows) is list and len(rows) <= 20)
    found = [row for row in rows if type(row) is dict
        and row.get("rowIndex") == ref["rowIndex"]
        and row.get("rowId") == ref["rowId"]]
    _need(len(found) == 1)
    number = market_numeric_claims.number(ref, found[0],
        observation_coverage=summary["payload"]["observationCoverage"])
    _need(number["value"] == raw["value"]
        and number["population"] == market_numeric_claims.ATTRIBUTION
        and number["ownSalesAttributionVerified"] is False)
    return {"referenceDigest": digest(ref), "readReceiptDigest":
        receipt["receiptDigest"], "value": number["value"],
        "partial": number["partial"], "population": number["population"]}


def check(raw_root, raw_results):
    """Check one fixed five-role candidate; never issue any runtime grant."""
    root = plan.root(raw_root)
    results = _copy(raw_results, 128 * 1024)
    _need(type(results) is list and len(results) == len(runtime.ROLES))
    jobs, dispatches, prepared = set(), set(), []
    for expected_role, item in zip(runtime.ROLES, results):
        _need(type(item) is dict and set(item) == _FIELDS
            and item["role"] == expected_role
            and type(item["jobId"]) is str
            and _ID.fullmatch(item["jobId"]) is not None
            and item["jobId"] not in jobs
            and type(item["answer"]) is str
            and 0 < len(item["answer"]) <= 20000
            and type(item["reads"]) is list and 1 <= len(item["reads"]) <= 40
            and type(item["numericClaims"]) is list
            and len(item["numericClaims"]) <= 100)
        jobs.add(item["jobId"])
        by_dispatch, tools, summary = {}, set(), None
        for raw_read in item["reads"]:
            receipt, data = _read(raw_read, expected_role, item["jobId"],
                root, dispatches)
            tools.add(receipt["toolName"])
            by_dispatch[receipt["toolDispatchId"]] = (receipt, data)
            if receipt["toolName"] == _MARKET and receipt["mode"] == "summary":
                _need(summary is None)
                summary = data
        _need(_REQUIRED[expected_role] <= tools)
        if root["withBudget"] and expected_role in {"promotion", "independent_review"}:
            _need(_BUDGET in tools)
        if expected_role in runtime.MARKET_ROLES:
            _need(summary is not None)
        else:
            _need(_MARKET not in tools)
        if expected_role == "market_b2b":
            for table_view, read_view in (("price_band_summary", "price_band"),
                    ("rank_entry_exit", "rank_entry_exit")):
                spec = next(row for row in summary["payload"]["tables"]
                    if row["view"] == table_view)
                if spec["rowCount"] > 0:
                    _need(any(read["receipt"]["toolName"] == _MARKET
                        and read["receipt"]["mode"] == "page"
                        and read["provider"]["arguments"].get("view") == read_view
                        and read["provider"]["arguments"].get("offset") == 0
                        and read["result"]["data"]["payload"].get(
                            "tableBindingDigest") == spec["sourceTableDigest"]
                        and read["result"]["data"]["payload"].get("rows")
                        for read in item["reads"]))
        claims = [_claim(raw, expected_role, item["jobId"], by_dispatch,
            summary) for raw in item["numericClaims"]]
        prepared.append({"role": expected_role, "jobId": item["jobId"],
            "answerDigest": digest(item["answer"]), "readCount": len(item["reads"]),
            "readReceiptDigests": [read["receipt"]["receiptDigest"]
                for read in item["reads"]], "marketClaims": claims})
    return {"schemaVersion": SCHEMA, "executionReportId": root["executionReportId"],
        "roles": prepared, "candidateChecksPassed": True,
        "persistedSourceIndependentlyLoaded": False,
        "owningRowsIndependentlyReplayed": False,
        "proseNumbersVerified": False, "agentExecutionAuthorized": False,
        "numericCitationAllowed": False, "humanReviewApproved": False,
        "reportPublishAuthorized": False}
