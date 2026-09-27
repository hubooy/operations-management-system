"""Process-local capacity permits; never restore from events or public JSON.

The 2 MiB limit counts canonical JSON, keys and digests, not Python RSS. Each
hit still contacts the current tool catalog and checks live owning roots.
Restart/clear loses all permits; a cold preparation may resolve budget facts.
Workflow lease/CAS and actual provider dispatch remain the caller's duties.
"""
from collections import OrderedDict
from dataclasses import dataclass, field
import threading
from types import SimpleNamespace

from django.db import connection

from . import business_screening_admission as admission, business_screening_runtime as runtime
from . import business_screening_runtime_contract as contract, transport
from .policy import AiError, canonical, identifier

MAX_ENTRIES, MAX_BYTES, WAIT_SECONDS = 64, 2*1024*1024, 12
_lock = threading.RLock()
_cache = OrderedDict()
_pending = {}
_bytes = 0
_epoch = 0


@dataclass
class _Flight:
    epoch: int
    thread: int
    event: threading.Event = field(default_factory=threading.Event)
    error: tuple | None = None


def _error(message="筛查容量许可已失效，请重新准备", code="conflict", status=409):
    return AiError(message,code,status)


def _key(report, principal):
    try:
        return principal.email.lower(),identifier(report.id)
    except (AttributeError,TypeError) as error:
        raise _error("容量许可要求实际报告身份", "invalid_request",400) from error


def _bound(report, principal):
    actual, _, _, _, _, _ = runtime.bound(report,principal)
    if actual.workflow.status not in {"queued","running"} or actual.workflow.cancel_requested:
        raise _error("当前工作流状态不允许派发", "conflict",409)
    return actual


def check(prepared, principal):
    """Live local root/status check, suitable inside caller's short mutation."""
    proof = admission.revalidate(prepared,principal)
    _bound(SimpleNamespace(id=proof["reportId"],snapshot_json=prepared._snapshot_json),principal)
    return proof


def _charge(key, prepared):
    # Validate internal canonical state before measuring or retaining it.
    _ = prepared.proof
    return sum(len(value.encode("utf-8")) for value in
        (canonical(key),prepared._snapshot_json,prepared._fixed_json,prepared._proof_json,prepared._digest))


def _remove(key):
    global _bytes
    entry = _cache.pop(key,None)
    if entry is not None:
        _bytes -= entry[1]


def _finish(key, flight, error=None):
    with _lock:
        if error is not None:
            # Do not retain traceback, model output, credentials or a failed
            # prepared object. Waiters get only the same bounded public error.
            flight.error = ((str(error)[:1000],error.code,error.status) if isinstance(error,AiError)
                else ("筛查容量准备失败","service_unavailable",503))
        if _pending.get(key) is flight:
            del _pending[key]
        flight.event.set()


def get(report, principal):
    """Fetch a freshly validated local permit; no long lock or transaction."""
    return _get(report,principal,waited=False)


def _get(report,principal,*,waited):
    global _bytes
    if connection.in_atomic_block:
        raise _error("容量许可网络检查须在最外层数据库事务之外", "invalid_request",400)
    key = _key(report,principal)
    try:
        actual = _bound(report,principal)
        with _lock:
            entry = _cache.get(key)
            epoch = _epoch
            if entry is not None:
                prepared = entry[0]
                flight, owner = None, False
            else:
                if waited:
                    raise _error("已准备许可不再驻留，请稍后重试", "service_unavailable",503)
                flight = _pending.get(key)
                if flight is not None:
                    if flight.thread == threading.get_ident():
                        raise _error("不能重入同一报告的容量准备", "service_unavailable",503)
                    owner = False
                else:
                    if len(_pending) >= MAX_ENTRIES:
                        raise _error("容量准备并发已满，请稍后重试", "service_unavailable",503)
                    flight = _Flight(epoch,threading.get_ident())
                    _pending[key] = flight
                    owner = True
        if entry is not None:
            # Exact current catalog order, schema and digest, even on a hit.
            try:
                admission._catalog(transport.catalog(principal,contract.SURFACE),actual.workflow)
            except (TypeError,ValueError,KeyError,AttributeError,RecursionError) as error:
                raise _error("筛查工具目录格式无效", "service_unavailable",503) from error
            check(prepared,principal)
            with _lock:
                if _epoch != epoch or _cache.get(key) is not entry:
                    raise _error("容量许可在检查期间已清除")
                _cache.move_to_end(key)
            return prepared
        if not owner:
            if not flight.event.wait(WAIT_SECONDS):
                raise _error("同一报告正在准备容量许可，请稍后重试", "service_unavailable",503)
            if flight.error is not None:
                raise AiError(*flight.error)
            # A completed flight is only a cache hint, never permission.
            return _get(report,principal,waited=True)
        try:
            prepared = admission.prepare(actual,principal)
            check(prepared,principal)
            amount = _charge(key,prepared)
            if amount > MAX_BYTES:
                raise _error("容量许可规范载荷超过缓存上限", "payload_too_large",413)
            with _lock:
                if _epoch != flight.epoch:
                    raise _error("准备期间容量许可缓存已清除")
                while _cache and (len(_cache) >= MAX_ENTRIES or _bytes+amount > MAX_BYTES):
                    oldest = next(iter(_cache))
                    _remove(oldest)
                _cache[key] = (prepared,amount)
                _bytes += amount
            _finish(key,flight)
            return prepared
        except BaseException as error:
            _finish(key,flight,error)
            raise
    except BaseException:
        with _lock:
            _remove(key)
        raise


def clear():
    """Internal test/restart equivalent; never a serialized permit import."""
    global _bytes,_epoch
    with _lock:
        _epoch += 1
        _cache.clear()
        _bytes = 0
        for flight in _pending.values():
            flight.error = ("容量许可缓存已清除","conflict",409)
            flight.event.set()


def _stats():
    with _lock:
        return {"entries":len(_cache),"canonicalBytes":_bytes,"pending":len(_pending)}
