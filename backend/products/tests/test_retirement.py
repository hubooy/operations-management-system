from __future__ import annotations

import sqlite3

from django.test import SimpleTestCase

from products.management.commands.retire_products_d1 import (
    RETIREMENT_GUARDS,
    _shared_receipt,
)


class ProductsRetirementSharedReceiptTests(SimpleTestCase):
    def test_shared_receipt_hashes_only_non_product_rows(self) -> None:
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
                CREATE TABLE inventory_import_uploads (
                    id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE inventory_import_upload_chunks (
                    upload_id TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE inventory_import_upload_results (
                    upload_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                );
                CREATE TABLE domain_retirement_receipts (
                    domain TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                );

                INSERT INTO import_content_fingerprints VALUES
                    (1, 'product-shipping-rates', 'retire-me'),
                    (2, 'sales', 'preserve-fingerprint');
                INSERT INTO import_content_attempts VALUES
                    (1, 'product-shipping-rates', 'retire-me'),
                    (2, 'finance', 'preserve-attempt');
                INSERT INTO import_scope_heads VALUES
                    ('product-shipping-rates', 'products-scope', 'retire-me'),
                    ('inventory', 'inventory-scope', 'preserve-head');
                INSERT INTO inventory_import_uploads VALUES
                    ('product-upload', 'sku-shipping-rates:old', 'retire-me'),
                    ('inventory-upload', 'inventory:current', 'preserve-upload');
                INSERT INTO inventory_import_upload_chunks VALUES
                    ('product-upload', 0, 'retire-me'),
                    ('inventory-upload', 0, 'preserve-chunk');
                INSERT INTO inventory_import_upload_results VALUES
                    ('product-upload', 'retire-me'),
                    ('inventory-upload', 'preserve-result');
                INSERT INTO domain_retirement_receipts VALUES
                    ('market', 'preserve-receipt');
                """
            )

            receipt = _shared_receipt(source)

            self.assertEqual(
                receipt["counts"],
                {
                    "fingerprints_other": 1,
                    "attempts_other": 1,
                    "heads_other": 1,
                    "uploads_other": 1,
                    "upload_chunks_other": 1,
                    "upload_results_other": 1,
                    "retirement_receipts_other": 1,
                },
            )
            self.assertEqual(set(receipt["digests"]), set(receipt["counts"]))
            self.assertRegex(str(receipt["digest"]), r"^[0-9a-f]{64}$")
            self.assertEqual(len(RETIREMENT_GUARDS), 18)
        finally:
            source.close()
