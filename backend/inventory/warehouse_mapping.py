from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re


WAREHOUSE_CATEGORIES = frozenset(
    {
        "jd",
        "dropship",
        "afterSales",
        "guangdong",
        "sample",
        "cainiao",
        "overseas",
        "virtual",
        "exception",
        "selfOperated",
    }
)
CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "inventory-warehouse-mapping.json"


@dataclass(frozen=True)
class WarehouseClassification:
    warehouse_type: str
    category: str
    include_in_inventory: bool
    label: str
    source: str


def _load_mapping() -> dict[str, dict[str, object]]:
    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError("库存仓库类型映射配置不可用") from error
    entries = payload.get("warehouses") if isinstance(payload, dict) else None
    if not isinstance(entries, dict) or not 1 <= len(entries) <= 2_000:
        raise RuntimeError("库存仓库类型映射配置无效")
    normalized: dict[str, dict[str, object]] = {}
    for warehouse, entry in entries.items():
        if not isinstance(warehouse, str) or not warehouse.strip() or warehouse != warehouse.strip():
            raise RuntimeError("库存仓库类型映射包含无效仓库名")
        if not isinstance(entry, dict) or set(entry) != {"category", "label", "includeInInventory"}:
            raise RuntimeError("库存仓库类型映射包含无效字段")
        category = entry.get("category")
        label = entry.get("label")
        included = entry.get("includeInInventory")
        if category not in WAREHOUSE_CATEGORIES - {"selfOperated"} or not isinstance(label, str) or not label or not isinstance(included, bool):
            raise RuntimeError("库存仓库类型映射包含无效分类")
        normalized[warehouse] = entry
    return normalized


WAREHOUSE_MAPPING = _load_mapping()


def _warehouse_type(category: str) -> str:
    if category == "jd":
        return "jd_rdc"
    if category in {"guangdong", "afterSales", "sample", "selfOperated"}:
        return "owned"
    return "other"


def classify_warehouse(
    warehouse: str,
    *,
    stored_type: str = "",
    stored_category: str = "",
    stored_include_in_inventory: bool = True,
    mapping: dict[str, dict[str, object]] | None = None,
) -> WarehouseClassification:
    normalized = warehouse.strip()
    configured = (mapping if mapping is not None else WAREHOUSE_MAPPING).get(normalized)
    if configured is not None:
        category = str(configured["category"])
        return WarehouseClassification(
            _warehouse_type(category),
            category,
            bool(configured["includeInInventory"]),
            str(configured["label"]),
            "configured",
        )
    if stored_category in WAREHOUSE_CATEGORIES:
        return WarehouseClassification(
            _warehouse_type(stored_category),
            stored_category,
            stored_include_in_inventory,
            stored_category,
            "stored",
        )
    if re.search(r"京东|rdc|dc仓|配送中心", normalized, re.I) or re.search(
        r"(?:平台仓|中件(?:消费品)?)[^\r\n]*-chn$", normalized, re.I
    ):
        category = "jd"
        label = "京东仓"
    elif "代发" in normalized:
        category = "dropship"
        label = "代发仓"
    elif "菜鸟" in normalized:
        category = "cainiao"
        label = "菜鸟仓"
    elif "售后" in normalized:
        category = "afterSales"
        label = "售后仓"
    elif "广东" in normalized:
        category = "guangdong"
        label = "广东仓"
    elif "样品" in normalized:
        category = "sample"
        label = "样品仓"
    else:
        category = "selfOperated"
        label = "自营仓"
    inferred_type = "owned" if stored_type == "owned" and category == "selfOperated" else _warehouse_type(category)
    return WarehouseClassification(
        inferred_type,
        category,
        normalized != "刷刷仓",
        label,
        "inferred",
    )
