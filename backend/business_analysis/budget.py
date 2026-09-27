"""Deterministic constrained allocation and explicitly hypothetical ad scenarios.

Inputs contain verified aggregate facts plus user planning assumptions. This is
not a causal forecast or an optimizer of incremental profit. Money uses integer
cents; all intermediate arithmetic uses exact fractions and half-up rounding.
"""
from fractions import Fraction
import re
from .contracts import AnalysisContractError, canonical, digest

MAX_MONEY = 10**12
DIMENSIONS = {"shop", "category", "spu", "sku", "keyword", "searchTerm"}
METRICS = ("spendCents", "clicks", "reportedOrderLines", "reportedGmvCents")


def fields(value, allowed, required=None):
    if not isinstance(value, dict) or set(value)-set(allowed) or not set(required or allowed) <= set(value):
        raise AnalysisContractError("预算参数字段无效或缺失")


def integer(value, lo, hi):
    if type(value) is not int or not lo <= value <= hi:
        raise AnalysisContractError("预算参数必须为范围内整数")
    return value


def text(value, size=100):
    if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > size or any(ord(c) < 32 for c in value):
        raise AnalysisContractError("预算说明或身份无效")
    return value


def normalize(value):
    fields(value, {"totalBudgetCents", "reserveCents", "horizonDays", "observationDays", "reviewAfterSpendBps", "minimumClicks", "minimumOrderLines", "targets", "scenarios"})
    total = integer(value["totalBudgetCents"], 1, MAX_MONEY)
    integer(value["reserveCents"], 0, total)
    horizon = integer(value["horizonDays"], 1, 93)
    integer(value["observationDays"], 1, horizon)
    integer(value["reviewAfterSpendBps"], 1, 10000)
    integer(value["minimumClicks"], 1, 1000000)
    integer(value["minimumOrderLines"], 1, 1000000)
    if not isinstance(value["targets"], list) or not 1 <= len(value["targets"]) <= 100:
        raise AnalysisContractError("预算对象数量须为 1—100")
    if not isinstance(value["scenarios"], list) or not 1 <= len(value["scenarios"]) <= 5:
        raise AnalysisContractError("预算情景数量须为 1—5")
    seen = set()
    for target in value["targets"]:
        fields(target, {"sourceKey", "dimension", "rowIndex", "rowId", "weight", "minBudgetCents", "maxBudgetCents", "ownerRole", "minimumRoasBps"})
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", text(target["sourceKey"], 160)) or not isinstance(target["dimension"], str) or target["dimension"] not in DIMENSIONS or not re.fullmatch(r"[a-f0-9]{64}", text(target["rowId"])):
            raise AnalysisContractError("预算对象引用无效")
        integer(target["rowIndex"], 0, 249999)
        integer(target["weight"], 1, 10000)
        integer(target["maxBudgetCents"], 0, MAX_MONEY)
        integer(target["minBudgetCents"], 0, target["maxBudgetCents"])
        integer(target["minimumRoasBps"], 0, 1000000)
        text(target["ownerRole"], 80)
        identity = (target["sourceKey"], target["dimension"], target["rowId"])
        if identity in seen:
            raise AnalysisContractError("预算对象重复")
        seen.add(identity)
    names = set()
    for scenario in value["scenarios"]:
        fields(scenario, {"name", "cpcFactorBps", "orderRateFactorBps", "orderValueFactorBps", "contributionMarginBps"})
        name = text(scenario["name"], 80)
        if name in names:
            raise AnalysisContractError("情景名称重复")
        names.add(name)
        for key in ("cpcFactorBps", "orderRateFactorBps", "orderValueFactorBps"):
            integer(scenario[key], 1000, 30000)
        if scenario["contributionMarginBps"] is not None:
            integer(scenario["contributionMarginBps"], 0, 10000)
    if len(canonical(value).encode()) > 48000:
        raise AnalysisContractError("预算参数超过容量")
    # Copy so normalization never aliases caller-owned mutable input.
    import json
    return json.loads(canonical(value))


def rounded(value, places=0):
    scaled = value * 10**places
    sign = -1 if scaled < 0 else 1
    scaled = abs(scaled)
    result = sign * ((scaled.numerator * 2 + scaled.denominator) // (2 * scaled.denominator))
    return result if not places else result / 10**places


def allocate(plan):
    """Capped weighted water filling; exact cents and stable largest remainders."""
    available = plan["totalBudgetCents"] - plan["reserveCents"]
    targets = plan["targets"]
    amounts = [t["minBudgetCents"] for t in targets]
    if sum(amounts) > available:
        raise AnalysisContractError("对象最低预算合计超过扣除预留后的可用预算")
    remaining = min(available, sum(t["maxBudgetCents"] for t in targets)) - sum(amounts)
    active = {i for i, t in enumerate(targets) if amounts[i] < t["maxBudgetCents"]}
    while remaining and active:
        weight = sum(targets[i]["weight"] for i in active)
        quotas = {i: Fraction(remaining*targets[i]["weight"], weight) for i in active}
        capped = {i for i in active if quotas[i] >= targets[i]["maxBudgetCents"]-amounts[i]}
        if capped:
            for i in capped:
                addition = targets[i]["maxBudgetCents"]-amounts[i]
                amounts[i] += addition
                remaining -= addition
            active -= capped
            continue
        for i in active:
            addition = quotas[i].numerator // quotas[i].denominator
            amounts[i] += addition
            remaining -= addition
        ordered = sorted(active, key=lambda i: (-(quotas[i] % 1), targets[i]["sourceKey"], targets[i]["dimension"], targets[i]["rowId"]))
        for i in ordered[:remaining]:
            amounts[i] += 1
        remaining = 0
    if any(not t["minBudgetCents"] <= a <= t["maxBudgetCents"] for t, a in zip(targets, amounts)) or sum(amounts) > available:
        raise AnalysisContractError("预算分配核对失败")
    return amounts


def calculate(raw_plan, baselines):
    plan = normalize(raw_plan)
    if len(baselines) != len(plan["targets"]):
        raise AnalysisContractError("预算证据对象数量不一致")
    amounts = allocate(plan)
    prepared = []
    for target, baseline, amount in zip(plan["targets"], baselines, amounts):
        if baseline["rowId"] != target["rowId"]:
            raise AnalysisContractError("预算证据行身份变化")
        days = integer(baseline["days"], 1, 93)
        missing = [key for key in METRICS if type(baseline["metrics"].get(key)) is not int]
        for metric in METRICS:
            metric_value = baseline["metrics"].get(metric)
            if metric not in missing and abs(metric_value) >= 10**15:
                raise AnalysisContractError("预算基数超过精确文件数值容量")
        errors = (["date_coverage_incomplete"] if not baseline["datesPresent"] else []) + (["missing_metrics"] if missing else [])
        if not missing and (baseline["metrics"]["spendCents"] <= 0 or baseline["metrics"]["clicks"] <= 0 or baseline["metrics"]["reportedOrderLines"] <= 0 or baseline["metrics"]["reportedGmvCents"] < 0):
            errors.append("nonpositive_or_negative_baseline")
        low_sample = not missing and (baseline["metrics"]["clicks"] < plan["minimumClicks"] or baseline["metrics"]["reportedOrderLines"] < plan["minimumOrderLines"])
        spend = baseline["metrics"].get("spendCents")
        normalized_spend = rounded(Fraction(spend*plan["horizonDays"], days)) if type(spend) is int and spend >= 0 and baseline["datesPresent"] else None
        prepared.append({"sourceKey": target["sourceKey"], "dimension": target["dimension"], "rowId": target["rowId"], "entity": baseline["entity"],
            "baseline": baseline, "budgetCents": amount, "equivalentBaselineSpendCents": normalized_spend,
            "budgetChangeCents": amount-normalized_spend if normalized_spend is not None else None,
            "status": "unavailable" if errors else "low_sample_scenario" if low_sample else "assumption_scenario", "missingMetrics": missing,
            "limitations": errors + (["sample_threshold_is_planning_rule_not_statistical_confidence"] if low_sample else []),
            "ownerRole": target["ownerRole"], "observationDays": plan["observationDays"],
            "reviewAfterSpendCents": rounded(Fraction(amount*plan["reviewAfterSpendBps"], 10000)),
            "minimumRoasBps": target["minimumRoasBps"],
            "rollbackRule": "到观察期或复盘花费先到者复核；只有数据覆盖与归因窗口可比时，低于最低归因产出比才触发人工暂停/回退评估；缺失先核查，不自动停投。"})
    scenarios = []
    for assumption in plan["scenarios"]:
        rows = []
        for prepared_row in prepared:
            clicks = orders = gmv = contribution = roas = None
            if prepared_row["status"] != "unavailable":
                facts, budget = prepared_row["baseline"]["metrics"], prepared_row["budgetCents"]
                clicks_exact = Fraction(budget*facts["clicks"]*10000, facts["spendCents"]*assumption["cpcFactorBps"])
                orders_exact = clicks_exact * Fraction(facts["reportedOrderLines"]*assumption["orderRateFactorBps"], facts["clicks"]*10000)
                gmv_exact = orders_exact * Fraction(facts["reportedGmvCents"]*assumption["orderValueFactorBps"], facts["reportedOrderLines"]*10000)
                clicks, orders, gmv = rounded(clicks_exact, 4), rounded(orders_exact, 4), rounded(gmv_exact)
                if abs(gmv) >= 10**15:
                    raise AnalysisContractError("预算情景金额超过精确文件数值容量")
                roas = rounded(Fraction(gmv, budget), 6) if budget else None
                if assumption["contributionMarginBps"] is not None:
                    contribution = rounded(Fraction(gmv*assumption["contributionMarginBps"], 10000))-budget
            rows.append({**prepared_row, "scenario": assumption["name"], "projectedClicks": clicks, "projectedOrderLines": orders,
                "projectedAttributedGmvCents": gmv, "projectedRoas": roas, "assumedContributionAfterAdCents": contribution})
        missing_rows = sum(row["projectedAttributedGmvCents"] is None for row in rows)
        known_gmv = sum(row["projectedAttributedGmvCents"] or 0 for row in rows)
        known_contribution = sum(row["assumedContributionAfterAdCents"] or 0 for row in rows)
        bases = sorted({row["baseline"].get("source", "unspecified") for row in rows})
        by_basis = [{"source": basis, "knownAttributedGmvCents": sum(row["projectedAttributedGmvCents"] or 0 for row in rows if row["baseline"].get("source", "unspecified") == basis),
            "unavailableTargets": sum(row["projectedAttributedGmvCents"] is None for row in rows if row["baseline"].get("source", "unspecified") == basis)} for basis in bases]
        scenarios.append({"assumptions": assumption, "rows": rows, "summary": {"knownAttributedGmvCents": known_gmv,
            "projectedAttributedGmvCents": known_gmv if not missing_rows and len(bases) == 1 else None, "unavailableTargets": missing_rows,
            "mixedReportingBases": len(bases) != 1, "byReportingBasis": by_basis,
            "assumedContributionAfterAdCents": known_contribution if len(bases) == 1 and all(row["assumedContributionAfterAdCents"] is not None for row in rows) else None,
            "breakEvenRoas": rounded(Fraction(10000, assumption["contributionMarginBps"]), 6) if assumption["contributionMarginBps"] else None}})
        if len(bases) != 1:
            scenarios[-1]["summary"]["knownAttributedGmvCents"] = None
    return {"schemaVersion": "business-budget-v1", "plan": plan, "planDigest": digest(plan),
        "allocation": {"totalBudgetCents": plan["totalBudgetCents"], "reservedCents": plan["reserveCents"], "allocatedCents": sum(amounts),
            "unallocatedCents": plan["totalBudgetCents"]-plan["reserveCents"]-sum(amounts), "targetCount": len(amounts), "scope": "selected_targets_only"},
        "scenarios": scenarios, "limitations": ["仅对选择对象分配；权重和边界来自输入，不代表最优预算", "成本、订单率及客单乘数是规划假设，10000表示不变", "历史归因效率不证明增量效果；不保证放量后线性增长", "归因金额不是ERP净销售或真实利润；贡献率为输入假设", "样本门槛为人工规划规则，不是统计置信度", "结果四舍五入；不自动执行投放或业务调整"]}
