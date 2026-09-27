"""Pure renderer-10 budget candidate over already verified owning inputs.

This module cannot authenticate a database row or approve a report. Its caller
must freshly obtain the approved DTO, renderer-9 proof, report binding and, when
present, the fixed budget from the owning services. It only checks that those
inputs reconcile and projects immutable source tables plus an unreviewed local
calculator. It never changes the renderer-9 file contract.
"""
from dataclasses import dataclass
import hashlib

from . import budget, budget_excel, budget_offline, budget_reference, promotion_trial_table_schema
from .contracts import AnalysisContractError, canonical, digest
from .report_files import Column, Table


SCHEMA = "business-promotion-budget-renderer10-candidate-v1"
TRIAL_KEYS = frozenset({"schemaVersion", "rendererVersion", "reportId",
    "contentDtoDigest", "humanReviewDigest", "promotionFileProofDigest",
    "sealedSourcesDigest", "sourceDescriptorDigest", "tableSchemaDigest",
    "actionTableKey", "actionRowCount", "actionRowDigest", "scopeTableKeys",
    "promotionTableKeys", "budgetDelivered", "proofDigest"})


@dataclass(frozen=True, slots=True)
class Candidate:
    tables: tuple[Table, ...]
    offline_budget: dict | None
    excel_budget: dict | None
    proof: dict


def _fail(message):
    raise AnalysisContractError(message)


def _sha(value):
    budget_reference._sha(value)
    return value


def _copy(value, maximum=64 * 1024):
    return budget_reference._copy(value, maximum)


def _roots(trial_proof, approved_binding, report_binding, approved_dto_digest):
    trial = _copy(trial_proof)
    approved = _copy(approved_binding)
    report = _copy(report_binding)
    if type(trial) is not dict or set(trial) != TRIAL_KEYS or (
            trial["schemaVersion"] != "business-promotion-trial-file-proof-v2"
            or trial["rendererVersion"] != 9 or trial["budgetDelivered"] is not False):
        _fail("上版推广试用证明无效")
    for key in ("contentDtoDigest", "humanReviewDigest", "promotionFileProofDigest",
            "sealedSourcesDigest", "sourceDescriptorDigest", "tableSchemaDigest",
            "actionRowDigest", "proofDigest"):
        _sha(trial[key])
    if trial["proofDigest"] != digest({key: value for key, value in trial.items()
            if key != "proofDigest"}):
        _fail("上版推广试用证明摘要不一致")
    if type(approved) is not dict or type(report) is not dict:
        _fail("已批准报告或来源绑定无效")
    review = approved.get("humanReview")
    if (type(review) is not dict or set(review) != {"status", "reviewDigest"}
            or review["status"] != "approved" or
            review["reviewDigest"] != trial["humanReviewDigest"] or
            _sha(approved_dto_digest) != trial["contentDtoDigest"]):
        _fail("预算候选缺少同一已批准内容")
    for key in ("reportId", "evidenceRunId", "evidenceVersion", "sealedDigest",
            "ownerEmail", "scope"):
        if key not in approved or key not in report or type(approved[key]) is not type(report[key]) or approved[key] != report[key]:
            _fail("预算候选与报告来源或账号不一致")
    if (trial["reportId"] != approved["reportId"] or report.get("role") != "admin"
            or report["scope"] is not None):
        _fail("预算候选报告身份无效")
    _sha(approved["sealedDigest"])
    return trial, approved, report


def _table_rows_digest(tables):
    # The row count is bounded by the fixed budget's <=100 targets and <=5
    # scenarios. The gap case contains only one row.
    return digest([{"key": table.key, "rows": [list(row) for row in table.rows]}
        for table in tables])


def _row_sha256(table):
    sha = hashlib.sha256()
    for row in table.rows:
        sha.update((canonical(list(row)) + "\n").encode("utf-8"))
    return sha.hexdigest()


def _gap():
    return Table("promotion-budget-gap-v1", "预算能力缺口",
        "本报告没有固定预算参数；批准正文的预算文字不是可执行预算或试算初值。",
        (Column("status", "状态"), Column("reason", "原因")),
        [["未交付", "没有同报告已批准且与封存证据绑定的固定预算输入；不生成金额、分配或可编辑公式。"]], 1)


def _tables(result, report_id):
    plan, first = result["plan"], result["scenarios"][0]["rows"]
    allocation_rows = []
    for target, row in zip(plan["targets"], first):
        facts = row["baseline"]["metrics"]
        allocation_rows.append([target["sourceKey"], target["dimension"],
            target["rowIndex"], target["rowId"], canonical(row["entity"]),
            target["ownerRole"], row["baseline"]["days"],
            row["baseline"]["datesPresent"], facts["spendCents"],
            facts["clicks"], facts["reportedOrderLines"],
            facts["reportedGmvCents"], target["weight"],
            target["minBudgetCents"], target["maxBudgetCents"],
            row["budgetCents"], row["equivalentBaselineSpendCents"],
            row["budgetChangeCents"], target["minimumRoasBps"],
            row["observationDays"], row["reviewAfterSpendCents"],
            row["status"], row["baseline"].get("source", "unspecified")])
    allocation = Table("promotion-budget-allocation-v1", "推广预算对象与分配初值",
        "分为金额单位；仅已选对象。基期事实与输入预算分开，缺日/缺指标不是零；初值是未复核的规划假设。",
        (Column("sourceKey", "来源键"), Column("dimension", "对象层级"),
         Column("rowIndex", "来源行序", "integer"), Column("rowId", "来源行ID"),
         Column("entity", "对象身份JSON"), Column("ownerRole", "责任角色"),
         Column("baselineDays", "基期天数", "integer"),
         Column("datesPresent", "日期完整"),
         Column("baselineSpendCents", "基期推广费分", "integer"),
         Column("baselineClicks", "基期点击", "integer"),
         Column("baselineOrderLines", "基期订单口径", "integer"),
         Column("baselineGmvCents", "基期归因金额分", "integer"),
         Column("weight", "输入权重", "integer"),
         Column("minBudgetCents", "最低预算分", "integer"),
         Column("maxBudgetCents", "最高预算分", "integer"),
         Column("allocatedBudgetCents", "分配初值分", "integer"),
         Column("normalizedBaselineSpendCents", "等天数基期推广费分", "integer"),
         Column("changeCents", "较基期差额分", "integer"),
         Column("minimumRoasBps", "最低归因产出比基点", "integer"),
         Column("observationDays", "观察天数", "integer"),
         Column("reviewAfterSpendCents", "提前复盘花费分", "integer"),
         Column("status", "测算状态"),
         Column("reportingBasis", "基期平台归因口径")),
        allocation_rows, len(allocation_rows))
    scenario_rows = []
    for scenario in result["scenarios"]:
        assumption = scenario["assumptions"]
        for row in scenario["rows"]:
            scenario_rows.append([row["sourceKey"], row["dimension"],
                row["rowId"], assumption["name"],
                assumption["cpcFactorBps"], assumption["orderRateFactorBps"],
                assumption["orderValueFactorBps"],
                assumption["contributionMarginBps"], row["budgetCents"],
                row["projectedClicks"], row["projectedOrderLines"],
                row["projectedAttributedGmvCents"], row["projectedRoas"],
                row["assumedContributionAfterAdCents"], row["status"],
                row["rollbackRule"], row["baseline"].get("source", "unspecified")])
    scenarios = Table("promotion-budget-scenarios-v1", "推广预算假设情景",
        "归因金额不是ERP净销售、利润或增量效果；贡献率为输入假设。低样本与不可测算分别保留。",
        (Column("sourceKey", "来源键"), Column("dimension", "对象层级"),
         Column("rowId", "来源行ID"), Column("scenario", "情景"),
         Column("cpcFactorBps", "点击成本乘数基点", "integer"),
         Column("orderRateFactorBps", "订单效率乘数基点", "integer"),
         Column("orderValueFactorBps", "订单金额乘数基点", "integer"),
         Column("contributionMarginBps", "假设贡献率基点", "integer"),
         Column("budgetCents", "预算分", "integer"),
         Column("projectedClicks", "情景点击", "decimal"),
         Column("projectedOrderLines", "情景订单口径", "decimal"),
         Column("projectedAttributedGmvCents", "情景归因金额分", "integer"),
         Column("projectedRoas", "情景归因产出比", "decimal"),
         Column("assumedContributionAfterAdCents", "假设贡献扣推广分", "integer"),
         Column("status", "状态"), Column("rollbackRule", "人工复盘与回退条件"),
         Column("reportingBasis", "基期平台归因口径")),
        scenario_rows, len(scenario_rows))
    summaries = []
    for scenario in result["scenarios"]:
        summary = scenario["summary"]
        summaries.append([scenario["assumptions"]["name"],
            summary["mixedReportingBases"], summary["unavailableTargets"],
            summary["knownAttributedGmvCents"],
            summary["projectedAttributedGmvCents"],
            summary["assumedContributionAfterAdCents"],
            summary["breakEvenRoas"], canonical(summary["byReportingBasis"])])
    summary_table = Table("promotion-budget-scenario-summary-v1", "推广预算情景汇总与口径",
        "只有同一平台归因口径且无缺值时才给完整情景合计；跨平台、缺指标和贡献率未知均不补零。",
        (Column("scenario", "情景"), Column("mixedReportingBases", "混合归因口径"),
         Column("unavailableTargets", "不可测算对象", "integer"),
         Column("knownAttributedGmvCents", "已知归因金额分", "integer"),
         Column("projectedAttributedGmvCents", "完整情景归因金额分", "integer"),
         Column("assumedContributionAfterAdCents", "假设贡献扣推广分", "integer"),
         Column("breakEvenRoas", "假设盈亏平衡产出比", "decimal"),
         Column("byReportingBasis", "各归因口径JSON")), summaries, len(summaries))
    return allocation, scenarios, summary_table


def project(*, trial_proof, approved_binding, report_binding,
            approved_dto_digest, budget_material=None):
    """Build a source-bound renderer-10 *candidate*, never a ready file proof.

    budget_material must be obtained through the owning `_roots` path and have
    exactly binding/reference/result. A missing budget is a visible gap only
    when the approved report itself has no budget reference.
    """
    trial, approved, report = _roots(trial_proof, approved_binding,
        report_binding, approved_dto_digest)
    plan_digest = approved.get("budgetPlanDigest")
    if plan_digest is None:
        if budget_material is not None or report.get("budgetRef") is not None:
            _fail("无预算报告不能借用其他预算材料")
        tables, offline = (_gap(),), None
        status, binding_digest = "missing_fixed_budget", None
        result_digest, reference_digest = None, None
        native_sheets = 0
    else:
        _sha(plan_digest)
        if budget_material is None:
            _fail("固定预算存在但未取得拥有方重算证明")
        material = _copy(budget_material, 2 * 1024 * 1024)
        if type(material) is not dict or set(material) != {"binding", "reference", "result"}:
            _fail("固定预算材料字段无效")
        try:
            binding = budget_reference.validate_binding(material["binding"])
            result, reference = material["result"], material["reference"]
            checked = budget_reference.validate_record(result["plan"], binding,
                reference, expected_binding=binding)
            if (binding["reportId"] != report["reportId"] or
                    binding["ownerEmail"] != report["ownerEmail"] or
                    binding["evidenceRunId"] != report["evidenceRunId"] or
                    binding["evidenceVersion"] != report["evidenceVersion"] or
                    binding["sealedDigest"] != report["sealedDigest"] or
                    binding["planDigest"] != plan_digest or
                    checked["reference"] != report.get("budgetRef") or
                    any(report.get(key) != binding[key] for key in
                        ("evidencePlanDigest", "catalogDigest") if key in report)):
                _fail("固定预算与已批准报告或封存来源不一致")
            baselines = [row["baseline"] for row in result["scenarios"][0]["rows"]]
            if any(type(base["datesPresent"]) is not bool for base in baselines):
                _fail("预算基期日期覆盖类型无效")
            expected = {**budget.calculate(checked["plan"], baselines),
                "evidenceRunId": binding["evidenceRunId"],
                "evidenceVersion": binding["evidenceVersion"],
                "evidencePlanDigest": binding["evidencePlanDigest"]}
            if canonical(result) != canonical(expected):
                _fail("固定预算结果与来源基数重算不一致")
            offline = budget_offline.payload(result, report["reportId"])
            offline["excelEnabled"] = True
            # Native model construction validates the editable initial values
            # before declaring the three-sheet reservation. Actual workbook
            # bytes and Office recalculation still belong to the file stage.
            _, native_proof = budget_excel.build(offline,
                budget_excel.TITLES, formula_version=2)
            tables = _tables(result, report["reportId"])
            status, binding_digest = "reconciled_fixed_budget_candidate", digest(binding)
            result_digest, reference_digest = digest(result), digest(reference)
            native_model_digest = digest(native_proof)
        except (KeyError, IndexError, TypeError, ValueError, AttributeError,
                OverflowError, UnicodeError, RecursionError) as error:
            raise AnalysisContractError("固定预算结果结构无效") from error
        native_sheets = 3
    if plan_digest is None:
        native_model_digest = None
    proof = {"schemaVersion": SCHEMA, "rendererVersion": 10,
        "reportId": report["reportId"], "status": status,
        "promotionTrialProofDigest": trial["proofDigest"],
        "approvedContentDigest": trial["contentDtoDigest"],
        "humanReviewDigest": trial["humanReviewDigest"],
        "sealedDigest": report["sealedDigest"],
        "budgetBindingDigest": binding_digest,
        "budgetReferenceDigest": reference_digest,
        "budgetResultDigest": result_digest,
        "budgetPlanDigest": plan_digest,
        "offlinePayloadDigest": digest(offline) if offline is not None else None,
        "nativeModelProofDigest": native_model_digest,
        "excelFormulaVersion": 2 if offline is not None else None,
        "tableSchemaDigest": promotion_trial_table_schema.digest_tables(tables),
        "tableRowsDigest": _table_rows_digest(tables),
        "tableKeys": [table.key for table in tables],
        "tableRowCounts": [table.row_count for table in tables],
        "tableRowDigests": [_row_sha256(table) for table in tables],
        "nativeBudgetSheets": native_sheets,
        "offlineBudgetEnabled": offline is not None,
        "editableAllocation": offline is not None,
        "candidateOnly": True}
    proof["proofDigest"] = digest(proof)
    return Candidate(tables, offline, offline, proof)
