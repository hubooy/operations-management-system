import json
from unittest.mock import Mock, patch
from django.test import SimpleTestCase
from types import SimpleNamespace
from . import dingtalk_transport as platform
from .policy import AiError
from . import transport


class DingTalkTransportTests(SimpleTestCase):
    def test_media_urls_are_fixed_and_private_dns_is_rejected(self):
        with patch.object(transport, "resolve_addresses") as lookup:
            for url in ("https://example.com/media/upload", "http://api.dingtalk.com/v1.0/oauth2/accessToken", "https://api.dingtalk.com/v1.0/robot/other"):
                with self.assertRaises(AiError):
                    platform.media_addresses(url)
            lookup.assert_not_called()
        private = [(2, 1, 6, "", ("127.0.0.1", 443))]
        with patch.object(transport, "resolve_addresses", return_value=private), patch.object(transport, "_public_addresses", return_value=private), self.assertRaises(AiError):
            platform.media_addresses(platform.TOKEN_API)

    def test_stream_dns_does_not_extend_model_or_arbitrary_origins(self):
        with patch.object(transport, "resolve_addresses") as lookup:
            for url in ("https://example.com/connect", "https://api.dingtalk.com/other", "http://api.dingtalk.com/v1.0/gateway/connections/open"):
                with self.assertRaises(AiError):
                    platform.stream_addresses(url)
            lookup.assert_not_called()

    def test_stream_rejects_private_and_synthetic_addresses_after_resolution(self):
        for ip in ("127.0.0.1", "198.18.1.31", "10.0.0.1"):
            addresses = [(2, 1, 6, "", (ip, 443))]
            with patch.object(transport, "resolve_addresses", return_value=addresses), patch.object(transport, "_public_addresses", return_value=addresses), self.assertRaises(AiError):
                platform.stream_addresses(platform.STREAM_API)

    def test_open_stream_pins_verified_addresses_without_model_allowlist_change(self):
        addresses = [(2, 1, 6, "", ("8.8.8.8", 443))]
        with patch.object(platform, "stream_addresses", return_value=addresses), patch.object(transport, "_bounded_json", return_value={"ticket": "fixture"}) as request:
            platform.open_stream("fixture-key", "fixture-secret")
        self.assertEqual(request.call_args.args[0], platform.STREAM_API)
        self.assertEqual(request.call_args.kwargs["fixed_addresses"], addresses)
        self.assertEqual(request.call_args.kwargs["maximum"], 16384)

    config = {"profile": "corp:operator", "corpId": "corp", "unifiedAppId": "app", "robotCode": "bot",
              "robotName": "志高助手", "groups": [{"id": "group", "name": "测试群聊"}]}

    def test_bot_media_upload_and_send_are_separate_bounded_official_calls(self):
        session = SimpleNamespace(sender_id="bound-staff", conversation_type="2", external_conversation_id="group")
        image = b"\x89PNG\r\n\x1a\nfixture"
        replies = [{"accessToken": "opaque-token"}, {"errcode": 0, "media_id": "@media"}, {"processQueryKey": "receipt"}]
        with patch.object(platform, "guard"), patch.object(platform, "credentials", return_value=("key", "secret")), patch.object(platform, "robot"), patch.object(platform, "verify_group"), patch.object(platform, "media_addresses", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]), patch.object(transport, "_bounded_json", side_effect=replies) as calls:
            platform.send_media(lambda: self.config, session, image, "系统页面.png", "image")
        self.assertEqual(calls.call_count, 3)
        self.assertEqual(calls.call_args_list[0].args[0], platform.TOKEN_API)
        self.assertIn("&type=image", calls.call_args_list[1].args[0])
        self.assertEqual(calls.call_args_list[2].args[1]["msgKey"], "sampleImageMsg")
        self.assertEqual(calls.call_args_list[2].args[1]["openConversationId"], "group")

    def test_media_rejects_bad_format_before_credentials_or_upload(self):
        session = SimpleNamespace(sender_id="bound-staff", conversation_type="1", external_conversation_id="dm")
        with patch.object(platform, "credentials") as credentials:
            for raw, name, kind in ((b"bad", "screenshot.png", "image"), (b"PK", "report.html", "file"), (b"PK", "../report.xlsx", "file")):
                with self.assertRaises(AiError):
                    platform.send_media(lambda: self.config, session, raw, name, kind)
        credentials.assert_not_called()

    def test_caption_image_is_one_markdown_send_for_person_and_group(self):
        for conversation_type in ("1", "2"):
            session = SimpleNamespace(sender_id="bound-staff", conversation_type=conversation_type, external_conversation_id="group")
            replies = [{"accessToken":"opaque-token"}, {"errcode":0,"media_id":"@media"}, {"processQueryKey":"receipt"}]
            guard = Mock()
            with patch.object(platform,"guard"), patch.object(platform,"credentials",return_value=("key","secret")), patch.object(platform,"robot"), patch.object(platform,"verify_group"), patch.object(platform,"media_addresses",return_value=[]), patch.object(transport,"_bounded_json",side_effect=replies) as calls:
                platform.send_media(lambda:self.config,session,b"\x89PNG\r\n\x1a\nfixture","页面.png","image","新品周销量趋势数据",before_send=guard)
            guard.assert_called_once()
            self.assertEqual(calls.call_count,3)
            endpoint,body=calls.call_args_list[-1].args
            self.assertEqual(endpoint,platform.GROUP_MEDIA_API if conversation_type=="2" else platform.PERSON_MEDIA_API)
            self.assertEqual(body["msgKey"],"sampleMarkdown")
            params=json.loads(body["msgParam"])
            self.assertEqual(params,{"title":"新品周销量趋势数据","text":"新品周销量趋势数据\n\n![完整截图](@media)"})

    def test_markdown_caption_is_literal_and_bad_media_or_oversize_denied(self):
        result=platform.image_markdown("标题\n![外部图](https://example.invalid) <img>","@media")
        self.assertIn("\\!\\[外部图\\]\\(https://example",result["text"])
        self.assertIn("&lt;img&gt;",result["text"])
        self.assertTrue(result["text"].endswith("![完整截图](@media)"))
        for media in ("@media)\n![injected](https://example.invalid)","https://example.invalid","@bad space"):
            with self.assertRaises(AiError): platform.image_markdown("标题",media)
        with patch.object(platform,"credentials") as credentials:
            for caption in ([],"长"*4001,"*"*4000):
                with self.assertRaises(AiError):
                    platform.send_media(lambda:self.config,SimpleNamespace(),b"\x89PNG\r\n\x1a\nfixture","页面.png","image",caption)
        credentials.assert_not_called()

    def test_post_upload_revocation_and_bad_receipt_never_fall_back_to_separate_messages(self):
        session=SimpleNamespace(sender_id="bound-staff",conversation_type="1",external_conversation_id="group")
        for revoked in (True,False):
            replies=[{"accessToken":"opaque-token"},{"errcode":0,"media_id":"@media"},{"processQueryKey":"receipt","invalidStaffIdList":["bound-staff"]}]
            before=Mock(side_effect=AiError("配置已变化","access_denied",403) if revoked else None)
            with patch.object(platform,"guard"), patch.object(platform,"credentials",return_value=("key","secret")), patch.object(platform,"robot"), patch.object(platform,"media_addresses",return_value=[]), patch.object(transport,"_bounded_json",side_effect=replies) as calls, self.assertRaises(AiError):
                platform.send_media(lambda:self.config,session,b"\x89PNG\r\n\x1a\nfixture","页面.png","image","标题",before_send=before)
            self.assertEqual(calls.call_count,2 if revoked else 3)
