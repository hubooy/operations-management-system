"""Request-local completed JSON reuse; not authorization or persistent cache.

No runtime uses this module until an explicit caller passes a trusted complete
binding, a live validator, and loaders that return only AFTER their source
contexts exit successfully. Never use a saved tool result as its own loader.
"""
from collections import OrderedDict
import hashlib
import json
import math
import threading

from .policy import AiError, canonical

MAX_BYTES = 4 * 1024 * 1024
MAX_ENTRIES = 64
MAX_BINDING_BYTES = 16384
MAX_KEY_BYTES = 8192
VALUE_BYTES = {"analysis": 38000, "budget": 2 * 1024 * 1024}
MAX_DEPTH = 24
MAX_NODES = 1_000_000
MAX_SAFE_INTEGER = 2**53 - 1


def _reject(message, code="derived_reuse_invalid"):
    raise AiError(message, code, 409)


def _encoded(value, maximum, *, scalar_floats=True):
    """Snapshot plain JSON with bounded work before canonical serialization."""
    remaining, nodes, ancestors = maximum, MAX_NODES, set()

    def charge(size):
        nonlocal remaining
        remaining -= size
        if remaining < 0:
            _reject("派生复用JSON超过原协议字节容量")

    def visit(item, depth):
        nonlocal nodes
        nodes -= 1
        if depth > MAX_DEPTH or nodes < 0:
            _reject("派生复用JSON深度或节点超限")
        kind = type(item)
        if kind is str:
            if len(item) > remaining:
                _reject("派生复用文本超限")
            try:
                charge(len(canonical(item).encode("utf-8")))
            except UnicodeError as error:
                raise AiError("派生复用文本编码无效", "derived_reuse_invalid", 409) from error
            return item
        if item is None or kind is bool:
            charge(len(canonical(item)))
            return item
        if kind is int:
            if abs(item) > MAX_SAFE_INTEGER:
                _reject("派生复用整数不能无损表达")
            charge(len(str(item)))
            return item
        if kind is float:
            if not scalar_floats or not math.isfinite(item):
                _reject("派生复用不接受非有限数值或浮点身份")
            charge(len(canonical(item)))
            return item
        if kind not in (dict, list):
            _reject("派生复用只接受普通JSON值")
        if id(item) in ancestors:
            _reject("派生复用不接受循环JSON")
        if len(item) > nodes:
            _reject("派生复用容器节点超限")
        ancestors.add(id(item))
        try:
            charge(2 + max(0, len(item)-1) + (len(item) if kind is dict else 0))
            if kind is list:
                return [visit(child, depth+1) for child in item]
            result = {}
            for key, child in item.items():
                if type(key) is not str:
                    _reject("派生复用对象键必须为文本")
                result[visit(key, depth+1)] = visit(child, depth+1)
            return result
        finally:
            ancestors.remove(id(item))

    if type(value) is not dict:
        _reject("派生复用载荷必须是普通JSON对象")
    frozen = visit(value, 0)
    raw = canonical(frozen).encode("utf-8")
    if len(raw) > maximum:
        _reject("派生复用JSON超过原协议字节容量")
    return raw


class CompletedReuse:
    """One owner/report/phase scope with explicit fresh validation on every use.

Binding fields and their authority belong to the caller. This helper compares
all supplied fields, never substitutes them for current_principal or bound().
Keys must include every semantic selector and algorithm version used by the
loader. Capacity overrides can only tighten the fixed defaults for tests.
"""
    def __init__(self, binding, *, validate, max_bytes=MAX_BYTES, max_entries=MAX_ENTRIES):
        if (type(max_bytes) is not int or not 0 < max_bytes <= MAX_BYTES
                or type(max_entries) is not int or not 0 < max_entries <= MAX_ENTRIES
                or not callable(validate) or type(binding) is not dict or not binding):
            _reject("派生复用范围或容量参数无效")
        self._binding = _encoded(binding, MAX_BINDING_BYTES, scalar_floats=False)
        self._validate = validate
        self._max_bytes, self._max_entries = max_bytes, max_entries
        self._entries, self._pending = OrderedDict(), set()
        self._bytes = 0
        self._thread = threading.get_ident()
        self._state = "new"
        self._checking = False
        self._hits = self._misses = self._loads = self._evictions = self._uncached = 0

    def _owner_thread(self):
        if threading.get_ident() != self._thread:
            _reject("派生复用不能跨线程或工作上下文使用")

    def _active(self):
        self._owner_thread()
        if self._state != "active":
            _reject("派生复用上下文尚未打开或已关闭")

    def _check(self):
        if self._checking:
            _reject("派生复用实时校验回调不能重入")
        self._checking = True
        try:
            current = _encoded(self._validate(), MAX_BINDING_BYTES, scalar_floats=False)
            if current != self._binding:
                _reject("派生复用的实时身份或固定绑定已变化", "derived_reuse_binding_changed")
        finally:
            self._checking = False

    def __enter__(self):
        self._owner_thread()
        if self._state != "new":
            _reject("派生复用上下文不能重入或重新打开")
        try:
            self._check()
            self._state = "active"
            return self
        except BaseException:
            self._clear()
            raise

    def _clear(self):
        self._entries.clear()
        self._pending.clear()
        self._bytes = 0
        self._binding = b""
        self._validate = None
        self._state = "closed"

    def close(self):
        self._owner_thread()
        self._clear()

    def __exit__(self, kind, error, trace):
        self._owner_thread()
        try:
            if kind is None and self._state == "active":
                self._check()
        finally:
            self._clear()
        return False

    def resolve(self, kind, key, loader, *, binding):
        self._active()
        if type(kind) is not str or kind not in VALUE_BYTES or not callable(loader):
            _reject("派生复用仅支持分析页或完整固定预算")
        if _encoded(binding, MAX_BINDING_BYTES, scalar_floats=False) != self._binding:
            _reject("派生复用调用跨固定范围", "derived_reuse_binding_changed")
        key_bytes = _encoded(key, MAX_KEY_BYTES, scalar_floats=False)
        identity = (kind, key_bytes)
        self._check()
        if identity in self._pending:
            _reject("派生复用同一结果尚未完成，禁止重入")
        cached = self._entries.get(identity)
        if cached is not None:
            raw, checksum, _ = cached
            if hashlib.sha256(raw).digest() != checksum:
                _reject("派生复用完成载荷摘要损坏")
            result = json.loads(raw)
            self._check()
            self._entries.move_to_end(identity)
            self._hits += 1
            return result
        self._misses += 1
        self._pending.add(identity)
        try:
            self._loads += 1
            # The trusted callback must close all verified Reader/mapping
            # contexts before return. Exceptions and unknown results propagate.
            value = loader()
            self._active()
            raw = _encoded(value, VALUE_BYTES[kind])
            self._check()
            self._active()
            # A validator must not close the scope behind this calculation.
            result = json.loads(raw)
            cost = len(raw) + len(key_bytes) + len(kind.encode()) + 32
            if cost > self._max_bytes:
                self._uncached += 1
                return result
            while self._entries and (len(self._entries) >= self._max_entries or self._bytes+cost > self._max_bytes):
                _, (_, _, removed) = self._entries.popitem(last=False)
                self._bytes -= removed
                self._evictions += 1
            self._entries[identity] = (raw, hashlib.sha256(raw).digest(), cost)
            self._bytes += cost
            return result
        finally:
            self._pending.discard(identity)

    def stats(self):
        self._owner_thread()
        return {"active":self._state=="active", "entries":len(self._entries), "bytes":self._bytes,
            "hits":self._hits, "misses":self._misses, "loads":self._loads,
            "evictions":self._evictions, "uncached":self._uncached}
