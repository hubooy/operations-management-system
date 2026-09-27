"""Non-authorizing integer cost envelope for a future paid five-Agent run.

An administrator must independently approve and bind current CNY tariff and FX
evidence. This module has no provider, wallet, budget ledger, or call authority.
"""
from datetime import datetime, timedelta, timezone
import re

from .contracts import AnalysisContractError, digest
from .screening_package import ROLES


SCHEMA = "business-market-five-agent-cost-envelope-candidate-v1"
TARIFF_SCHEMA = "business-model-cny-tariff-candidate-v1"
NANO_YUAN_PER_CENT = 10_000_000
MILLION = 1_000_000
PRICE_DENOMINATOR = NANO_YUAN_PER_CENT * MILLION
MAX_CAP_CENTS = 100_000_000
HEX = re.compile(r"[0-9a-f]{64}\Z")
IDENTIFIER = re.compile(r"[A-Za-z0-9_.:-]{1,160}\Z")
TIMESTAMP = "%Y-%m-%dT%H:%M:%SZ"


def _need(ok, message="市场五Agent费用上限合同无效"):
    if not ok:
        raise AnalysisContractError(message)


def _time(value):
    _need(type(value) is str and len(value) == 20 and value.endswith("Z"),
        "费率生效时刻须为精确UTC秒")
    try:
        parsed = datetime.strptime(value, TIMESTAMP).replace(tzinfo=timezone.utc)
    except ValueError as error:
        raise AnalysisContractError("费率生效时刻无效") from error
    _need(parsed.strftime(TIMESTAMP) == value)
    return parsed


def _integer(value, low, high, label):
    _need(type(value) is int and low <= value <= high, label)
    return value


def _ceil_cents(input_tokens, output_tokens, tariff):
    nanos_per_million = (
        input_tokens * tariff["inputNanoYuanPerMillionTokens"] +
        output_tokens * tariff["outputNanoYuanPerMillionTokens"])
    return (nanos_per_million + PRICE_DENOMINATOR - 1) // PRICE_DENOMINATOR


def reserve(tariff, jobs, *, model_id, model_version, at_utc,
            approved_cap_cents, approval_digest, chargeable_tools=False):
    """Calculate a fail-closed worst-case provider reservation, not approval."""
    _need(type(tariff) is dict and set(tariff) == {
        "schemaVersion", "providerId", "modelId", "modelVersion",
        "currency", "inputNanoYuanPerMillionTokens",
        "outputNanoYuanPerMillionTokens", "rateSourceDigest",
        "effectiveAtUtc", "expiresAtUtc"})
    _need(tariff["schemaVersion"] == TARIFF_SCHEMA
        and tariff["currency"] == "CNY"
        and type(tariff["providerId"]) is str
        and IDENTIFIER.fullmatch(tariff["providerId"]) is not None
        and type(model_id) is str and IDENTIFIER.fullmatch(model_id) is not None
        and tariff["modelId"] == model_id
        and type(model_version) is int and 1 <= model_version <= 1_000_000
        and tariff["modelVersion"] == model_version
        and type(tariff["rateSourceDigest"]) is str
        and HEX.fullmatch(tariff["rateSourceDigest"]) is not None,
        "模型、提供方或费率来源不匹配")
    for name in ("inputNanoYuanPerMillionTokens",
                 "outputNanoYuanPerMillionTokens"):
        _integer(tariff[name], 1, 10**15, "付费模型费率缺失或超出范围")
    first, last, now = (_time(tariff["effectiveAtUtc"]),
        _time(tariff["expiresAtUtc"]), _time(at_utc))
    _need(first <= now < last and last - first <= timedelta(days=31),
        "模型费率过期或有效期过宽")
    cap = _integer(approved_cap_cents, 1, MAX_CAP_CENTS,
        "人工批准费用上限无效")
    _need(type(approval_digest) is str and HEX.fullmatch(approval_digest)
        is not None, "人工批准摘要缺失")
    _need(chargeable_tools is False,
        "另有收费工具时不能仅按模型token准入")
    _need(type(jobs) is list and len(jobs) == len(ROLES)
        and all(type(job) is dict and set(job) == {
            "role", "maxRounds", "maxInputTokensPerRound",
            "maxOutputTokensPerRound"} for job in jobs)
        and [job["role"] for job in jobs] == list(ROLES),
        "五Agent角色或费用计划不完整")
    rows = []
    total = 0
    for job in jobs:
        rounds = _integer(job["maxRounds"], 1, 20,
            "模型轮次超出范围")
        input_tokens = _integer(job["maxInputTokensPerRound"], 1, 1_000_000,
            "输入token上限无效")
        output_tokens = _integer(job["maxOutputTokensPerRound"], 1, 128_000,
            "输出token上限无效")
        one_round = _ceil_cents(input_tokens, output_tokens, tariff)
        reserved = one_round * rounds
        _need(0 < reserved <= MAX_CAP_CENTS,
            "单Agent费用上限超出安全范围")
        total += reserved
        _need(total <= cap, "最坏费用超过人工批准上限")
        rows.append({"role": job["role"], "maxRounds": rounds,
            "maxInputTokensPerRound": input_tokens,
            "maxOutputTokensPerRound": output_tokens,
            "maxCostCentsPerRound": one_round,
            "reservationCents": reserved})
    result = {"schemaVersion": SCHEMA,
        "providerId": tariff["providerId"], "modelId": model_id,
        "modelVersion": model_version, "currency": "CNY",
        "tariffDigest": digest(tariff),
        "rateSourceDigest": tariff["rateSourceDigest"],
        "approvedCapCents": cap, "approvalDigest": approval_digest,
        "jobs": rows, "reservationRequiredCents": total,
        "unreservedHeadroomCents": cap - total,
        "tariffAuthorityVerified": False,
        "humanApprovalAuthorityVerified": False,
        "providerUsageCategoryCoverageVerified": False,
        "fundsReservedInDurableLedger": False,
        "providerCallsAllowed": False,
        "currencyConversionVerified": False,
        "limitations": [
            "纯整数上界只覆盖显式输入和输出token类别，未知计费项或收费工具须拒绝",
            "CNY费率与汇率须由受保护拥有方核权威有效期；摘要不是批准或真实计费证明",
            "真正调用前仍须持久原子预算预留、逐轮许可和未知结果不重试"]}
    return {**result, "envelopeDigest": digest(result)}


def reconcile_usage(envelope, tariff, usage):
    """Calculate bounded observed token cost; never release reserved funds."""
    _need(type(envelope) is dict and envelope.get("schemaVersion") == SCHEMA
        and envelope.get("envelopeDigest") == digest({key: value
            for key, value in envelope.items() if key != "envelopeDigest"})
        and envelope.get("tariffDigest") == digest(tariff),
        "费用观察与原预留合同不一致")
    expected = reserve(tariff, [{key: job[key] for key in (
        "role", "maxRounds", "maxInputTokensPerRound",
        "maxOutputTokensPerRound")} for job in envelope["jobs"]],
        model_id=envelope["modelId"], model_version=envelope["modelVersion"],
        at_utc=tariff["effectiveAtUtc"],
        approved_cap_cents=envelope["approvedCapCents"],
        approval_digest=envelope["approvalDigest"])
    _need(expected == envelope, "费用预留不是固定合同的完整结果")
    _need(type(usage) is list and len(usage) <= 100)
    plans = {job["role"]: job for job in envelope["jobs"]}
    _need(set(plans) == set(ROLES) and len(plans) == len(ROLES))
    observed = set()
    total = 0
    for item in usage:
        _need(type(item) is dict and set(item) == {
            "role", "round", "inputTokens", "outputTokens"})
        role = item["role"]
        _need(type(role) is str and role in plans)
        plan = plans[role]
        round_number = _integer(item["round"], 1, plan["maxRounds"],
            "费用轮次超出预留")
        _need((role, round_number) not in observed,
            "费用轮次重复上报")
        observed.add((role, round_number))
        input_tokens = _integer(item["inputTokens"], 0,
            plan["maxInputTokensPerRound"], "输入token超出预留")
        output_tokens = _integer(item["outputTokens"], 0,
            plan["maxOutputTokensPerRound"], "输出token超出预留")
        total += _ceil_cents(input_tokens, output_tokens, tariff)
        _need(total <= envelope["reservationRequiredCents"],
            "累计费用超出预留")
    result = {"schemaVersion": "business-market-cost-observation-candidate-v1",
        "envelopeDigest": envelope["envelopeDigest"],
        "observedRoundCount": len(observed), "observedCostCents": total,
        "reservedCents": envelope["reservationRequiredCents"],
        "unconsumedReservationCents":
            envelope["reservationRequiredCents"] - total,
        "providerUsageAuthenticated": False,
        "actualProviderChargeVerified": False,
        "fundsReleasedOrCharged": False,
        "providerCallsAllowed": False}
    return {**result, "observationDigest": digest(result)}
