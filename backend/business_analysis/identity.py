"""Conservative, exact product matching over completely verified source pages."""
from collections import defaultdict
from .contracts import AnalysisContractError, PageReconciler, MAX_SAFE_INTEGER


def product_reconciliation(sales_pages, master_pages, *, max_rows=5000):
    def read(pages, kind):
        verifier, records, scope = PageReconciler(), [], None
        for page in pages:
            if kind == "sales" and page.get("source") != "erp_sales" or kind == "master" and page.get("sourceDataset") != "product_master":
                raise AnalysisContractError("商品关联来源类型不匹配")
            current = (page["filters"]["platform"], page["filters"]["shop"])
            if scope is not None and current != scope:
                raise AnalysisContractError("来源店铺身份变化")
            scope = current
            verifier.consume(page, request_cursor=verifier.expected_cursor)
            records.extend(page["items"])
            if len(records) > max_rows:
                raise AnalysisContractError("关联超过当前分区容量；不得截取后作为全量")
            if any((row["platform"], row["shopName"]) != scope for row in page["items"]):
                raise AnalysisContractError("来源页包含其他店铺")
        return scope, records, verifier.result()
    sales_scope, sales, sales_evidence = read(sales_pages, "sales")
    master_scope, masters, master_evidence = read(master_pages, "master")
    if sales_scope != master_scope:
        raise AnalysisContractError("销售与主数据店铺不一致")
    candidates = defaultdict(set)
    for row in masters:
        code = row.get("dimensions", {}).get("merchantCode")
        if code and row.get("skuId"):
            candidates[code].add((row["skuId"], row.get("spuId")))
    groups = {}
    total = defaultdict(int)
    for row in sales:
        # ERP product_code is deliberately not a fallback for online_spec_code.
        matched = candidates.get(row.get("onlineSpecCode"), set())
        status = "matched" if len(matched) == 1 else "ambiguous" if matched else "unmatched"
        sku, spu = next(iter(matched)) if status == "matched" else (None, None)
        key = (status, sku, spu)
        group = groups.setdefault(key, {"status": status, "skuId": sku, "spuId": spu, "rowCount": 0, "metrics": defaultdict(int)})
        group["rowCount"] += 1
        for metric, value in row["metrics"].items():
            if type(value) is not int:
                raise AnalysisContractError("销售核对指标缺失或无效")
            group["metrics"][metric] += value
            total[metric] += value
            if abs(total[metric]) > MAX_SAFE_INTEGER or abs(group["metrics"][metric]) > MAX_SAFE_INTEGER:
                raise AnalysisContractError("关联汇总超过无损整数范围")
    if any(value["missingRows"] or total[key] != (value["value"] or 0) for key, value in sales_evidence["metrics"].items()):
        raise AnalysisContractError("关联金额未通过源核对")
    return {"schemaVersion": "business-product-mapping-v1", "platform": sales_scope[0], "shopName": sales_scope[1],
        "sources": {"sales": sales_evidence, "master": master_evidence}, "rowCount": len(sales), "reconciled": True,
        "mappingBasis": "exact_online_spec_code_to_current_master_merchant_code_not_historical_mapping",
        "groups": list(groups.values()), "totals": dict(total),
        "coverage": {status: sum(g["rowCount"] for g in groups.values() if g["status"] == status) for status in ("matched", "ambiguous", "unmatched")},
        "limitations": ["歧义和未匹配金额保留独立组，不复制给任何SKU", "当前主数据不是历史主数据", "未分摊关键词利润", "不是广告成交与ERP销售口径一致的证明"]}
