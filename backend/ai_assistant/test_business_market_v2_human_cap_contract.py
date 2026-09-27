"""Pure tests for an explicit CNY cap without pricing or provider authority."""
from copy import deepcopy
from unittest import TestCase

from .business_market_v2_human_cap_contract import (
    HumanCapInputError, SCHEMA, request)


NOW = "2026-09-26T01:00:00Z"
VALID = {"schemaVersion": SCHEMA, "ledgerId": "a" * 64,
    "planId": "b" * 64, "reportId": "report-1",
    "ledgerDigest": "c" * 64, "planDigest": "d" * 64,
    "reportSnapshotDigest": "e" * 64,
    "modelConfigDigest": "f" * 64,
    "approvedCapCents": 1200, "expiresAtUtc": "2026-09-27T01:00:00Z",
    "explicitApproval": True}


class HumanCapContractTests(TestCase):
    def test_exact_explicit_ceiling_is_canonical_and_still_closed(self):
        first = request(VALID, now_utc=NOW)
        second = request(dict(reversed(list(VALID.items()))), now_utc=NOW)
        self.assertEqual(first, second)
        self.assertEqual(first["approvedCapCents"], 1200)
        self.assertFalse(first["providerCallsAllowed"])
        self.assertFalse(first["fundsReserved"])

    def test_claim_or_implicit_action_cannot_be_approval(self):
        for field, value in (("explicitApproval", False),
                ("approvedCapCents", True), ("approvedCapCents", 0),
                ("approvedCapCents", 100_000_001),
                ("ledgerDigest", "not-a-digest"),
                ("expiresAtUtc", NOW),
                ("expiresAtUtc", "2026-11-01T01:00:00Z")):
            with self.subTest(field=field, value=value):
                changed = deepcopy(VALID)
                changed[field] = value
                with self.assertRaises(HumanCapInputError):
                    request(changed, now_utc=NOW)
        changed = deepcopy(VALID)
        changed["providerCallsAllowed"] = True
        with self.assertRaises(HumanCapInputError):
            request(changed, now_utc=NOW)
