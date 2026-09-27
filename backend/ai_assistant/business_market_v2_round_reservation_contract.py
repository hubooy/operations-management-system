"""Pure, non-authorizing contract for a future market-v2 paid-round ledger.

The 0065 cost row is a zero-reservation *requirement*.  These functions only
derive bounded slot/intent bytes and model an append-only lifecycle.  They do
not attest a tariff, an approval, a database row, or a provider result.  The
runtime paid gate remains closed until a separately reviewed SQL owner can
atomically reserve each slot and persist dispatch-start before network I/O.
"""
import re

from business_analysis.contracts import AnalysisContractError, digest

from . import business_market_v2_cost_candidate as cost_candidate


SCHEMA = "business-market-v2-round-reservation-quote-v1"
PROJECTION_SCHEMA = "business-market-v2-reservation-projection-v1"
HEX = re.compile(r"[0-9a-f]{64}\Z")
PHASES = ("reserved_awaiting_dispatch", "dispatch_outcome_unknown",
          "result_observed_unverified", "result_verified_closed")
EVENTS = {"reserve": ("quoted_unreserved", PHASES[0]),
          "dispatch_start": (PHASES[0], PHASES[1]),
          "result_observed": (PHASES[1], PHASES[2])}


def _need(ok, message="市场v2逐轮预留候选不满足固定边界"):
    if not ok:
        raise AnalysisContractError(message)


def _hex(value):
    return type(value) is str and HEX.fullmatch(value) is not None


def _candidate(value):
    """Recompute all 0065 arithmetic; still no SQL or authority verification."""
    _need(type(value) is dict)
    try:
        envelope, tariff, model = (value[key] for key in
            ("envelope", "tariff", "model"))
        plan_id = value["planId"]
        jobs = [{key: row[key] for key in ("role", "maxRounds",
            "maxInputTokensPerRound", "maxOutputTokensPerRound")}
            for row in envelope["jobs"]]
        expected = cost_candidate.build(plan_id, model, tariff, jobs,
            at_utc=tariff["effectiveAtUtc"],
            cap_claim_cents=value["approvedCapClaimCents"],
            approval_claim_digest=envelope["approvalDigest"])
    except (KeyError, TypeError, ValueError, IndexError) as error:
        raise AnalysisContractError("市场v2费用需求候选不可复算") from error
    _need(value == expected, "市场v2费用需求候选与重新计费不一致")
    return value


def quote_round(candidate, ledger_id, role, round_number, request_digest):
    """Derive a stable role/round slot and exact request-bound intent.

    A changed request uses the same slot ID but a different intent digest, so
    future SQL must reject it as a conflicting replay.  This quote deliberately
    carries zero reserved cents and no execution permission.
    """
    value = _candidate(candidate)
    _need(_hex(ledger_id) and _hex(request_digest))
    _need(type(role) is str and type(round_number) is int)
    matches = [row for row in value["envelope"]["jobs"]
        if row["role"] == role]
    _need(len(matches) == 1)
    row = matches[0]
    _need(1 <= round_number <= row["maxRounds"])
    slot = {"planId": value["planId"], "ledgerId": ledger_id,
        "role": role, "round": round_number}
    slot_id = digest({"schemaVersion": SCHEMA, **slot})
    intent_digest = digest({"slotId": slot_id,
        "requestDigest": request_digest,
        "maxCostCents": row["maxCostCentsPerRound"],
        "maxInputTokens": row["maxInputTokensPerRound"],
        "maxOutputTokens": row["maxOutputTokensPerRound"]})
    return {"schemaVersion": SCHEMA, **slot, "slotId": slot_id,
        "candidateDigest": value["candidateDigest"],
        "envelopeDigest": value["envelopeDigest"],
        "requestDigest": request_digest, "intentDigest": intent_digest,
        "maxCostCents": row["maxCostCentsPerRound"],
        "maxInputTokens": row["maxInputTokensPerRound"],
        "maxOutputTokens": row["maxOutputTokensPerRound"],
        "phase": "quoted_unreserved", "reservedCents": 0,
        "providerCallsAllowed": False}


def advance_phase(phase, event):
    """Model irreversible dispatch start; unknown outcomes have no retry edge."""
    _need(type(phase) is str and type(event) is str and event in EVENTS)
    before, after = EVENTS[event]
    _need(phase == before, "市场v2逐轮状态不能重放或倒退")
    return after


def project_held_budget(candidate, ledger_id, entries):
    """Conservatively hold full cost for every persisted reservation.

    Observed provider usage is not an authenticated charge, so this projection
    never releases funds.  Its entries are untrusted: only a future SQL-owned
    ledger may use its own locked rows as inputs to an admission decision.
    """
    value = _candidate(candidate)
    _need(_hex(ledger_id) and type(entries) is list and len(entries) <= 100)
    seen = set()
    rounds = {}
    phases = {}
    held = 0
    unknown = 0
    for item in entries:
        _need(type(item) is dict and set(item) == {"quote", "phase"})
        quote, phase = item["quote"], item["phase"]
        _need(type(quote) is dict and type(phase) is str and phase in PHASES)
        expected = quote_round(value, ledger_id, quote.get("role"),
            quote.get("round"), quote.get("requestDigest"))
        _need(quote == expected, "市场v2预留行与报价不一致")
        _need(quote["slotId"] not in seen, "市场v2轮次重复预留")
        seen.add(quote["slotId"])
        rounds.setdefault(quote["role"], set()).add(quote["round"])
        phases[(quote["role"], quote["round"])] = phase
        held += quote["maxCostCents"]
        unknown += phase == "dispatch_outcome_unknown"
        _need(held <= value["approvedCapClaimCents"],
            "市场v2预留总额超过候选上限")
    _need(all(values == set(range(1, max(values) + 1))
        for values in rounds.values()), "市场v2同一角色轮次存在缺口")
    _need(all(phases[(role, number)] == "result_verified_closed"
        for role, values in rounds.items() for number in values
        if number < max(values)), "市场v2前一轮未经保护验证不能开始下一轮")
    return {"schemaVersion": PROJECTION_SCHEMA,
        "planId": value["planId"], "ledgerId": ledger_id,
        "candidateDigest": value["candidateDigest"],
        "reservedSlotCount": len(seen), "unknownOutcomeCount": unknown,
        "conservativeHeldCents": held,
        "candidateCapClaimCents": value["approvedCapClaimCents"],
        "candidateHeadroomCents": value["approvedCapClaimCents"] - held,
        "authorityVerified": False, "fundsReservedInDatabase": False,
        "providerCallsAllowed": False}
