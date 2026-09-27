"""Unregistered Table projection of one checked diagnostic review plan.

This does not authorize an Agent, renderer, download, bid, price or spend.
The caller must pass the very composition it rechecked for the 13-table
preview; the paired writer then consumes this Table for both output formats.
"""
from __future__ import annotations

from . import diagnostic_action_plan_v1 as contract
from .contracts import canonical
from .report_files import Column, Table


KEY = "diagnostic_action_plan"
COLUMNS = (
    Column("actionId", "动作ID"),
    Column("reportRoot", "同报告根JSON"),
    Column("phase", "计划阶段"),
    Column("dimension", "分析维度"),
    Column("targetIdentityStatus", "目标身份状态"),
    Column("direction", "建议方向"),
    Column("factBasis", "事实依据等级"),
    Column("primaryTableKey", "主报告证据表"),
    Column("primarySourceDigest", "主证据来源摘要"),
    Column("primarySourceStatus", "主证据来源状态"),
    Column("externalContextRefs", "外部背景引用JSON"),
    Column("ownerRole", "责任角色"),
    Column("budgetPrinciple", "预算原则"),
    Column("budgetCapCents", "预算上限（分）", "integer"),
    Column("budgetCapSource", "预算上限来源"),
    Column("budgetCapStatus", "预算上限状态"),
    Column("kpiMetric", "KPI指标定义"),
    Column("kpiSourceTableKey", "KPI来源表"),
    Column("kpiBaselineValue", "KPI基线", "integer"),
    Column("kpiTargetValue", "KPI目标", "integer"),
    Column("kpiVerificationStatus", "KPI核验状态"),
    Column("observationDays", "观察天数", "integer"),
    Column("observationStartGate", "观察期启动条件"),
    Column("stopConditions", "停止条件JSON"),
    Column("rollback", "回退方式"),
    Column("executionAllowed", "自动执行许可"),
    Column("automaticBidAllowed", "自动投放许可"),
    Column("automaticPriceChangeAllowed", "自动调价许可"),
)


def project(candidate, report, *, verify_current_composition,
            market_result=None, verify_market_result=None):
    checked = contract.check_candidate(candidate, report,
        verify_current_composition=verify_current_composition,
        market_result=market_result, verify_market_result=verify_market_result)
    rows = []
    for action in checked["actions"]:
        primary = action["primaryEvidence"]
        budget = action["budget"]
        kpi = action["kpi"]
        observation = action["observation"]
        rows.append((action["id"], canonical(checked["reportRoot"]),
            action["phase"], action["dimension"],
            action["targetIdentityStatus"], action["direction"],
            action["factBasis"], primary["tableKey"],
            primary["sourceDigest"], primary["status"],
            canonical(action["contextEvidence"]), action["ownerRole"],
            budget["principle"], budget["capCents"], budget["capSource"],
            "known_zero" if budget["capCents"] == 0 else "pending_approved_cap",
            kpi["metric"], kpi["sourceTableKey"], kpi["baselineValue"],
            kpi["targetValue"], kpi["verificationStatus"],
            observation["days"], observation["startGate"],
            canonical(action["stopConditions"]), action["rollback"],
            action["executionAllowed"], action["automaticBidAllowed"],
            action["automaticPriceChangeAllowed"]))
    return Table(KEY, "深度诊断调整计划（人工复核）",
        "表级证据支持复核方向，不证明具体商品数值或执行许可；缺源、未知预算与KPI基线保留。",
        COLUMNS, tuple(rows), len(rows))
