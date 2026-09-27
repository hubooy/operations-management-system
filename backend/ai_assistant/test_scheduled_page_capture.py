import os
import json
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import patch
from unittest import skipUnless

from django.test import SimpleTestCase

from . import scheduled_page_capture as capture
from .policy import AiError


class ScheduledPageCaptureTests(SimpleTestCase):
    def test_only_fixed_local_pages_are_accepted(self):
        with patch.dict(os.environ, {"TERUISI_DINGTALK_SCREENSHOT_PROFILE": ""}):
            for page in ("https://example.com", "settings:permissions", "../dashboard", "dashboard:overview?target=other"):
                with self.assertRaises(AiError):
                    capture.capture(page, "admin@example.com")

    def test_missing_profile_fails_before_chrome_launch(self):
        with patch.dict(os.environ, {"TERUISI_DINGTALK_SCREENSHOT_PROFILE": ""}), patch.object(capture.subprocess, "Popen") as launch:
            with self.assertRaises(AiError):
                capture.capture("dashboard:overview", "admin@example.com")
        launch.assert_not_called()

    def test_stale_chrome_port_marker_is_removed_only_inside_dedicated_profile(self):
        with TemporaryDirectory() as folder:
            marker = capture.Path(folder) / "DevToolsActivePort"
            marker.write_text("not-a-port\n", encoding="utf-8")
            with patch.dict(os.environ, {"TERUISI_DINGTALK_SCREENSHOT_PROFILE": folder,
                                      "TERUISI_DINGTALK_CHROME": "missing-chrome"}):
                with self.assertRaises(AiError):
                    capture.capture("dashboard:overview", "admin@example.com")
            self.assertFalse(marker.exists())

    @skipUnless(os.name == "nt" and capture.Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe").is_file(), "isolated Windows Chrome fixture")
    def test_real_headless_browser_captures_only_matching_logged_in_page(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/api/ai/screenshot-identity":
                    raw = json.dumps({"email": "fixture@example.invalid"}).encode()
                    mime = "application/json"
                else:
                    raw = ("<html><body><h1>合成经营看板</h1><p>" + "已验证的合成数据。" * 20 + "</p></body></html>").encode()
                    mime = "text/html; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            profile = tempfile.TemporaryDirectory()
            try:
                with patch.dict(os.environ, {"TERUISI_DINGTALK_SCREENSHOT_PROFILE": profile.name}), patch.object(capture, "ORIGIN", f"http://127.0.0.1:{server.server_port}"), patch.object(capture, "IDENTITY_TIMEOUT", 1):
                    raw = capture.capture("dashboard:overview", "fixture@example.invalid")
                    self.assertTrue(raw.startswith(b"\x89PNG\r\n\x1a\n"))
                    self.assertFalse((capture.Path(profile.name) / "DevToolsActivePort").exists())
                    with self.assertRaises(AiError):
                        capture.capture("dashboard:overview", "other@example.invalid")
            finally:
                for attempt in range(20):
                    try:
                        profile.cleanup()
                        break
                    except PermissionError:
                        if attempt == 19:
                            raise
                        time.sleep(.25)  # Chrome child handles close shortly after its root process.
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
