"""Unregistered diagnosis of a promotion-profile Agent's fixed references.

Shape and numeric verification are separate from complete Agent reading,
independent review approval, causal interpretation, and human review.
"""
from copy import deepcopy
import json
import math

from business_analysis import screening_package
from . import business_promotion_claims as promotion_claims
from . import business_promotion_runtime as runtime
from . import business_promotion_runtime_contract as contract
from . import business_promotion_tools as tools
from . import business_screening_claims as candidate_claims
from . import business_screening_diagnosis as old
from . import business_screening_packages as packages
from .policy import AiError, canonical

SCHEMA = "business-promotion-diagnosis-v1"
MAX_RESULT_BYTES = 2 * 1024 * 1024
_MASK = {"candidateId":"0"*64, "metric":"spendCents", "field":"value"}


def _reject(message="词货诊断引用或结论结构无效"):
    raise AiError(message, "conflict", 409)


def validate_reference(role, reference):
    """Pure role policy. Review answers themselves still have no references."""
    if type(role) is not str or role not in contract.PROMOTION_ROLES:
        _reject("该角色不能输出词货数值引用")
    if type(reference) is not dict or reference.get("kind") != "promotion_keyword_sku":
        _reject("词货引用必须有独立类型")
    return deepcopy(promotion_claims._reference(reference))


def _diagnosis(value, role):
    if role == "independent_review":
        return None
    if role == "report":
        return value.get("diagnosis") if type(value) is dict else None
    return value


def validate_answer(role, answer):
    """Pure v1 five-role shape plus explicitly typed promotion references."""
    if type(role) is not str or role not in screening_package.ROLES:
        _reject("诊断角色无效")
    if type(answer) is not str:
        _reject("Agent 输出必须为 JSON 文本")
    try:
        if len(answer.encode("utf-8")) > old.contract.OUTPUT_LIMITS[role]:
            raise AiError("专业分析超出固定输出容量", "payload_too_large", 413)
        value = json.loads(answer, object_pairs_hook=old._unique,
            parse_constant=lambda _: _reject("非有限 JSON 数值"))
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise AiError("专业分析不是有效 JSON", "conflict", 409) from error
    value = old._bounded(value, old.contract.OUTPUT_LIMITS[role])
    if role == "independent_review":
        # The frozen review graph allows approved/conflicts/limitations only.
        # Standalone review references can be verified, but cannot be smuggled
        # into that answer format without a new versioned protocol.
        return old.validate_answer(role, canonical(value))
    masked = deepcopy(value)
    diag = _diagnosis(masked, role)
    original = _diagnosis(value, role)
    if type(diag) is dict and type(diag.get("findings")) is list:
        for finding in diag["findings"]:
            if type(finding) is not dict or type(finding.get("references")) is not list:
                continue
            for index, reference in enumerate(finding["references"]):
                if type(reference) is dict and "kind" in reference:
                    validate_reference(role, reference)
                    finding["references"][index] = deepcopy(_MASK)
    checked = old.validate_answer(role, canonical(masked))
    checked_diag = _diagnosis(checked, role)
    if type(checked_diag) is dict and type(original) is dict:
        for checked_finding, original_finding in zip(checked_diag["findings"], original["findings"]):
            for index, reference in enumerate(original_finding["references"]):
                if type(reference) is dict and reference.get("kind") == "promotion_keyword_sku":
                    checked_finding["references"][index] = deepcopy(reference)
    return checked


def resolve_reference(job, reference, principal):
    """Resolve a standalone review/promotion/report numeric reference."""
    report, role, guard = tools._job(job.id, principal)
    if role not in contract.PROMOTION_ROLES:
        _reject("当前实际 Agent 角色不能引用词货数值")
    fixed = runtime.bound_persisted(report.id, principal)
    value = promotion_claims.resolve(job, validate_reference(role, reference), principal)
    if (tools._job(job.id, principal) != (report, role, guard)
            or canonical(runtime.bound_persisted(report.id, principal)) != canonical(fixed)):
        _reject("词货引用核验期间 Agent 或报告根已变化")
    return value


def _detail(job, reference, report, snapshot, principal):
    mode = "mapped" if "pairKey" in reference else "native"
    selector = {key:reference[key] for key in ("sourceKey", "baselineKey", "pairKey",
        "baselinePairKey", "dimension") if key in reference}
    args = {"runId":snapshot["evidenceRunId"], "reportId":report.id,
        "screeningId":snapshot["screeningIntent"]["id"], "mode":mode,
        "offset":reference["rowIndex"], **selector}
    page = tools.read(job.id, "analysis", args, principal)
    table, rows = page["table"], page["table"]["rows"]
    if (page["mode"] != mode or page["selector"] != selector or not rows
            or rows[0]["rowIndex"] != reference["rowIndex"]
            or rows[0]["id"] != reference["rowId"]):
        _reject("分析明细行与固定引用不一致")
    row, metric, field = rows[0], reference["metric"], reference["field"]
    if field == "value":
        entry = row["metrics"].get(metric) or {}
        number, partial = entry.get("value"), bool(entry.get("missingRows"))
    elif field == "ratio":
        number, partial = row["ratios"].get(metric), False
    else:
        number = row["comparisons"].get(metric, {}).get(field)
        baseline = (row.get("baselineMetrics") or {}).get(metric) or {}
        partial = field == "baseline" and bool(baseline.get("missingRows"))
    if type(number) not in (int, float) or type(number) is float and not math.isfinite(number):
        _reject("分析明细引用没有可用的有限数值")
    value = {"reference":deepcopy(reference), "value":number, "partial":partial,
        "entity":deepcopy(row["entity"]), "reportId":report.id,
        "verification":{"numericReferenceVerified":True, "referenceReadVerified":False,
            "completeAgentReadingVerified":False, "causalityVerified":False,
            "humanReviewRequired":True},
        "limitations":deepcopy(table.get("limitations", []))}
    if mode == "mapped":
        value.update(mappingBindingDigest=table["bindingDigest"],
            mappingBinding=deepcopy(table["binding"]),
            baselineMappingBinding=deepcopy(table["baselineBinding"]))
    else:
        value.update(sourceRef=table["source"]["sourceRef"],
            evidenceDigest=table["source"]["evidenceDigest"])
    return value


def validate(job, answer, principal):
    """Verify all structured numbers for this actual job, never whole reading."""
    report, role, guard = tools._job(job.id, principal)
    fixed = runtime.bound_persisted(report.id, principal)
    parsed = validate_answer(role, answer)
    if role == "independent_review":
        result = {"schemaVersion":SCHEMA, "reportId":report.id, "role":role,
            "review":parsed, "numericReferencesVerified":False,
            "completeAgentReadingVerified":False, "independentReviewApproved":False,
            "humanReviewRequired":True}
    else:
        snapshot = json.loads(report.snapshot_json)
        diag = _diagnosis(parsed, role)
        references = [ref for finding in diag["findings"] for ref in finding["references"]]
        candidate_proof = None
        if any("candidateId" in ref for ref in references):
            ready = packages.prepare(snapshot["screeningIntent"]["id"], principal)
            candidate_proof = candidate_claims.prepare(ready, role, principal)
            row, _ = candidate_claims._checked(candidate_proof, principal)
            if row.report_id != report.id or row.id != snapshot["screeningIntent"]["id"]:
                _reject("候选引用来自另一报告或筛查")
        findings = []
        for finding in diag["findings"]:
            item = {key:deepcopy(value) for key,value in finding.items() if key != "references"}
            facts = []
            for reference in finding["references"]:
                if reference.get("kind") == "promotion_keyword_sku":
                    fact = resolve_reference(job, reference, principal)
                    if finding["kind"] == "action" and not fact["actionableKeywordSku"]:
                        _reject("缺少明确推广 SKU 身份，不能给出具体词货动作")
                elif "candidateId" in reference:
                    fact = candidate_claims.resolve(candidate_proof, reference, principal)
                else:
                    fact = _detail(job, reference, report, snapshot, principal)
                facts.append(fact)
            item["facts"] = facts
            findings.append(item)
        result = {"schemaVersion":SCHEMA, "reportId":report.id,
            "screeningId":snapshot["screeningIntent"]["id"], "role":role,
            "summary":diag["summary"], "findings":findings,
            "numericReferencesVerified":bool(references), "verifiedReferenceCount":len(references),
            "completeAgentReadingVerified":False,
            "independentReviewApproved":False, "humanReviewRequired":True,
            "limitations":["数值引用已由当前封存根重算；完整 Agent 阅读另行核验",
                "关键词×推广SKU与计划上下文两表是同一事实的不同分组，费用不能相加",
                "平台归因成交不是 ERP 净销售、增量利润或因果证明"]}
        if role == "report":
            result["sections"] = deepcopy(parsed["sections"])
    if (tools._job(job.id, principal) != (report, role, guard)
            or canonical(runtime.bound_persisted(report.id, principal)) != canonical(fixed)):
        _reject("诊断期间实际 Agent 或封存报告已变化")
    if len(canonical(result).encode("utf-8")) > MAX_RESULT_BYTES:
        raise AiError("完整诊断核验结果超过容量", "payload_too_large", 413)
    return result
