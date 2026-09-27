"""One bounded, revision-bound entry per reader process; never cache transactions."""
from copy import deepcopy
import json
from threading import Lock
from time import monotonic

from django.conf import settings
from django.db import connection

from .errors import MarketApiError
from .revisions import revision_value


class FilterCache:
    def __init__(self):
        self.lock = Lock()
        self.entry = None
        self.failure = None

    def read(self, key, loader, revision):
        if not self.lock.acquire(timeout=5):
            raise MarketApiError("筛选选项正在更新，请稍后重新加载。", status=503, code="service_unavailable")
        try:
            if self.failure and self.failure[0] == key and monotonic() < self.failure[1]:
                raise MarketApiError("筛选统计暂时无法完成，请稍后重新加载。", status=503, code="service_unavailable")
            if self.entry and self.entry[0] == key and monotonic() < self.entry[1]:
                return deepcopy(self.entry[2])
            try:
                value = loader()
            except Exception:
                # Share a short failure backoff with waiting callers, without
                # retaining the exception, SQL, business data or credentials.
                self.failure = (key, monotonic() + 2)
                raise
            self.failure = None
            # A concurrent import must never stamp old or mixed counts as a new revision.
            if revision() != key[-1]:
                raise MarketApiError("市场数据已更新，请重新加载筛选选项。", status=503, code="service_unavailable")
            if len(json.dumps(value, ensure_ascii=False).encode()) <= 2 * 1024 * 1024:
                self.entry = (key, monotonic() + 300, deepcopy(value))
            else:
                self.entry = None
            return value
        finally:
            self.lock.release()


cache = FilterCache()


def cached_filters(loader):
    # Tests, imports and commands can see uncommitted rows. Do not publish those
    # into a process cache or reuse a committed snapshot in their transaction.
    if connection.in_atomic_block or not connection.get_autocommit():
        return loader()
    database = connection.settings_dict
    key = tuple(str(database.get(k, "")) for k in ("ENGINE", "HOST", "PORT", "NAME", "USER")) + (
        str(settings.MARKET_WRITE_AUTHORITY_EPOCH), revision_value(),
    )
    return cache.read(key, loader, revision_value)
