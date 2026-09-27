"""Network boundary for the local-only Django projection service."""

from __future__ import annotations

from ipaddress import ip_address

from django.conf import settings
from django.core.exceptions import DisallowedHost
from django.http import HttpRequest, JsonResponse


class LoopbackOnlyMiddleware:
    """Reject traffic that did not originate from this machine.

    The public browser boundary remains the local Worker on port 3000.  Django
    deliberately ignores forwarded-address headers and accepts only the socket
    peer address supplied by the WSGI server.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def _error(message: str, code: str, status: int) -> JsonResponse:
        response = JsonResponse({"error": message, "code": code}, status=status)
        response["Cache-Control"] = "no-store"
        return response

    def __call__(self, request: HttpRequest):
        raw_address = request.META.get("REMOTE_ADDR", "")
        try:
            is_loopback = ip_address(raw_address).is_loopback
        except ValueError:
            is_loopback = False
        if not is_loopback:
            return self._error("服务仅允许本机访问。", "loopback_only", 403)

        # ALLOWED_HOSTS is only evaluated when get_host() is called.  Do it at
        # the network boundary so even minimal health views reject DNS-rebinding
        # and arbitrary Host headers.
        try:
            request.get_host()
        except DisallowedHost:
            return self._error("请求主机无效。", "invalid_host", 400)

        header_bytes = 0
        for key, value in request.META.items():
            if key.startswith("HTTP_") or key in {"CONTENT_LENGTH", "CONTENT_TYPE"}:
                header_bytes += len(str(key).encode("utf-8")) + len(str(value).encode("utf-8")) + 4
        if header_bytes > settings.DJANGO_MAX_HEADER_BYTES:
            return self._error(
                "请求头超过安全上限。", "request_headers_too_large", 431
            )

        raw_content_length = request.META.get("CONTENT_LENGTH", "")
        if raw_content_length:
            try:
                content_length = int(raw_content_length)
            except (TypeError, ValueError):
                return self._error("Content-Length 无效。", "invalid_content_length", 400)
            if content_length < 0:
                return self._error("Content-Length 无效。", "invalid_content_length", 400)
            if content_length > settings.DJANGO_MAX_BODY_BYTES:
                return self._error("请求正文超过安全上限。", "request_body_too_large", 413)
        return self.get_response(request)
