"""Fail-closed PostgreSQL/smoke evidence for the separate D1 retirement operator."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from django.db import DatabaseError, transaction
from django.utils import timezone

from sales.authority_lock import acquire_sales_write_authority_shared_lock
from sales.cutover_attestation import (
    SalesCutoverAttestationError,
    require_valid_cutover_attestation,
)
from sales.models import SalesWriteAuthority


SMOKE_RECEIPT_VERSION = "sales-postgresql-smoke-receipt-v1"
RETIREMENT_PREFLIGHT_VERSION = "sales-retirement-preflight-v1"
SMOKE_RECEIPT_MAX_TTL = timedelta(minutes=10)
SMOKE_RECEIPT_CLOCK_SKEW = timedelta(seconds=30)
MAX_SMOKE_RECEIPT_BYTES = 64 * 1024
REQUIRED_SMOKE_CHECKS = (
    "writer_readiness",
    "sales_summary",
    "sales_category_analysis",
    "sales_category_analysis_detail",
    "sales_write_transaction_rollback_probe",
)
SMOKE_RESPONSE_CONTRACTS = {
    "writer_readiness": "sales-writer-readiness-v1",
    "sales_summary": "sales-summary-dashboard-v1",
    "sales_category_analysis": "sales-category-analysis-page-v1",
    "sales_category_analysis_detail": "sales-category-detail-empty-sentinel-v1",
}

_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_CUTOVER_ID = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_RFC3339_UTC_SECONDS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class RetirementPreflightError(RuntimeError):
    """A public, deliberately non-sensitive retirement preflight failure."""


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _exact_object(value: object, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise RetirementPreflightError("smoke receipt 契约无效")
    return value


def _utc_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not _RFC3339_UTC_SECONDS.fullmatch(value):
        raise RetirementPreflightError("smoke receipt 时间无效")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as error:
        raise RetirementPreflightError("smoke receipt 时间无效") from error
    return parsed


def smoke_online_paths(observed_at: datetime) -> dict[str, str]:
    business_date = observed_at.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
    return {
        "writer_readiness": "/health/ready",
        "sales_summary": "/api/sales/summary?range=today&view=dashboard",
        "sales_category_analysis": (
            f"/api/sales/category-analysis?startDate={business_date}"
            f"&endDate={business_date}&page=1&pageSize=1"
        ),
        "sales_category_analysis_detail": (
            f"/api/sales/category-analysis/detail?startDate={business_date}"
            f"&endDate={business_date}&category=__retirement_smoke_no_match__"
        ),
    }


def _safe_identity(value: object, *, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise RetirementPreflightError(f"{label} 无效")
    return value


def _read_receipt(
    path_value: str, expected_file_sha256: str
) -> tuple[dict[str, Any], str, Path]:
    if not isinstance(path_value, str) or not os.path.isabs(path_value):
        raise RetirementPreflightError("smoke receipt 路径无效")
    if not _HEX_64.fullmatch(expected_file_sha256):
        raise RetirementPreflightError("smoke receipt 文件摘要无效")
    original = Path(path_value)
    if original.suffix.lower() != ".json" or original.is_symlink():
        raise RetirementPreflightError("smoke receipt 路径无效")
    try:
        resolved = original.resolve(strict=True)
        before = resolved.stat()
        if not resolved.is_file() or before.st_size < 1 or before.st_size > MAX_SMOKE_RECEIPT_BYTES:
            raise RetirementPreflightError("smoke receipt 文件无效")
        raw = resolved.read_bytes()
        after = resolved.stat()
    except RetirementPreflightError:
        raise
    except (OSError, RuntimeError) as error:
        raise RetirementPreflightError("smoke receipt 文件不可读") from error
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after or len(raw) != before.st_size:
        raise RetirementPreflightError("smoke receipt 文件读取期间发生变化")
    file_sha256 = _sha256_bytes(raw)
    if file_sha256 != expected_file_sha256:
        raise RetirementPreflightError("smoke receipt 文件摘要不匹配")
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RetirementPreflightError("smoke receipt JSON 无效") from error
    return _exact_object(decoded, {"payload", "payloadSha256"}), file_sha256, resolved


def _read_smoke_evidence(path: Path, expected_sha256: str) -> dict[str, Any]:
    if path.is_symlink() or not _HEX_64.fullmatch(expected_sha256):
        raise RetirementPreflightError("smoke evidence 文件无效")
    try:
        before = path.stat()
        raw = path.read_bytes()
        after = path.stat()
    except OSError as error:
        raise RetirementPreflightError("smoke evidence 文件不可读") from error
    if (
        not path.is_file()
        or before.st_size < 1
        or before.st_size > 16 * 1024
        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or len(raw) != before.st_size
        or _sha256_bytes(raw) != expected_sha256
    ):
        raise RetirementPreflightError("smoke evidence 文件摘要或身份不匹配")
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RetirementPreflightError("smoke evidence JSON 无效") from error
    evidence = _exact_object(decoded, set(decoded) if isinstance(decoded, dict) else set())
    if raw != f"{_canonical_json(evidence)}\n".encode("utf-8"):
        raise RetirementPreflightError("smoke evidence 不是规范化证据")
    return evidence


def _validate_smoke_evidence_files(payload: dict[str, Any], receipt_path: Path) -> None:
    evidence_directory = Path(f"{receipt_path}.evidence")
    if evidence_directory.is_symlink():
        raise RetirementPreflightError("smoke evidence 目录无效")
    try:
        if not evidence_directory.is_dir():
            raise RetirementPreflightError("smoke evidence 目录缺失")
        names = {item.name for item in evidence_directory.iterdir()}
    except OSError as error:
        raise RetirementPreflightError("smoke evidence 目录不可读") from error
    expected_names = {f"{name}.json" for name in REQUIRED_SMOKE_CHECKS}
    if names != expected_names:
        raise RetirementPreflightError("smoke evidence 文件集合不完整")
    results = payload["results"]
    expected_paths = smoke_online_paths(_utc_timestamp(payload["checkedAt"]))
    for name in REQUIRED_SMOKE_CHECKS:
        evidence = _read_smoke_evidence(
            evidence_directory / f"{name}.json",
            results[name]["evidenceSha256"],
        )
        if name in expected_paths:
            evidence = _exact_object(evidence, {
                "version", "check", "method", "path", "statusCode",
                "bodySha256", "salesDataRevision", "salesSourceRevision",
                "responseContract", "observedAt",
            })
            data_revision = evidence["salesDataRevision"]
            source_revision = evidence["salesSourceRevision"]
            if (
                evidence["version"] != "sales-smoke-check-evidence-v1"
                or evidence["check"] != name
                or evidence["method"] != "GET"
                or evidence["path"] != expected_paths[name]
                or evidence["responseContract"] != SMOKE_RESPONSE_CONTRACTS[name]
                or type(evidence["statusCode"]) is not int
                or evidence["statusCode"] != 200
                or not isinstance(evidence["bodySha256"], str)
                or not _HEX_64.fullmatch(evidence["bodySha256"])
                or evidence["observedAt"] != payload["checkedAt"]
                or not isinstance(data_revision, str)
                or not isinstance(source_revision, str)
                or (name == "writer_readiness" and (data_revision != "" or source_revision != ""))
                or (
                    name != "writer_readiness"
                    and (
                        data_revision != source_revision
                        or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", data_revision)
                    )
                )
            ):
                raise RetirementPreflightError("online smoke evidence 无效")
        else:
            evidence = _exact_object(evidence, {
                "version", "check", "status", "cutoverId", "authorityEpoch",
                "requestReceiptRollbackVerified", "observedAt",
            })
            try:
                authority_epoch = str(uuid.UUID(str(evidence["authorityEpoch"])))
            except (ValueError, TypeError, AttributeError) as error:
                raise RetirementPreflightError("writer rollback evidence 无效") from error
            if (
                evidence["version"] != "sales-smoke-check-evidence-v1"
                or evidence["check"] != name
                or evidence["status"] != "passed"
                or evidence["cutoverId"] != payload["cutoverId"]
                or authority_epoch != evidence["authorityEpoch"]
                or evidence["requestReceiptRollbackVerified"] is not True
                or evidence["observedAt"] != payload["checkedAt"]
            ):
                raise RetirementPreflightError("writer rollback evidence 无效")


def validate_smoke_receipt(
    receipt: dict[str, Any],
    *,
    expected_plan_id: str,
    expected_cutover_id: str,
    expected_attestation_sha256: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    payload_sha256 = receipt["payloadSha256"]
    if not isinstance(payload_sha256, str) or not _HEX_64.fullmatch(payload_sha256):
        raise RetirementPreflightError("smoke receipt payload 摘要无效")
    payload = _exact_object(receipt["payload"], {
        "version",
        "planId",
        "cutoverId",
        "attestationPayloadSha256",
        "checkedAt",
        "expiresAt",
        "requiredChecks",
        "results",
    })
    if _sha256_bytes(_canonical_json(payload).encode("utf-8")) != payload_sha256:
        raise RetirementPreflightError("smoke receipt payload 摘要不匹配")
    if (
        payload["version"] != SMOKE_RECEIPT_VERSION
        or payload["planId"] != expected_plan_id
        or payload["cutoverId"] != expected_cutover_id
        or payload["attestationPayloadSha256"] != expected_attestation_sha256
    ):
        raise RetirementPreflightError("smoke receipt 身份不匹配")
    required_checks = payload["requiredChecks"]
    if not isinstance(required_checks, list) or required_checks != list(REQUIRED_SMOKE_CHECKS):
        raise RetirementPreflightError("smoke receipt required checks 不完整")
    results = _exact_object(payload["results"], set(REQUIRED_SMOKE_CHECKS))
    for name in REQUIRED_SMOKE_CHECKS:
        result = _exact_object(results[name], {"status", "evidenceSha256"})
        if result["status"] != "passed" or not isinstance(result["evidenceSha256"], str):
            raise RetirementPreflightError("smoke receipt check 未通过")
        if not _HEX_64.fullmatch(result["evidenceSha256"]):
            raise RetirementPreflightError("smoke receipt check 证据无效")

    checked_at = _utc_timestamp(payload["checkedAt"])
    expires_at = _utc_timestamp(payload["expiresAt"])
    observed_now = now or timezone.now()
    if observed_now.tzinfo is None:
        observed_now = observed_now.replace(tzinfo=UTC)
    observed_now = observed_now.astimezone(UTC)
    ttl = expires_at - checked_at
    if (
        ttl <= timedelta(0)
        or ttl > SMOKE_RECEIPT_MAX_TTL
        or checked_at > observed_now + SMOKE_RECEIPT_CLOCK_SKEW
        or observed_now >= expires_at
    ):
        raise RetirementPreflightError("smoke receipt 已过期或时效无效")
    return payload


def verify_retirement_preflight(
    *,
    plan_id: str,
    cutover_id: str,
    attestation_sha256: str,
    smoke_receipt_path: str,
    smoke_receipt_sha256: str,
    now: datetime | None = None,
) -> dict[str, object]:
    plan = _safe_identity(plan_id, pattern=_HEX_64, label="planId")
    cutover = _safe_identity(cutover_id, pattern=_CUTOVER_ID, label="cutoverId")
    attestation_digest = _safe_identity(
        attestation_sha256, pattern=_HEX_64, label="attestation sha256"
    )
    receipt, receipt_file_sha256, resolved_receipt_path = _read_receipt(
        smoke_receipt_path, smoke_receipt_sha256
    )
    payload = validate_smoke_receipt(
        receipt,
        expected_plan_id=plan,
        expected_cutover_id=cutover,
        expected_attestation_sha256=attestation_digest,
        now=now,
    )
    _validate_smoke_evidence_files(payload, resolved_receipt_path)
    try:
        with transaction.atomic():
            acquire_sales_write_authority_shared_lock()
            authority = SalesWriteAuthority.objects.get(id=1)
            if authority.status != "active" or authority.cutover_id != cutover:
                raise RetirementPreflightError("PostgreSQL authority 未处于本次 active 终态")
            attestation = require_valid_cutover_attestation(
                cutover_id=cutover,
                payload_sha256=attestation_digest,
            )
    except RetirementPreflightError:
        raise
    except (SalesWriteAuthority.DoesNotExist, SalesCutoverAttestationError, DatabaseError) as error:
        raise RetirementPreflightError("PostgreSQL cutover 证明验证失败") from error

    migration = attestation["postgresqlMigration"]
    evidence: dict[str, object] = {
        "status": "verified",
        "version": RETIREMENT_PREFLIGHT_VERSION,
        "planId": plan,
        "cutoverId": cutover,
        "attestationPayloadSha256": attestation_digest,
        "pgAuthorityStatus": "active",
        "pgAuthorityEpoch": str(authority.authority_epoch),
        "migrationVerifyRunId": migration["verifyRunId"],
        "requiredChecks": list(REQUIRED_SMOKE_CHECKS),
        "checkedAt": payload["checkedAt"],
        "expiresAt": payload["expiresAt"],
        "smokeReceiptSha256": receipt_file_sha256,
    }
    evidence["evidenceSha256"] = _sha256_bytes(
        _canonical_json(evidence).encode("utf-8")
    )
    return evidence
