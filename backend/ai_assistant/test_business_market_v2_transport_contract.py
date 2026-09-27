from copy import deepcopy
from unittest import TestCase

from business_analysis.contracts import AnalysisContractError, digest
from . import business_market_v2_transport_contract as contract
from .test_business_market_v2_fifth_read_contract import call


def args(mode="summary"):
    value = {"reportId": "admitted-report", "marketContextDigest": "b"*64,
        "mode": mode}
    if mode == "page":
        value.update(view="price_band", offset=0, limit=20)
    if mode == "row":
        value.update(view="rank_entry_exit", rowIndex=0, rowId="c"*64)
    return value


def result():
    value = {"schemaVersion": contract.RESULT_SCHEMA,
        "surface": contract.SURFACE, "profile": contract.PROFILE,
        "toolName": contract.TOOL, "reportId": "admitted-report",
        "sourceReportId": "source-report", "role": "market_b2b",
        "mode": "summary", "identityClaimDigest": "a"*64,
        "jobIdClaim": "future-job", "providerDispatchIdClaim": "future-provider",
        "providerCallIdClaim": "call-1", "marketManifestDigest": "d"*64,
        "payload": {"tables": []}, "citationBases": [],
        "sourceResultDigest": "e"*64,
        "numericReferenceRequiredFields": ["metric", "field"],
        "serverFullMarketMaterialVerified": True,
        "sameJobProviderPersisted": False, "persistedRead": False,
        "registeredTool": False, "authorityVerified": False}
    value["resultDigest"] = digest(value)
    return value


class MarketV2TransportContractTests(TestCase):
    def test_exact_surface_profile_and_three_argument_modes(self):
        proposed = contract.definition()
        self.assertFalse(proposed["registered"])
        self.assertEqual(proposed["execution"]["timeoutMs"], 12000)
        self.assertEqual(proposed["execution"]["maxResultCharacters"], 38000)
        self.assertEqual([branch["properties"]["mode"]["enum"][0]
            for branch in proposed["inputSchema"]["oneOf"]],
            ["summary", "page", "row"])
        self.assertEqual(proposed["definitionDigest"], digest({key: value
            for key, value in proposed.items() if key != "definitionDigest"}))
        for mode in ("summary", "page", "row"):
            self.assertEqual(contract.request(contract.SURFACE, contract.PROFILE,
                contract.TOOL, call(), args(mode))[1], args(mode))
        for surface, profile, tool in (("other", contract.PROFILE, contract.TOOL),
                (contract.SURFACE, "v1", contract.TOOL),
                (contract.SURFACE, contract.PROFILE, "other")):
            with self.assertRaises(AnalysisContractError):
                contract.request(surface, profile, tool, call(), args())
        for bad in ({**args(), "extra": True},
                {**args("page"), "offset": True},
                {**args("row"), "rowId": "bad"},
                {**args("row"), "offset": 0},
                {**args(), "reportId": "other"}):
            with self.assertRaises(AnalysisContractError):
                contract.request(contract.SURFACE, contract.PROFILE,
                    contract.TOOL, call(), bad)

    def test_complete_capacity_rejects_utf8_and_utf16_without_truncation(self):
        normal = result()
        self.assertEqual(contract.result(normal), normal)
        huge = deepcopy(normal)
        huge["payload"]["text"] = "中" * 13000
        huge["resultDigest"] = digest({key: value for key, value in huge.items()
            if key != "resultDigest"})
        with self.assertRaises(AnalysisContractError):
            contract.result(huge)
        emoji = deepcopy(normal)
        emoji["payload"]["text"] = "😀" * 9500
        emoji["resultDigest"] = digest({key: value for key, value in emoji.items()
            if key != "resultDigest"})
        with self.assertRaises(AnalysisContractError):
            contract.result(emoji)
        forged = {**normal, "persistedRead": True}
        forged["resultDigest"] = digest({key: value for key, value in forged.items()
            if key != "resultDigest"})
        with self.assertRaises(AnalysisContractError):
            contract.result(forged)

    def test_deadline_rejects_at_boundary_and_backwards_clock(self):
        now = [10.0]
        fence = contract.Deadline(lambda: now[0])
        now[0] = 21.999
        self.assertGreater(fence.check(), 11900)
        now[0] = 22.0
        with self.assertRaises(AnalysisContractError):
            fence.check()
        now[0] = 9.0
        with self.assertRaises(AnalysisContractError):
            fence.check()
