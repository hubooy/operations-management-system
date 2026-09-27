from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

from django.test import SimpleTestCase

from inventory.dingtalk_sync import DwsCli, MAX_DWS_OUTPUT_BYTES, load_target
from inventory.errors import InventoryApiError


class DwsCliFailureTests(SimpleTestCase):
    def run_result(self, stdout: str, stderr: str = "", returncode: int = 0):
        cli = DwsCli(load_target())
        result = subprocess.CompletedProcess([], returncode, stdout, stderr)
        with patch.object(cli, "_command_prefix", return_value=["node", "dws.js"]), patch(
            "inventory.dingtalk_sync.subprocess.run", return_value=result,
        ) as runner:
            try:
                return cli.run("aitable", "table", "get", "--base-id", "test-base")
            finally:
                runner.assert_called_once()

    def test_stderr_only_legacy_auth_failure_has_actionable_redacted_message(self):
        diagnostic = (
            "Error: resolve access token: 旧版登录态已无法由当前认证服务刷新; "
            "MCP token exchange failed: invalidParameter.authCode.notFound; "
            "access_token=secret-sentinel profile=private-profile"
        )
        with self.assertLogs("inventory.dingtalk_sync", level="WARNING") as logs:
            with self.assertRaisesMessage(InventoryApiError, "钉钉登录授权已失效") as caught:
                self.run_result("", diagnostic, 1)
        self.assertEqual(caught.exception.status, 503)
        self.assertIn("重新授权", str(caught.exception))
        self.assertIn("原备货计划", str(caught.exception))
        self.assertIn("command=aitable table get category=auth_required exit_code=1", logs.output[0])
        for sentinel in ("secret-sentinel", "private-profile", "test-base", "access_token"):
            self.assertNotIn(sentinel, str(caught.exception) + "".join(logs.output))

    def test_structured_errors_in_either_stream_are_classified(self):
        cases = [
            ({"success": False, "error": {"reason": "invalid_grant"}}, "登录授权已失效"),
            ({"status": "error", "error": {"code": "permission_denied"}}, "访问权限不足"),
            ({"success": False, "error": {"reason": "rate_limit"}}, "请求过于频繁"),
            ({"success": False, "error": {"reason": "confirmation_required"}}, "操作需要确认"),
            ({"error": {"message": "context deadline exceeded"}}, "网络异常"),
            ({"error": {"message": "MCP token exchange failed: context deadline exceeded"}}, "网络异常"),
        ]
        for payload, message in cases:
            for stream in ("stdout", "stderr"):
                with self.subTest(payload=payload, stream=stream):
                    output = json.dumps(payload)
                    with self.assertRaisesMessage(InventoryApiError, message):
                        self.run_result(output if stream == "stdout" else "", output if stream == "stderr" else "", 0 if stream == "stdout" else 1)

    def test_unknown_failure_does_not_blame_login_or_expose_output(self):
        with self.assertRaisesMessage(InventoryApiError, "钉钉请求失败") as caught:
            self.run_result("", "unknown failure: secret-sentinel", 2)
        self.assertNotIn("登录授权已失效", str(caught.exception))
        self.assertNotIn("secret-sentinel", str(caught.exception))

    def test_success_with_stderr_warning_is_not_reclassified(self):
        payload = {"success": True, "data": {"value": "invalid_token"}, "error": {}}
        self.assertEqual(self.run_result(json.dumps(payload), "warning: timeout default changed"), payload)

    def test_nonzero_exit_cannot_be_overridden_by_success_json(self):
        with self.assertRaisesMessage(InventoryApiError, "登录授权已失效"):
            self.run_result('{"success":true}', "invalidParameter.authCode.notFound", 1)

    def test_empty_stdout_with_auth_diagnostic_even_on_zero_exit(self):
        with self.assertRaisesMessage(InventoryApiError, "登录授权已失效"):
            self.run_result("", "invalidParameter.authCode.notFound")

    def test_invalid_or_nonobject_success_response_is_rejected(self):
        for stdout in ("", "not-json", "[]", "null", "[" * 1100):
            with self.subTest(stdout=stdout[:12]):
                with self.assertRaisesMessage(InventoryApiError, "返回内容无效"):
                    self.run_result(stdout)

    def test_combined_output_limit_includes_stderr_and_whitespace(self):
        for stdout, stderr in ((" " * MAX_DWS_OUTPUT_BYTES + "{}", ""), ("{}", "x" * MAX_DWS_OUTPUT_BYTES)):
            with self.assertRaisesMessage(InventoryApiError, "超过安全上限"):
                self.run_result(stdout, stderr)

    def test_process_failures_are_safe_and_never_retried(self):
        failures = [
            (subprocess.TimeoutExpired(["dws", "secret-sentinel"], 35), "请求超时"),
            (OSError("secret-sentinel"), "无法启动"),
            (UnicodeError("secret-sentinel"), "响应编码异常"),
        ]
        for failure, message in failures:
            with self.subTest(message=message):
                cli = DwsCli(load_target())
                with patch.object(cli, "_command_prefix", return_value=["node", "dws.js"]), patch(
                    "inventory.dingtalk_sync.subprocess.run", side_effect=failure,
                ) as runner:
                    with self.assertRaisesMessage(InventoryApiError, message) as caught:
                        cli.run("aitable", "record", "create")
                runner.assert_called_once()
                self.assertNotIn("secret-sentinel", str(caught.exception))
                self.assertTrue(caught.exception.__suppress_context__)
