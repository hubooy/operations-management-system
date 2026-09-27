"""Budget scenarios from the fixed evidence of an owner-bound report."""
from datetime import date
import json
from business_analysis import budget
from business_analysis.contracts import AnalysisContractError
from business_analysis.results import stream_table
from . import business_evidence
from .business_sealed import Reader
from .policy import AiError, authorize_owner, digest, fields, integer, passive


def resolve(evidence_id, raw_plan, principal):
    evidence = business_evidence.get_run(evidence_id, principal)
    if evidence.status != "sealed":
        raise AiError("预算测算须使用已封存证据", "conflict", 409)
    try:
        plan = budget.normalize(raw_plan)
        reader = Reader(evidence, principal)
        sources = {s["key"]: s for s in reader.sources}
        groups, scopes, dates = {}, {}, set()
        for position, target in enumerate(plan["targets"]):
            source = sources.get(target["sourceKey"])
            if not source or source["domain"] != "netshop" or source["query"].get("dataset") != "promotion" or source["query"].get("window", "current") != "current":
                raise AiError("预算基数须为本期推广来源，不能用销售或市场样本替代", "conflict", 409)
            query = source["query"]
            scope = (query["platform"], query["shop"])
            group = (source["key"], target["dimension"])
            if scope in scopes and scopes[scope] != group:
                raise AiError("同一店铺预算不能混用重叠来源或不同聚合维度", "conflict", 409)
            scopes[scope] = group
            dates.add((query["startDate"], query["endDate"]))
            groups.setdefault(group, []).append((position, target))
        if len(dates) != 1:
            raise AiError("预算对象须使用相同本期区间", "conflict", 409)
        baselines = [None]*len(plan["targets"])
        for (key, dimension), targets in groups.items():
            expected = reader.info(key)["expected"]
            selected = {target["rowIndex"]: (position, target) for position, target in targets}
            if len(selected) != len(targets):
                raise AiError("预算对象行位置重复", "conflict", 409)
            with stream_table(reader.pages(key), dimension, expected) as (table, rows):
                header = table["sourceMetadata"]
                for row in rows:
                    if row["rowIndex"] not in selected:
                        continue
                    position, target = selected[row["rowIndex"]]
                    if row["id"] != target["rowId"] or row["dimensionMissing"]:
                        raise AiError("预算对象身份缺失或引用已变化", "conflict", 409)
                    start, end = next(iter(dates))
                    baselines[position] = {"rowId": row["id"], "entity": row["entity"], "days": (date.fromisoformat(end)-date.fromisoformat(start)).days+1,
                        "datesPresent": (header.get("coverage") or {}).get("status") == "dates_present",
                        "source": header["source"], "metricSemantics": header.get("metricSemantics"),
                        "metrics": {key: value["value"] if value and not value["missingRows"] else None for key, value in row["metrics"].items() if key in budget.METRICS}}
        if any(row is None for row in baselines):
            raise AiError("预算对象引用不在完整分析表中", "conflict", 409)
        result = budget.calculate(plan, baselines)
    except AnalysisContractError as error:
        raise AiError(str(error), "invalid_request", 400) from error
    return {**result, "evidenceRunId": evidence.id, "evidenceVersion": evidence.version, "evidencePlanDigest": digest(evidence.plan_json)}


def for_report(report, principal):
    authorize_owner(report, principal)
    snapshot = json.loads(report.snapshot_json)
    if "budgetRef" in snapshot or getattr(report, "budget_plan_id", None) or snapshot.get("executionProfile") == "business-agent-budget-reference-v1":
        from .business_budget_store import resolve_fixed
        return resolve_fixed(report, principal)
    if snapshot.get("budgetPlan") is None:
        return None
    result = resolve(snapshot["evidenceRunId"], snapshot["budgetPlan"], principal)
    if (result["evidenceVersion"], result["evidencePlanDigest"], result["planDigest"]) != (snapshot["evidenceVersion"], snapshot["evidencePlanDigest"], snapshot["budgetPlanDigest"]):
        raise AiError("预算与报告固定版本不一致", "conflict", 409)
    return result


def read(report_id, params, principal):
    from .reports import get
    fields(params, {"runId", "offset", "limit"}, {"runId"})
    report = get(report_id, principal)
    snapshot = json.loads(report.snapshot_json)
    if params["runId"] != snapshot.get("evidenceRunId"):
        raise AiError("预算证据身份不一致", "access_denied", 403)
    try:
        offset, limit = int(params.get("offset", 0)), int(params.get("limit", 10))
        integer(offset, "offset", lo=0, hi=100)
        integer(limit, "limit", hi=20)
    except (TypeError, ValueError) as error:
        raise AiError("预算分页无效") from error
    result = for_report(report, principal)
    if result is None:
        raise AiError("该报告没有固定预算参数", "not_found", 404)
    scenarios, rows = result["scenarios"], []
    outcomes = {"scenario", "projectedClicks", "projectedOrderLines", "projectedAttributedGmvCents", "projectedRoas", "assumedContributionAfterAdCents"}
    for index in range(offset, min(offset+limit, result["allocation"]["targetCount"])):
        item = scenarios[0]["rows"][index]
        rows.append({"rowIndex": index, **{k: v for k, v in item.items() if k not in outcomes},
            "outcomes": [{k: v for k, v in scenario["rows"][index].items() if k in outcomes} for scenario in scenarios]})
    total = result["allocation"]["targetCount"]
    return passive({"schemaVersion": result["schemaVersion"], "reportId": report.id, "evidenceRunId": result["evidenceRunId"],
        "planDigest": result["planDigest"], "allocation": result["allocation"], "scenarios": [{"assumptions": s["assumptions"], "summary": s["summary"]} for s in scenarios],
        "rows": rows, "pagination": {"offset": offset, "limit": limit, "total": total, "hasMore": offset+len(rows) < total,
            "nextOffset": offset+len(rows) if offset+len(rows) < total else None}, "limitations": result["limitations"]}, 150000)


def preview(report_id, payload, principal):
    from .reports import get
    fields(payload, {"budgetPlan"}, {"budgetPlan"})
    report = get(report_id, principal)
    snapshot = json.loads(report.snapshot_json)
    if "budgetRef" in snapshot or getattr(report, "budget_plan_id", None) or snapshot.get("executionProfile") == "business-agent-budget-reference-v1":
        from .business_budget_store import load
        fixed = load(report, principal)
        result = resolve(fixed.binding["evidenceRunId"], payload["budgetPlan"], principal)
        if (result["evidenceVersion"], result["evidencePlanDigest"]) != (fixed.binding["evidenceVersion"], fixed.binding["evidencePlanDigest"]):
            raise AiError("试算来源与原报告版本不一致", "conflict", 409)
        return passive({"previewOnly": True, "reportId": report.id, "originalPlanDigest": fixed.binding["planDigest"], "budget": result}, 1024*1024)
    if snapshot.get("budgetPlan") is None:
        raise AiError("该报告没有预算分析配置", "not_found", 404)
    result = resolve(snapshot["evidenceRunId"], payload["budgetPlan"], principal)
    if result["evidenceVersion"] != snapshot["evidenceVersion"] or result["evidencePlanDigest"] != snapshot["evidencePlanDigest"] or digest(snapshot["budgetPlan"]) != snapshot["budgetPlanDigest"]:
        raise AiError("试算来源与原报告版本不一致", "conflict", 409)
    return passive({"previewOnly": True, "reportId": report.id, "originalPlanDigest": snapshot["budgetPlanDigest"], "budget": result}, 1024*1024)
