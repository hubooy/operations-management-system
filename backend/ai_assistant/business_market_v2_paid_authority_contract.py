"""Complete synthetic authority *data* contract; never a real approval.

Production prices, FX evidence, billing categories and a human cap must be
independently adopted by protected owners.  This module only checks the shape
and arithmetic of a candidate used in an isolated database rehearsal.
"""
from datetime import timedelta
import re

from business_analysis.contracts import AnalysisContractError, digest
from business_analysis.market_model_cost_envelope import _time

from .business_market_v2_round_reservation_contract import _candidate


SCHEMA = "business-market-v2-paid-authority-rehearsal-v1"
SOURCE_SCHEMA = "business-market-v2-source-rate-rehearsal-v1"
APPROVAL_SCHEMA = "business-market-v2-cap-approval-rehearsal-v1"
HEX = re.compile(r"[0-9a-f]{64}\Z")
CATEGORIES = ["input_tokens", "output_tokens"]


def _need(ok):
    if not ok:
        raise AnalysisContractError("市场v2权威费率/人工上限候选无效")


def build(candidate, source, approval, *, at_utc):
    value = _candidate(candidate)
    _need(type(source) is dict and set(source) == {
        "schemaVersion", "providerId", "modelId", "modelVersion",
        "sourceCurrency", "inputNanoPerMillionTokens",
        "outputNanoPerMillionTokens", "cnyFxNumerator", "cnyFxDenominator",
        "rateEvidenceDigest", "fxEvidenceDigest", "categoriesEvidenceDigest",
        "chargeCategories", "effectiveAtUtc", "expiresAtUtc"})
    _need(source["schemaVersion"] == SOURCE_SCHEMA
        and source["providerId"] == value["tariff"]["providerId"]
        and source["modelId"] == value["model"]["id"]
        and type(source["modelVersion"]) is int
        and source["modelVersion"] == value["model"]["version"]
        and source["sourceCurrency"] in ("CNY", "USD")
        and source["chargeCategories"] == CATEGORIES
        and all(type(source[key]) is str
            and HEX.fullmatch(source[key]) is not None for key in (
                "rateEvidenceDigest", "categoriesEvidenceDigest"))
        and source["rateEvidenceDigest"] ==
            value["tariff"]["rateSourceDigest"])
    for key in ("inputNanoPerMillionTokens", "outputNanoPerMillionTokens",
                "cnyFxNumerator", "cnyFxDenominator"):
        _need(type(source[key]) is int and 1 <= source[key] <= 10**15)
    if source["sourceCurrency"] == "CNY":
        _need(source["cnyFxNumerator"] == source["cnyFxDenominator"] == 1
            and source["fxEvidenceDigest"] is None)
    else:
        _need(type(source["fxEvidenceDigest"]) is str
            and HEX.fullmatch(source["fxEvidenceDigest"]) is not None)
    for source_key, tariff_key in (("inputNanoPerMillionTokens",
            "inputNanoYuanPerMillionTokens"), ("outputNanoPerMillionTokens",
            "outputNanoYuanPerMillionTokens")):
        calculated = (source[source_key] * source["cnyFxNumerator"]
            + source["cnyFxDenominator"] - 1) // source["cnyFxDenominator"]
        _need(calculated == value["tariff"][tariff_key])
    now = _time(at_utc)
    first, last = _time(source["effectiveAtUtc"]), _time(source["expiresAtUtc"])
    _need(first <= now < last and last - first <= timedelta(days=31)
        and source["effectiveAtUtc"] == value["tariff"]["effectiveAtUtc"]
        and source["expiresAtUtc"] == value["tariff"]["expiresAtUtc"])
    _need(type(approval) is dict and set(approval) == {
        "schemaVersion", "planId", "actorEmail", "approvedCapCents",
        "approvalEvidenceDigest", "approvedAtUtc", "expiresAtUtc"})
    _need(approval["schemaVersion"] == APPROVAL_SCHEMA
        and approval["planId"] == value["planId"]
        and type(approval["actorEmail"]) is str
        and approval["actorEmail"] == approval["actorEmail"].lower()
        and "@" in approval["actorEmail"]
        and type(approval["approvedCapCents"]) is int
        and approval["approvedCapCents"] == value["approvedCapClaimCents"]
        and type(approval["approvalEvidenceDigest"]) is str
        and HEX.fullmatch(approval["approvalEvidenceDigest"]) is not None
        and approval["approvalEvidenceDigest"] ==
            value["envelope"]["approvalDigest"]
        and _time(approval["approvedAtUtc"]) <= now <
            _time(approval["expiresAtUtc"])
        and _time(approval["expiresAtUtc"]) -
            _time(approval["approvedAtUtc"]) <= timedelta(days=31))
    result = {"schemaVersion": SCHEMA, "planId": value["planId"],
        "candidateDigest": value["candidateDigest"],
        "tariffDigest": value["tariffDigest"],
        "source": source, "sourceDigest": digest(source),
        "approval": approval, "approvalDigest": digest(approval),
        "requiredCents": value["requiredCents"],
        "approvedCapCents": approval["approvedCapCents"],
        "syntheticOnly": True, "authorityIndependentlyVerified": False,
        "providerCallsAllowed": False}
    return {**result, "authorityDigest": digest(result)}
