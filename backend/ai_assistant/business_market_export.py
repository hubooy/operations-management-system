"""Unregistered complete market materials from sealed, report-bound owning views.

Prepared bytes are candidate file material, never independent authorization.
No partial result escapes on failed traversal, integrity check, or final fence.
"""
from dataclasses import dataclass
import hashlib
import json

from business_analysis.contracts import MAX_SAFE_INTEGER
from business_analysis.partitioned import Checkpoint
from . import business_market_dynamics as owning
from .policy import AiError, canonical, digest


VIEWS = ("price_band_summary", "price_band_members", "rank_entry_exit")
MAX_ROWS, MAX_BYTES, MAX_PAGES, MAX_CHUNK_BYTES = 200_000, 64 * 1024 * 1024, 20_000, 38_000
_TOKEN = object()


@dataclass(frozen=True, slots=True, init=False)
class PreparedMarketExport:
    _manifest: str
    _chunks: tuple

    def __init__(self, token, manifest, chunks):
        if token is not _TOKEN: raise AiError("市场材料只能由完整封存准备过程构造")
        object.__setattr__(self, "_manifest", canonical(manifest))
        object.__setattr__(self, "_chunks", tuple(chunks))

    @property
    def manifest(self): return json.loads(self._manifest)

    def ndjson_pages(self, view):
        if view not in VIEWS: raise AiError("市场导出视图无效")
        return iter(tuple(raw for kind, raw in self._chunks if kind == view))


def _need(condition, message="市场导出材料与封存视图不一致"):
    if not condition: raise AiError(message, "conflict", 409)


def _bounds(limits):
    value = {"maxRows": MAX_ROWS, "maxBytes": MAX_BYTES, "maxPages": MAX_PAGES}
    if limits is not None:
        if type(limits) is not dict or set(limits)-set(value): raise AiError("市场材料容量参数无效")
        for key, limit in limits.items():
            if type(limit) is not int or not 1 <= limit <= value[key]: raise AiError("市场材料容量只能收紧")
            value[key] = limit
    return value


def _project(view, row, group=None):
    if view == "price_band_summary":
        return {"bandKey": row["bandKey"], "memberCount": len(row["members"]),
            "metrics": row["metrics"], "sourceGroupId": row["rowId"]}
    if view == "price_band_members":
        return {"bandKey": group["bandKey"], "sourceGroupId": group["rowId"],
            "date": row["date"], "skuId": row["skuId"], "spuId": row["spuId"],
            "sample": row["sample"], "sourceRowHash": row["sourceRowHash"]}
    return {"skuId": row["skuId"], "spuId": row["spuId"], "current": row["current"],
        "baseline": row["baseline"], "status": row["status"],
        "rankImprovement": row["rankImprovement"], "sourceGroupId": row["rowId"]}


def _add(view, value, binding, chunks, specs, counters, bounds, check):
    _need(value["tableDigest"] == binding["tableBindingDigest"]
        and value["tableDigest"] == digest({k:v for k,v in value.items() if k != "tableDigest"}))
    root = binding["tableBindingDigest"]
    original_rows = value["rows"]
    if view == "price_band_summary":
        projected = (_project(view, group) for group in original_rows)
    elif view == "price_band_members":
        projected = (_project(view, member, group) for group in original_rows for member in group["members"])
    else:
        projected = (_project(view, row) for row in original_rows)
    sha, size, count, pages, pending = hashlib.sha256(), 0, 0, 0, bytearray()
    def emit():
        nonlocal pending, pages
        raw = bytes(pending)
        chunks.append((view, raw)); sha.update(raw); pages += 1
        counters["pages"] += 1
        if counters["pages"] > bounds["maxPages"]: raise AiError("市场完整材料页数超过容量", "payload_too_large", 413)
        pending = bytearray()
    for body in projected:
        if check is not None and count % 1000 == 0:
            check({"stage": "market_export", "view": view, "rows": count})
        row = {"rowIndex": count, **body}
        row["id"] = digest([root, view, count, body])
        line = (canonical(row)+"\n").encode("utf-8")
        if len(line) > MAX_CHUNK_BYTES: raise AiError("单条市场材料超过固定分片容量", "payload_too_large", 413)
        if pending and len(pending)+len(line) > MAX_CHUNK_BYTES: emit()
        pending.extend(line)
        size += len(line); count += 1
        counters["rows"] += 1; counters["bytes"] += len(line)
        if counters["rows"] > bounds["maxRows"] or counters["bytes"] > bounds["maxBytes"]:
            raise AiError("市场完整材料超过容量，不返回部分内容", "payload_too_large", 413)
    emit()  # Even an empty view has one complete, hashable page.
    specs.append({"view": view, "binding": json.loads(canonical(binding)),
        "sourceTableDigest": root, "rowCount": count, "pageCount": pages,
        "ndjsonBytes": size, "ndjsonSha256": sha.hexdigest()})


def prepare(report_id, source_key, baseline_key, principal, *, bands, checkpoint=None, limits=None):
    """Build three complete tables from the same fixed report and explicit days."""
    bounds = _bounds(limits)
    check = Checkpoint.wrap(checkpoint)
    chunks, specs, counters, fixed = [], [], {"rows": 0, "bytes": 0, "pages": 0}, None
    for owning_view in ("price_band", "rank_entry_exit"):
        with owning.table(report_id, source_key, owning_view, principal,
                baseline_key=baseline_key if owning_view == "rank_entry_exit" else None,
                bands=bands if owning_view == "price_band" else None) as (value, binding):
            if fixed is None: fixed = json.loads(canonical(binding["reportBinding"]))
            else: _need(canonical(fixed) == canonical(binding["reportBinding"]), "市场两视图报告绑定变化")
            for view in (("price_band_summary", "price_band_members") if owning_view == "price_band"
                    else ("rank_entry_exit",)):
                _add(view, value, binding, chunks, specs, counters, bounds, check)
    _need(specs[0]["sourceTableDigest"] == specs[1]["sourceTableDigest"]
        and specs[0]["binding"] == specs[1]["binding"], "价格带分组与成员来源不一致")
    manifest = {"schemaVersion": "business-market-export-materials-v1",
        "algorithmVersion": owning.market_dynamics.ALGORITHM_VERSION,
        "reportBinding": fixed, "sourceKey": source_key, "baselineKey": baseline_key,
        "bands": json.loads(canonical(bands)), "tables": specs,
        "rowCount": counters["rows"], "pageCount": counters["pages"],
        "ndjsonBytes": counters["bytes"], "registeredRenderer": False,
        "authority": {"selectedTopSampleReconciled": True, "wholeMarketCoverageVerified": False,
            "ownProductIdentityVerified": False},
        "limitations": ["逐日TOP样本不代表全行业或本店经营。", "样本缺席不等于零销量。",
            "价格区间上下界和缺失分别保留；价格带汇总与成员明细不可相加。",
            "进出榜仅比较明确单日的观察，历史归属和店铺利润均未证明。"]}
    manifest["manifestDigest"] = digest(manifest)
    if len(canonical(manifest).encode("utf-8"))+counters["bytes"] > bounds["maxBytes"]:
        raise AiError("市场清单及完整材料超过容量", "payload_too_large", 413)
    if check is not None: check({"stage": "market_export", "phase": "complete"})
    owning.report_binding._revalidate(fixed, principal)
    return PreparedMarketExport(_TOKEN, manifest, chunks)
