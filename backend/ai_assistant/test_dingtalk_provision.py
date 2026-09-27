from unittest.mock import patch
from django.test import SimpleTestCase
from . import dingtalk_provision as provision
from .policy import AiError
from .test_dingtalk_enterprise import CONFIG


class ProvisionIdentityTests(SimpleTestCase):
    def response(self, args, profile):
        if args[:3] == ["contact", "user", "get-self"]: return {"success": True}
        if args[:2] == ["profile", "list"]:
            return {"profiles": [{"profile": CONFIG["profile"], "corpId": "corp", "status": "active"}]}
        if args[:3] == ["dev", "app", "list"]:
            return {"hasMore": False, "items": [{"robotName": "志高助手", "unifiedAppId": "app"}]}
        if args[:3] == ["dev", "app", "robot"]:
            return {"name": "志高助手", "robotCode": "bot", "mode": "STREAM", "configured": True, "robotStatus": "ONLINE"}
        if args[:2] == ["chat", "search"]:
            return {"result": {"hasMore": False, "groups": [{"title": "测试群聊", "openConversationId": "group"}]}}
        if args[:3] == ["chat", "group", "bots"]:
            return {"result": {"bots": [{"name": "志高助手", "robotCode": "bot", "status": 1}]}}
        if args[:3] == ["dev", "app", "credentials"]:
            return {"unifiedAppId": "app", "appKey": "bot", "appSecret": "fixture-secret"}
        raise AssertionError("Unexpected operator command")

    def test_operator_checks_live_identity_before_adopting_credentials(self):
        with patch.object(provision, "dws", side_effect=self.response) as calls:
            self.assertEqual(provision.credentials(CONFIG), ("bot", "fixture-secret"))
        self.assertEqual(calls.call_args_list[0].args[0], ["contact", "user", "get-self"])
        self.assertEqual(calls.call_args_list[-1].args[0][:3], ["dev", "app", "credentials"])

    def test_incomplete_or_ambiguous_app_and_wrong_key_fail_closed(self):
        for stage, replacement in (
            (["dev", "app", "list"], {"hasMore": True, "items": []}),
            (["dev", "app", "list"], {"hasMore": False, "items": [{"robotName": "志高助手", "unifiedAppId": "app"}]*2}),
            (["dev", "app", "credentials"], {"unifiedAppId": "app", "appKey": "other", "appSecret": "fixture"}),
            (["chat", "group", "bots"], {"result": {"bots": []}}),
        ):
            def response(args, profile):
                return replacement if args[:len(stage)] == stage else self.response(args, profile)
            with patch.object(provision, "dws", side_effect=response), self.assertRaises(AiError):
                provision.credentials(CONFIG)
