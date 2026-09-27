"""Conservative market-to-current-shop product candidates from sealed owning pages.

The market sample has no shop owner. A candidate is only a review aid; it never
attributes market sales, historical ownership, or competitor products to a shop.
"""
from __future__ import annotations

import json

from .contracts import AnalysisContractError, PageReconciler, canonical, coverage as date_coverage, digest, strict_date


SCHEMA_VERSION = "market-own-product-candidates-v1"
MAX_PAGES = 1000
MAX_ROWS = 20000
MAX_REQUESTED_IDS = 1000
MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_PAGE_BYTES = 128 * 1024
MAX_RESULT_BYTES = 38000


def _require(condition, message):
    if not condition:
        raise AnalysisContractError(message)


def _text(value, *, empty=False):
    return type(value) is str and (empty or bool(value)) and len(value) <= 600


def _read(pages, *, kind, expected):
    """Reconcile an entire bounded source against an independent sealed proof."""
    _require(type(expected) is dict and expected.get("reconciled") is True,
        "缺少独立封存的完整来源核对记录")
    verifier, metadata, items, total_bytes, count = PageReconciler(), None, [], 0, 0
    for page in pages:
        _require(type(page) is dict and type(page.get("items")) is list,
            "来源页格式无效")
        encoded = canonical(page).encode("utf-8")
        total_bytes += len(encoded)
        count += 1
        _require(count <= MAX_PAGES and len(encoded) <= MAX_PAGE_BYTES
            and total_bytes <= MAX_SOURCE_BYTES and len(items) + len(page["items"]) <= MAX_ROWS,
            "来源页、行数或字节容量超过上限，不得截断")
        _require((page.get("source") == "market_daily_top" and page.get("sourceDataset") == "market_daily_top")
            if kind == "market" else (page.get("sourceDataset") == "product_master"
            and page.get("source") in {"jd_product_master", "tmall_product_master"}),
            "来源类型不匹配")
        current = {key: page.get(key) for key in ("source", "sourceDataset", "sourceRef", "sourceRevision", "filters", "coverage")}
        if metadata is None:
            metadata = current
        else:
            # Only the first page carries coverage, but all other identity and
            # query fields must stay byte-for-byte equivalent throughout.
            _require(all(current[key] == metadata[key] for key in ("source", "sourceDataset", "sourceRef", "sourceRevision", "filters"))
                and current["coverage"] is None, "分页期间来源身份或查询发生变化")
        verifier.consume(page, request_cursor=verifier.expected_cursor)
        items.extend(page["items"])
    proof = verifier.result()
    _require(canonical(proof) == canonical(expected), "来源与独立封存核对记录不一致")
    return metadata, items, proof


class CandidateResult:
    def __init__(self, summary, rows):
        self._summary = canonical(summary)
        self._rows = tuple(canonical(row) for row in rows)

    def summary(self):
        return json.loads(self._summary)

    def page(self, offset=0, limit=20):
        _require(type(offset) is int and 0 <= offset <= len(self._rows)
            and type(limit) is int and 1 <= limit <= 100, "候选分页参数无效")
        rows = []
        for raw in self._rows[offset:offset + limit]:
            candidate = rows + [json.loads(raw)]
            envelope = {**self.summary(), "rows": candidate,
                "pagination": {"offset": offset, "limit": limit, "total": len(self._rows),
                    "nextOffset": offset + len(candidate) if offset + len(candidate) < len(self._rows) else None}}
            _require(len(canonical(envelope).encode("utf-8")) <= MAX_RESULT_BYTES or rows,
                "单条候选记录超过输出容量")
            if len(canonical(envelope).encode("utf-8")) > MAX_RESULT_BYTES:
                break
            rows = candidate
        envelope = {**self.summary(), "rows": rows,
            "pagination": {"offset": offset, "limit": limit, "total": len(self._rows),
                "nextOffset": offset + len(rows) if offset + len(rows) < len(self._rows) else None}}
        _require(len(canonical(envelope).encode("utf-8")) <= MAX_RESULT_BYTES,
            "候选汇总超过输出容量")
        envelope["pageDigest"] = digest(envelope)
        _require(len(canonical(envelope).encode("utf-8")) <= MAX_RESULT_BYTES,
            "候选页超过输出容量")
        return envelope


def build_candidates(market_pages, master_pages, *, platform, shop, start_date, end_date,
                     market_expected, master_expected, requested_product_ids=None):
    """Return a deterministic, copy-on-read candidate result from complete facts."""
    _require(platform == "京东" and _text(shop) and platform == platform.strip()
        and shop == shop.strip(), "必须指定精确平台与店铺")
    first, last = strict_date(start_date), strict_date(end_date)
    _require(first <= last and (last - first).days < 93, "候选日期范围无效")
    requested = [] if requested_product_ids is None else requested_product_ids
    _require(type(requested) is list and len(requested) <= MAX_REQUESTED_IDS
        and all(_text(value) and len(value) <= 160 for value in requested)
        and len(set(requested)) == len(requested), "待核查商品主键列表无效或超过容量")
    market, ranking, market_proof = _read(market_pages, kind="market", expected=market_expected)
    master, products, master_proof = _read(master_pages, kind="master", expected=master_expected)
    mf, nf = market["filters"], master["filters"]
    _require(type(mf) is dict and type(nf) is dict and mf.get("platform") == platform
        and mf.get("shop") == "" and nf.get("platform") == platform
        and nf.get("shop") == shop and nf.get("dataset") == "master"
        and nf.get("window") == "current" and master["source"] == "jd_product_master",
        "市场与当前主数据的平台或店铺不一致")
    window = (mf.get("periods") or {}).get(mf.get("window", "current"), {})
    _require(window.get("startDate") == start_date and window.get("endDate") == end_date,
        "市场样本日期范围与明确选择不一致")
    _require(canonical(market["coverage"]) == canonical(date_coverage(window,
        [entry.get("date") for entry in ranking])), "市场日期覆盖与完整行不一致")
    coverage = master["coverage"]
    _require(type(coverage) is dict and coverage.get("historicalMapping") is False,
        "主数据快照覆盖信息缺失")
    snapshot = coverage.get("snapshotDate")
    if snapshot is not None:
        strict_date(snapshot)
    _require(coverage.get("status") in {"current_master", "no_records"}
        and (coverage["status"] == "current_master") == bool(snapshot),
        "主数据快照状态不一致")
    sku, spu = {}, {}
    for product in products:
        _require(type(product) is dict and product.get("platform") == platform
            and product.get("shopName") == shop
            and product.get("batchId") == coverage.get("batchId"),
            "主数据行不属于选定店铺或封存批次")
        _require(all(value is None or value == "" or _text(value)
            for value in (product.get("skuId"), product.get("spuId"))),
            "主数据商品主键无效")
        for field, index in (("skuId", sku), ("spuId", spu)):
            key = product.get(field)
            if key is None or key == "":
                continue
            _require(_text(key), "主数据商品主键无效")
            index.setdefault(key, []).append(product)
    rows = []
    ranked_ids = set()
    for entry in ranking:
        _require(type(entry) is dict and entry.get("platform") == platform
            and entry.get("shopName") == "", "市场行被错误标为店铺商品")
        _require(type(entry.get("rowId")) is str and entry["rowId"].isascii()
            and entry["rowId"].isdigit() and int(entry["rowId"]) > 0,
            "市场行身份无效")
        day = strict_date(entry.get("date"))
        _require(first <= day <= last, "市场行日期不在明确选择范围内")
        dimension = mf.get("rankingDimension")
        _require(dimension in {"SKU", "SPU"}, "市场榜单维度无效")
        field, index = ("skuId", sku) if dimension == "SKU" else ("spuId", spu)
        identity = entry.get(field)
        if identity:
            ranked_ids.add(identity)
        rank = (entry.get("sample") or {}).get("rank")
        _require(identity is None or _text(identity), "市场商品主键无效")
        _require(rank is None or (type(rank) is int and rank > 0), "市场名次无效")
        matches = index.get(identity, []) if identity else []
        # Multiple SKU variants under one SPU still identify the same SPU.
        unique = {(p.get("skuId"), p.get("spuId")) for p in matches}
        ambiguous = len(matches) > 1 if dimension == "SKU" else len({p.get("spuId") for p in matches}) > 1
        if rank is None:
            reason = "unranked"
        elif not identity:
            reason = "missing_market_id"
        elif not snapshot:
            reason = "missing_current_master"
        elif not matches:
            reason = "unmatched_current_master"
        elif ambiguous or (dimension == "SKU" and len(unique) != 1):
            reason = "ambiguous_current_master"
        else:
            reason = None
        matched = matches[0] if reason is None else None
        rows.append({"marketRowId": entry.get("rowId"), "marketDate": entry["date"],
            "marketSourceRowHash": entry.get("sourceRowHash"),
            "rankingDimension": dimension, "marketProductId": identity, "rank": rank,
            "status": "candidate" if matched else "unresolved", "reason": reason,
            "selectedPlatform": platform, "selectedShop": shop,
            "currentMasterSnapshotDate": snapshot, "currentMasterBatchId": coverage.get("batchId"),
            "currentMasterSkuId": matched.get("skuId") if matched and dimension == "SKU" else None,
            "currentMasterSpuId": matched.get("spuId") if matched else None,
            "currentMasterSourceRowHash": matched.get("sourceRowHash") if matched and dimension == "SKU" else None,
            "manualConfirmationRequired": True, "historicalOwnershipVerified": False,
            "marketSalesAttributedToShop": False})
    for identity in requested:
        if identity in ranked_ids:
            continue
        rows.append({"marketRowId": None, "marketDate": None,
            "marketSourceRowHash": None,
            "rankingDimension": mf["rankingDimension"], "marketProductId": identity,
            "rank": None, "status": "unresolved",
            "reason": "not_ranked_in_sample" if market["coverage"]["status"] == "dates_present"
                else "market_coverage_incomplete",
            "selectedPlatform": platform, "selectedShop": shop,
            "currentMasterSnapshotDate": snapshot, "currentMasterBatchId": coverage.get("batchId"),
            "currentMasterSkuId": None, "currentMasterSpuId": None,
            "currentMasterSourceRowHash": None,
            "manualConfirmationRequired": True, "historicalOwnershipVerified": False,
            "marketSalesAttributedToShop": False})
    _require(len(rows) <= MAX_ROWS, "候选结果超过行容量，不得截断")
    rows.sort(key=lambda row: (row["marketDate"] or "9999-12-31", row["rankingDimension"],
        row["marketProductId"] or "", int(row["marketRowId"] or 0)))
    counts = {"candidate": sum(row["status"] == "candidate" for row in rows),
        "unresolved": sum(row["status"] == "unresolved" for row in rows)}
    summary = {"schemaVersion": SCHEMA_VERSION, "platform": platform, "shop": shop,
        "startDate": start_date, "endDate": end_date, "rowCount": len(rows), "counts": counts,
        "sourceProofs": {"market": market_proof, "currentMaster": master_proof},
        "sourceRevisions": {"market": market["sourceRevision"],
            "currentMaster": master["sourceRevision"]},
        "coverage": {"market": market["coverage"], "currentMaster": coverage},
        "requestedProductIdsDigest": digest(sorted(requested)),
        "semantics": "精确商品主键生成的人工复核候选；当前主数据不证明历史归属，市场样本不属于店铺。"}
    summary["resultDigest"] = digest([summary, rows])
    _require(len(canonical(summary).encode("utf-8")) <= MAX_RESULT_BYTES,
        "候选汇总超过输出容量")
    return CandidateResult(summary, rows)
