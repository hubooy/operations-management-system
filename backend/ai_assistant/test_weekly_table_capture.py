import json
import os
import struct
import tempfile
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest import skipUnless
from unittest.mock import patch

from django.test import SimpleTestCase

from . import scheduled_page_capture as capture
from .weekly_table_capture import previous_complete_week, capture_weekly_table
from .policy import AiError


class WeeklyTableCaptureTests(SimpleTestCase):
    def test_latest_complete_week_uses_shanghai_and_rolls_on_monday(self):
        self.assertEqual(previous_complete_week(datetime(2026, 9, 13, 15, 59, tzinfo=timezone.utc)), ("2026-08-31", "2026-09-06"))
        self.assertEqual(previous_complete_week(datetime(2026, 9, 13, 16, 0, tzinfo=timezone.utc)), ("2026-09-07", "2026-09-13"))
        self.assertEqual(previous_complete_week(datetime(2026, 1, 1, tzinfo=timezone.utc)), ("2025-12-22", "2025-12-28"))

    def test_oversized_table_is_rejected_before_screenshot_instead_of_cropped(self):
        calls = []
        def command(method, params):
            calls.append(method)
            value = {"ready": True} if len(calls) == 1 else {"x": 0, "y": 0, "width": 4000, "height": 16000}
            return {"result": {"value": value}}
        with self.assertRaises(AiError):
            capture_weekly_table(command, "2026-09-07", "2026-09-13")
        self.assertNotIn("Page.captureScreenshot", calls)

    @skipUnless(os.name == "nt" and capture.Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe").is_file(), "isolated Windows Chrome fixture")
    def test_real_browser_full_overflow_and_readiness_failures(self):
        scenario = {"mode": "ok", "identity_reads": 0}
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/api/ai/screenshot-identity":
                    scenario["identity_reads"] += 1
                    email = "other@example.invalid" if scenario["mode"] == "identity_changed" and scenario["identity_reads"] > 1 else "fixture@example.invalid"
                    raw = json.dumps({"email": email}).encode()
                    mime = "application/json"
                elif self.path == "/missing.png":
                    self.send_error(404)
                    return
                else:
                    start = "2026-08-31" if scenario["mode"] == "wrong_week" else "2026-09-07"
                    total = 31 if scenario["mode"] == "missing_row" else 30
                    state = "error" if scenario["mode"] == "load_error" else "ready"
                    image = '<img src="/missing.png" loading="lazy">' if scenario["mode"] == "broken_image" else ''
                    cells = ''.join('<td>全部内容与周销量 123</td>' for _ in range(10))
                    rows = ''.join('<tr>'+cells+'</tr>' for _ in range(30))
                    raw = f'''<html><head><meta charset="utf-8"><style>
                      body{{font-family:sans-serif}} .launch-followup-matrix{{width:600px;overflow:hidden}}
                      .launch-followup-matrix-scroll{{height:auto;max-height:200px;overflow:auto}}
                      table{{width:2200px;min-width:2200px;border-collapse:collapse}} td,th{{height:76px;border:1px solid blue}}
                      th{{background:#4477c8;color:white}}
                    </style></head><body><div style="height:1300px">OTHER PAGE CONTENT</div>
                      <div class="new-product-followup-view" data-report-state="{state}">
                        <section class="launch-followup-matrix" data-weekly-report-table="true"
                          data-report-week-start="{start}" data-report-week-end="2026-09-13"
                          data-report-row-count="30" data-report-total="{total}" data-report-column-count="10">
                          <header><h3>钉钉周报完整表格</h3><span>{start} 至 2026-09-13</span><button>不会截图</button></header>
                          <div class="launch-followup-matrix-scroll"><table><thead><tr>{'<th>周销量</th>'*10}</tr></thead>
                          <tbody>{rows}</tbody></table>{image}</div><footer>完整表格最后一行</footer>
                        </section>
                      </div></body></html>'''.encode()
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
        profile = tempfile.TemporaryDirectory()
        try:
            with patch.dict(os.environ, {"TERUISI_DINGTALK_SCREENSHOT_PROFILE": profile.name}), patch.object(capture, "ORIGIN", f"http://127.0.0.1:{server.server_port}"), patch.object(capture, "previous_complete_week", return_value=("2026-09-07", "2026-09-13")):
                raw = capture.capture("workflow:launch-followup", "fixture@example.invalid")
                width, height = struct.unpack(">II", raw[16:24])
                self.assertGreaterEqual(width, 2200)
                self.assertGreater(height, 2400)
                for mode in ("wrong_week", "missing_row", "load_error", "broken_image", "identity_changed"):
                    with self.subTest(mode=mode):
                        scenario.update(mode=mode, identity_reads=0)
                        with self.assertRaises(AiError):
                            capture.capture("workflow:launch-followup", "fixture@example.invalid")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            for attempt in range(20):
                try:
                    profile.cleanup()
                    break
                except PermissionError:
                    if attempt == 19:
                        raise
                    time.sleep(.25)
