from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from sales.views import _consistent_read, _read_cache_lock


class SalesReadSingleflightTests(SimpleTestCase):
    @override_settings(SALES_READ_CACHE_SECONDS=60)
    def test_concurrent_cold_reads_compute_once(self):
        cache.clear()
        _read_cache_lock.cache_clear()
        calls = 0
        calls_lock = Lock()

        def loader():
            nonlocal calls
            with calls_lock:
                calls += 1
            time.sleep(0.05)
            return {"ok": True}

        with patch("sales.views.revision_token", return_value="8:5"):
            with ThreadPoolExecutor(max_workers=4) as executor:
                results = list(executor.map(lambda _index: _consistent_read(loader, "same"), range(4)))

        self.assertEqual(calls, 1)
        self.assertEqual([result[2] for result in results].count("miss"), 1)
        self.assertEqual([result[2] for result in results].count("hit"), 3)
