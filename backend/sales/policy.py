"""Validated sales-import policy shared by the PostgreSQL write domain."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


POLICY_PATH = Path(__file__).resolve().parent / "resources" / "sales-import-policy.json"


class SalesImportPolicyError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def sales_import_policy() -> dict[str, object]:
    try:
        payload = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SalesImportPolicyError("销售导入策略无法读取") from error
    if not isinstance(payload, dict):
        raise SalesImportPolicyError("销售导入策略格式无效")
    version = payload.get("version")
    time_zone = payload.get("timeZone")
    channels = payload.get("approvedSalesChannels")
    warehouses = payload.get("excludedWarehouses")
    cost_source = payload.get("costSource")
    zero_cost_names = cost_source.get("zeroCostProductNames") if isinstance(cost_source, dict) else None
    if not isinstance(version, str) or not version.strip() or time_zone != "Asia/Shanghai":
        raise SalesImportPolicyError("销售导入策略版本或时区无效")
    if (
        not isinstance(channels, list)
        or not channels
        or any(not isinstance(item, str) or not item.strip() for item in channels)
        or len(set(channels)) != len(channels)
    ):
        raise SalesImportPolicyError("销售渠道白名单无效")
    if (
        not isinstance(warehouses, list)
        or not warehouses
        or any(not isinstance(item, str) or not item.strip() for item in warehouses)
    ):
        raise SalesImportPolicyError("销售排除仓策略无效")
    if not isinstance(zero_cost_names, list) or any(
        not isinstance(item, str) or not item.strip() for item in zero_cost_names
    ):
        raise SalesImportPolicyError("销售零成本例外策略无效")
    return {
        "version": version.strip(),
        "timeZone": time_zone,
        "approvedSalesChannels": tuple(item.strip() for item in channels),
        "excludedWarehouses": tuple(item.strip() for item in warehouses),
        "zeroCostProductNames": tuple(item.strip() for item in zero_cost_names),
    }


def policy_version() -> str:
    return str(sales_import_policy()["version"])


def approved_sales_channels() -> tuple[str, ...]:
    return sales_import_policy()["approvedSalesChannels"]  # type: ignore[return-value]


def excluded_sales_warehouses() -> tuple[str, ...]:
    return sales_import_policy()["excludedWarehouses"]  # type: ignore[return-value]


def zero_cost_product_names() -> tuple[str, ...]:
    return sales_import_policy()["zeroCostProductNames"]  # type: ignore[return-value]
