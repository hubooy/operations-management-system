"""Read-only owner preflight cannot create jobs or paid dispatches."""
from unittest import TestCase
from unittest.mock import patch

from django.test import override_settings

from . import business_market_v6_paused_topology_contract as contract
from . import business_market_v6_paused_topology_owner as owner
from .policy import AiError
from .test_business_market_v2_read_plan_v6_contract import source as source_plan
from .test_business_market_v6_paused_topology_contract import CLIENT, vector


class PausedTopologyOwnerTests(TestCase):
    def test_default_closed_before_any_owner_query(self):
        with override_settings(DJANGO_ENVIRONMENT="test",
                DJANGO_PROCESS_ROLE="ai_reader",
                AI_MARKET_V6_PAUSED_TOPOLOGY_PREPARE_ENABLED=False), \
                patch.object(owner, "_actor") as actor:
            with self.assertRaises(AiError):
                owner.prepare("source-report", CLIENT, None)
            actor.assert_not_called()

    def test_verified_read_only_proposal_rechecks_every_root_without_writes(self):
        proposal, cost, actor, quota = vector()
        source_id = proposal["snapshot"]["sourceExecutionReportId"]
        plan = source_plan()
        gate = {"planId": plan["planId"],
            "costLedgerId": cost["ledgerId"], "reservedCents": 0,
            "providerCallsAllowed": False}
        principal = object()
        with override_settings(DJANGO_ENVIRONMENT="test",
                DJANGO_PROCESS_ROLE="development",
                AI_MARKET_V6_PAUSED_TOPOLOGY_PREPARE_ENABLED=True), \
                patch.object(owner, "_actor", return_value=actor) as current, \
                patch.object(owner.source_plan, "prepare",
                    return_value=proposal) as old_prepare, \
                patch.object(owner.plans, "read", return_value=plan) as plans, \
                patch.object(owner.costs, "read", return_value=cost) as costs, \
                patch.object(owner.paid_gate, "inspect",
                    return_value=gate) as paid, \
                patch.object(owner, "_quota", return_value=quota) as quota_read, \
                patch.object(owner, "_unused", return_value=True) as unused, \
                patch("ai_assistant.provider.turn") as provider:
            result = owner.prepare(source_id, CLIENT, principal)
            self.assertTrue(result["candidateOnly"])
            self.assertFalse(result["jobsPersisted"])
            self.assertFalse(result["providerCallsAllowed"])
            self.assertEqual(result["snapshot"]["sourceExecutionReportId"],
                source_id)
            self.assertEqual(old_prepare.call_count, 1)
            self.assertEqual(current.call_count, 2)
            self.assertEqual(plans.call_count, 2)
            self.assertEqual(costs.call_count, 2)
            self.assertEqual(paid.call_count, 2)
            self.assertEqual(quota_read.call_count, 2)
            self.assertEqual(unused.call_count, 2)
            provider.assert_not_called()

    def test_late_account_version_change_rejects_the_whole_candidate(self):
        proposal, cost, actor, quota = vector()
        source_id = proposal["snapshot"]["sourceExecutionReportId"]
        plan = source_plan()
        gate = {"planId": plan["planId"],
            "costLedgerId": cost["ledgerId"], "reservedCents": 0,
            "providerCallsAllowed": False}
        with override_settings(DJANGO_ENVIRONMENT="test",
                DJANGO_PROCESS_ROLE="development",
                AI_MARKET_V6_PAUSED_TOPOLOGY_PREPARE_ENABLED=True), \
                patch.object(owner, "_actor", side_effect=[actor,
                    {**actor, "version": actor["version"] + 1}]), \
                patch.object(owner.source_plan, "prepare",
                    return_value=proposal), \
                patch.object(owner.plans, "read", return_value=plan), \
                patch.object(owner.costs, "read", return_value=cost), \
                patch.object(owner.paid_gate, "inspect", return_value=gate), \
                patch.object(owner, "_quota", return_value=quota), \
                patch.object(owner, "_unused", return_value=True):
            with self.assertRaises(AiError):
                owner.prepare(source_id, CLIENT, object())
