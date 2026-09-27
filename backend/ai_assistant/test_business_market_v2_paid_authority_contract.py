"""Synthetic rate/FX/cap shape is not a real authority."""
from copy import deepcopy
from unittest import TestCase

from business_analysis.contracts import AnalysisContractError

from . import business_market_v2_paid_authority_contract as authority
from .test_business_market_v2_cost_candidate import build


def source(**changes):
    value = {"schemaVersion": authority.SOURCE_SCHEMA,
        "providerId": "configured-provider", "modelId": "configured-model",
        "modelVersion": 3, "sourceCurrency": "CNY",
        "inputNanoPerMillionTokens": 1_000_000_000,
        "outputNanoPerMillionTokens": 2_000_000_000,
        "cnyFxNumerator": 1, "cnyFxDenominator": 1,
        "rateEvidenceDigest": "a" * 64, "fxEvidenceDigest": None,
        "categoriesEvidenceDigest": "c" * 64,
        "chargeCategories": list(authority.CATEGORIES),
        "effectiveAtUtc": "2026-09-25T00:00:00Z",
        "expiresAtUtc": "2026-10-01T00:00:00Z"}
    return {**value, **changes}


def approval(**changes):
    value = {"schemaVersion": authority.APPROVAL_SCHEMA,
        "planId": "c" * 64, "actorEmail": "admin@example.invalid",
        "approvedCapCents": 1200, "approvalEvidenceDigest": "b" * 64,
        "approvedAtUtc": "2026-09-25T00:30:00Z",
        "expiresAtUtc": "2026-09-26T00:30:00Z"}
    return {**value, **changes}


class MarketV2PaidAuthorityContractTests(TestCase):
    def test_fixed_rehearsal_contract_is_closed(self):
        result = authority.build(build(), source(), approval(),
            at_utc="2026-09-25T01:00:00Z")
        self.assertEqual(result["requiredCents"], 1200)
        self.assertEqual(result["approvedCapCents"], 1200)
        self.assertTrue(result["syntheticOnly"])
        self.assertFalse(result["authorityIndependentlyVerified"])
        self.assertFalse(result["providerCallsAllowed"])

    def test_fee_category_fx_and_cap_ambiguity_refuses(self):
        cases = [
            (source(chargeCategories=["input_tokens"]), approval()),
            (source(chargeCategories=[*authority.CATEGORIES, "tool_calls"]),
             approval()),
            (source(sourceCurrency="USD", fxEvidenceDigest=None), approval()),
            (source(cnyFxNumerator=2), approval()),
            (source(inputNanoPerMillionTokens=1), approval()),
            (source(expiresAtUtc="2026-09-25T00:59:59Z"), approval()),
            (source(), approval(approvedCapCents=1201)),
            (source(), approval(actorEmail="ADMIN@example.invalid")),
            (source(), approval(expiresAtUtc="2026-09-25T00:59:59Z")),
        ]
        for rate, cap in cases:
            with self.subTest(rate=rate, cap=cap), self.assertRaises(
                    AnalysisContractError):
                authority.build(build(), rate, cap,
                    at_utc="2026-09-25T01:00:00Z")
