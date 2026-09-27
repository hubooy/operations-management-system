from __future__ import annotations

import hashlib
import json


def netshop_row(
    *,
    source: str = "jd_sku_daily",
    dataset: str = "sku_daily",
    platform: str = "京东",
    shop_name: str = "京东一店",
    business_date: str = "2026-08-30",
    snapshot_date: str = "",
    sku_id: str = "SKU-1",
    spu_id: str = "SPU-1",
    product_code: str = "P-1",
    product_name: str = "商用饮水机",
    metrics: dict[str, object] | None = None,
    raw: dict[str, object] | None = None,
    row_number: int = 2,
) -> dict[str, object]:
    metrics = metrics or {
        "pageViews": 100,
        "visitors": 50,
        "transactionAmountCents": 12_345,
        "transactionQuantity": 2,
    }
    raw = raw or {"商品名称": product_name}
    identity = "\x1f".join(
        [source, dataset, platform, shop_name, business_date, snapshot_date, sku_id, spu_id, product_code]
    )
    raw_hash = hashlib.sha256(
        json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "sourceRowNumber": row_number,
        "sourceRowKey": hashlib.sha256(identity.encode()).hexdigest(),
        "sourceRowHash": raw_hash,
        "source": source,
        "dataset": dataset,
        "platform": platform,
        "shopName": shop_name,
        "businessDate": business_date,
        "snapshotDate": snapshot_date,
        "productCode": product_code,
        "productName": product_name,
        "skuId": sku_id,
        "spuId": spu_id,
        "warehouseType": "",
        "metrics": metrics,
        "raw": raw,
    }


def prepared_payload(*rows: dict[str, object], raw_seed: str = "netshop-file") -> dict[str, object]:
    first = rows[0]
    dates = sorted({str(row["businessDate"]) for row in rows if row["businessDate"]})
    return {
        "schemaVersion": "netshop-normalized-v1",
        "disposition": "prepared",
        "fileName": "商品明细.xlsx",
        "fileSizeBytes": 2_048,
        "rawFileHash": hashlib.sha256(raw_seed.encode()).hexdigest(),
        "source": first["source"],
        "dataset": first["dataset"],
        "platform": first["platform"],
        "shopName": first["shopName"],
        "sheetName": "明细",
        "note": "",
        "snapshotDate": first["snapshotDate"],
        "startDate": dates[0] if dates else "",
        "endDate": dates[-1] if dates else "",
        "rows": list(rows),
        "warnings": [],
        "parserTotals": {"rowCount": len(rows)},
        "imagePersistence": {},
    }


def body_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
