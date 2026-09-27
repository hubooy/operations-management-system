"""Pure, default-closed market model transport and rate-source contract.

No provider-specific independent pricing adapter is installed.  The only
accepted rate is an explicitly enabled synthetic test vector from reserved
``.invalid`` domains.  A digest of caller-supplied pricing bytes is never
official tariff authority or permission to call a model.  No API secret is
accepted here; credential ownership and provider account identity remain
unverified even when the model's non-secret transport settings match.
"""
from datetime import datetime, timedelta, timezone
import json
import re
from urllib.parse import urlsplit

from business_analysis.contracts import AnalysisContractError, canonical, digest
from business_analysis.promotion_views import _copy


SCHEMA = "business-market-v2-rate-source-admission-candidate-v1"
SOURCE_SCHEMA = "business-market-v2-rate-source-test-vector-v1"
TEST_RATE_URL = "https://pricing.example.invalid/market-v2/test-vector-v1"
TEST_FX_URL = "https://fx.example.invalid/market-v2/test-vector-v1"
CATEGORIES = ["input_tokens", "output_tokens"]
MODEL_FIELDS = {"id", "version", "status", "modelType", "protocol",
    "modelName", "baseUrl", "generationOptionsJson", "maxTokens",
    "maxToolRounds", "maxTotalToolCalls", "timeoutMs", "reasoningMode",
    "temperatureMilli"}
SOURCE_FIELDS = {"schemaVersion", "sourceKind", "rateSourceUrl",
    "sourceObservedAtUtc", "effectiveAtUtc", "expiresAtUtc",
    "providerId", "modelTransportFingerprint", "sourceCurrency",
    "inputNanoPerMillionTokens", "outputNanoPerMillionTokens",
    "billableCategories", "unknownChargeCategories", "toolsChargeable",
    "billingCategoriesEvidenceDigest", "sourceBytesDigest", "fx"}
FX_FIELDS = {"sourceKind", "sourceUrl", "observedAtUtc", "numerator",
    "denominator", "evidenceDigest"}
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,160}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_HOST = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*\Z")
_PATH = re.compile(r"(?:/[A-Za-z0-9._~-]+)*\Z")
_UTC = "%Y-%m-%dT%H:%M:%SZ"
_DAY = timedelta(days=1)


def _need(ok):
    if not ok:
        raise AnalysisContractError("市场模型身份或费率来源候选不可核验")


def _sha(value):
    _need(type(value) is str and _SHA.fullmatch(value) is not None)


def _time(value):
    _need(type(value) is str and len(value) == 20 and value.endswith("Z"))
    try:
        parsed = datetime.strptime(value, _UTC).replace(tzinfo=timezone.utc)
    except ValueError as error:
        raise AnalysisContractError("费率来源时间无效") from error
    _need(parsed.strftime(_UTC) == value)
    return parsed


def _url(value):
    """Normalize only an unambiguous HTTPS origin plus path; no query data."""
    _need(type(value) is str and 1 <= len(value) <= 1000
        and value.isascii() and "\\" not in value and "%" not in value)
    try:
        parsed = urlsplit(value)
        host, port = (parsed.hostname or "").lower(), parsed.port
    except ValueError as error:
        raise AnalysisContractError("模型或费率来源地址无效") from error
    path = parsed.path.rstrip("/")
    _need(parsed.scheme == "https" and parsed.username is None
        and parsed.password is None and not parsed.query and not parsed.fragment
        and _HOST.fullmatch(host) is not None and "." in host
        and _PATH.fullmatch(path) is not None
        and not any(piece in {".", ".."} for piece in path.split("/"))
        and port != 0)
    return "https://" + host + (":" + str(port)
        if port is not None and port != 443 else "") + path


def model_transport(raw_model):
    """Fingerprint a supplied model projection; it is not an owning DB read."""
    model = _copy(raw_model, 16 * 1024)
    _need(type(model) is dict and set(model) == MODEL_FIELDS
        and type(model["id"]) is str and _ID.fullmatch(model["id"])
        is not None and model["status"] == "enabled"
        and model["modelType"] == "text"
        and model["protocol"] in {"openai_compatible", "anthropic"}
        and type(model["modelName"]) is str
        and 1 <= len(model["modelName"]) <= 120
        and type(model["version"]) is int and model["version"] >= 1
        and type(model["maxTokens"]) is int
        and 128 <= model["maxTokens"] <= 131072
        and type(model["maxToolRounds"]) is int
        and 1 <= model["maxToolRounds"] <= 62
        and type(model["maxTotalToolCalls"]) is int
        and 1 <= model["maxTotalToolCalls"] <= 300
        and type(model["timeoutMs"]) is int
        and 3000 <= model["timeoutMs"] <= 600000
        and model["reasoningMode"] in {"auto", "disabled"}
        and type(model["temperatureMilli"]) is int
        and 0 <= model["temperatureMilli"] <=
            (1000 if model["protocol"] == "anthropic" else 2000))
    options_text = model["generationOptionsJson"]
    _need(type(options_text) is str
        and len(options_text.encode("utf-8")) <= 8192)
    try:
        options = json.loads(options_text)
    except (TypeError, ValueError) as error:
        raise AnalysisContractError("模型生成选项无效") from error
    _need(type(options) is dict and canonical(options) == options_text)
    fixed = {"id": model["id"], "version": model["version"],
        "protocol": model["protocol"],
        "canonicalBaseUrl": _url(model["baseUrl"]),
        "modelName": model["modelName"],
        "generationOptionsDigest": digest(options),
        "maxTokens": model["maxTokens"],
        "maxToolRounds": model["maxToolRounds"],
        "maxTotalToolCalls": model["maxTotalToolCalls"],
        "timeoutMs": model["timeoutMs"],
        "reasoningMode": model["reasoningMode"],
        "temperatureMilli": model["temperatureMilli"]}
    return {"schemaVersion":
        "business-market-v2-model-transport-fingerprint-v1",
        "model": fixed, "fingerprint": digest(fixed),
        "currentModelOwnedRead": False,
        "providerIdentityVerified": False,
        "credentialAccountVerified": False}


def admit_test_vector(raw_model, raw_source, *, at_utc,
        allow_synthetic_test_vector=False):
    """Check arithmetic/shape only; every authority bit remains closed."""
    _need(allow_synthetic_test_vector is True)
    transport = model_transport(raw_model)
    source = _copy(raw_source, 16 * 1024)
    _need(type(source) is dict and set(source) == SOURCE_FIELDS
        and source["schemaVersion"] == SOURCE_SCHEMA
        and source["sourceKind"] == "synthetic_test_vector"
        and _url(source["rateSourceUrl"]) == TEST_RATE_URL
        and source["modelTransportFingerprint"] == transport["fingerprint"]
        and type(source["providerId"]) is str
        and _ID.fullmatch(source["providerId"]) is not None
        and source["sourceCurrency"] in {"CNY", "USD"}
        and source["billableCategories"] == CATEGORIES
        and source["unknownChargeCategories"] == []
        and source["toolsChargeable"] is False)
    _sha(source["billingCategoriesEvidenceDigest"])
    _sha(source["sourceBytesDigest"])
    now = _time(at_utc)
    observed = _time(source["sourceObservedAtUtc"])
    first, last = _time(source["effectiveAtUtc"]), _time(
        source["expiresAtUtc"])
    _need(observed <= now and now-observed <= _DAY
        and first <= now < last and last-first <= timedelta(days=31))
    rates = []
    for name in ("inputNanoPerMillionTokens",
            "outputNanoPerMillionTokens"):
        value = source[name]
        _need(type(value) is int and 1 <= value <= 10**15)
        rates.append(value)
    fx = source["fx"]
    _need(type(fx) is dict and set(fx) == FX_FIELDS)
    numerator, denominator = fx["numerator"], fx["denominator"]
    if source["sourceCurrency"] == "CNY":
        _need(fx == {"sourceKind": "none", "sourceUrl": None,
            "observedAtUtc": None, "numerator": 1,
            "denominator": 1, "evidenceDigest": None})
    else:
        _need(fx["sourceKind"] == "synthetic_test_vector"
            and _url(fx["sourceUrl"]) == TEST_FX_URL
            and type(numerator) is int and 1 <= numerator <= 10**15
            and type(denominator) is int and 1 <= denominator <= 10**15)
        _sha(fx["evidenceDigest"])
        fx_time = _time(fx["observedAtUtc"])
        _need(fx_time <= now and now-fx_time <= _DAY)
    cny = [(value*numerator+denominator-1)//denominator for value in rates]
    _need(all(1 <= value <= 10**15 for value in cny))
    body = {"schemaVersion": SCHEMA,
        "modelTransportFingerprint": transport["fingerprint"],
        "sourceDigest": digest(source),
        "providerId": source["providerId"],
        "sourceCurrency": source["sourceCurrency"],
        "inputNanoYuanPerMillionTokens": cny[0],
        "outputNanoYuanPerMillionTokens": cny[1],
        "billableCategories": list(CATEGORIES),
        "sourceObservedAtUtc": source["sourceObservedAtUtc"],
        "effectiveAtUtc": source["effectiveAtUtc"],
        "expiresAtUtc": source["expiresAtUtc"],
        "testVectorOnly": True,
        "currentModelOwnedRead": False,
        "credentialAccountVerified": False,
        "sourceIndependentlyVerified": False,
        "fxIndependentlyVerified": False,
        "allBillingCategoriesVerified": False,
        "tariffAuthorityVerified": False,
        "humanCapApproved": False,
        "providerCallsAllowed": False}
    return {**body, "admissionDigest": digest(body)}
