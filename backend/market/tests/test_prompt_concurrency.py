from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.db import connection, connections
from django.db.models.query import QuerySet
from django.test import TransactionTestCase

from market.annotations import _create_prompt
from market.models import MarketAnnotationPromptVersion, MarketAnnotationPromptAudit
from sales.auth import Principal


@skipUnless(connection.vendor == "postgresql", "Requires real PostgreSQL category locks")
class PromptConcurrencyTests(TransactionTestCase):
    principal = Principal("prompt@example.invalid", "Synthetic", "admin", None)

    def create(self, category):
        try:
            return _create_prompt({"category": category, "segments": ["Synthetic"],
                                   "promptBody": "Synthetic only"}, self.principal)
        finally:
            connections.close_all()

    def concurrent_creates(self, category, count=4):
        barrier = Barrier(count)
        def create():
            barrier.wait(timeout=10)
            return self.create(category)
        with ThreadPoolExecutor(max_workers=count) as pool:
            futures = [pool.submit(create) for _ in range(count)]
            return sorted(future.result(timeout=30)["version"] for future in futures)

    def test_first_category_and_existing_category_allocate_distinct_versions(self):
        self.assertEqual(self.concurrent_creates("Concurrent"), [1, 2, 3, 4])
        self.assertEqual(self.concurrent_creates("Concurrent"), [5, 6, 7, 8])
        self.assertEqual(MarketAnnotationPromptAudit.objects.filter(category="Concurrent").count(), 8)

    def test_different_categories_do_not_block_each_other(self):
        # Both categories must reach MAX concurrently; a global lock would
        # deadlock this barrier and fail, even if it allocated valid versions.
        barrier = Barrier(2)
        original = QuerySet.aggregate
        def aggregate(query, *args, **kwargs):
            result = original(query, *args, **kwargs)
            if query.model is MarketAnnotationPromptVersion:
                barrier.wait(timeout=10)
            return result
        with patch.object(QuerySet, "aggregate", aggregate):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(self.create, category) for category in ["Left", "Right"]]
                self.assertEqual([future.result(timeout=30)["version"] for future in futures], [1, 1])

    def test_audit_failure_rolls_back_version_and_releases_lock(self):
        with patch("market.annotations._audit", side_effect=RuntimeError("synthetic audit failure")):
            with self.assertRaises(RuntimeError):
                self.create("Rollback")
        self.assertFalse(MarketAnnotationPromptVersion.objects.filter(category="Rollback").exists())
        self.assertEqual(self.create("Rollback")["version"], 1)
