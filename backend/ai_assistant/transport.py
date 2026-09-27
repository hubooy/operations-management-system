"""Bounded HTTPS, exact origins, fixed DNS result, no redirects or automatic retries."""

from __future__ import annotations
import base64
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
import socket
import ssl
import time
from contextlib import contextmanager
from contextvars import ContextVar
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from threading import BoundedSemaphore, Event, Thread
from urllib.parse import urlencode, urlsplit
from django.conf import settings
from .policy import AiError, canonical, current_principal, uid

_deadline = ContextVar("ai_network_deadline", default=None)
_cancel_check = ContextVar("ai_network_cancel_check", default=None)
_dns_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ai-dns")
_dns_slots = BoundedSemaphore(4)
_synthetic_network = ipaddress.ip_network("198.18.0.0/15")


class ProviderHttpError(AiError):
    """Keep transport status for audit without disclosing an upstream body."""
    def __init__(self, status):
        super().__init__(f"服务返回 HTTP {status}",
                         "provider_rate_limited" if status == 429 else "provider_error", 503)
        self.diagnostics = {"httpStatus": status}


@contextmanager
def request_budget(seconds):
    parent = _deadline.get()
    deadline = time.monotonic() + seconds
    token = _deadline.set(min(parent, deadline) if parent is not None else deadline)
    try:
        yield
    finally:
        _deadline.reset(token)


def remaining_budget(default=120):
    check = _cancel_check.get()
    if check:
        check()
    deadline = _deadline.get()
    remaining = (
        default if deadline is None else min(default, deadline - time.monotonic())
    )
    if remaining <= 0:
        raise AiError("AI 请求已达到总时间上限", "provider_timeout", 503)
    return remaining


def limit_request_budget(seconds):
    current = _deadline.get()
    desired = time.monotonic() + seconds
    _deadline.set(min(current, desired) if current is not None else desired)


@contextmanager
def request_cancellation(check):
    token = _cancel_check.set(check)
    try:
        yield
    finally:
        _cancel_check.reset(token)


def watch_socket_cancellation(current_socket):
    """Wake a blocked HTTP read on disconnect, even with a long task deadline."""
    check = _cancel_check.get()
    stopped = Event()
    def watch():
        while not stopped.wait(.25):
            try:
                check()
            except Exception:
                owned_socket = current_socket()
                try:
                    owned_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                # Windows select is not woken by local shutdown alone. Detach
                # transfers ownership so HTTP makefile references cannot defer
                # closing the handle, and later cleanup cannot close it twice.
                handle = owned_socket.detach()
                if handle != -1:
                    socket.close(handle)
                    return
    thread = Thread(target=watch, name="ai-http-cancellation", daemon=True) if check else None
    if thread:
        thread.start()
    def stop():
        stopped.set()
        if thread:
            thread.join(timeout=1)
    return stop


def bounded_sse(url, body, headers, *, timeout, collector):
    from .model_capabilities import MAX_PROVIDER_STREAM_BYTES
    return _bounded_json(url, {**body, "stream": True}, {**headers, "Accept": "text/event-stream"},
                         timeout=timeout, stream_collector=collector, maximum=MAX_PROVIDER_STREAM_BYTES)


def resolve_addresses(host, port, timeout):
    if not _dns_slots.acquire(blocking=False):
        raise AiError("DNS 请求繁忙", "service_unavailable", 503)
    future = _dns_pool.submit(socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM)
    future.add_done_callback(lambda _: _dns_slots.release())
    try:
        return future.result(timeout=min(5, timeout))
    except (OSError, FutureTimeout) as error:
        raise AiError("DNS 查询失败或超时", "service_unavailable", 503) from error


def bounded_json(
    url, body, headers=None, *, timeout=60, maximum=2 * 1024 * 1024, internal=False
):
    return _bounded_json(
        url, body, headers, timeout=timeout, maximum=maximum, internal=internal
    )


def public_model_addresses(url, addresses, timeout):
    from .configuration import endpoint
    return _public_addresses(url, addresses, timeout, endpoint)


def _public_addresses(url, addresses, timeout, origin_guard):
    """Replace proxy synthetic DNS only; never connect to or allowlist fake IPs."""
    ips = [ipaddress.ip_address(addr[4][0]) for addr in addresses]
    if not any(ip in _synthetic_network for ip in ips):
        return addresses
    if any(not ip.is_global and ip not in _synthetic_network for ip in ips):
        raise AiError("请求目标解析到非公网地址", "access_denied", 403)
    # Each caller supplies its own exact origin policy before DNS recovery.
    origin_guard(url)
    parts = urlsplit(url)
    host = parts.hostname
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise AiError("禁止非公网模型地址", "access_denied", 403)
    started = time.monotonic()
    budget = min(5, timeout)
    result = []
    for record_type in (1, 28):
        remaining = budget - (time.monotonic() - started)
        if remaining <= 0:
            raise AiError("DNS 查询超时", "service_unavailable", 503)
        # Fixed public IP avoids the same intercepted OS lookup. TLS verifies
        # this IP's certificate. No provider headers/body are sent to DNS.
        answer = _bounded_json(
            "https://1.1.1.1/dns-query?"
            + urlencode({"name": host, "type": record_type}),
            None,
            {"Accept": "application/dns-json"},
            timeout=remaining,
            maximum=16 * 1024,
            method="GET",
            fixed_addresses=[
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    ("1.1.1.1", 443),
                )
            ],
        )
        question = answer.get("Question")
        records = answer.get("Answer", [])
        if (
            type(answer.get("Status")) is not int
            or answer["Status"] != 0
            or answer.get("TC") is not False
            or not isinstance(question, list)
            or len(question) != 1
            or not isinstance(question[0], dict)
            or not isinstance(question[0].get("name"), str)
            or question[0].get("name", "").rstrip(".").lower() != host.lower()
            or type(question[0].get("type")) is not int
            or question[0].get("type") != record_type
            or not isinstance(records, list)
            or len(records) > 32
        ):
            raise AiError("公网 DNS 响应无效", "service_unavailable", 503)
        for record in records:
            if not isinstance(record, dict):
                raise AiError("公网 DNS 记录无效", "service_unavailable", 503)
            if type(record.get("type")) is not int:
                raise AiError("公网 DNS 记录类型无效", "service_unavailable", 503)
            if record["type"] not in (1, 28):
                continue
            if record["type"] != record_type or not isinstance(record.get("data"), str):
                raise AiError("公网 DNS 地址类型无效", "service_unavailable", 503)
            try:
                ip = ipaddress.ip_address(record.get("data"))
            except ValueError as error:
                raise AiError(
                    "公网 DNS 地址无效", "service_unavailable", 503
                ) from error
            if (
                not ip.is_global
                or ip.is_multicast
                or ip.version != (4 if record["type"] == 1 else 6)
            ):
                raise AiError("请求目标解析到非公网地址", "access_denied", 403)
            family = socket.AF_INET if ip.version == 4 else socket.AF_INET6
            target = (str(ip), parts.port or 443)
            if ip.version == 6:
                target += (0, 0)
            item = (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", target)
            if item not in result:
                result.append(item)
    if not result or len(result) > 32:
        raise AiError("公网 DNS 未返回有效地址", "service_unavailable", 503)
    return result


def _bounded_json(
    url,
    body,
    headers=None,
    *,
    timeout=60,
    maximum=2 * 1024 * 1024,
    internal=False,
    method="POST",
    fixed_addresses=None,
    stream_collector=None,
):
    parts = urlsplit(url)
    if (
        parts.scheme not in {"https", "http"}
        or parts.username
        or parts.password
        or parts.fragment
    ):
        raise AiError("请求目标无效")
    host = parts.hostname
    port = parts.port or (443 if parts.scheme == "https" else 80)
    local = host in {"127.0.0.1", "localhost", "::1"}
    permit_local = (
        internal
        or settings.DJANGO_ENVIRONMENT == "development"
        and os.getenv("AI_ALLOW_LOCAL_MODEL_ENDPOINTS") == "true"
    )
    if internal and not local:
        raise AiError("内部执行桥必须使用回环地址", "service_unavailable", 503)
    if parts.scheme == "http" and not (local and permit_local):
        raise AiError("请求必须使用 HTTPS")
    timeout = remaining_budget(timeout)
    started = time.monotonic()
    addresses = (
        fixed_addresses
        if fixed_addresses is not None
        else resolve_addresses(host, port, timeout)
    )
    if not addresses or len(addresses) > 32:
        raise AiError("DNS 结果异常", "service_unavailable", 503)
    if not internal and fixed_addresses is None and not local:
        addresses = public_model_addresses(
            url, addresses, timeout - (time.monotonic() - started)
        )
    for addr in addresses:
        address = ipaddress.ip_address(addr[4][0])
        if (not address.is_global or address.is_multicast) and not (
            local and permit_local
        ):
            raise AiError("请求目标解析到非公网地址", "access_denied", 403)
    family, socktype, proto, _, target = addresses[0]
    remaining = timeout - (time.monotonic() - started)
    if remaining <= 0:
        raise AiError("请求超时", "provider_timeout", 503)
    sock = socket.socket(family, socktype, proto)
    sock.settimeout(remaining)
    connection = None
    stop_watching = watch_socket_cancellation(lambda: sock)

    def bounded_socket():
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            raise AiError("请求超时", "provider_timeout", 503)
        sock.settimeout(remaining)

    try:
        sock.connect(target)
        if parts.scheme == "https":
            bounded_socket()
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host, do_handshake_on_connect=False)
            remaining_budget(timeout)
            sock.do_handshake()
        connection = http.client.HTTPConnection(host, port, timeout=remaining)
        connection.sock = sock
        # Fixed, audited media adapters may supply a bounded multipart body.
        # Model and tool JSON callers retain the canonical serialization path.
        if isinstance(body, bytes) and len(body) > 3 * 1024 * 1024:
            raise AiError("请求正文超过上限", "payload_too_large", 413)
        data = (body if isinstance(body, bytes) else canonical(body).encode()) if method == "POST" else None
        request_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            **(headers or {}),
        }
        bounded_socket()
        connection.request(
            method,
            parts.path + ("?" + parts.query if parts.query else ""),
            data,
            request_headers,
        )
        bounded_socket()
        response = connection.getresponse()
        if 300 <= response.status < 400:
            raise AiError("拒绝重定向", "provider_redirect", 503)
        length = response.getheader("Content-Length")
        if length and (not length.isdigit() or int(length) > maximum):
            raise AiError("响应超限", "response_too_large", 503)
        if stream_collector is not None:
            if not 200 <= response.status < 300:
                raise ProviderHttpError(response.status)
            if "text/event-stream" not in (response.getheader("Content-Type") or "").lower():
                raise AiError("模型端点未返回 SSE，未自动重发请求", "invalid_provider_response", 503)
        chunks = []
        count = 0
        while not response.isclosed():
            remaining_budget(timeout)
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise AiError("请求超时", "provider_timeout", 503)
            sock.settimeout(remaining)
            part = response.read1(min(65536, maximum - count + 1))
            if not part:
                break
            count += len(part)
            if count > maximum:
                raise AiError("响应超限", "response_too_large", 503)
            if stream_collector is None:
                chunks.append(part)
            else:
                stream_collector.feed(part)
                if stream_collector.done:
                    break
        if not 200 <= response.status < 300:
            raise ProviderHttpError(response.status)
        remaining_budget(timeout)
        value = (stream_collector.finish() if stream_collector is not None
                 else json.loads(b"".join(chunks).decode("utf-8")))
        if not isinstance(value, dict):
            raise AiError("响应 JSON 无效", "invalid_provider_response", 503)
        return value
    except TimeoutError as error:
        remaining_budget(timeout)
        raise AiError(
            "模型或工具服务等待超时，本次请求未自动重试。请稍后重新发送或缩小分析范围。",
            "provider_timeout", 503,
        ) from error
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise AiError(
            "服务返回了无效的 JSON 响应", "invalid_provider_response", 503
        ) from error
    except (OSError, ValueError, http.client.HTTPException) as error:
        remaining_budget(timeout)
        raise AiError(
            "服务请求失败或响应格式无效", "provider_unavailable", 503
        ) from error
    finally:
        stop_watching()
        if connection:
            connection.close()
        sock.close()


def signed_headers(path, body, principal, request_id=None):
    secret = os.getenv("TERUISI_DJANGO_INTERNAL_SECRET", "")
    if len(secret.encode()) < 32:
        raise AiError("内部签名服务未配置", "service_unavailable", 503)
    request_id = request_id or uid("ai-edge")
    stamp = str(int(time.time()))
    encoded = (
        base64.urlsafe_b64encode(
            canonical(
                {
                    "email": principal.email,
                    "displayName": principal.display_name,
                    "role": principal.role,
                    "scope": principal.scope,
                }
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    sha = hashlib.sha256(canonical(body).encode()).hexdigest()
    message = "\n".join(["v1", stamp, request_id, "POST", path, "", sha, encoded])
    signature = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return {
        "X-Teruisi-Principal": encoded,
        "X-Teruisi-Timestamp": stamp,
        "X-Teruisi-Request-Id": request_id,
        "X-Teruisi-Content-SHA256": sha,
        "X-Teruisi-Signature": "v1=" + signature,
    }


def edge(action, payload, principal):
    if action not in {"authorize_background", "catalog", "execute", "dataset"}:
        raise AiError("AI 内部动作不受支持", "access_denied", 403)
    if action != "authorize_background":
        current_principal(principal)
    base = os.getenv("TERUISI_DJANGO_AI_EDGE_BASE_URL", "").rstrip("/")
    if not base:
        raise AiError("AI 工具执行桥未配置", "service_unavailable", 503)
    path = "/api/ai/internal/edge"
    body = {"action": action, **payload}
    return bounded_json(
        base + path,
        body,
        signed_headers(path, body, principal),
        timeout=35,
        maximum=10 * 1024 * 1024,
        internal=True,
    )


def catalog(principal, surface):
    result = edge("catalog", {"surface": surface}, principal)
    entries = result.get("entries")
    if (
        not isinstance(entries, list)
        or len(entries) > 100
        or len({v.get("name") for v in entries}) != len(entries)
    ):
        raise AiError("中央工具目录无效", "service_unavailable", 503)
    return entries


def execute_tool(
    name, args, principal, *, surface, request_id, provider_call_id="", policy_digest=""
):
    return edge(
        "execute",
        {
            "name": name,
            "arguments": args,
            "surface": surface,
            "requestId": request_id,
            "providerCallId": provider_call_id,
            "policyDigest": policy_digest,
        },
        principal,
    )
