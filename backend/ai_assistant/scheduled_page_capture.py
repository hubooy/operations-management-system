"""Capture one allowlisted local application page in a dedicated logged-in Chrome profile."""
import base64
import json
import os
from pathlib import Path
import socket
import subprocess
import time
from urllib.request import Request, urlopen

from .policy import AiError
from .weekly_table_capture import capture_weekly_table, previous_complete_week

PAGES = {
    "dashboard:overview": ("dashboard", "overview"),
    "shop:analysis": ("shop", "analysis"),
    "shop:products": ("shop", "products"),
    "shop:promotion": ("shop", "promotion"),
    "sales:overview": ("sales", "overview"),
    "inventory:overview": ("inventory", "overview"),
    "inventory:guangdong": ("inventory", "guangdong"),
    "market:ranking": ("market", "ranking"),
    "workflow:launch-followup": ("workflow", "launch-followup"),
}
ORIGIN = "http://127.0.0.1:3000"
IDENTITY_TIMEOUT = 25


def capture(page_key, owner_email):
    if page_key not in PAGES:
        raise AiError("截图页面不在允许列表")
    profile_name = os.environ.get("TERUISI_DINGTALK_SCREENSHOT_PROFILE", "")
    if not profile_name:
        raise AiError("定时截图专用浏览器尚未配置", "channel_unavailable", 503)
    try:
        profile = Path(profile_name).resolve(strict=True)
    except OSError:
        raise AiError("定时截图专用浏览器尚未登录", "channel_unavailable", 503) from None
    if not profile.is_dir() or profile.is_symlink():
        raise AiError("定时截图浏览器目录无效", "channel_unavailable", 503)
    port_file = profile / "DevToolsActivePort"
    if port_file.exists():
        try:
            old_port = int(port_file.read_text().splitlines()[0])
            if not 0 < old_port < 65536:
                raise ValueError("invalid CDP port")
            with socket.create_connection(("127.0.0.1", old_port), timeout=.5):
                raise AiError("定时截图专用浏览器仍在运行，请先关闭登录窗口", "channel_unavailable", 503)
        except (OSError, ValueError, IndexError):
            port_file.unlink()  # Stale Chrome marker inside the dedicated profile.
    chrome = Path(os.environ.get("TERUISI_DINGTALK_CHROME", r"C:\Program Files\Google\Chrome\Application\chrome.exe"))
    if not chrome.is_file():
        raise AiError("截图浏览器不可用", "channel_unavailable", 503)
    module, view = PAGES[page_key]
    process = subprocess.Popen([str(chrome), "--headless=new", "--no-first-run", "--no-default-browser-check",
        "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0",
        "--user-data-dir=" + str(profile), "--window-size=1440,1000", "about:blank"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not port_file.is_file():
            if process.poll() is not None:
                break
            time.sleep(.1)
        if not port_file.is_file():
            raise AiError("截图浏览器未能启动；请关闭占用专用登录目录的窗口", "channel_unavailable", 503)
        port = int(port_file.read_text().splitlines()[0])
        if not 0 < port < 65536:
            raise ValueError("invalid CDP port")
        request = Request(f"http://127.0.0.1:{port}/json/new?about:blank", method="PUT")
        with urlopen(request, timeout=5) as response:
            tab = json.load(response)
        websocket_url = tab["webSocketDebuggerUrl"]
        if not websocket_url.startswith(f"ws://127.0.0.1:{port}/devtools/page/"):
            raise ValueError("invalid CDP tab")
        return _capture_tab(websocket_url, module, view, owner_email)
    except AiError:
        raise
    except Exception:
        raise AiError("定时页面截图结果未确认", "channel_unavailable", 503) from None
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        port_file.unlink(missing_ok=True)


def _capture_tab(websocket_url, module, view, owner_email):
    from websockets.sync.client import connect
    from websockets.exceptions import ConnectionClosed
    sequence = 0
    with connect(websocket_url, open_timeout=10, max_size=4 * 1024 * 1024, proxy=None) as socket:
        def command(method, params=None):
            nonlocal sequence
            sequence += 1
            socket.send(json.dumps({"id": sequence, "method": method, "params": params or {}}))
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                try:
                    reply = json.loads(socket.recv(timeout=max(.1, deadline-time.monotonic())))
                except (TimeoutError, ConnectionClosed):
                    break
                if reply.get("id") == sequence:
                    if "error" in reply:
                        raise AiError("截图浏览器拒绝读取页面", "channel_unavailable", 503)
                    return reply.get("result", {})
            raise AiError("截图页面响应超时", "channel_unavailable", 503)
        command("Page.enable")
        command("Runtime.enable")
        command("Emulation.setTimezoneOverride", {"timezoneId": "Asia/Shanghai"})
        weekly = (module, view) == PAGES["workflow:launch-followup"]
        week_start, week_end = previous_complete_week()
        url = f"{ORIGIN}/?module={module}&view={view}"
        if weekly:
            url += f"&reportCapture=weekly&reportWeek={week_start}"
        command("Page.navigate", {"url": url})
        identity_script = "fetch('/api/ai/screenshot-identity',{cache:'no-store'}).then(async r=>r.ok?await r.json():null)"
        deadline = time.monotonic() + IDENTITY_TIMEOUT
        identity = None
        while time.monotonic() < deadline:
            result = command("Runtime.evaluate", {"expression": identity_script, "awaitPromise": True, "returnByValue": True})
            identity = result.get("result", {}).get("value")
            if isinstance(identity, dict) and identity.get("email") == owner_email.lower():
                break
            time.sleep(.5)
        if not isinstance(identity, dict) or identity.get("email") != owner_email.lower():
            raise AiError("截图登录账号与任务创建人不一致或权限已失效", "access_denied", 403)
        if weekly:
            image = capture_weekly_table(command, week_start, week_end)
        else:
            time.sleep(3)
            result = command("Runtime.evaluate", {"expression": "document.body?.innerText?.length || 0", "returnByValue": True})
            if result.get("result", {}).get("value", 0) < 100:
                raise AiError("截图页面内容尚未就绪", "channel_unavailable", 503)
            image = command("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}).get("data")
        if not isinstance(image, str):
            raise AiError("截图字节缺失", "channel_unavailable", 503)
        raw = base64.b64decode(image, validate=True)
        if not raw.startswith(b"\x89PNG\r\n\x1a\n") or len(raw) > 2 * 1024 * 1024:
            raise AiError("截图文件无效或超过2 MiB", "payload_too_large", 413)
        final_identity = command("Runtime.evaluate", {"expression": identity_script, "awaitPromise": True, "returnByValue": True}).get("result", {}).get("value")
        if not isinstance(final_identity, dict) or final_identity.get("email") != owner_email.lower():
            raise AiError("截图后登录身份或权限已变化", "access_denied", 403)
        return raw
