from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.db import OperationalError

from market.filter_cache import FilterCache, cached_filters
from market.errors import MarketApiError
from market import read_health
from . import test_api
from sales.tests.factories import TEST_SECRET


class FilterCacheTests(SimpleTestCase):
    def test_revision_replacement_database_isolation_and_mutation_safety(self):
        cache = FilterCache()
        calls = []
        def load():
            calls.append(1)
            return {"categories": [{"value": "fixture", "count": len(calls)}]}
        first = cache.read(("db-a", "rev-a"), load, lambda: "rev-a")
        first["categories"].clear()
        self.assertEqual(cache.read(("db-a", "rev-a"), load, lambda: "rev-a")["categories"][0]["count"], 1)
        cache.read(("db-a", "rev-b"), load, lambda: "rev-b")
        cache.read(("db-b", "rev-b"), load, lambda: "rev-b")
        self.assertEqual(len(calls), 3)

    def test_failed_and_changed_revision_results_are_not_cached(self):
        cache = FilterCache()
        with self.assertRaises(MarketApiError):
            cache.read(("db", "old"), lambda: {"bad": True}, lambda: "new")
        self.assertIsNone(cache.entry)
        with self.assertRaises(RuntimeError):
            cache.read(("db", "new"), lambda: (_ for _ in ()).throw(RuntimeError()), lambda: "new")
        self.assertIsNone(cache.entry)
        with self.assertRaises(MarketApiError):
            cache.read(("db", "new"), lambda: self.fail("failure stampede"), lambda: "new")
        with patch("market.filter_cache.monotonic", return_value=cache.failure[1] + 1):
            self.assertEqual(cache.read(("db", "new"), lambda: {"ok": True}, lambda: "new"), {"ok": True})

    def test_concurrent_cold_requests_compute_once(self):
        cache = FilterCache()
        entered, release = Event(), Event()
        calls = []
        def load():
            calls.append(1)
            entered.set()
            if not release.wait(3):
                raise AssertionError("fixture wait expired")
            return {"count": 42}
        with ThreadPoolExecutor(max_workers=3) as pool:
            first = pool.submit(cache.read, ("db", "rev"), load, lambda: "rev")
            self.assertTrue(entered.wait(3))
            followers = [pool.submit(cache.read, ("db", "rev"), load, lambda: "rev") for _ in range(2)]
            release.set()
            self.assertEqual([future.result() for future in [first, *followers]], [{"count": 42}] * 3)
        self.assertEqual(len(calls), 1)

    def test_ttl_and_oversized_payload_do_not_retain_old_entry(self):
        cache = FilterCache()
        with patch("market.filter_cache.monotonic", return_value=0):
            cache.read(("db", "rev"), lambda: {"a": 1}, lambda: "rev")
        with patch("market.filter_cache.monotonic", return_value=301):
            self.assertEqual(cache.read(("db", "rev"), lambda: {"a": 2}, lambda: "rev"), {"a": 2})
        cache.read(("db", "next"), lambda: {"a": "x" * (2 * 1024 * 1024)}, lambda: "next")
        self.assertIsNone(cache.entry)


class TransactionFilterCacheTests(TestCase):
    def test_transaction_never_uses_or_publishes_process_cache(self):
        with patch("market.filter_cache.cache.read", side_effect=AssertionError("transaction escaped")):
            self.assertEqual(cached_filters(lambda: {"value": "uncommitted"}), {"value": "uncommitted"})


class MarketReadHealthTests(SimpleTestCase):
    def setUp(self):
        read_health._reads.clear()

    def test_two_errors_degrade_success_recovers_expiry_becomes_unknown(self):
        from django.test import RequestFactory
        request = RequestFactory().get("/", REMOTE_ADDR="127.0.0.1")
        import json
        def status():
            return json.loads(read_health.endpoint(request).content)["status"]
        with patch("market.read_health.time", return_value=1000), patch("market.read_health.monotonic", return_value=2):
            self.assertEqual(status(), "unknown")
            read_health.record("ranking", 1, "query_timeout")
            self.assertEqual(status(), "observing")
            read_health.record("ranking", 1, "query_timeout")
            self.assertEqual(status(), "degraded")
            read_health.record("ranking", 1)
            self.assertEqual(status(), "unknown")
            read_health.record("filter_options", 1)
            self.assertEqual(status(), "healthy")
        with patch("market.read_health.time", return_value=2000):
            self.assertEqual(status(), "unknown")
        self.assertEqual(read_health.endpoint(RequestFactory().get("/", REMOTE_ADDR="192.0.2.1")).status_code, 403)
        self.assertEqual(read_health.endpoint(RequestFactory().post("/")).status_code, 405)


class ReadTimeoutContractTests(TestCase):
    post_query = test_api.MarketApiContractTests.post_query

    @patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
    def test_query_timeout_is_explicit_and_private_details_are_not_exposed(self):
        import psycopg
        error = OperationalError("private SQL detail")
        error.__cause__ = psycopg.errors.QueryCanceled("private query")
        with patch("market.views.filter_options", side_effect=error):
            response = self.post_query({"operation": "filter_options"}, "timeout")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "query_timeout")
        self.assertIn("筛选统计超时", response.json()["error"])
        self.assertNotIn("private", response.content.decode())
