"""Separated proposals preserve identity and never manufacture authority."""
from unittest import TestCase

from business_analysis.contracts import AnalysisContractError, digest

from . import business_market_v2_authority_adoption_contract as adoption
from .test_business_market_v2_cost_candidate import build as candidate
from .test_business_market_v2_paid_authority_contract import source, approval


def plan(**root_changes):
    return {"planId": "c" * 64, "executionRoot": {
        "executionReportId": "report-1",
        "ownerEmail": "admin@example.invalid", **root_changes}}


def prepared(**changes):
    options = {"plan_receipt": plan(), "candidate": candidate(),
        "cost_ledger_id": "d" * 64, "source": source(),
        "approval": approval(), "owner_version": 3,
        "at_utc": "2026-09-25T01:00:00Z"}
    return adoption.build(**{**options, **changes})


class MarketV2AuthorityAdoptionContractTests(TestCase):
    def test_two_independent_pending_bytes_bind_plan_model_owner_and_cap(self):
        value = prepared()
        rate, cap = value["rate"], value["cap"]
        self.assertEqual(rate["executionReportId"], "report-1")
        self.assertEqual(rate["ownerVersion"], 3)
        self.assertEqual(rate["modelVersion"], 3)
        self.assertEqual(rate["rateProposalDigest"], digest({key: item for
            key, item in rate.items() if key != "rateProposalDigest"}))
        self.assertEqual(cap["rateProposalDigest"], rate["rateProposalDigest"])
        self.assertEqual(cap["approvedCapClaimCents"], 1200)
        for item in (value, rate, cap):
            self.assertFalse(item["providerCallsAllowed"])
        for key in ("sourceIndependentlyVerified", "fxIndependentlyVerified",
                "allBillingCategoriesVerified"):
            self.assertFalse(rate[key])
        self.assertFalse(cap["humanApprovalVerified"])

    def test_changed_rate_requires_new_cap_proposal(self):
        original = prepared()
        changed = source(categoriesEvidenceDigest="e" * 64)
        next_value = prepared(source=changed)
        self.assertNotEqual(original["rate"]["rateProposalDigest"],
            next_value["rate"]["rateProposalDigest"])
        self.assertNotEqual(original["cap"]["capProposalDigest"],
            next_value["cap"]["capProposalDigest"])

    def test_unknown_billable_category_or_expiry_refuses(self):
        for changed in (
                {"source": source(chargeCategories=["input_tokens",
                    "output_tokens", "tool_calls"])},
                {"source": source(expiresAtUtc="2026-09-25T00:59:59Z")},
                {"approval": approval(approvedCapCents=1201)},
                {"approval": approval(expiresAtUtc="2026-09-25T00:59:59Z")},
                {"plan_receipt": plan(ownerEmail="other@example.invalid")},
                {"plan_receipt": {"planId": "f" * 64,
                    "executionRoot": plan()["executionRoot"]}},
                {"owner_version": True},
                {"cost_ledger_id": "not-a-digest"},
                {"approval": None}):
            with self.subTest(changed=changed), self.assertRaises(
                    AnalysisContractError):
                prepared(**changed)
