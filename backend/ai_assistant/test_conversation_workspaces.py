from unittest.mock import patch
from django.test import TestCase, override_settings
from . import chat, models as m, conversation_workspace as workspace
from .page_context import normalize, VIEWS
from .policy import AiError, mutation
from . import tests as ai_domain_tests


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class WorkspaceTests(TestCase):
    setUp = ai_domain_tests.AiDomainTests.setUp
    user = ai_domain_tests.AiDomainTests.user

    def context(self, module="sales", **kw):
        return {"version": 1, "module": module, "view": next(iter(VIEWS[module])),
                "period": None, "filters": {}, **kw}

    def ask(self, key, module="sales", actor=None, **kw):
        body = {"clientRequestId": key, "message": "新话题", "workspaceModule": module, **kw}
        return chat.answer(body, actor or self.owner, key)

    def test_owner_module_restore_and_legacy_are_separate(self):
        a = self.ask("sales-a", pageContext=self.context())
        b = self.ask("stock-b", "inventory", pageContext=self.context("inventory"))
        other = self.ask("other-a", actor=self.other)
        admin = self.user("admin@example.invalid", "admin", None)
        self.assertEqual([r["id"] for r in chat.listing({"workspaceModule": "sales"}, self.owner)["items"]], [a["conversationId"]])
        self.assertEqual(chat.messages({"conversationId": b["conversationId"], "workspaceModule": "inventory"}, self.owner)["conversation"]["workspaceModule"], "inventory")
        for principal in (self.other, admin):
            with self.assertRaises(AiError):
                chat.messages({"conversationId": a["conversationId"]}, principal)
        with self.assertRaises(AiError):
            chat.messages({"conversationId": b["conversationId"], "workspaceModule": "sales"}, self.owner)
        self.assertNotIn(other["conversationId"], [r["id"] for r in chat.listing({}, admin)["items"]])
        old = m.AiConversations.objects.create(id="old", title="旧会话", created_by=self.owner.email)
        m.AiConversationScopes.objects.create(conversation=old, scope_json="null")
        self.assertEqual(workspace.public(old), {"workspaceModule": "ai", "pageContext": None})

    def test_replay_does_not_duplicate_or_move_conversation(self):
        context = self.context(filters={"shops": ["测试店"]})
        a = self.ask("once", pageContext=context)
        self.assertEqual(self.ask("once", pageContext=context), a)
        self.assertEqual(m.AiConversations.objects.count(), 1)
        self.assertEqual(m.AiConversationWorkspace.objects.count(), 1)
        self.assertEqual(m.AiConversationMessages.objects.count(), 2)
        with self.assertRaises(AiError):
            self.ask("changed", "market", conversationId=a["conversationId"])
        with self.assertRaises(AiError):
            chat.answer({"clientRequestId": "bypass", "message": "新话题", "conversationId": a["conversationId"]}, self.owner, "bypass")

    def test_context_inheritance_clear_and_request_presence(self):
        a = self.ask("context", pageContext=self.context(filters={"skus": ["sku-1"]}))
        self.ask("inherit", conversationId=a["conversationId"])
        self.assertIn("sku-1", m.AiConversationWorkspace.objects.get().page_context_json)
        with self.assertRaises(AiError):
            self.ask("inherit", conversationId=a["conversationId"], pageContext=None)
        self.ask("clear", conversationId=a["conversationId"], pageContext=None)
        self.assertEqual(m.AiConversationWorkspace.objects.get().page_context_json, "null")

    def test_activation_restores_older_conversation_and_delete_cascades(self):
        a = self.ask("first")
        b = self.ask("second")
        with mutation(self.owner):
            workspace.activate({"action": "activate", "conversationId": a["conversationId"], "workspaceModule": "sales"}, self.owner)
        self.assertEqual(chat.listing({"workspaceModule": "sales"}, self.owner)["items"][0]["id"], a["conversationId"])
        with mutation(self.owner):
            chat.delete(a["conversationId"], self.owner)
        self.assertEqual(m.AiConversationWorkspace.objects.get().conversation_id, b["conversationId"])
        self.assertTrue(m.AiConversationDeletionAudits.objects.exists())

    def test_activation_order_survives_equal_and_backward_wall_clock(self):
        from datetime import timedelta
        a = self.ask("same-clock-first")
        b = self.ask("same-clock-second")
        latest = m.AiConversationWorkspace.objects.get(conversation_id=b['conversationId']).last_opened_at
        for target, instant in ((a, latest), (b, latest-timedelta(seconds=1)), (a, latest)):
            with patch('ai_assistant.conversation_workspace.timezone.now', return_value=instant), mutation(self.owner):
                workspace.activate({'action':'activate','conversationId':target['conversationId'],'workspaceModule':'sales'},self.owner)
            self.assertEqual(chat.listing({'workspaceModule':'sales'},self.owner)['items'][0]['id'],target['conversationId'])

    def test_activation_clock_isolated_by_owner_and_module(self):
        from datetime import timedelta
        from django.utils import timezone
        a = self.ask('clock-owner')
        other = self.ask('clock-other',actor=self.other)
        inventory = self.ask('clock-inventory','inventory')
        now = timezone.now()
        with mutation(self.owner):
            m.AiConversationWorkspace.objects.filter(conversation_id__in=[other['conversationId'],inventory['conversationId']]).update(last_opened_at=now+timedelta(days=100))
        with patch('ai_assistant.conversation_workspace.timezone.now',return_value=now), mutation(self.owner):
            workspace.activate({'action':'activate','conversationId':a['conversationId'],'workspaceModule':'sales'},self.owner)
        self.assertLess(m.AiConversationWorkspace.objects.get(conversation_id=a['conversationId']).last_opened_at, now+timedelta(seconds=1))

    def test_receipt_recovery_is_read_only_and_owner_scoped(self):
        a = self.ask("recover")
        with patch("ai_assistant.provider.turn", side_effect=AssertionError("must not call model")):
            result = chat.messages({"clientRequestId": "recover", "workspaceModule": "sales"}, self.owner)
        self.assertEqual(result["request"], {"status": "succeeded", "conversationId": a["conversationId"], "assistantMessageId": a["assistantMessageId"]})
        with self.assertRaises(AiError):
            chat.messages({"clientRequestId": "recover", "workspaceModule": "sales"}, self.other)

    def test_all_views_and_untrusted_context(self):
        for module, views in VIEWS.items():
            for view in views:
                self.assertEqual(normalize(self.context(module, view=view))["view"], view)
        for context in [self.context(view="not_real"), self.context(filters={"password": "forbidden"}),
                        self.context(period={"startDate": "2026-02-30", "endDate": "2026-03-01"}),
                        self.context(filters={"shops": ["x"] * 21})]:
            with self.assertRaises(AiError):
                self.ask("invalid", pageContext=context)
        self.assertEqual(m.AiConversations.objects.count(), 0)

    def test_exact_reply_recovery_and_scope_shrink(self):
        from sales.auth import Principal
        a = self.ask("exact-recovery")
        for i in range(40):
            chat.append(a["conversationId"], "user", f"fixture-{i}")
        result = chat.messages({"conversationId": a["conversationId"], "workspaceModule": "sales", "messageId": a["assistantMessageId"]}, self.owner)
        self.assertEqual([item["id"] for item in result["items"]], [a["assistantMessageId"]])
        narrowed = Principal(self.owner.email, self.owner.display_name, self.owner.role, {"warehouses": [], "channels": [], "platforms": []})
        self.assertEqual(chat.listing({"workspaceModule": "sales"}, narrowed)["items"], [])
        for query in [{"conversationId": a["conversationId"], "workspaceModule": "sales"}, {"clientRequestId": "exact-recovery", "workspaceModule": "sales"}]:
            with self.assertRaises(AiError):
                chat.messages(query, narrowed)
        with self.assertRaises(AiError):
            normalize(self.context("customer_service", filters={"query": "private fixture"}))
