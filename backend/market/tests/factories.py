from __future__ import annotations

import hashlib
import json

from market.import_service import ROW_KEYS
from market.revisions import canonical_json


def natural_key(row: dict[str, object]) -> str:
    parts = [
        str(row["periodStart"]),
        str(row["periodEnd"]),
        str(row["category"]),
        str(row["scope"]),
        str(row["priceBandFilter"]),
        str(row["rankingDimension"]),
        str(row["skuCode"]),
    ]
    return "market-key-v2|" + "|".join(
        f"{len(value.encode('utf-8'))}:{value}" for value in parts
    )


def market_row(**overrides) -> dict[str, object]:
    row: dict[str, object] = {
        "naturalKey": "",
        "sourceRowNumber": 2,
        "periodStart": "2026-08-01",
        "periodEnd": "2026-08-31",
        "category": "商用净饮水设备",
        "scope": "热销商品榜",
        "priceBandFilter": "全部",
        "rankingDimension": "SKU",
        "operationMode": "POP",
        "subcategory": "商用直饮机",
        "rank": 1,
        "skuCode": "SKU-001",
        "productName": "志高商用直饮机",
        "brand": "志高",
        "priceCents": 129_900,
        "priceLowCents": 129_900,
        "priceHighCents": 129_900,
        "priceEstimated": False,
        "priceRaw": "1299",
        "gmvCents": 5_000_000,
        "gmvLowCents": 4_500_000,
        "gmvHighCents": 5_500_000,
        "gmvRaw": "5万",
        "quantity": 50,
        "quantityLow": 45,
        "quantityHigh": 55,
        "quantityRaw": "50",
        "pageViews": 2_000,
        "pageViewsRaw": "2000",
        "visitors": 1_000,
        "visitorsLow": 900,
        "visitorsHigh": 1_100,
        "visitorsRaw": "1000",
        "conversionBps": 500,
        "conversionLowBps": 450,
        "conversionHighBps": 550,
        "conversionRaw": "5%",
        "cartCustomers": 120,
        "cartCustomersRaw": "120",
        "searchClicks": 800,
        "searchClicksRaw": "800",
        "imageUrl": "https://img.example.test/sku-001.jpg",
        "productUrl": "https://item.jd.com/sku-001.html",
        "raw": {"商品": "志高商用直饮机", "排名": 1},
    }
    row.update(overrides)
    row["naturalKey"] = natural_key(row)
    assert set(row) == ROW_KEYS
    return row


def prepared_payload(*rows: dict[str, object], raw_seed: str = "market-file") -> dict[str, object]:
    values = list(rows or (market_row(),))
    ranges = sorted(
        {
            canonical_json(
                {
                    "category": str(row["category"]),
                    "scope": str(row["scope"]),
                    "rankingDimension": str(row["rankingDimension"]),
                    "priceBandFilter": str(row["priceBandFilter"]),
                    "periodStart": str(row["periodStart"]),
                    "periodEnd": str(row["periodEnd"]),
                }
            ): {
                "category": str(row["category"]),
                "scope": str(row["scope"]),
                "rankingDimension": str(row["rankingDimension"]),
                "priceBandFilter": str(row["priceBandFilter"]),
                "periodStart": str(row["periodStart"]),
                "periodEnd": str(row["periodEnd"]),
            }
            for row in values
        }.values(),
        key=canonical_json,
    )
    scope = {"sourceType": "market_ranking", "ranges": ranges}
    business_rows = [
        {
            key: row[key]
            for key in sorted(ROW_KEYS - {"sourceRowNumber", "raw", "naturalKey"})
        }
        for row in sorted(values, key=lambda item: str(item["naturalKey"]))
    ]
    content_hash = hashlib.sha256(
        canonical_json(
            {
                "contractVersion": "market-import-v1",
                "scope": scope,
                "rows": business_rows,
            }
        ).encode()
    ).hexdigest()
    return {
        "contractVersion": "market-import-v1",
        "sourceType": "market_ranking",
        "fileName": "市场榜单.xlsx",
        "fileSizeBytes": 1024,
        "rawFileHash": hashlib.sha256(raw_seed.encode()).hexdigest(),
        "contentHash": content_hash,
        "sheetName": "商品榜单",
        "rows": values,
        "warnings": [],
        "scope": scope,
    }


def body_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
