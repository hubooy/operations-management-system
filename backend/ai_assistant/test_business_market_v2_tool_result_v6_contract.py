"""A replay match is useful, but never substitutes for a protected read."""
from copy import deepcopy
from unittest import TestCase

from business_analysis.contracts import AnalysisContractError, canonical, digest

from . import business_market_v2_tool_result_v6_contract as contract
from . import business_market_v2_read_plan_v6_contract as v6
from .test_business_market_v2_read_plan_v6_contract import cost, source
from .test_business_market_v2_transport_contract import args, result


def fixture(mode="summary", role="market_b2b"):
    original = source()
    proposal = v6.build(original, cost(), "market-v6-report-1",
        "market-v6-flow-1")
    root = original["executionRoot"]
    call = {"schemaVersion":
        "business-market-v2-fifth-read-injected-call-v1",
        "admittedReportId": root["admittedReportId"],
        "jobId": v6._job_id("market-v6-report-1", role),
        "providerDispatchId": "future-provider-1",
        "providerCallId": "call-1", "role": role,
        "marketManifestDigest": root["manifestDigest"],
        "marketContextDigest": root["marketContextDigest"]}
    arguments = {**args(mode), "reportId": root["admittedReportId"],
        "marketContextDigest": root["marketContextDigest"]}
    observed = {**result(), "reportId": root["admittedReportId"],
        "role": role, "mode": mode, "identityClaimDigest": digest(call),
        "jobIdClaim": call["jobId"],
        "providerDispatchIdClaim": call["providerDispatchId"],
        "providerCallIdClaim": call["providerCallId"],
        "marketManifestDigest": root["manifestDigest"]}
    observed["resultDigest"] = digest({key: value for key, value
        in observed.items() if key != "resultDigest"})
    return proposal, original, call, arguments, observed


class ToolResultV6ContractTests(TestCase):
    def test_fresh_replay_binds_proposed_slot_but_grants_nothing(self):
        inputs = fixture()
        calls = []

        def replay(call, arguments):
            calls.append((call, arguments))
            return deepcopy(inputs[4])

        checked = contract.check(*inputs, replay)
        self.assertEqual(calls, [(inputs[2], inputs[3])])
        self.assertEqual(checked["newReportId"], "market-v6-report-1")
        self.assertEqual(checked["proposedJobId"], inputs[2]["jobId"])
        self.assertEqual(checked["toolResultDigest"], digest(inputs[4]))
        self.assertTrue(checked["replayCallbackMatched"])
        self.assertTrue(checked["candidateOnly"])
        for field in ("protectedRowsIndependentlyLoaded",
                "providerResponseAuthenticated", "toolDispatchPersisted",
                "agentReadPersisted", "numericCitationAllowed",
                "humanReviewApproved", "reportPublishAuthorized"):
            self.assertFalse(checked[field], field)

    def test_cross_report_role_job_provider_or_source_fails(self):
        base = fixture()
        mutations = []
        for index, key, value in ((0, "snapshotDigest", "0" * 64),
                (2, "jobId", "other-job"),
                (2, "providerDispatchId", "market-v6-report-1"),
                (2, "marketManifestDigest", "0" * 64),
                (4, "jobIdClaim", "other-job"),
                (4, "providerCallIdClaim", "other-call"),
                (4, "role", "report")):
            changed = deepcopy(base)
            changed[index][key] = value
            if index == 4:
                changed[index]["resultDigest"] = digest({name: item
                    for name, item in changed[index].items()
                    if name != "resultDigest"})
            mutations.append((key, changed))
        changed = deepcopy(base)
        changed[1]["executionRoot"]["ownerEmail"] = "other@example.com"
        mutations.append(("source owner", changed))
        for name, item in mutations:
            with self.subTest(name=name), self.assertRaises(AnalysisContractError):
                contract.check(*item, lambda *_: item[4])

    def test_no_synthetic_or_claimed_persisted_read(self):
        base = fixture()
        cases = []
        for field, value in (("syntheticOnly", True),
                ("reportPersisted", True),
                ("externalProviderCalled", True)):
            changed = deepcopy(base)
            changed[0]["snapshot"][field] = value
            changed[0]["snapshotJson"] = canonical(changed[0]["snapshot"])
            changed[0]["snapshotDigest"] = digest(changed[0]["snapshot"])
            cases.append((field, changed))
        for field in ("persistedRead", "sameJobProviderPersisted",
                "registeredTool", "authorityVerified"):
            changed = deepcopy(base)
            changed[4][field] = True
            changed[4]["resultDigest"] = digest({key: value for key, value
                in changed[4].items() if key != "resultDigest"})
            cases.append((field, changed))
        for name, item in cases:
            with self.subTest(name=name), self.assertRaises(AnalysisContractError):
                contract.check(*item, lambda *_: item[4])

    def test_changed_arguments_or_replay_result_fails_without_fallback(self):
        base = fixture("page")
        changed = deepcopy(base)
        changed[3]["offset"] = True
        with self.assertRaises(AnalysisContractError):
            contract.check(*changed, lambda *_: changed[4])
        changed = deepcopy(base)
        changed[4]["payload"] = {"rows": [{"rowId": "a" * 64}]}
        changed[4]["resultDigest"] = digest({key: value for key, value
            in changed[4].items() if key != "resultDigest"})
        with self.assertRaises(AnalysisContractError):
            contract.check(*changed, lambda *_: base[4])
        with self.assertRaises(AnalysisContractError):
            contract.check(*base, lambda *_: {**base[4], "reportId": "other"})

    def test_capacity_and_no_callback_fail_closed(self):
        base = fixture()
        changed = deepcopy(base)
        changed[4]["payload"] = {"text": "中" * 13000}
        changed[4]["resultDigest"] = digest({key: value for key, value
            in changed[4].items() if key != "resultDigest"})
        with self.assertRaises(AnalysisContractError):
            contract.check(*changed, lambda *_: changed[4])
        with self.assertRaises(AnalysisContractError):
            contract.check(*base, None)
