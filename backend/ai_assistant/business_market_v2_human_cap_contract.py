"""Exact, default-closed input for one administrator's market report cap.

This contract records an explicit CNY ceiling. It does not validate a tariff,
reserve money, authenticate a provider account, or permit a model request.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re


SCHEMA = "business-market-v2-human-cap-approval-request-v1"
FIELDS = {"schemaVersion", "ledgerId", "planId", "reportId",
    "ledgerDigest", "planDigest", "reportSnapshotDigest",
    "modelConfigDigest", "approvedCapCents", "expiresAtUtc",
    "explicitApproval"}
SHA = re.compile(r"[0-9a-f]{64}\Z")
IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")
UTC = "%Y-%m-%dT%H:%M:%SZ"


class HumanCapInputError(ValueError):
    pass


def _need(value):
    if not value:
        raise HumanCapInputError("市场报告费用上限请求无效")


def _time(value):
    _need(type(value) is str and len(value) == 20 and value.endswith("Z"))
    try:
        parsed = datetime.strptime(value, UTC).replace(tzinfo=timezone.utc)
    except ValueError as error:
        raise HumanCapInputError("费用上限有效期无效") from error
    _need(parsed.strftime(UTC) == value)
    return parsed


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False)


def request(payload, *, now_utc):
    """Validate an exact user action, then return canonical bytes and digest."""
    _need(type(payload) is dict and set(payload) == FIELDS)
    _need(payload["schemaVersion"] == SCHEMA
        and payload["explicitApproval"] is True)
    for field in ("ledgerId", "planId", "ledgerDigest", "planDigest",
            "reportSnapshotDigest", "modelConfigDigest"):
        _need(type(payload[field]) is str and SHA.fullmatch(payload[field]))
    _need(type(payload["reportId"]) is str
        and IDENTIFIER.fullmatch(payload["reportId"]))
    cents = payload["approvedCapCents"]
    _need(type(cents) is int and 1 <= cents <= 100_000_000)
    now, expiry = _time(now_utc), _time(payload["expiresAtUtc"])
    _need(now < expiry <= now + timedelta(days=31))
    encoded = canonical(payload)
    _need(len(encoded.encode("utf-8")) <= 4096)
    return {"canonicalJson": encoded,
        "requestDigest": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        "approvedCapCents": cents, "expiresAtUtc": payload["expiresAtUtc"],
        "providerCallsAllowed": False, "fundsReserved": False}
