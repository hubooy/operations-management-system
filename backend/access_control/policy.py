from __future__ import annotations

import hashlib
import json
import re
from typing import Any


ROLE_CATALOG: dict[str, dict[str, object]] = {
    "viewer": {
        "label": "查看者",
        "description": "只读访问本人数据范围内的业务页面与安全查询。",
        "rank": 10,
        "permissions": ["data.read"],
    },
    "analyst": {
        "label": "分析员",
        "description": "在只读数据范围内使用分析、搜索与 AI 查询能力。",
        "rank": 20,
        "permissions": ["data.read", "analytics.read", "ai.query"],
    },
    "operator": {
        "label": "运营人员",
        "description": "执行已授权的运营事务、工作流和受控业务写入。",
        "rank": 30,
        "permissions": [
            "data.read", "analytics.read", "ai.query", "operations.write",
            "workflow.execute",
        ],
    },
    "admin": {
        "label": "管理员",
        "description": "管理系统配置、数据导入、用户、角色分配与权限审计。",
        "rank": 40,
        "permissions": [
            "data.read", "analytics.read", "ai.query", "operations.write",
            "workflow.execute", "imports.execute", "settings.write",
            "access_control.manage", "access_control.audit.read",
        ],
    },
}
ROLE_CODES = frozenset(ROLE_CATALOG)
USER_STATUSES = frozenset({"active", "disabled"})
SCOPE_KEYS = ("warehouses", "channels", "platforms")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")


class PolicyError(ValueError):
    pass


def normalize_email(value: object) -> str:
    if not isinstance(value, str):
        raise PolicyError("邮箱格式无效")
    email = value.strip().lower()
    if not email or len(email) > 320 or not EMAIL_RE.fullmatch(email):
        raise PolicyError("邮箱格式无效")
    return email


def normalize_display_name(value: object) -> str:
    if not isinstance(value, str):
        raise PolicyError("显示名称必须为 1-200 个字符")
    display_name = value.strip()
    if not display_name or len(display_name) > 200:
        raise PolicyError("显示名称必须为 1-200 个字符")
    return display_name


def normalize_scope(value: object) -> dict[str, list[str]] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != set(SCOPE_KEYS):
        raise PolicyError("数据范围必须完整声明仓库、渠道和平台")
    result: dict[str, list[str]] = {}
    for key in SCOPE_KEYS:
        raw_items = value.get(key)
        if not isinstance(raw_items, list) or len(raw_items) > 500:
            raise PolicyError("数据范围数组无效")
        items: list[str] = []
        seen: set[str] = set()
        for raw in raw_items:
            if not isinstance(raw, str):
                raise PolicyError("数据范围只允许字符串")
            item = raw.strip()
            if not item or len(item) > 100:
                raise PolicyError("数据范围值必须为 1-100 个字符")
            if item not in seen:
                seen.add(item)
                items.append(item)
        result[key] = sorted(items)
    return result


def scope_covers(current: dict[str, list[str]] | None, snapshot: dict[str, list[str]] | None) -> bool:
    if current is None:
        return True
    if snapshot is None:
        return False
    return all(set(snapshot[key]).issubset(current[key]) for key in SCOPE_KEYS)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
