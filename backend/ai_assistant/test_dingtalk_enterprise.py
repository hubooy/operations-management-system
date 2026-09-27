import base64
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from . import dingtalk_bot_credentials as vault, dingtalk_transport as platform, transport
from .policy import AiError


CONFIG = {"profile": "corp:operator", "corpId": "corp", "unifiedAppId": "app", "robotCode": "bot",
          "robotName": "志高助手", "groups": [{"id": "group", "name": "测试群聊"}]}


@skipUnless(os.name == "nt", "CurrentUser DPAPI is Windows-only")
class EnterpriseCredentialsTests(SimpleTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "bot.json"
        self.env = patch.dict(os.environ, {"TERUISI_DINGTALK_BOT_CREDENTIALS": str(self.path)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def provision(self):
        with patch("ai_assistant.dingtalk_provision.credentials", return_value=("bot", "fixture-secret")):
            vault.provision(CONFIG, str(self.path))

    def test_real_dpapi_roundtrip_is_bound_and_not_plaintext_or_dws_at_runtime(self):
        self.provision()
        self.assertNotIn(b"fixture-secret", self.path.read_bytes())
        with patch("ai_assistant.dingtalk_provision.credentials", side_effect=AssertionError("DWS unavailable")):
            self.assertEqual(vault.read_credentials(CONFIG), ("bot", "fixture-secret"))
        for key in ("profile", "corpId", "unifiedAppId", "robotCode", "robotName"):
            changed = {**CONFIG, key: "other"}
            with self.subTest(key=key), self.assertRaises(AiError):
                vault.read_credentials(changed)

    def test_create_only_and_failed_verification_do_not_replace_or_create_credentials(self):
        with patch("ai_assistant.dingtalk_provision.credentials", side_effect=AiError("denied")):
            with self.assertRaises(AiError): vault.provision(CONFIG, str(self.path))
        self.assertFalse(self.path.exists())
        self.provision()
        before = self.path.read_bytes()
        with patch("ai_assistant.dingtalk_provision.credentials") as remote, self.assertRaises(AiError):
            vault.provision(CONFIG, str(self.path))
        remote.assert_not_called()
        self.assertEqual(before, self.path.read_bytes())

    def test_malformed_duplicate_plaintext_and_modified_ciphertext_fail_closed(self):
        self.provision()
        outer = json.loads(self.path.read_bytes())
        raw = base64.b64decode(outer["payloadDpapiBase64"])
        damaged = {**outer, "payloadDpapiBase64": base64.b64encode(raw[:-1] + bytes([raw[-1] ^ 1])).decode()}
        for content in (b"{}", b'{"version":1,"version":1,"payloadDpapiBase64":"AAAA"}',
                        json.dumps(damaged).encode(), b'{"appSecret":"fixture-secret"}', b"x"*65537):
            self.path.write_bytes(content)
            with self.assertRaises(AiError) as error: vault.read_credentials(CONFIG)
            self.assertEqual(error.exception.code, "bot_credentials_unavailable")
            self.assertNotIn("fixture-secret", str(error.exception))

    def test_missing_relative_and_hardlinked_files_are_denied(self):
        with self.assertRaises(AiError): vault.read_credentials(CONFIG)
        with patch.dict(os.environ, {"TERUISI_DINGTALK_BOT_CREDENTIALS": "relative.json"}), self.assertRaises(AiError):
            vault.read_credentials(CONFIG)
        self.provision()
        os.link(self.path, self.path.with_name("alias.json"))
        with self.assertRaises(AiError): vault.read_credentials(CONFIG)


class EnterpriseDeliveryTests(SimpleTestCase):
    def setUp(self):
        for target, replacement in (("guard", Mock()), ("credentials", Mock(return_value=("bot", "fixture-secret"))),
                                    ("media_addresses", Mock(return_value=[]))):
            item = patch.object(platform, target, replacement)
            item.start()
            self.addCleanup(item.stop)
        item = patch("ai_assistant.dingtalk_provision.dws", side_effect=AssertionError("personal authorization unavailable"))
        item.start()
        self.addCleanup(item.stop)

    def response(self, url, body, **kwargs):
        if url == platform.TOKEN_API: return {"accessToken": "fixture-token"}
        if url == platform.GROUP_BOTS_API:
            return {"chatbotInstanceVOList": [{"name": "志高助手", "robotCode": "bot"}]}
        return {"processQueryKey": "receipt", "invalidStaffIdList": [], "flowControlledStaffIdList": []}

    def session(self, group=False):
        return SimpleNamespace(sender_id="bound-staff", conversation_type="2" if group else "1", external_conversation_id="group")

    def test_text_to_person_and_exact_group_needs_no_personal_login(self):
        for group in (False, True):
            with patch.object(transport, "_bounded_json", side_effect=self.response) as calls:
                platform.send(lambda: CONFIG, self.session(group), "只读结果")
            url, body = calls.call_args.args
            self.assertEqual(url, platform.GROUP_MEDIA_API if group else platform.PERSON_MEDIA_API)
            self.assertEqual(body["msgKey"], "sampleText")
            self.assertEqual(json.loads(body["msgParam"]), {"content": "只读结果"})
            self.assertEqual(body.get("openConversationId"), "group" if group else None)
            self.assertEqual(body.get("userIds"), None if group else ["bound-staff"])

    def test_each_send_uses_fresh_application_token_and_never_replays(self):
        with patch.object(transport, "_bounded_json", side_effect=self.response) as calls:
            platform.send(lambda: CONFIG, self.session(), "first")
            platform.send(lambda: CONFIG, self.session(), "second")
        self.assertEqual(sum(c.args[0] == platform.TOKEN_API for c in calls.call_args_list), 2)
        for receipt in ({}, {"processQueryKey": "x", "invalidStaffIdList": ["staff"]},
                        {"processQueryKey": "x", "flowControlledStaffIdList": ["staff"]}):
            with patch.object(transport, "_bounded_json", side_effect=[{"accessToken": "token"}, receipt]) as calls:
                with self.assertRaises(AiError): platform.send(lambda: CONFIG, self.session(), "result")
                self.assertEqual(calls.call_count, 2)

    def test_removed_duplicate_wrong_bot_or_revoked_group_never_sends(self):
        for broken_url, broken in (
            (platform.GROUP_BOTS_API, {"chatbotInstanceVOList": []}),
            (platform.GROUP_BOTS_API, {"chatbotInstanceVOList": [{"name": "志高助手", "robotCode": "other"}]}),
            (platform.GROUP_BOTS_API, {"chatbotInstanceVOList": [{"name": "志高助手", "robotCode": "bot"}]*2}),
        ):
            def response(url, body, **kw):
                return broken if url == broken_url else self.response(url, body, **kw)
            with patch.object(transport, "_bounded_json", side_effect=response) as calls, self.assertRaises(AiError):
                platform.send(lambda: CONFIG, self.session(True), "result")
            self.assertFalse(any(c.args[0] in (platform.GROUP_MEDIA_API, platform.PERSON_MEDIA_API) for c in calls.call_args_list))
        with patch.object(transport, "_bounded_json", side_effect=self.response) as calls, self.assertRaises(AiError):
            platform.send(lambda: {**CONFIG, "groups": []}, self.session(True), "result")
        self.assertEqual(calls.call_count, 1)

    def test_revocation_after_token_fetch_and_token_failure_do_not_send(self):
        with patch.object(platform, "guard", side_effect=[None, AiError("revoked", status=403)]), \
                patch.object(transport, "_bounded_json", side_effect=self.response) as calls, self.assertRaises(AiError):
            platform.send(lambda: CONFIG, self.session(), "result")
        self.assertEqual(calls.call_count, 1)
        before = Mock(side_effect=AiError("task revoked", status=403))
        with patch.object(transport, "_bounded_json", side_effect=self.response) as calls, self.assertRaises(AiError):
            platform.send(lambda: CONFIG, self.session(), "result", before_send=before)
        before.assert_called_once()
        self.assertEqual(calls.call_count, 1)

    def test_group_display_name_never_resolves_or_redirects_the_approved_id(self):
        renamed = {**CONFIG, "groups": [{"id": "group", "name": "new display label"}]}
        with patch.object(transport, "_bounded_json", side_effect=self.response) as calls:
            platform.send(lambda: renamed, self.session(True), "result")
        self.assertEqual(calls.call_args.args[1]["openConversationId"], "group")
        verification = [c for c in calls.call_args_list if c.args[0] == platform.GROUP_BOTS_API]
        self.assertEqual(verification[0].args[1], {"openConversationId": "group"})
        with patch.object(transport, "_bounded_json", return_value={"errcode": 1}) as calls, self.assertRaises(AiError):
            platform.send(lambda: CONFIG, self.session(), "result")
        self.assertEqual(calls.call_count, 1)
