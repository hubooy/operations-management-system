from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Sequence

from sales.auth import Principal

from .errors import NetshopApiError


SALES_CONSUMER_PATH = "/api/sales/consumers/query"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
# The sales reader publishes the authoritative pair
# ``<sales revision>:<ERP reference revision>``.  Keep this separate from the
# netshop reader's own ``<revision>:<digest prefix>`` response contract.
REVISION_RE = re.compile(r"^\d+:\d+$")
CONTROLLED_JD_ALIASES = {
    "志高商用设备旗舰店": (
        "志高商用设备旗舰店（亿用）",
        "京东-志高商用设备旗舰店（亿用）",
    ),
    "志高商用厨电旗舰店": (
        "志高商用厨电旗舰店",
        "京东-志高商用厨电旗舰店",
    ),
    "志高切肉机旗舰店": (
        "志高切肉机旗舰店（志高迈德豪）",
        "京东-志高切肉机旗舰店（志高迈德豪）",
    ),
    "志高商用洗碗机旗舰店": (
        "志高商用洗碗机旗舰店（志高炊之王）",
        "京东-志高商用洗碗机旗舰店（志高炊之王）",
    ),
}


def _unavailable() -> NetshopApiError:
    return NetshopApiError(
        "Django 销售读取服务暂时不可用",
        code="service_unavailable",
        status=503,
    )


def _base_url() -> str:
    value = (
        os.getenv("TERUISI_DJANGO_SALES_READER_BASE_URL", "").strip()
        or os.getenv("TERUISI_DJANGO_SALES_BASE_URL", "").strip()
        or "http://127.0.0.1:8001"
    )
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise _unavailable()
    if parsed.scheme == "http" and (parsed.hostname or "").lower() not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise _unavailable()
    return value.rstrip("/")


def _principal_envelope(principal: Principal) -> str:
    raw = json.dumps(
        {
            "email": principal.email,
            "displayName": principal.display_name,
            "role": principal.role,
            "scope": principal.scope,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def read_sales_consumer(
    principal: Principal, payload: dict[str, object]
) -> tuple[dict[str, object], str]:
    secret = os.getenv("TERUISI_DJANGO_INTERNAL_SECRET", "")
    if len(secret.encode("utf-8")) < 32:
        raise _unavailable()
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if not body or len(body) > 512 * 1024:
        raise _unavailable()
    digest = hashlib.sha256(body).hexdigest()
    timestamp = str(int(time.time()))
    request_id = str(uuid.uuid4())
    envelope = _principal_envelope(principal)
    canonical = "\n".join(
        [
            "v1",
            timestamp,
            request_id,
            "POST",
            SALES_CONSUMER_PATH,
            "",
            digest,
            envelope,
        ]
    )
    signature = hmac.new(
        secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    request = urllib.request.Request(
        f"{_base_url()}{SALES_CONSUMER_PATH}",
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "X-Teruisi-Content-SHA256": digest,
            "X-Teruisi-Principal": envelope,
            "X-Teruisi-Request-Id": request_id,
            "X-Teruisi-Signature": f"v1={signature}",
            "X-Teruisi-Timestamp": timestamp,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > MAX_RESPONSE_BYTES:
                raise _unavailable()
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise _unavailable()
            content_type = response.headers.get("Content-Type", "")
            revision = response.headers.get("X-Sales-Data-Revision", "")
            if not re.match(r"^application/(?:json|[a-z0-9.+-]+\+json)(?:\s*;|$)", content_type, re.I):
                raise _unavailable()
            if not REVISION_RE.fullmatch(revision):
                raise _unavailable()
            value = json.loads(raw.decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as error:
        raise _unavailable() from error
    if not isinstance(value, dict) or value.get("operation") != payload.get("operation"):
        raise _unavailable()
    data = value.get("data")
    if not isinstance(data, dict):
        raise _unavailable()
    return data, revision


def sales_alias(platform: str, canonical_shop_name: str) -> dict[str, object]:
    if platform == "京东" and canonical_shop_name in CONTROLLED_JD_ALIASES:
        raw_shop, raw_channel = CONTROLLED_JD_ALIASES[canonical_shop_name]
        return {
            "platform": platform,
            "canonicalShopName": canonical_shop_name,
            "rawShopName": raw_shop,
            "rawChannel": raw_channel,
        }
    return {
        "platform": platform,
        "canonicalShopName": canonical_shop_name,
        "rawShopName": canonical_shop_name,
        "rawChannel": None,
    }


def sales_product_metrics(
    principal: Principal,
    *,
    identities: Sequence[dict[str, str]],
    outlets: Sequence[dict[str, str]],
    start_date: str | None,
    end_exclusive: str | None,
    allowed_channels: list[str] | None,
) -> tuple[dict[tuple[str, str, str], dict[str, object]], dict[str, object], str]:
    resolved_identities: list[dict[str, object]] = []
    for identity in identities:
        code = identity["salesProductCode"].strip()
        if identity["platform"] != "京东" or not code or code == "--":
            continue
        resolved_identities.append(
            {
                **sales_alias(identity["platform"], identity["shopName"]),
                "salesProductCode": code,
            }
        )
    resolved_outlets = [sales_alias(item["platform"], item["shopName"]) for item in outlets if item["platform"] == "京东"]
    data, revision = read_sales_consumer(
        principal,
        {
            "operation": "netshop_product_metrics",
            "identities": resolved_identities if start_date else [],
            "outletScopes": resolved_outlets,
            "startDate": start_date,
            "endDate": end_exclusive,
            "allowedChannels": allowed_channels,
        },
    )
    rows = data.get("rows")
    if not isinstance(rows, list) or not isinstance(data.get("platform"), str):
        raise _unavailable()
    mapped: dict[tuple[str, str, str], dict[str, object]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise _unavailable()
        try:
            key = (str(row["platform"]), str(row["shopName"]), str(row["salesProductCode"]))
            values = {
                name: int(row[name])
                for name in (
                    "grossSalesCents",
                    "refundAmountCents",
                    "netSalesCents",
                    "grossProfitCents",
                    "absoluteQuantity",
                    "absoluteCostCents",
                )
            }
        except (KeyError, TypeError, ValueError) as error:
            raise _unavailable() from error
        if key in mapped or any(abs(value) > 9_007_199_254_740_991 for value in values.values()):
            raise _unavailable()
        quantity = values["absoluteQuantity"]
        net = values["netSalesCents"]
        mapped[key] = {
            "costPriceCents": values["absoluteCostCents"] / quantity if quantity > 0 else None,
            "netSalesCents": net,
            "grossMarginRate": values["grossProfitCents"] / net if net != 0 else None,
            "refundRate": values["refundAmountCents"] / values["grossSalesCents"] if values["grossSalesCents"] > 0 else None,
            "salesMatched": True,
        }
    return mapped, data, revision
