"""The pending 0065 cost row must never authorize a network provider turn."""
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from . import business_market_v2_paid_gate as gate
from .policy import AiError


def job(tools=(), workflow_id="flow-market"):
    return SimpleNamespace(allowed_tools_json=json.dumps(list(tools)),
        workflow_run_id=workflow_id)


class MarketV2PaidGateTests(SimpleTestCase):
    def test_market_family_is_recognized_even_without_business_context(self):
        report=SimpleNamespace(snapshot_json='{"executionProfile":'
            '"business-agent-screening-promotion-market-synthetic-v4"}')
        with patch.object(gate.m.AiReportRun.objects,"filter") as rows:
            rows.return_value.only.return_value.first.return_value=report
            self.assertTrue(gate.is_market_job(job()))
            rows.return_value.only.return_value.first.return_value=None
            self.assertTrue(gate.is_market_job(job(
                ["get_business_market_v2_keyword_sku"])))
            self.assertFalse(gate.is_market_job(job(["unrelated_tool"])))

    def test_legacy_promotion_and_generic_agents_are_not_intercepted(self):
        for profile in ("business-agent-screening-promotion-reference-v1", None):
            with self.subTest(profile=profile), patch.object(
                    gate.m.AiReportRun.objects,"filter") as rows, \
                    override_settings(AI_MARKET_V2_PAID_RUNTIME_ENABLED=False):
                rows.return_value.only.return_value.first.return_value=(
                    SimpleNamespace(snapshot_json=json.dumps(
                        {"executionProfile":profile})) if profile else None)
                ordinary=job(["get_business_promotion_screening_package"])
                self.assertFalse(gate.is_market_job(ordinary))
                self.assertIsNone(gate.before_reservation(ordinary))

    def test_malformed_tool_policy_fails_closed_without_type_error(self):
        malformed=SimpleNamespace(allowed_tools_json='[{"name":"tool"}]',
            workflow_run_id=None)
        with self.assertRaises(AiError) as failure:
            gate.before_reservation(malformed)
        self.assertEqual(failure.exception.code,"market_v2_paid_gate_closed")

    def test_each_reservation_refuses_even_when_flag_was_enabled(self):
        for enabled,code in ((False,"market_v2_paid_runtime_disabled"),
                (True,"market_v2_paid_reservation_unavailable")):
            with self.subTest(enabled=enabled), override_settings(
                    AI_MARKET_V2_PAID_RUNTIME_ENABLED=enabled), patch.object(
                    gate,"is_market_job",return_value=True):
                with self.assertRaises(AiError) as failure:
                    gate.before_reservation(job())
                self.assertEqual(failure.exception.code,code)
        with patch.object(gate,"is_market_job",return_value=False):
            self.assertIsNone(gate.before_reservation(job()))

    def test_read_only_preflight_never_promotes_claimed_cap_to_permission(self):
        principal=SimpleNamespace(email="test@example.invalid")
        plan={"planId":"a"*64}
        cost={"planId":"a"*64,"ledgerId":"ledger", "requiredCents":25,
            "ledgerReservedCents":0,"providerCallsAllowed":False}
        with patch.object(gate,"current_principal"), patch(
                "ai_assistant.business_market_v2_execution_plan.read",
                return_value=plan), patch(
                "ai_assistant.business_market_v2_cost_admission.read",
                return_value=cost) as read:
            result=gate.inspect("report",principal)
            read.assert_called_once_with("a"*64,principal)
            self.assertEqual(result["requiredCents"],25)
            self.assertEqual(result["reservedCents"],0)
            self.assertFalse(result["providerCallsAllowed"])
            self.assertTrue(all(result[key] is False for key in (
                "tariffAuthorityVerified","currencyConversionVerified",
                "extraChargeCategoryCoverageVerified",
                "humanApprovalAuthorityVerified",
                "durableProviderReservationVerified",
                "unknownOutcomeNonRetryVerified")))
            for drift in ({"ledgerReservedCents":25},
                    {"providerCallsAllowed":True},{"planId":"b"*64}):
                read.return_value={**cost,**drift}
                with self.assertRaises(AiError):
                    gate.inspect("report",principal)
