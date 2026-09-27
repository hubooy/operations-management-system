"""No-database contract tests for the shared tool audit consumer.

Surface is audit provenance, not tool authorization. The signed edge owns the
surface/catalog gate; consumer checks the authenticated actor and preserves the
surface verbatim without widening pandas or dataset entry points.
"""
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from . import chat, transport, views
from .policy import AiError, digest


class BusinessBudgetAuditContractTests(SimpleTestCase):
    principal = SimpleNamespace(email="budget-audit@example.invalid", role="admin", scope=None)
    names = ("get_business_budget_directory_v1", "get_business_budget_analysis_table_v1", "get_business_budget_scenarios_v1")

    def body(self, surface="business_agent_budget_v1", name=None, status="started"):
        return {"operation": "tool-audit", "entry": {
            "requestId": "synthetic-budget-request", "invocationId": "synthetic-invocation",
            "providerCallId": "synthetic-provider-call", "actorEmail": self.principal.email,
            "actorRole": self.principal.role, "surface": surface, "toolName": name or self.names[0],
            "status": status, "durationMs": 7, "arguments": {"runId": "evidence-1", "token": "never-store"},
            "result": {"returned": 2}}}

    def test_budget_all_tools_and_statuses_keep_exact_surface_and_audit_binding(self):
        with patch.object(views, "mutation", side_effect=lambda: nullcontext()), patch.object(chat.m.AiToolAuditLogs.objects, "create") as create:
            for name in self.names:
                for status in ("started", "succeeded", "failed"):
                    body = self.body(name=name, status=status)
                    self.assertEqual(views.consumer(body, self.principal, "transport-request"), {"ok": True})
                    written = create.call_args.kwargs
                    self.assertEqual((written["surface"], written["tool_name"], written["status"]), ("business_agent_budget_v1", name, status))
                    self.assertEqual((written["actor_email"], written["actor_role"]), (self.principal.email, self.principal.role))
                    self.assertEqual((written["request_id"], written["invocation_id"], written["provider_call_id"]),
                        ("synthetic-budget-request", "synthetic-invocation", "synthetic-provider-call"))
                    self.assertEqual(written["response_digest"], digest(body["entry"]["result"]))
                    self.assertNotIn("never-store", written["arguments_json"])
            self.assertEqual(create.call_count, 9)

    def test_existing_surface_provenance_remains_unchanged(self):
        with patch.object(views, "mutation", side_effect=lambda: nullcontext()), patch.object(chat.m.AiToolAuditLogs.objects, "create") as create:
            for surface in ("ai_chat", "dingtalk_chat", "ai_agent", "ai_sandbox", "market_ai", "customer_service_ai", "codex_mcp", "test", "business_collection", "business_agent_v2"):
                self.assertEqual(views.consumer(self.body(surface=surface), self.principal, "transport-request"), {"ok": True})
                self.assertEqual(create.call_args.kwargs["surface"], surface)

    def test_mismatched_actor_and_status_reject_before_audit_mutation(self):
        with patch.object(views, "mutation") as mutation, patch.object(chat, "audit") as audit:
            for changes in ({"actorEmail": "other@example.invalid"}, {"actorRole": "operator"}, {"status": "complete"}):
                body = self.body(); body["entry"].update(changes)
                with self.assertRaises(AiError):
                    views.consumer(body, self.principal, "transport-request")
            mutation.assert_not_called(); audit.assert_not_called()

    def test_audit_storage_failure_cannot_acknowledge_success(self):
        with patch.object(views, "mutation", side_effect=lambda: nullcontext()), patch.object(chat.m.AiToolAuditLogs.objects, "create", side_effect=RuntimeError("synthetic unavailable")):
            with self.assertRaisesRegex(RuntimeError, "synthetic unavailable"):
                views.consumer(self.body(), self.principal, "transport-request")

    def test_transport_keeps_new_surface_and_policy_binding_without_replay(self):
        entries = [{"name": name} for name in self.names]
        with patch.object(transport, "edge", return_value={"entries": entries}) as edge:
            self.assertEqual(transport.catalog(self.principal, "business_agent_budget_v1"), entries)
            edge.assert_called_once_with("catalog", {"surface": "business_agent_budget_v1"}, self.principal)
        with patch.object(transport, "edge", return_value={"ok": True}) as edge:
            self.assertEqual(transport.execute_tool(self.names[2], {"runId": "evidence-1", "reportId": "report-1"}, self.principal,
                surface="business_agent_budget_v1", request_id="request-1", provider_call_id="call-1", policy_digest="a"*64), {"ok": True})
            edge.assert_called_once()
            self.assertEqual(edge.call_args.args[1]["surface"], "business_agent_budget_v1")
            self.assertEqual(edge.call_args.args[1]["policyDigest"], "a"*64)
