"""Complete internal NDJSON materials; no renderer/file-run registration.

Nothing is returned until both view contexts and final live authorization pass.
Prepared bytes are not a transferable authorization token; a future file writer
must reauthorize their fixed report before publication or delivery.
"""
from dataclasses import dataclass
import hashlib
import json

from business_analysis.contracts import MAX_SAFE_INTEGER
from business_analysis.partitioned import Checkpoint
from . import business_promotion_keyword_sku as owning
from .policy import AiError, canonical, digest

VIEWS = ("keyword_sku", "keyword_sku_context")
MAX_ROWS, MAX_BYTES, MAX_PAGES = 250_000, 64 * 1024 * 1024, 20_000
_TOKEN = object()


@dataclass(frozen=True, slots=True, init=False)
class PreparedPromotionExport:
    _manifest: str
    _chunks: tuple

    def __init__(self, token, manifest, chunks):
        if token is not _TOKEN: raise AiError("推广材料只能由完整准备过程构造")
        object.__setattr__(self, "_manifest", canonical(manifest))
        object.__setattr__(self, "_chunks", tuple(chunks))

    @property
    def manifest(self): return json.loads(self._manifest)

    def ndjson_pages(self, view):
        if view not in VIEWS: raise AiError("推广材料视图无效")
        return iter(tuple(raw for kind, raw in self._chunks if kind == view))


def _conflict(message="推广完整导出材料校验失败"):
    raise AiError(message, "conflict", 409)


def prepare(report_id, source_key, principal, *, baseline_key=None, checkpoint=None, limits=None):
    bounds = {"maxRows": MAX_ROWS, "maxBytes": MAX_BYTES, "maxPages": MAX_PAGES}
    if limits is not None:
        if type(limits) is not dict or set(limits)-set(bounds): raise AiError("推广材料容量参数无效")
        for key, value in limits.items():
            if type(value) is not int or not 1 <= value <= bounds[key]: raise AiError("推广材料容量只能收紧")
            bounds[key] = value
    check = Checkpoint.wrap(checkpoint)
    chunks, tables, fixed_report = [], [], None
    total_rows, total_bytes, total_pages = 0, 0, 0
    # Each view independently scans complete fixed facts; their expenses overlap.
    for view in VIEWS:
        with owning.table(report_id, source_key, view, principal, baseline_key=baseline_key, checkpoint=check) as (table, binding):
            header = table.header()
            if binding["tableBindingDigest"] != header["tableBindingDigest"]: _conflict()
            if fixed_report is None: fixed_report = json.loads(canonical(binding["reportBinding"]))
            elif canonical(fixed_report) != canonical(binding["reportBinding"]): _conflict("两张推广视图的报告绑定变化")
            offset, count, pages, size = 0, 0, 0, 0
            sha = hashlib.sha256()
            missing_sku_rows, unqualified_rows = 0, 0
            sums = {"current": {"value": None, "presentGroups": 0, "missingFactRows": 0},
                    "baseline": {"value": None, "presentGroups": 0, "missingFactRows": 0}}
            while True:
                if check is not None: check({"stage": "promotion_export", "view": view, "offset": offset})
                page = table.page(offset, 20)
                if page["pageDigest"] != digest({k:v for k,v in page.items() if k != "pageDigest"}): _conflict("推广页摘要不匹配")
                if canonical(header) != canonical({k:v for k,v in page.items() if k not in {"rows", "pagination", "pageDigest"}}): _conflict()
                rows, pagination = page["rows"], page["pagination"]
                if pagination != {"offset": offset, "limit": 20, "returned": len(rows), "total": header["total"],
                    "nextOffset": offset+len(rows) if offset+len(rows)<header["total"] else None}: _conflict()
                for index, row in enumerate(rows, offset):
                    if row["rowIndex"] != index or row["id"] != digest([header["tableBindingDigest"], row["entity"]]): _conflict("推广行位置或身份不匹配")
                    missing_sku_rows += row["entity"].get("promotedSkuId") is None
                    unqualified_rows += row["identityQualified"] is not True
                    for side, key in (("current", "metrics"), ("baseline", "baselineMetrics")):
                        cell = (row[key] or {}).get("spendCents")
                        if cell is not None:
                            sums[side]["missingFactRows"] += cell["missingRows"]
                            if cell["value"] is not None:
                                sums[side]["value"] = (sums[side]["value"] or 0) + cell["value"]
                                sums[side]["presentGroups"] += 1
                                if abs(sums[side]["value"]) > MAX_SAFE_INTEGER: _conflict("推广材料汇总超安全整数")
                raw = b"".join((canonical(row)+"\n").encode("utf-8") for row in rows)
                total_rows += len(rows); total_bytes += len(raw); total_pages += 1
                if total_rows > bounds["maxRows"] or total_bytes > bounds["maxBytes"] or total_pages > bounds["maxPages"]:
                    raise AiError("推广完整材料超过容量，不返回部分内容", "payload_too_large", 413)
                chunks.append((view, raw)); sha.update(raw)
                count += len(rows); pages += 1; size += len(raw)
                if pagination["nextOffset"] is None: break
                if not rows or pagination["nextOffset"] <= offset: _conflict("推广分页未推进")
                offset = pagination["nextOffset"]
            if count != header["total"]: _conflict("推广材料未完整遍历")
            tables.append({"view": view, "binding": binding, "header": header, "rowCount": count,
                "pageCount": pages, "ndjsonBytes": size, "ndjsonSha256": sha.hexdigest(), "spendTotals": sums,
                "missingPromotedSkuGroups": missing_sku_rows, "unqualifiedIdentityGroups": unqualified_rows})
    # Same facts grouped two ways must conserve spend, but groups need not match.
    for side in ("current", "baseline"):
        a, b = (table["spendTotals"][side] for table in tables)
        if (a["value"], a["missingFactRows"]) != (b["value"], b["missingFactRows"]): _conflict("两种分组费用对账不一致")
    manifest = {"schemaVersion": "business-promotion-export-materials-v1", "reportBinding": fixed_report,
        "sourceKey": source_key, "baselineKey": baseline_key, "algorithmVersion": owning.promotion_keyword_sku.ALGORITHM_VERSION,
        "tables": tables, "rowCount": total_rows, "ndjsonBytes": total_bytes,
        "tableExpensesAreAdditive": False, "registeredRenderer": False,
        "limitations": ["两表是同一推广事实的不同分组，花费不可相加。", "平台归因成交不是ERP净销售、利润或因果增量。",
            "缺推广SKU桶保留原金额，不代表可操作的自家商品。", "材料尚未注册文件renderer，使用前仍须复验报告授权。"]}
    manifest["manifestDigest"] = digest(manifest)
    if len(canonical(manifest).encode("utf-8")) + total_bytes > bounds["maxBytes"]:
        raise AiError("推广完整清单及材料超过容量", "payload_too_large", 413)
    if check is not None: check({"stage": "promotion_export", "phase": "complete"})
    owning.report_binding._revalidate(fixed_report, principal)
    return PreparedPromotionExport(_TOKEN, manifest, chunks)
