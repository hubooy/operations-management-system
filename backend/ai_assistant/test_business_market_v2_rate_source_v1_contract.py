"""Synthetic transport/rate vectors never become paid-call authority."""
from copy import deepcopy
from unittest import TestCase

from business_analysis.contracts import AnalysisContractError, canonical, digest

from . import business_market_v2_rate_source_v1_contract as contract


NOW = "2026-09-26T01:00:00Z"


def model():
    return {"id": "synthetic-model", "version": 3,
        "status": "enabled", "modelType": "text",
        "protocol": "openai_compatible", "modelName": "fictional-v1",
        "baseUrl": "https://MODEL.example.invalid:443/v1/",
        "generationOptionsJson": canonical({"temperature": 0.2}),
        "maxTokens": 4096, "maxToolRounds": 5,
        "maxTotalToolCalls": 15, "timeoutMs": 60000,
        "reasoningMode": "auto", "temperatureMilli": 200}


def source(current=None, currency="CNY"):
    current = current or model()
    fx = ({"sourceKind": "none", "sourceUrl": None,
        "observedAtUtc": None, "numerator": 1,
        "denominator": 1, "evidenceDigest": None}
        if currency == "CNY" else
        {"sourceKind": "synthetic_test_vector",
         "sourceUrl": contract.TEST_FX_URL,
         "observedAtUtc": "2026-09-26T00:30:00Z",
         "numerator": 7, "denominator": 2,
         "evidenceDigest": "d" * 64})
    return {"schemaVersion": contract.SOURCE_SCHEMA,
        "sourceKind": "synthetic_test_vector",
        "rateSourceUrl": contract.TEST_RATE_URL,
        "sourceObservedAtUtc": "2026-09-26T00:00:00Z",
        "effectiveAtUtc": "2026-09-25T00:00:00Z",
        "expiresAtUtc": "2026-09-27T00:00:00Z",
        "providerId": "synthetic-provider",
        "modelTransportFingerprint":
            contract.model_transport(current)["fingerprint"],
        "sourceCurrency": currency,
        "inputNanoPerMillionTokens": 1_000_000_001,
        "outputNanoPerMillionTokens": 2_000_000_001,
        "billableCategories": list(contract.CATEGORIES),
        "unknownChargeCategories": [], "toolsChargeable": False,
        "billingCategoriesEvidenceDigest": "b" * 64,
        "sourceBytesDigest": "c" * 64, "fx": fx}


class MarketV2RateSourceContractTests(TestCase):
    def test_fingerprint_normalizes_origin_path_and_options(self):
        first = contract.model_transport(model())
        self.assertEqual(first["model"]["canonicalBaseUrl"],
            "https://model.example.invalid/v1")
        self.assertEqual(first["model"]["generationOptionsDigest"],
            digest({"temperature": 0.2}))
        equivalent = model()
        equivalent["baseUrl"] = "https://model.example.invalid/v1"
        self.assertEqual(contract.model_transport(equivalent)["fingerprint"],
            first["fingerprint"])
        self.assertFalse(first["currentModelOwnedRead"])
        self.assertFalse(first["providerIdentityVerified"])
        self.assertFalse(first["credentialAccountVerified"])
        self.assertEqual(first["model"]["timeoutMs"], 60000)
        self.assertEqual(first["model"]["reasoningMode"], "auto")
        self.assertEqual(first["model"]["temperatureMilli"], 200)

    def test_synthetic_cny_and_usd_math_stays_closed(self):
        with self.assertRaises(AnalysisContractError):
            contract.admit_test_vector(model(), source(), at_utc=NOW)
        cny = contract.admit_test_vector(model(), source(), at_utc=NOW,
            allow_synthetic_test_vector=True)
        self.assertEqual(cny["inputNanoYuanPerMillionTokens"], 1_000_000_001)
        self.assertEqual(cny["billableCategories"], contract.CATEGORIES)
        self.assertEqual(cny["admissionDigest"], digest({key: value
            for key, value in cny.items() if key != "admissionDigest"}))
        usd = contract.admit_test_vector(model(), source(currency="USD"),
            at_utc=NOW, allow_synthetic_test_vector=True)
        self.assertEqual(usd["inputNanoYuanPerMillionTokens"],
            (1_000_000_001 * 7 + 1) // 2)
        self.assertEqual(usd["outputNanoYuanPerMillionTokens"],
            (2_000_000_001 * 7 + 1) // 2)
        for value in (cny, usd):
            self.assertTrue(value["testVectorOnly"])
            for flag in ("currentModelOwnedRead", "sourceIndependentlyVerified",
                    "credentialAccountVerified", "fxIndependentlyVerified",
                    "allBillingCategoriesVerified",
                    "tariffAuthorityVerified", "humanCapApproved",
                    "providerCallsAllowed"):
                self.assertFalse(value[flag], flag)

    def test_model_transport_changes_or_secret_field_reject(self):
        original = model()
        evidence = source(original)
        changes = {"protocol": "anthropic", "modelName": "other-v1",
            "version": 4, "baseUrl": "https://model.example.invalid/v2",
            "generationOptionsJson": canonical({"temperature": 0.3}),
            "timeoutMs": 61000, "reasoningMode": "disabled",
            "temperatureMilli": 350}
        for field, value in changes.items():
            changed = deepcopy(original)
            changed[field] = value
            with self.subTest(field=field), self.assertRaises(
                    AnalysisContractError):
                contract.admit_test_vector(changed, evidence, at_utc=NOW,
                    allow_synthetic_test_vector=True)
        with self.assertRaises(AnalysisContractError):
            contract.model_transport({**original, "apiKey": "never-import"})
        for bad in ("https://user:pass@model.example.invalid/v1",
                "https://model.example.invalid/v1?token=x",
                "https://model.example.invalid/v1/../v2",
                "http://model.example.invalid/v1"):
            changed = deepcopy(original)
            changed["baseUrl"] = bad
            with self.subTest(url=bad), self.assertRaises(
                    AnalysisContractError):
                contract.model_transport(changed)
        changed = deepcopy(original)
        changed["generationOptionsJson"] = '{ "temperature": 0.2 }'
        with self.assertRaises(AnalysisContractError):
            contract.model_transport(changed)
        for field, value in (("timeoutMs", 2999),
                ("timeoutMs", 600001), ("reasoningMode", "unbounded"),
                ("temperatureMilli", 2001), ("maxTokens", 127),
                ("maxToolRounds", 63), ("maxTotalToolCalls", 301)):
            changed = deepcopy(original)
            changed[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(
                    AnalysisContractError):
                contract.model_transport(changed)
        anthropic = deepcopy(original)
        anthropic["protocol"] = "anthropic"
        anthropic["temperatureMilli"] = 1001
        with self.assertRaises(AnalysisContractError):
            contract.model_transport(anthropic)

    def test_official_claim_arbitrary_endpoint_and_billing_gap_reject(self):
        base = source()
        cases = []
        changed = deepcopy(base)
        changed["sourceKind"] = "official_provider_quote"
        changed["rateSourceUrl"] = "https://model.example.invalid/v1/pricing"
        cases.append(changed)
        for field, value in (("billableCategories",
                ["input_tokens", "output_tokens", "cached_input_tokens"]),
                ("unknownChargeCategories", ["tool_calls"]),
                ("toolsChargeable", True),
                ("billingCategoriesEvidenceDigest", "bad"),
                ("sourceBytesDigest", "bad"),
                ("rateSourceUrl", "https://elsewhere.example.invalid/price")):
            changed = deepcopy(base)
            changed[field] = value
            cases.append(changed)
        for item in cases:
            with self.subTest(item=item), self.assertRaises(
                    AnalysisContractError):
                contract.admit_test_vector(model(), item, at_utc=NOW,
                    allow_synthetic_test_vector=True)

    def test_stale_source_expired_rate_and_malformed_fx_reject(self):
        for field, value in (("sourceObservedAtUtc", "2026-09-24T00:00:00Z"),
                ("sourceObservedAtUtc", "2026-09-27T00:00:00Z"),
                ("expiresAtUtc", "2026-09-26T01:00:00Z"),
                ("effectiveAtUtc", "2026-09-27T00:00:00Z")):
            changed = source()
            changed[field] = value
            with self.subTest(field=field), self.assertRaises(
                    AnalysisContractError):
                contract.admit_test_vector(model(), changed, at_utc=NOW,
                    allow_synthetic_test_vector=True)
        usd = source(currency="USD")
        for field, value in (("denominator", 0),
                ("numerator", True),
                ("sourceUrl", "https://fx.example.invalid/other"),
                ("observedAtUtc", "2026-09-24T00:00:00Z"),
                ("evidenceDigest", "missing")):
            changed = deepcopy(usd)
            changed["fx"][field] = value
            with self.subTest(field=field), self.assertRaises(
                    AnalysisContractError):
                contract.admit_test_vector(model(), changed, at_utc=NOW,
                    allow_synthetic_test_vector=True)
        cny = source()
        cny["fx"]["numerator"] = 2
        with self.assertRaises(AnalysisContractError):
            contract.admit_test_vector(model(), cny, at_utc=NOW,
                allow_synthetic_test_vector=True)
