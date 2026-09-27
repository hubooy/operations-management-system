"""Run the real local PostgreSQL cutover smoke and atomically persist its evidence."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import tempfile
import time
import uuid
from datetime import UTC, timedelta
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from sales.models import SalesWriteRequestReceipt
from sales.cutover_attestation import require_valid_cutover_attestation
from sales.retirement_preflight import (
    REQUIRED_SMOKE_CHECKS,
    SMOKE_RESPONSE_CONTRACTS,
    SMOKE_RECEIPT_VERSION,
    _canonical_json,
    smoke_online_paths,
)
from sales.write_service import lock_active_write_authority


SMOKE_CHECK_EVIDENCE_VERSION = "sales-smoke-check-evidence-v1"
SMOKE_RECEIPT_BUNDLE_VERSION = "sales-smoke-receipt-bundle-v1"
MAX_HTTP_RESPONSE_BYTES = 8 * 1024 * 1024
HTTP_TIMEOUT_SECONDS = 30
WRITER_DATABASE_ROLE = "teruisi_sales_writer"
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_CUTOVER_ID = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


class SmokeReceiptGenerationError(RuntimeError):
    """A non-sensitive smoke generation error safe for an operator terminal."""


HttpRequester = Callable[[str, dict[str, str], int], tuple[int, dict[str, str], bytes]]


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _validate_identity(value: str, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise SmokeReceiptGenerationError(f"{label} 无效")
    return value


def _loopback_base_url(value: str, expected_port: int) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise SmokeReceiptGenerationError("smoke 服务地址无效") from error
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or port != expected_port
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise SmokeReceiptGenerationError("smoke 服务必须是固定 127.0.0.1 端口")
    return f"http://127.0.0.1:{expected_port}"


def _signed_headers(path_and_query: str, secret: str, observed_unix: int) -> dict[str, str]:
    if len(secret.encode("utf-8")) < 32:
        raise SmokeReceiptGenerationError("Django 内部签名密钥未安全配置")
    parsed = urlsplit(path_and_query)
    principal = {
        "email": "retirement-smoke@local.invalid",
        "displayName": "Retirement Smoke",
        "role": "admin",
        "scope": None,
    }
    principal_bytes = json.dumps(
        principal, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(principal_bytes).decode("ascii").rstrip("=")
    timestamp = str(observed_unix)
    request_id = f"retirement-smoke-{uuid.uuid4().hex}"
    body_sha256 = _sha256(b"")
    canonical = "\n".join([
        "v1", timestamp, request_id, "GET", parsed.path, parsed.query,
        body_sha256, encoded,
    ])
    signature = hmac.new(
        secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return {
        "Accept": "application/json",
        "X-Teruisi-Principal": encoded,
        "X-Teruisi-Timestamp": timestamp,
        "X-Teruisi-Request-Id": request_id,
        "X-Teruisi-Content-SHA256": body_sha256,
        "X-Teruisi-Signature": f"v1={signature}",
    }


def _default_http_requester(
    url: str, headers: dict[str, str], timeout_seconds: int
) -> tuple[int, dict[str, str], bytes]:
    try:
        with urlopen(Request(url, headers=headers, method="GET"), timeout=timeout_seconds) as response:
            body = response.read(MAX_HTTP_RESPONSE_BYTES + 1)
            response_headers = {key.lower(): value for key, value in response.headers.items()}
            status = int(response.status)
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise SmokeReceiptGenerationError("本机 Django smoke HTTP 请求失败") from error
    if len(body) > MAX_HTTP_RESPONSE_BYTES:
        raise SmokeReceiptGenerationError("本机 Django smoke 响应超出上限")
    return status, response_headers, body


def _online_evidence(
    *,
    check: str,
    base_url: str,
    observed_at: str,
    observed_unix: int,
    secret: str,
    requester: HttpRequester,
    path_and_query: str,
) -> dict[str, object]:
    headers = {} if check == "writer_readiness" else _signed_headers(
        path_and_query, secret, observed_unix
    )
    status, response_headers, body = requester(
        f"{base_url}{path_and_query}", headers, HTTP_TIMEOUT_SECONDS
    )
    if status != 200 or len(body) > MAX_HTTP_RESPONSE_BYTES:
        raise SmokeReceiptGenerationError("本机 Django smoke 检查未通过")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SmokeReceiptGenerationError("本机 Django smoke 响应不是有效 JSON") from error
    if not isinstance(payload, dict):
        raise SmokeReceiptGenerationError("本机 Django smoke 响应契约无效")
    if check == "writer_readiness":
        if payload.get("status") != "ready" or payload.get("writer") != "ready":
            raise SmokeReceiptGenerationError("writer readiness smoke 未通过")
        sales_data_revision = ""
        sales_source_revision = ""
    else:
        sales_data_revision = response_headers.get("x-sales-data-revision", "")
        sales_source_revision = response_headers.get("x-sales-source-revision", "")
        if (
            sales_data_revision != sales_source_revision
            or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", sales_data_revision)
        ):
            raise SmokeReceiptGenerationError("sales smoke revision 双头不一致或无效")
        if check == "sales_summary":
            current = payload.get("current")
            cutoff = payload.get("dataCutoffDate")
            if (
                payload.get("projection") != "dashboard"
                or not isinstance(current, dict)
                or not {"netSalesCents", "netQuantity", "grossMarginRate"}.issubset(current)
                or not (cutoff is None or isinstance(cutoff, str))
            ):
                raise SmokeReceiptGenerationError("sales summary smoke 响应契约无效")
        elif check == "sales_category_analysis":
            summary = payload.get("summary")
            details = payload.get("details")
            pagination = details.get("pagination") if isinstance(details, dict) else None
            if (
                not isinstance(summary, dict)
                or not {"netSalesCents", "categoryCount"}.issubset(summary)
                or not isinstance(details, dict)
                or not isinstance(details.get("items"), list)
                or not isinstance(pagination, dict)
                or not {"page", "pageSize", "total", "returned", "truncated"}.issubset(pagination)
                or "dataCutoffDate" not in payload
            ):
                raise SmokeReceiptGenerationError("sales category smoke 响应契约无效")
        elif check == "sales_category_analysis_detail":
            totals = payload.get("totals")
            pagination = payload.get("pagination")
            if (
                payload.get("category") != "__retirement_smoke_no_match__"
                or not isinstance(totals, dict)
                or not {"netSalesCents", "platformCount", "shopCount"}.issubset(totals)
                or not isinstance(payload.get("platforms"), list)
                or not isinstance(pagination, dict)
                or not {"total", "returned", "truncated", "limit"}.issubset(pagination)
            ):
                raise SmokeReceiptGenerationError("sales category detail smoke 响应契约无效")
    return {
        "version": SMOKE_CHECK_EVIDENCE_VERSION,
        "check": check,
        "method": "GET",
        "path": path_and_query,
        "statusCode": status,
        "bodySha256": _sha256(body),
        "salesDataRevision": sales_data_revision,
        "salesSourceRevision": sales_source_revision,
        "responseContract": SMOKE_RESPONSE_CONTRACTS[check],
        "observedAt": observed_at,
    }


class _ExpectedRollback(Exception):
    pass


def _writer_rollback_evidence(
    cutover_id: str, attestation_sha256: str, observed_at: str
) -> dict[str, object]:
    if connection.vendor != "postgresql":
        raise SmokeReceiptGenerationError("smoke 生成必须使用 PostgreSQL writer 连接")
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_user")
            current_user = str(cursor.fetchone()[0])
    except Exception as error:
        raise SmokeReceiptGenerationError("无法验证 PostgreSQL writer 数据库角色") from error
    if current_user != WRITER_DATABASE_ROLE:
        raise SmokeReceiptGenerationError("smoke 生成连接不是受控 sales writer 角色")

    request_id = f"retirement-smoke-{uuid.uuid4().hex}"
    authority_epoch = ""
    try:
        with transaction.atomic():
            authority = lock_active_write_authority()
            if authority.cutover_id != cutover_id:
                raise SmokeReceiptGenerationError("writer authority 与 smoke cutover 不一致")
            require_valid_cutover_attestation(
                cutover_id=cutover_id,
                payload_sha256=attestation_sha256,
            )
            authority_epoch = str(authority.authority_epoch)
            SalesWriteRequestReceipt.objects.create(
                request_id=request_id,
                actor_email="retirement-smoke@local.invalid",
                method="POST",
                path="/internal/retirement-smoke/rollback",
                body_sha256=_sha256(b"retirement-smoke"),
                claim_token=uuid.uuid4().hex,
                status="processing",
                response_status=0,
                response_payload={},
                expires_at=timezone.now() + timedelta(minutes=5),
            )
            if not SalesWriteRequestReceipt.objects.filter(request_id=request_id).exists():
                raise SmokeReceiptGenerationError("writer rollback probe 临时写入未生效")
            raise _ExpectedRollback
    except _ExpectedRollback:
        pass
    if SalesWriteRequestReceipt.objects.filter(request_id=request_id).exists():
        raise SmokeReceiptGenerationError("writer rollback probe 遗留了临时写入")
    return {
        "version": SMOKE_CHECK_EVIDENCE_VERSION,
        "check": "sales_write_transaction_rollback_probe",
        "status": "passed",
        "cutoverId": cutover_id,
        "authorityEpoch": authority_epoch,
        "requestReceiptRollbackVerified": True,
        "observedAt": observed_at,
    }


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        # Python's os.open cannot open a Windows directory handle.  Use the
        # documented backup-semantics handle and FlushFileBuffers so the atomic
        # bundle rename is durably ordered on NTFS as well as POSIX filesystems.
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.restype = ctypes.c_void_p
        handle = kernel32.CreateFileW(
            str(path),
            0x40000000,  # GENERIC_WRITE (required by FlushFileBuffers)
            0x00000001 | 0x00000002 | 0x00000004,  # SHARE read/write/delete
            None,
            3,  # OPEN_EXISTING
            0x02000000,  # FILE_FLAG_BACKUP_SEMANTICS
            None,
        )
        invalid_handle = ctypes.c_void_p(-1).value
        if handle in {None, invalid_handle}:
            raise SmokeReceiptGenerationError("smoke evidence 目录持久化失败")
        try:
            if kernel32.FlushFileBuffers(ctypes.c_void_p(handle)) == 0:
                raise SmokeReceiptGenerationError("smoke evidence 目录持久化失败")
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_file(path: Path, value: object) -> str:
    raw = f"{_canonical_json(value)}\n".encode("utf-8")
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return _sha256(raw)


def generate_smoke_receipt_bundle(
    *,
    plan_id: str,
    cutover_id: str,
    attestation_sha256: str,
    output_directory: str,
    reader_base_url: str = "http://127.0.0.1:8001",
    writer_base_url: str = "http://127.0.0.1:8002",
    requester: HttpRequester | None = None,
    now=None,
    writer_probe: Callable[[str, str, str], dict[str, object]] | None = None,
) -> dict[str, object]:
    plan = _validate_identity(plan_id, _HEX_64, "planId")
    cutover = _validate_identity(cutover_id, _CUTOVER_ID, "cutoverId")
    attestation_digest = _validate_identity(
        attestation_sha256, _HEX_64, "attestation sha256"
    )
    reader = _loopback_base_url(reader_base_url, 8001)
    writer = _loopback_base_url(writer_base_url, 8002)
    if not os.path.isabs(output_directory):
        raise SmokeReceiptGenerationError("smoke receipt 输出目录必须是绝对路径")
    destination = Path(output_directory)
    if destination.exists() or destination.suffix:
        raise SmokeReceiptGenerationError("smoke receipt 输出目录必须是新的无后缀目录")
    parent = destination.parent.resolve(strict=True)
    if not parent.is_dir() or parent.is_symlink():
        raise SmokeReceiptGenerationError("smoke receipt 输出父目录无效")

    observed = (now or timezone.now()).astimezone(UTC).replace(microsecond=0)
    observed_at = observed.strftime("%Y-%m-%dT%H:%M:%SZ")
    observed_unix = int(observed.timestamp())
    online_paths = smoke_online_paths(observed)
    secret = str(settings.DJANGO_INTERNAL_SECRET)
    http_requester = requester or _default_http_requester
    evidence: dict[str, dict[str, object]] = {
        "writer_readiness": _online_evidence(
            check="writer_readiness", base_url=writer, observed_at=observed_at,
            observed_unix=observed_unix, secret=secret, requester=http_requester,
            path_and_query=online_paths["writer_readiness"],
        )
    }
    for check in (
        "sales_summary", "sales_category_analysis", "sales_category_analysis_detail"
    ):
        evidence[check] = _online_evidence(
            check=check, base_url=reader, observed_at=observed_at,
            observed_unix=observed_unix, secret=secret, requester=http_requester,
            path_and_query=online_paths[check],
        )
    evidence["sales_write_transaction_rollback_probe"] = (
        writer_probe or _writer_rollback_evidence
    )(cutover, attestation_digest, observed_at)

    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=parent))
    published = False
    try:
        receipt_path = temporary / "receipt.json"
        evidence_directory = temporary / "receipt.json.evidence"
        evidence_directory.mkdir()
        evidence_hashes: dict[str, str] = {}
        for check in REQUIRED_SMOKE_CHECKS:
            evidence_hashes[check] = _write_file(
                evidence_directory / f"{check}.json", evidence[check]
            )
        _fsync_directory(evidence_directory)
        receipt_payload = {
            "version": SMOKE_RECEIPT_VERSION,
            "planId": plan,
            "cutoverId": cutover,
            "attestationPayloadSha256": attestation_digest,
            "checkedAt": observed_at,
            "expiresAt": (observed + timedelta(minutes=5)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "requiredChecks": list(REQUIRED_SMOKE_CHECKS),
            "results": {
                check: {"status": "passed", "evidenceSha256": evidence_hashes[check]}
                for check in REQUIRED_SMOKE_CHECKS
            },
        }
        receipt = {
            "payload": receipt_payload,
            "payloadSha256": _sha256(
                _canonical_json(receipt_payload).encode("utf-8")
            ),
        }
        receipt_file_sha256 = _write_file(receipt_path, receipt)
        _write_file(temporary / "bundle.json", {
            "version": SMOKE_RECEIPT_BUNDLE_VERSION,
            "receiptFile": "receipt.json",
            "receiptSha256": receipt_file_sha256,
        })
        _fsync_directory(temporary)
        os.rename(temporary, destination)
        published = True
        _fsync_directory(parent)
    finally:
        if not published:
            shutil.rmtree(temporary, ignore_errors=True)
    return {
        "status": "completed",
        "version": SMOKE_RECEIPT_BUNDLE_VERSION,
        "planId": plan,
        "cutoverId": cutover,
        "attestationPayloadSha256": attestation_digest,
        "checkedAt": observed_at,
        "receiptSha256": receipt_file_sha256,
    }
