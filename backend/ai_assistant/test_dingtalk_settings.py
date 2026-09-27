from copy import deepcopy
from unittest.mock import patch, Mock
from django.test import TestCase, override_settings
from . import dingtalk, dingtalk_settings as service, models as m, datasets
from .tests import AiDomainTests, ADMIN, CATALOG
from .test_dingtalk import DingTalkTests
from .policy import AiError, revision
from .control_models import AiMutationAudit
from .transport import signed_headers
import hashlib
import hmac
import os


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class DingTalkSettingsTests(TestCase):
    user = AiDomainTests.user
    data = DingTalkTests.data

    def call(self, path, payload=None, principal=None, method="POST", request_id=None):
        def headers(*args):
            result = signed_headers(*args)
            if method == "PATCH":
                message = "\n".join(["v1", result["X-Teruisi-Timestamp"], result["X-Teruisi-Request-Id"], method,
                    path, "", result["X-Teruisi-Content-SHA256"], result["X-Teruisi-Principal"]])
                result["X-Teruisi-Signature"] = "v1=" + hmac.new(os.environ["TERUISI_DJANGO_INTERNAL_SECRET"].encode(), message.encode(), hashlib.sha256).hexdigest()
            return result
        with patch("ai_assistant.tests.signed_headers", side_effect=headers):
            return AiDomainTests.call(self, path, payload, principal, method, request_id)

    def setUp(self):
        AiDomainTests.setUp(self)
        self.base = {"version": 1, "enabled": True, "profile": "corp:operator", "corpId": "corp",
            "unifiedAppId": "app", "robotCode": "robot", "robotName": "志高助手",
            "groups": [{"id": "group", "name": "测试群聊"}],
            "bindings": [{"senderId": "staff", "ownerEmail": self.owner.email, "role": self.owner.role, "scope": self.owner.scope}]}
        service.initialize(self.base)

    def save(self, **changes):
        return service.save({"enabled": True, "groups": [{"id": "group", "name": "测试群聊", "enabled": True}],
            "expectedVersion": service.read(ADMIN)["config"]["version"], **changes}, ADMIN)

    def test_settings_are_admin_only_and_secrets_and_bindings_stay_private(self):
        for principal in (self.owner, self.other, self.viewer, self.user("scoped-admin@example.invalid", "admin", self.owner.scope)):
            with self.assertRaises(AiError):
                service.read(principal)
            with self.assertRaises(AiError):
                service.save({}, principal)
        public = service.read(ADMIN)["config"]
        self.assertEqual(public["replyMode"], "source")
        self.assertFalse(set(public) & {"profile", "bindings", "corpId", "appSecret", "identity_json"})

    def test_api_save_audits_and_conflicting_versions_fail_without_overwrite(self):
        payload = {"enabled": False, "groups": [], "expectedVersion": 1}
        before = int(revision())
        response = self.call("/api/ai/dingtalk-settings", payload, ADMIN, method="PATCH", request_id="ding-settings-1")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(response.json()["config"]["enabled"])
        self.assertGreater(int(revision()), before)
        self.assertTrue(AiMutationAudit.objects.filter(request_id="ding-settings-1").exists())
        conflict = self.call("/api/ai/dingtalk-settings", payload, ADMIN, method="PATCH", request_id="ding-settings-2")
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(service.read(ADMIN)["config"]["version"], 2)
        replay = self.call("/api/ai/dingtalk-settings", payload, ADMIN, method="PATCH", request_id="ding-settings-1")
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(service.read(ADMIN)["config"]["version"], 2)

    def test_api_read_and_write_reject_non_admin(self):
        self.assertEqual(self.call("/api/ai/dingtalk-settings", principal=self.owner, method="GET").status_code, 403)
        self.assertEqual(self.call("/api/ai/dingtalk-settings", {}, self.owner, method="PATCH").status_code, 403)
        self.assertEqual(self.call("/api/ai/dingtalk-settings", principal=ADMIN, method="GET").status_code, 200)

    def test_invalid_groups_identity_injection_and_duplicate_ids_are_rejected(self):
        for value in ([{"id": "group", "name": "群", "enabled": "yes"}],
                      [{"id": "a,b", "name": "群", "enabled": True}],
                      [{"id": "same", "name": "群", "enabled": True}]*2,
                      [{"id": str(i), "name": "群", "enabled": True} for i in range(31)]):
            with self.assertRaises(AiError):
                self.save(groups=value)
        with self.assertRaises(AiError):
            self.save(robotCode="other")
        self.assertEqual(service.read(ADMIN)["config"]["version"], 1)

    def test_multiple_groups_isolate_context_and_reply_to_source(self):
        self.save(groups=[{"id": "group", "name": "测试群聊", "enabled": True}, {"id": "group2", "name": "经营群", "enabled": True}])
        config = service.effective(self.base)
        for group in ("group", "group2"):
            dingtalk.accept(config, self.data(msgId=group, conversationType="2", conversationId=group, isInAtList=True))
        self.assertEqual(m.AiDingTalkSession.objects.count(), 2)
        sender = Mock()
        with patch.object(dingtalk.chat, "answer", return_value={"reply": "经营结论"}):
            for _ in range(4):
                dingtalk.step(lambda: service.effective(self.base), sender)
        self.assertEqual([c.args[0].external_conversation_id for c in sender.call_args_list], ["group", "group", "group2", "group2"])
        self.assertIn("本群回复", sender.call_args_list[0].args[1])

    def test_disabling_group_revokes_queued_and_ready_requests(self):
        for status in ("queued", "ready"):
            config = service.effective(self.base)
            receipt = dingtalk.accept(config, self.data(msgId=status, conversationType="2", conversationId="group", isInAtList=True))
            m.AiDingTalkReceipt.objects.filter(pk=receipt).update(status=status, reply="结果")
            self.save(groups=[{"id": "group", "name": "测试群聊", "enabled": False}])
            sender = Mock()
            with patch.object(dingtalk.chat, "answer") as answer:
                dingtalk.step(lambda: service.effective(self.base), sender)
            sender.assert_not_called()
            answer.assert_not_called()
            with self.assertRaises(AiError):
                dingtalk.accept(service.effective(self.base), self.data(msgId=status+"-new", conversationType="2", conversationId="group", isInAtList=True))
            self.save()

    def test_disabled_policy_persists_across_receiver_restart_and_identity_is_fenced(self):
        self.save(enabled=False)
        service.initialize(self.base)
        self.assertFalse(service.effective(self.base)["enabled"])
        with self.assertRaises(AiError):
            service.effective({**self.base, "robotCode": "other"})
        with self.assertRaises(AiError):
            service.initialize({**self.base, "robotCode": "other"})

    def test_dataset_discovery_and_nested_execution_preserve_dingtalk_surface(self):
        entries = []
        for name in ("get_data_freshness", "get_finance_page_data"):
            entry = deepcopy(CATALOG[0])
            entry["name"] = name
            entry["execution"]["allowedSurfaces"] = ["dingtalk_chat"]
            entries.append(entry)
        with patch.object(datasets.transport, "catalog", return_value=entries) as catalog, patch.object(datasets.transport, "execute_tool", side_effect=lambda name, *a, **kw: {"ok": True, "toolName": name, "data": {"dataCutoffDate": "2026-09-08"}}) as execute:
            result = datasets.consumer({"operation": "datasets-query", "dataset": "finance_analysis", "queryJson": "{}", "surface": "dingtalk_chat"}, ADMIN, "ding-data")
            self.assertEqual(result["source"]["domain"], "finance")
            catalog.assert_called_with(ADMIN, "dingtalk_chat")
            self.assertTrue(all(c.kwargs["surface"] == "dingtalk_chat" for c in execute.call_args_list))
        entries[1]["execution"]["allowedSurfaces"] = ["ai_chat"]
        with patch.object(datasets.transport, "catalog", return_value=entries), self.assertRaises(AiError):
            datasets.query("finance_analysis", {}, ADMIN, "ding-denied", surface="dingtalk_chat")
