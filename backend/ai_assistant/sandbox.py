"""Deterministic JSON AST transforms. No eval, process, file or network operations."""

import json
import math
from . import models as m, transport
from .policy import (
    AiError,
    canonical,
    choice,
    digest,
    fields,
    integer,
    mutation,
    owned,
    page,
    passive,
    record,
    text,
    uid,
)

DATASETS = ["sales_category", "netshop_product_daily", "netshop_promotion"]
OPS = {
    "filter": {"op", "field", "operator", "textValue", "numberValue", "values"},
    "select": {"op", "fields"},
    "derive": {
        "op",
        "as",
        "operator",
        "leftField",
        "leftValue",
        "rightField",
        "rightValue",
    },
    "group": {"op", "groupBy", "metrics"},
    "sort": {"op", "field", "direction"},
    "limit": {"op", "count"},
}


def field(value):
    result = text(value, "field", 80)
    if result in {"__proto__", "constructor", "prototype"}:
        raise AiError("字段名无效")
    return result


def field_list(value, minimum=0):
    if (
        not isinstance(value, list)
        or not minimum <= len(value) <= 20
        or len(set(value)) != len(value)
    ):
        raise AiError("字段数组无效")
    return [field(v) for v in value]


def numeric(value):
    return value if type(value) in {int, float} and math.isfinite(value) else None


def scalar_text(value):
    return (
        ""
        if value is None
        else "true"
        if value is True
        else "false"
        if value is False
        else str(value)
    )


def validate_steps(steps):
    if not isinstance(steps, list) or len(steps) > 8:
        raise AiError("分析步骤最多 8 步")
    for s in steps:
        if not isinstance(s, dict) or s.get("op") not in OPS:
            raise AiError("分析步骤无效")
        op = s["op"]
        fields(s, OPS[op], {"op"})
        if op in {"filter", "sort"}:
            field(s.get("field"))
        if op == "filter":
            operator = choice(
                s.get("operator"),
                ["eq", "ne", "contains", "gt", "gte", "lt", "lte", "in"],
                "operator",
            )
            if operator == "in":
                if (
                    not isinstance(s.get("values"), list)
                    or not 1 <= len(s["values"]) <= 20
                ):
                    raise AiError("values 无效")
                for v in s["values"]:
                    text(v, "value", 120, empty=True)
            elif operator in ["gt", "gte", "lt", "lte"]:
                if numeric(s.get("numberValue")) is None:
                    raise AiError("numberValue 必须为有限数值")
            else:
                text(s.get("textValue"), "textValue", 240, empty=True)
        elif op == "select":
            field_list(s.get("fields"), 1)
        elif op == "derive":
            field(s.get("as"))
            choice(
                s.get("operator"), ["add", "subtract", "multiply", "divide"], "operator"
            )
            for side in ["left", "right"]:
                if (side + "Field" in s) == (side + "Value" in s):
                    raise AiError("操作数必须且只能指定字段或数值")
                if side + "Field" in s:
                    field(s[side + "Field"])
                elif numeric(s[side + "Value"]) is None:
                    raise AiError("操作数无效")
        elif op == "group":
            names = field_list(s.get("groupBy", []))
            metrics = s.get("metrics")
            if (
                not isinstance(metrics, list)
                or not 1 <= len(metrics) <= 10
                or len(names) + len(metrics) > 20
            ):
                raise AiError("聚合字段超限")
            for metric in metrics:
                fields(metric, {"aggregate", "field", "as"}, {"aggregate", "as"})
                field(metric["as"])
                choice(
                    metric["aggregate"],
                    ["count", "sum", "avg", "min", "max"],
                    "aggregate",
                )
                if metric["aggregate"] != "count" or "field" in metric:
                    field(metric.get("field"))
        elif op == "sort":
            choice(s.get("direction", "desc"), ["asc", "desc"], "direction")
        elif op == "limit":
            integer(s.get("count"), "count", 1, 100)
    return steps


def transform(raw_rows, steps):
    validate_steps(steps)
    if not isinstance(raw_rows, list) or len(raw_rows) > 50:
        raise AiError("源行超限")
    rows = []
    for source in raw_rows:
        if not isinstance(source, dict):
            raise AiError("源行格式无效")
        row = {}
        for k, v in list(source.items())[:20]:
            field(k)
            if type(v) in {str, bool} or v is None or numeric(v) is not None:
                row[k] = v[:500] if isinstance(v, str) else v
        rows.append(row)
    applied = []
    for step in steps:
        op = step["op"]
        if op == "filter":

            def accepted(row):
                value = row.get(step["field"])
                operator = step["operator"]
                if operator == "in":
                    return scalar_text(value) in step["values"]
                if operator in ["gt", "gte", "lt", "lte"]:
                    if numeric(value) is None:
                        return False
                    target = step["numberValue"]
                    return {
                        "gt": value > target,
                        "gte": value >= target,
                        "lt": value < target,
                        "lte": value <= target,
                    }[operator]
                actual, target = scalar_text(value), step["textValue"]
                return (
                    target.lower() in actual.lower()
                    if operator == "contains"
                    else actual == target
                    if operator == "eq"
                    else actual != target
                )

            rows = [r for r in rows if accepted(r)]
        elif op == "select":
            rows = [{k: r[k] for k in step["fields"] if k in r} for r in rows]
        elif op == "derive":
            for row in rows:
                left = (
                    numeric(row.get(step["leftField"]))
                    if "leftField" in step
                    else step["leftValue"]
                )
                right = (
                    numeric(row.get(step["rightField"]))
                    if "rightField" in step
                    else step["rightValue"]
                )
                value = None
                if left is not None and right is not None:
                    operator = step["operator"]
                    value = (
                        left + right
                        if operator == "add"
                        else left - right
                        if operator == "subtract"
                        else left * right
                        if operator == "multiply"
                        else left / right
                        if right
                        else None
                    )
                row[step["as"]] = numeric(value)
        elif op == "group":
            groups = {}
            for row in rows:
                groups.setdefault(
                    canonical([row.get(k) for k in step.get("groupBy", [])]), []
                ).append(row)
            grouped = []
            for key, group in groups.items():
                result = dict(zip(step.get("groupBy", []), json.loads(key)))
                for metric in step["metrics"]:
                    nums = (
                        [
                            r.get(metric["field"])
                            for r in group
                            if numeric(r.get(metric["field"])) is not None
                        ]
                        if "field" in metric
                        else []
                    )
                    agg = metric["aggregate"]
                    value = (
                        (len(nums) if "field" in metric else len(group))
                        if agg == "count"
                        else None
                        if not nums
                        else sum(nums)
                        if agg == "sum"
                        else sum(nums) / len(nums)
                        if agg == "avg"
                        else min(nums)
                        if agg == "min"
                        else max(nums)
                    )
                    result[metric["as"]] = numeric(value)
                grouped.append(result)
            rows = grouped
        elif op == "sort":
            rows = sorted(
                rows,
                key=lambda r: (
                    r.get(step["field"]) is not None,
                    0 if numeric(r.get(step["field"])) is not None else 1,
                    r.get(step["field"])
                    if numeric(r.get(step["field"])) is not None
                    else scalar_text(r.get(step["field"])),
                ),
                reverse=step.get("direction", "desc") == "desc",
            )
        else:
            rows = rows[: step["count"]]
        rows = [dict(list(row.items())[:20]) for row in rows]
        applied.append({"op": op, "rowsAfter": len(rows)})
        passive(rows, 128000)
    return {"rows": rows, "stepsApplied": applied}


def describe():
    return {
        "executionEnvironment": "deterministic_json_ast",
        "networkAccess": "none_during_transform",
        "arbitraryCode": False,
        "datasets": [
            {
                "id": value,
                "title": title,
                "allowedRoles": ["analyst", "operator", "admin"]
                if value == "sales_category"
                else ["viewer", "analyst", "operator", "admin"],
                "query": [
                    "startDate",
                    "endDate",
                    "categories",
                    "channels",
                    "platforms",
                    "productQueries",
                    "sortBy",
                    "direction",
                    "limit",
                ]
                if value == "sales_category"
                else ["startDate", "endDate", "platform", "shop", "query", "limit"],
                "notes": "源查询按真实 principal 数据范围过滤，金额单位为人民币分。",
            }
            for value, title in zip(
                DATASETS, ["销售品类分析明细", "网店商品日表现", "网店推广表现"]
            )
        ],
        "operations": list(OPS),
        "limits": {
            "maximumSourceRows": 50,
            "maximumSteps": 8,
            "maximumOutputRows": 100,
            "maximumOutputColumns": 20,
            "maximumSerializedCharacters": 32000,
        },
    }


def run(body, principal, request_id):
    fields(body, {"dataset", "query", "steps"}, {"dataset"})
    dataset = choice(body["dataset"], DATASETS, "dataset")
    steps = validate_steps(body.get("steps", []))
    query = body.get("query", {})
    allowed = describe()["datasets"][DATASETS.index(dataset)]["query"]
    fields(query, allowed)
    if dataset == "sales_category" and (
        principal.role == "viewer"
        or not query.get("startDate")
        or not query.get("endDate")
    ):
        raise AiError("销售分析需要日期和分析权限", "access_denied", 403)
    if "limit" in query:
        integer(query["limit"], "limit", 1, 50)
    passive(query, 8000)
    loaded = transport.edge(
        "dataset",
        {
            "dataset": dataset,
            "query": {**query, "limit": min(query.get("limit", 50), 50)},
        },
        principal,
    )
    result = transform(loaded["rows"], steps)
    result.update(
        sandbox={
            "executionEnvironment": "deterministic_json_ast",
            "arbitraryCode": False,
            "evalUsed": False,
            "networkAccessDuringTransform": False,
        },
        dataset=dataset,
        filtersApplied=loaded.get("filtersApplied", {}),
        sourceTotal=loaded.get("sourceTotal", len(loaded["rows"])),
        sourceRows=len(loaded["rows"]),
        returned=len(result["rows"]),
        truncated=bool(loaded.get("truncated"))
        or loaded.get("sourceTotal", len(loaded["rows"])) > len(loaded["rows"]),
        dataCutoffDate=loaded.get("dataCutoffDate"),
        columns=list(dict.fromkeys(k for r in result["rows"] for k in r))[:20],
    )
    if len(canonical(result)) > 32000:
        raise AiError(
            "分析结果超过安全字符上限，请增加筛选或 limit",
            "analysis_result_too_large",
            413,
        )
    with mutation(principal):
        row = m.AiAnalysisRuns.objects.create(
            id=uid("ai-analysis"),
            owner_email=principal.email.lower(),
            actor_role=principal.role,
            scope_json=canonical(principal.scope),
            dataset=dataset,
            query_digest=digest(query),
            plan_digest=digest(steps),
            operations_json=canonical([s["op"] for s in steps]),
            data_cutoff_date=loaded.get("dataCutoffDate"),
            source_rows=len(loaded["rows"]),
            returned_rows=result["returned"],
            truncated=int(result["truncated"]),
            result_digest=digest(result),
            request_id=request_id,
        )
    return {**result, "runId": row.id, "resultDigest": row.result_digest}


def history(params, principal):
    fields(params, {"page", "pageSize"})
    return {
        **describe(),
        "history": page(
            owned(m.AiAnalysisRuns.objects.all(), principal).order_by(
                "-created_at", "id"
            ),
            params,
            mapper=lambda r: record(
                r,
                "id dataset operations_json data_cutoff_date source_rows returned_rows truncated result_digest created_at",
                json_fields={"operations_json"},
                bool_fields={"truncated"},
            ),
        ),
    }
