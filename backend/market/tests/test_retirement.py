from __future__ import annotations

import sqlite3

from django.test import SimpleTestCase

from market.management.commands.retire_market_d1 import _shared_receipt


class MarketRetirementSharedReceiptTests(SimpleTestCase):
    def test_shared_receipt_hashes_only_non_market_rows(self) -> None:
        source = sqlite3.connect(":memory:")
        source.row_factory = sqlite3.Row
        try:
            source.executescript(
                """
                CREATE TABLE import_content_fingerprints (
                    sequence INTEGER NOT NULL,
                    domain TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE import_content_attempts (
                    sequence INTEGER NOT NULL,
                    domain TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE import_scope_heads (
                    domain TEXT NOT NULL,
                    scope_key TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                INSERT INTO import_content_fingerprints VALUES
                    (1, 'market', 'retire-me'),
                    (2, 'sales', 'preserve-fingerprint');
                INSERT INTO import_content_attempts VALUES
                    (1, 'market', 'retire-me'),
                    (2, 'finance', 'preserve-attempt');
                INSERT INTO import_scope_heads VALUES
                    ('market', 'market-scope', 'retire-me'),
                    ('netshop', 'netshop-scope', 'preserve-head');
                """
            )

            receipt = _shared_receipt(source)

            self.assertEqual(
                receipt["counts"],
                {
                    "fingerprints_other": 1,
                    "attempts_other": 1,
                    "heads_other": 1,
                },
            )
            self.assertEqual(set(receipt["digests"]), set(receipt["counts"]))
            self.assertRegex(str(receipt["digest"]), r"^[0-9a-f]{64}$")
        finally:
            source.close()
