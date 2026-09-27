"""Closed result gate checks receipt ownership and exact market cells."""
from copy import deepcopy
from unittest import TestCase

from business_analysis.contracts import AnalysisContractError, digest
from business_analysis.test_market_numeric_claims import price, rank
from . import business_market_v2_execution_snapshot_contract as execution
from . import business_market_v2_result_candidate as candidate
from . import business_market_v2_read_receipt_contract as read_contract
from . import business_promotion_market_runtime_v2_contract as runtime


def root(with_budget=False):
    return {"executionReportId": "execution-1", "admittedReportId": "admitted-1",
        "parkedReportId": "parked-1", "sourceReportId": "source-1",
        "ownerEmail": "owner@example.invalid", "selectorDigest": "a"*64,
        "manifestDigest": "b"*64, "marketContextDigest": "c"*64,
        "contextProofDigest": "d"*64, "executionSnapshotDigest": "e"*64,
        "withBudget": with_budget}


def one_read(role, job_id, name, index, *, mode="read", payload=None,
        available=True, view=None):
    dispatch = f"tool-{role}-{index}"
    if name == execution.TOOL_ORDER[4]:
        data = {"role": role, "reportId": "admitted-1",
            "marketManifestDigest": "b"*64, "jobIdClaim": job_id,
            "providerDispatchIdClaim": f"provider-{role}",
            "providerCallIdClaim": f"call-{role}-{index}", "mode": mode,
            "serverFullMarketMaterialVerified": True,
            "payload": payload or {"observationCoverage": {
                "currentDatePresent": True, "baselineDatePresent": True,
                "bothDatesPresent": True}, "marketAndOwnSalesAdditive": False},
            "citationBases": []}
        if mode == "summary":
            price_ref, _ = price()
            rank_ref, _, _ = rank()
            data["payload"]["tables"] = [{"view": view, "rowCount": 1,
                "sourceTableDigest": (price_ref["tableBindingDigest"]
                    if view == "price_band_summary" else
                    rank_ref["tableBindingDigest"] if view == "rank_entry_exit"
                    else "f"*64)}
                for view in ("price_band_summary", "price_band_members",
                    "rank_entry_exit")]
    else:
        data = {"toolName": name, "roleClaim": role,
            "reportId": "admitted-1", "status": "available" if available
                else "unavailable_no_fixed_budget",
            "payload": {"sample": True} if available else None,
            "sourceResultDigest": digest({"sample": True}) if available else None}
    data["resultDigest"] = digest(data)
    result = {"ok": True, "toolName": name, "auditStatus": "recorded",
        "data": data}
    receipt = {"schemaVersion": read_contract.RECEIPT_SCHEMA,
        "executionReportId": "execution-1", "admittedReportId": "admitted-1",
        "ownerEmail": "owner@example.invalid", "jobId": job_id,
        "providerDispatchId": f"provider-{role}",
        "providerCallId": f"call-{role}-{index}",
        "toolDispatchId": dispatch, "toolName": name, "role": role,
        "mode": mode, "contextProofDigest": "d"*64,
        "toolResultDigest": digest(result), "receiptDigest": digest([dispatch, result]),
        "persistedRead": True, "numericCitationAllowed": False,
        "agentExecutionAuthorized": False}
    arguments = {"reportId": "admitted-1",
        **({"marketContextDigest": "c"*64, "mode": mode}
            if name == execution.TOOL_ORDER[4] else {"role": role})}
    if mode == "page":
        arguments.update(view=view, offset=0, limit=20)
    elif mode == "row":
        arguments.update(view=view,
            rowIndex=payload["row"]["rowIndex"], rowId=payload["row"]["rowId"])
    provider = {"jobId": job_id, "dispatchId": f"provider-{role}",
        "state": "succeeded", "callId": f"call-{role}-{index}",
        "toolDispatchId": dispatch, "toolName": name,
        "arguments": arguments,
        "calls": [{"id": f"call-{role}-{index}", "name": name,
            "arguments": arguments}]}
    return {"receipt": receipt, "provider": provider, "result": result}


def fixture(with_budget=False):
    results = []
    for role in runtime.ROLES:
        job_id = "job-" + role
        tools = sorted(candidate._REQUIRED[role])
        if with_budget and role in {"promotion", "independent_review"}:
            tools.append(execution.TOOL_ORDER[2])
        reads = [one_read(role, job_id, name, i,
            mode="summary" if name == execution.TOOL_ORDER[4] else "read",
            available=name != execution.TOOL_ORDER[2] or with_budget)
            for i, name in enumerate(tools)]
        results.append({"role": role, "jobId": job_id,
            "answer": "来源、边界与调整前提", "reads": reads,
            "numericClaims": []})
    target = results[2]
    price_ref, price_row = price()
    rank_ref, rank_row, _ = rank()
    for view, ref, row in (("price_band", price_ref, price_row),
            ("rank_entry_exit", rank_ref, rank_row)):
        target["reads"].append(one_read("market_b2b", target["jobId"],
            execution.TOOL_ORDER[4], len(target["reads"]), mode="page",
            view=view, payload={"rows": [row], "pagination": {"offset": 0},
                "tableBindingDigest": ref["tableBindingDigest"]}))
    ref, row = price()
    ref["reportId"] = "admitted-1"
    ref["jobId"] = target["jobId"]
    row_read = one_read("market_b2b", target["jobId"], execution.TOOL_ORDER[4],
        len(target["reads"]), mode="row", payload={"row": row,
        "tableBindingDigest": ref["tableBindingDigest"]}, view="price_band")
    row_read["result"]["data"]["citationBases"] = [{key: value
        for key, value in ref.items() if key not in {"metric", "field"}}]
    refresh(row_read)
    target["reads"].append(row_read)
    target["numericClaims"].append({"toolDispatchId":
        row_read["receipt"]["toolDispatchId"], "reference": ref, "value": 10})
    return root(with_budget), results


def refresh(read):
    read["result"]["data"]["resultDigest"] = digest({key: value for key, value
        in read["result"]["data"].items() if key != "resultDigest"})
    read["receipt"]["toolResultDigest"] = digest(read["result"])


class MarketV2ResultCandidateTests(TestCase):
    def test_five_roles_same_job_reads_and_exact_row_still_do_not_publish(self):
        for has_budget in (False, True):
            fixed, roles = fixture(has_budget)
            value = candidate.check(fixed, roles)
            self.assertEqual([x["role"] for x in value["roles"]], list(runtime.ROLES))
            self.assertEqual(value["roles"][2]["marketClaims"][0]["value"], 10)
            self.assertTrue(value["candidateChecksPassed"])
            for flag in ("persistedSourceIndependentlyLoaded",
                    "owningRowsIndependentlyReplayed", "proseNumbersVerified",
                    "agentExecutionAuthorized", "numericCitationAllowed",
                    "humanReviewApproved", "reportPublishAuthorized"):
                self.assertFalse(value[flag])

    def test_missing_role_read_cross_job_provider_and_digest_fail(self):
        fixed, roles = fixture()
        cases = []
        changed = deepcopy(roles); changed[1]["reads"] = changed[1]["reads"][:1]
        cases.append(changed)
        changed = deepcopy(roles); changed[2]["reads"][0]["receipt"]["jobId"] = "other-job"
        cases.append(changed)
        changed = deepcopy(roles); changed[2]["reads"][0]["receipt"]["providerDispatchId"] = "other-provider"
        cases.append(changed)
        changed = deepcopy(roles); changed[2]["reads"][0]["result"]["data"]["role"] = "report"
        cases.append(changed)
        changed = deepcopy(roles); changed[2]["reads"][0]["receipt"]["toolResultDigest"] = "0"*64
        cases.append(changed)
        changed = deepcopy(roles); changed[3]["reads"][0]["receipt"]["toolDispatchId"] = roles[0]["reads"][0]["receipt"]["toolDispatchId"]
        cases.append(changed)
        for item in cases:
            with self.subTest(case=cases.index(item)):
                with self.assertRaises(AnalysisContractError):
                    candidate.check(fixed, item)

    def test_wrong_number_missing_cell_or_observation_is_never_zero(self):
        fixed, roles = fixture()
        changed = deepcopy(roles); changed[2]["numericClaims"][0]["value"] = 0
        with self.assertRaises(AnalysisContractError):
            candidate.check(fixed, changed)
        changed = deepcopy(roles)
        row_read = changed[2]["reads"][-1]
        row_read["result"]["data"]["payload"]["row"]["metrics"][
            "sampleGmvLowerCents"]["value"] = None
        refresh(row_read)
        changed[2]["numericClaims"][0]["value"] = 0
        with self.assertRaises(AnalysisContractError):
            candidate.check(fixed, changed)
        changed = deepcopy(roles)
        summary = next(read for read in changed[2]["reads"]
            if read["receipt"]["mode"] == "summary")
        summary["result"]["data"]["payload"]["observationCoverage"][
            "baselineDatePresent"] = False
        refresh(summary)
        with self.assertRaises(AnalysisContractError):
            candidate.check(fixed, changed)

    def test_unknown_budget_and_foreign_row_reference_fail(self):
        fixed, roles = fixture()
        changed = deepcopy(roles)
        budget = next(read for read in changed[4]["reads"]
            if read["receipt"]["toolName"] == execution.TOOL_ORDER[2])
        budget["result"]["data"]["status"] = "available"
        refresh(budget)
        with self.assertRaises(AnalysisContractError):
            candidate.check(fixed, changed)
        changed = deepcopy(roles)
        changed[2]["numericClaims"][0]["reference"]["rowId"] = "0"*64
        with self.assertRaises(AnalysisContractError):
            candidate.check(fixed, changed)

    def test_market_role_must_read_summary_and_first_pages_from_same_job(self):
        fixed, roles = fixture()
        changed = deepcopy(roles)
        changed[2]["reads"] = [item for item in changed[2]["reads"]
            if item["receipt"]["mode"] != "summary"]
        with self.assertRaises(AnalysisContractError):
            candidate.check(fixed, changed)
        changed = deepcopy(roles)
        changed[2]["reads"] = [item for item in changed[2]["reads"]
            if not (item["receipt"]["mode"] == "page"
                and item["provider"]["arguments"]["view"] == "rank_entry_exit")]
        with self.assertRaises(AnalysisContractError):
            candidate.check(fixed, changed)
        changed = deepcopy(roles)
        row = changed[2]["reads"][-1]
        row["provider"]["arguments"]["rowId"] = "0"*64
        row["provider"]["calls"][0]["arguments"]["rowId"] = "0"*64
        with self.assertRaises(AnalysisContractError):
            candidate.check(fixed, changed)

    def test_unknown_source_status_cannot_be_numeric_zero_or_a_read(self):
        fixed, roles = fixture()
        changed = deepcopy(roles)
        package = next(item for item in changed[0]["reads"]
            if item["receipt"]["toolName"] == execution.TOOL_ORDER[0])
        package["result"]["data"]["status"] = "unknown"
        package["result"]["data"]["payload"] = {"value": 0}
        refresh(package)
        with self.assertRaises(AnalysisContractError):
            candidate.check(fixed, changed)
