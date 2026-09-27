"""Integer reservation boundaries for a future authorized paid market run."""
from copy import deepcopy
import unittest

from . import market_model_cost_envelope as cost
from .contracts import AnalysisContractError, digest
from .screening_package import ROLES


def tariff(**changes):
    value = {"schemaVersion": cost.TARIFF_SCHEMA,
        "providerId": "configured-provider", "modelId": "configured-model",
        "modelVersion": 3, "currency": "CNY",
        "inputNanoYuanPerMillionTokens": 1_000_000_000,
        "outputNanoYuanPerMillionTokens": 2_000_000_000,
        "rateSourceDigest": "a" * 64,
        "effectiveAtUtc": "2026-09-25T00:00:00Z",
        "expiresAtUtc": "2026-10-01T00:00:00Z"}
    return {**value, **changes}


def jobs(**changes):
    return [{"role": role, "maxRounds": 2,
        "maxInputTokensPerRound": 1_000_000,
        "maxOutputTokensPerRound": 100_000, **changes}
        for role in ROLES]


def reserve(*, rate=None, plan=None, cap=1200, **kwargs):
    return cost.reserve(rate or tariff(), plan or jobs(),
        model_id="configured-model", model_version=3,
        at_utc="2026-09-25T01:00:00Z",
        approved_cap_cents=cap, approval_digest="b" * 64, **kwargs)


class MarketModelCostEnvelopeTests(unittest.TestCase):
    def test_five_roles_round_up_each_call_and_never_grant_permission(self):
        value = reserve()
        self.assertEqual(value["reservationRequiredCents"], 1200)
        self.assertEqual(value["unreservedHeadroomCents"], 0)
        self.assertEqual([row["role"] for row in value["jobs"]], list(ROLES))
        self.assertTrue(all(row["maxCostCentsPerRound"] == 120
            and row["reservationCents"] == 240 for row in value["jobs"]))
        self.assertEqual(value["envelopeDigest"], digest({key: item
            for key, item in value.items() if key != "envelopeDigest"}))
        for key in ("tariffAuthorityVerified", "humanApprovalAuthorityVerified",
                "providerUsageCategoryCoverageVerified",
                "fundsReservedInDurableLedger", "providerCallsAllowed",
                "currencyConversionVerified"):
            self.assertFalse(value[key])
        tiny = reserve(plan=jobs(maxInputTokensPerRound=1,
            maxOutputTokensPerRound=1), cap=10)
        self.assertEqual(tiny["reservationRequiredCents"], 10)
        self.assertTrue(all(row["maxCostCentsPerRound"] == 1
            for row in tiny["jobs"]))

    def test_unknown_expired_or_unapproved_costs_fail_closed(self):
        cases = [
            {"rate": tariff(inputNanoYuanPerMillionTokens=0)},
            {"rate": tariff(currency="USD")},
            {"rate": tariff(modelId="other")},
            {"rate": tariff(rateSourceDigest="not-a-digest")},
            {"rate": tariff(expiresAtUtc="2026-09-25T00:00:00Z")},
            {"rate": tariff(expiresAtUtc="2026-11-30T00:00:00Z")},
            {"cap": 1199},
            {"plan": jobs()[:-1]},
            {"plan": list(reversed(jobs()))},
            {"plan": jobs(maxRounds=21)},
            {"chargeable_tools": True},
        ]
        for change in cases:
            with self.subTest(change=change), self.assertRaises(
                    AnalysisContractError):
                reserve(**change)

    def test_observed_usage_is_bounded_but_not_billing_authority(self):
        envelope = reserve()
        observed = cost.reconcile_usage(envelope, tariff(), [
            {"role": "commerce", "round": 1,
             "inputTokens": 100_000, "outputTokens": 10_000},
            {"role": "promotion", "round": 2,
             "inputTokens": 0, "outputTokens": 0},
        ])
        self.assertEqual(observed["observedCostCents"], 12)
        self.assertFalse(observed["providerUsageAuthenticated"])
        self.assertFalse(observed["actualProviderChargeVerified"])
        self.assertFalse(observed["fundsReleasedOrCharged"])
        for bad in ([{"role": "commerce", "round": 1,
                    "inputTokens": 1_000_001, "outputTokens": 0}],
                [{"role": "commerce", "round": 1,
                    "inputTokens": 1, "outputTokens": 1}] * 2,
                [{"role": "commerce", "round": 3,
                    "inputTokens": 1, "outputTokens": 1}]):
            with self.assertRaises(AnalysisContractError):
                cost.reconcile_usage(envelope, tariff(), bad)
        forged = deepcopy(envelope)
        forged["providerCallsAllowed"] = True
        forged["envelopeDigest"] = digest({key: item for key, item in
            forged.items() if key != "envelopeDigest"})
        with self.assertRaises(AnalysisContractError):
            cost.reconcile_usage(forged, tariff(), [])
