"""No pure quote or replay projection may become a paid-call grant."""
from copy import deepcopy
from unittest import TestCase

from business_analysis.contracts import AnalysisContractError, digest

from . import business_market_v2_round_reservation_contract as rounds
from .test_business_market_v2_cost_candidate import build


LEDGER = "d" * 64
REQUEST = "e" * 64


def quoted(role="commerce", number=1, request=REQUEST, candidate=None):
    return rounds.quote_round(candidate or build(), LEDGER, role, number,
        request)


class MarketV2RoundReservationContractTests(TestCase):
    def test_exact_slot_and_request_intent_are_distinct_and_closed(self):
        one = quoted()
        same = quoted()
        different = quoted(request="f" * 64)
        self.assertEqual(one, same)
        self.assertEqual(one["slotId"], different["slotId"])
        self.assertNotEqual(one["intentDigest"], different["intentDigest"])
        self.assertEqual(one["maxCostCents"], 120)
        self.assertEqual(one["reservedCents"], 0)
        self.assertFalse(one["providerCallsAllowed"])
        self.assertEqual(quoted("report", 2)["maxCostCents"], 120)

    def test_model_tariff_candidate_claims_cannot_change_quote(self):
        original = build()
        for mutation in (
                lambda value: value.update(providerCallsAllowed=True),
                lambda value: value.update(reservedCents=100),
                lambda value: value["tariff"].update(
                    inputNanoYuanPerMillionTokens=1),
                lambda value: value["envelope"]["jobs"][0].update(
                    maxCostCentsPerRound=1),
                lambda value: value.update(approvedCapClaimCents=10000)):
            with self.subTest(mutation=mutation):
                tampered = deepcopy(original)
                mutation(tampered)
                tampered["candidateDigest"] = digest({key: item for
                    key, item in tampered.items() if key != "candidateDigest"})
                with self.assertRaises(AnalysisContractError):
                    quoted(candidate=tampered)

    def test_invalid_role_round_and_request_are_rejected(self):
        for role, number, request in (("other", 1, REQUEST),
                ("commerce", 0, REQUEST), ("commerce", 3, REQUEST),
                ("commerce", True, REQUEST), ("commerce", 1, "not-a-sha")):
            with self.subTest(role=role, number=number), self.assertRaises(
                    AnalysisContractError):
                quoted(role, number, request)

    def test_phase_machine_has_no_unknown_outcome_retry_or_release(self):
        phase = rounds.advance_phase("quoted_unreserved", "reserve")
        self.assertEqual(phase, "reserved_awaiting_dispatch")
        phase = rounds.advance_phase(phase, "dispatch_start")
        self.assertEqual(phase, "dispatch_outcome_unknown")
        for event in ("reserve", "dispatch_start"):
            with self.subTest(event=event), self.assertRaises(
                    AnalysisContractError):
                rounds.advance_phase(phase, event)
        self.assertEqual(rounds.advance_phase(phase, "result_observed"),
            "result_observed_unverified")
        for event in rounds.EVENTS:
            with self.subTest(event=event), self.assertRaises(
                    AnalysisContractError):
                rounds.advance_phase("result_observed_unverified", event)

    def test_projection_holds_full_amount_even_after_result_observed(self):
        candidate = build()
        items = [{"quote": quoted(candidate=candidate),
            "phase": "result_verified_closed"},
            {"quote": quoted("commerce", 2, candidate=candidate),
             "phase": "dispatch_outcome_unknown"}]
        result = rounds.project_held_budget(candidate, LEDGER, items)
        self.assertEqual(result["conservativeHeldCents"], 240)
        self.assertEqual(result["candidateHeadroomCents"], 960)
        self.assertEqual(result["unknownOutcomeCount"], 1)
        self.assertFalse(result["authorityVerified"])
        self.assertFalse(result["fundsReservedInDatabase"])
        self.assertFalse(result["providerCallsAllowed"])

    def test_projection_rejects_duplicate_conflict_gap_or_fake_row(self):
        candidate = build()
        first = {"quote": quoted(candidate=candidate),
            "phase": "reserved_awaiting_dispatch"}
        for entries in ([first, first],
                [first, {"quote": quoted("commerce", 2, candidate=candidate),
                  "phase": "reserved_awaiting_dispatch"}],
                [{"quote": quoted("commerce", 2, candidate=candidate),
                  "phase": "reserved_awaiting_dispatch"}],
                [{"quote": {**first["quote"], "maxCostCents": 1},
                  "phase": "reserved_awaiting_dispatch"}],
                [{"quote": first["quote"], "phase": "unknown"}],
                [{"quote": first["quote"],
                  "phase": "reserved_awaiting_dispatch", "grant": True}]):
            with self.subTest(entries=entries), self.assertRaises(
                    AnalysisContractError):
                rounds.project_held_budget(candidate, LEDGER, entries)
