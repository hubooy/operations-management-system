"""Deterministic row/table volumes, independent of rendering and persistence.

This plans known table descriptors only. It neither reads facts nor estimates
compressed or uncompressed file size. Every rendered volume still needs actual
byte-capacity verification before a future delivery protocol may publish it.
"""
from copy import deepcopy
import re
import unicodedata

from .contracts import AnalysisContractError, MAX_SAFE_INTEGER, digest


REQUEST_SCHEMA = "business-volume-request-v1"
MANIFEST_SCHEMA = "business-volume-plan-v1"


def _fields(value, expected, name):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise AnalysisContractError(f"{name}字段集合无效")


def _integer(value, lo, hi, name):
    if type(value) is not int or not lo <= value <= hi:
        raise AnalysisContractError(f"{name}须为{lo}—{hi}之间的整数")
    return value


def _text(value, maximum, name):
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum:
        raise AnalysisContractError(f"{name}须为精确的1—{maximum}字符文本")
    if any(unicodedata.category(c) in {"Cc", "Cs"} for c in value):
        raise AnalysisContractError(f"{name}包含无效字符")
    return value


def _request(value):
    _fields(value, {"schemaVersion", "reportId", "evidenceDigest", "rendererVersion", "tables"}, "多卷规划请求")
    if value["schemaVersion"] != REQUEST_SCHEMA:
        raise AnalysisContractError("多卷规划请求版本不支持")
    report = _text(value["reportId"], 160, "报告ID")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", report):
        raise AnalysisContractError("报告ID格式无效")
    evidence = value["evidenceDigest"]
    if not isinstance(evidence, str) or not re.fullmatch(r"[0-9a-f]{64}", evidence):
        raise AnalysisContractError("证据摘要须为小写SHA-256")
    renderer = _integer(value["rendererVersion"], 1, 10000, "渲染版本")
    tables = value["tables"]
    if not isinstance(tables, list) or len(tables) > 12000:
        raise AnalysisContractError("完整表描述列表最多12000项")
    seen, total = set(), 0
    for table in tables:
        _fields(table, {"key", "title", "rowCount", "columnCount"}, "表描述")
        key = _text(table["key"], 160, "表key")
        if key in seen:
            raise AnalysisContractError("表key重复，不得合并或覆盖")
        seen.add(key)
        _text(table["title"], 240, "表标题")
        total += _integer(table["rowCount"], 0, MAX_SAFE_INTEGER, "完整表行数")
        _integer(table["columnCount"], 1, 160, "表列数")
        if total > MAX_SAFE_INTEGER:
            raise AnalysisContractError("全部表总行数超过JavaScript安全整数")
    return {"schemaVersion": REQUEST_SCHEMA, "reportId": report, "evidenceDigest": evidence,
            "rendererVersion": renderer, "tables": deepcopy(tables)}


def build(request, *, max_tables=120, max_rows=1_000_000, native_budget_sheets=0, max_volumes=100):
    """Plan all descriptors in input order, or fail without a partial manifest.

Native budget sheets occupy only the first volume. If they exhaust its table
slots, that first volume is explicitly budget_only; source tables start in the
next volume. Empty source tables still occupy one visible table descriptor.
"""
    value = _request(request)
    _integer(max_tables, 1, 120, "每卷总表上限")
    _integer(max_rows, 1, 1_000_000, "每片行数上限")
    _integer(max_volumes, 1, 100, "卷数上限")
    if type(native_budget_sheets) is not int or native_budget_sheets not in (0, 3):
        raise AnalysisContractError("原生预算附加页只能为0或3")
    if native_budget_sheets > max_tables:
        raise AnalysisContractError("首卷不能容纳全部原生预算页")
    tables = value["tables"]
    if not tables and not native_budget_sheets:
        raise AnalysisContractError("没有数据表或预算页，不生成空交付卷")
    fragment_counts = [max(1, (t["rowCount"] + max_rows - 1) // max_rows) for t in tables]
    fragment_total = sum(fragment_counts)
    volume_count = max(1, (fragment_total + native_budget_sheets + max_tables - 1) // max_tables)
    if volume_count > max_volumes:
        raise AnalysisContractError(f"完整计划需要{volume_count}卷，超过{max_volumes}卷上限；未生成截断计划")
    volumes = []

    def new_volume():
        budget = native_budget_sheets if not volumes else 0
        item = {"volumeIndex": len(volumes) + 1, "volumeCount": volume_count,
                "nativeBudgetSheets": budget, "kind": "budget_only" if budget else "data", "tables": []}
        volumes.append(item)
        return item

    current = new_volume()
    for table, count in zip(tables, fragment_counts):
        for index in range(count):
            if len(current["tables"]) + current["nativeBudgetSheets"] == max_tables:
                current = new_volume()
            offset = index * max_rows
            limit = min(max_rows, table["rowCount"] - offset)
            fragment = {**table, "fragmentIndex": index + 1, "fragmentCount": count,
                        "rowOffset": offset, "rowLimit": limit,
                        "fragmentKey": "fragment-" + digest({"key": table["key"], "rowOffset": offset, "rowLimit": limit})}
            current["tables"].append(fragment)
            current["kind"] = "data_and_budget" if current["nativeBudgetSheets"] else "data"
    if len(volumes) != volume_count:
        raise AnalysisContractError("内部多卷容量核对失败")
    manifest = {"schemaVersion": MANIFEST_SCHEMA, "reportId": value["reportId"],
                "evidenceDigest": value["evidenceDigest"], "rendererVersion": value["rendererVersion"],
                "sourceDescriptorDigest": digest(tables), "requestDigest": digest(value),
                "capacity": {"maxTables": max_tables, "maxRows": max_rows, "maxVolumes": max_volumes,
                             "nativeBudgetSheets": native_budget_sheets},
                "byteCapacity": "requires_actual_render_verification", "volumeCount": volume_count,
                "sourceTableCount": len(tables), "fragmentCount": fragment_total,
                "totalRows": sum(t["rowCount"] for t in tables), "tables": tables, "volumes": volumes}
    return {**manifest, "planDigest": digest(manifest)}


def _same_bounded(actual, expected):
    """Visit only the trusted template shape, never serialize untrusted values.

Exact builtin types also prevent Python equality from equating True with 1 or
1.0 with 1. Check container/string lengths before visiting any supplied content;
recursion depth and total visited nodes are bounded by the rebuilt template.
"""
    if type(actual) is not type(expected):
        return False
    if type(expected) is dict:
        return len(actual) == len(expected) and all(
            key in actual and _same_bounded(actual[key], expected[key]) for key in expected)
    if type(expected) is list:
        return len(actual) == len(expected) and all(
            _same_bounded(actual[index], item) for index, item in enumerate(expected))
    if type(expected) is str:
        return len(actual) == len(expected) and actual == expected
    return actual == expected


def verify(manifest, request, *, max_tables=120, max_rows=1_000_000, native_budget_sheets=0, max_volumes=100):
    """Verify against the caller's trusted original request and capacity policy.

Rebuilding the bounded descriptor-only plan proves complete half-open coverage,
order, source identities and report/evidence/renderer bindings. A self-consistent
digest alone is insufficient: tampered manifests cannot replace trusted inputs.
Returns True only for an exact valid plan; malformed/altered manifests raise.
"""
    expected = build(request, max_tables=max_tables, max_rows=max_rows,
                     native_budget_sheets=native_budget_sheets, max_volumes=max_volumes)
    if type(manifest) is not dict or len(manifest) != len(expected) or any(key not in manifest for key in expected):
        raise AnalysisContractError("多卷清单字段集合无效")
    if not _same_bounded(manifest["schemaVersion"], MANIFEST_SCHEMA):
        raise AnalysisContractError("多卷清单版本不支持")
    if any(not _same_bounded(manifest[key], expected[key]) for key in expected if key != "planDigest"):
        raise AnalysisContractError("多卷清单与原始报告、来源、容量、顺序或完整行片段不一致")
    # build() has already hashed the independently reconstructed, bounded body.
    # Exact typed equality above proves the supplied body is that same body;
    # reserializing an untrusted manifest is unnecessary and unsafe before bounds.
    if not _same_bounded(manifest["planDigest"], expected["planDigest"]):
        raise AnalysisContractError("多卷规划摘要不匹配")
    return True
