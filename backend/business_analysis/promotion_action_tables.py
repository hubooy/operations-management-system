"""Pure projection of approved promotion actions into one reviewable table.

This consumes a detached content DTO. Its digest is a change fence, not an
authorization token: the owning report service must recheck approval and
source authority before publishing files. No action is executed here.
"""

import math
import re

from ai_assistant import business_promotion_content_contract as approved_contract

from .contracts import AnalysisContractError, canonical, digest
from .report_files import Column, Table, text


KEY = "promotion-approved-actions-v1"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_ACTION = frozenset(("object", "change", "prerequisites", "successMetric",
    "observationDays", "rollback", "priority", "ownerRole", "budgetImpact"))
_PRIORITY = {"high": "高", "medium": "中", "low": "低"}
_PROMOTION_METRICS = frozenset(("spendCents", "reportedGmvCents",
    "directGmvCents", "indirectGmvCents", "newCustomerGmvCents"))
_PROMOTION_FIELDS = frozenset(("value", "baseline", "difference", "changeRate"))
_DIMENSION = {"shop": ("shopName", "店铺"), "category": ("category", "品类"),
    "spu": ("spuId", "SPU"), "sku": ("skuId", "SKU"),
    "keyword": ("keyword", "关键词")}
_COLUMNS = (
    Column("sourceRole", "结论角色"), Column("findingId", "结论ID"),
    Column("findingTitle", "结论标题"), Column("objectType", "定位对象类型"),
    Column("objectId", "定位对象身份JSON"), Column("actionStatus", "行动状态"),
    Column("approvedObject", "批准原文·操作对象"),
    Column("change", "建议调整（未执行）"), Column("prerequisites", "执行前提"),
    Column("successMetric", "成功指标"), Column("observationDays", "观察天数", "integer"),
    Column("rollback", "停止或回退规则"), Column("priority", "优先级"),
    Column("ownerRole", "责任角色"), Column("budgetImpactStatus", "预算影响核对状态"),
    Column("budgetImpact", "预算影响批准原文"),
    Column("explanation", "批准的判断与依据"), Column("citationPointers", "完整原始引用指针JSON"),
    Column("verifiedNumbers", "引用数值JSON（原字段与原单位）"),
    Column("rowDigest", "本行SHA-256"),
)


def _need(condition, message="推广行动表的批准内容或引用无效"):
    if not condition:
        raise AnalysisContractError(message)


def _word(value):
    _need(type(value) is str and bool(value.strip()))
    return text(value)


def _sha(value):
    _need(type(value) is str and _SHA.fullmatch(value) is not None)


def _citation(fact, binding, role):
    _need(type(fact) is dict and type(fact.get("reference")) is dict)
    ref = fact["reference"]
    _need("kind" not in ref or ref["kind"] == "promotion_keyword_sku")
    if ref.get("kind") == "promotion_keyword_sku":
        _need(set(ref) in ({"kind", "sourceKey", "view", "rowIndex", "rowId",
            "tableBindingDigest", "metric", "field"}, {"kind", "sourceKey", "baselineKey",
            "view", "rowIndex", "rowId", "tableBindingDigest", "metric", "field"}))
        _need(role in ("promotion", "report") and
              ref["view"] in ("keyword_sku", "keyword_sku_context"))
        _sha(ref["rowId"]); _sha(ref["tableBindingDigest"])
        _need(type(ref["rowIndex"]) is int and 0 <= ref["rowIndex"] < 250_000)
        for key in ("sourceKey", "metric", "field"):
            _word(ref[key])
        _need(ref["metric"] in _PROMOTION_METRICS and
              ref["field"] in _PROMOTION_FIELDS and
              (ref["field"] == "value" or "baselineKey" in ref))
        if "baselineKey" in ref: _word(ref["baselineKey"])
        selector = binding["promotionSelector"]
        _need(ref["sourceKey"] == selector["sourceKey"] and
              ref.get("baselineKey") == selector.get("baselineKey") and
              fact.get("reportId") == binding["reportId"] and
              fact.get("jobId") == binding["jobs"][role]["jobId"])
    elif "candidateId" in ref:
        _sha(ref["candidateId"])
        _need(type(ref.get("screening")) is dict and type(ref.get("row")) is dict,
              "候选引用缺少固定筛查行指针")
        _need(type(ref.get("evidenceBinding")) is dict and
              ref["evidenceBinding"].get("reportId") == binding["reportId"] and
              ref.get("role") == role)
        _word(fact.get("metric")); _word(fact.get("field"))
    else:
        _need(type(ref.get("rowIndex")) is int and 0 <= ref["rowIndex"] < 250_000)
        _sha(ref.get("rowId"))
        _need(type(ref.get("dimension")) is str and ref["dimension"] in _DIMENSION and
              ("sourceKey" in ref or "pairKey" in ref))
        _word(ref.get("metric")); _word(ref.get("field"))
        _need(fact.get("reportId") == binding["reportId"] and
              fact.get("jobId") == binding["jobs"][role]["jobId"])
    number = fact.get("value")
    _need(type(number) in (int, float) and (type(number) is not float or math.isfinite(number)),
          "引用数值缺失或非有限数值")
    _need(type(fact.get("verification")) is dict and
          (fact["verification"].get("numericReferenceVerified") is True or
           fact["verification"].get("candidateNumberVerified") is True) and
          fact["verification"].get("completedAgentReadVerified") is True)
    _need(type(fact.get("entity")) is dict)
    return ref, number


def _identity(fact, binding, role):
    """Only exact structured identities can make an approved action actionable."""
    ref, _ = _citation(fact, binding, role)
    entity = fact["entity"]
    if ref.get("kind") == "promotion_keyword_sku":
        if fact.get("actionableKeywordSku") is not True or fact.get("identityQualified") is not True:
            return None
        keys = ("platform", "shopName", "keyword", "promotedSkuId")
        if any(type(entity.get(key)) is not str or not entity[key].strip() for key in keys):
            return None
        return "关键词×推广SKU", canonical({key: entity[key] for key in keys})
    if fact.get("identityQualified") is False:
        return None
    if "dimension" in ref:
        key, label = _DIMENSION[ref["dimension"]]
    else:
        found = [(key, label) for key, label in (("skuId", "SKU"), ("spuId", "SPU"),
            ("category", "品类"), ("shopName", "店铺"))
            if key in entity]
        if not found: return None
        key, label = found[0]
    if type(entity.get(key)) is not str or not entity[key].strip():
        return None
    scope = {name: entity[name] for name in ("platform", "shopName")
        if type(entity.get(name)) is str and entity[name].strip()}
    if label != "店铺" and len(scope) != 2:
        return None
    return label, canonical({**scope, key: entity[key]})


def project(approved):
    """Return one Table with every approved action finding, including gaps.

    Non-action findings do not become rows. Ambiguous or missing object identity
    remains a visible non-executable row with null object ID and full citation.
    """
    try:
        dto = approved_contract.check(approved)
    except (approved_contract.ContentContractError, TypeError, ValueError) as error:
        raise AnalysisContractError("推广行动表输入未通过已批准内容合同") from error
    binding = dto["binding"]
    _need(binding["humanReview"]["status"] == "approved",
          "推广行动表必须来自实际批准状态")
    analyses = [("report", dto["content"]["diagnosis"])] + [
        (role, dto["content"]["professionalAnalyses"][role])
        for role in approved_contract.SPECIALISTS]
    rows, pending = [], []
    for role, analysis in analyses:
        _need(type(analysis) is dict and type(analysis.get("findings")) is list)
        seen_ids = set()
        for finding in analysis["findings"]:
            _need(type(finding) is dict and type(finding.get("kind")) is str and finding["kind"] in
                  {"action", "observation", "hypothesis", "gap"})
            if finding["kind"] != "action":
                _need("action" not in finding)
                continue
            _need(type(finding.get("action")) is dict and set(finding["action"]) == _ACTION)
            _need(type(finding.get("facts")) is list and 1 <= len(finding["facts"]) <= 6)
            for key in ("id", "title", "explanation"): _word(finding.get(key))
            _need(finding["id"] not in seen_ids, "同一角色结论ID重复")
            seen_ids.add(finding["id"])
            action = finding["action"]
            for key in _ACTION - {"observationDays", "priority"}: _word(action[key])
            _need(type(action["observationDays"]) is int and 1 <= action["observationDays"] <= 90
                  and type(action["priority"]) is str and action["priority"] in _PRIORITY)
            references, numbers, identities = [], [], set()
            unqualified_promotion = False
            for fact in finding["facts"]:
                ref, number = _citation(fact, binding, role)
                references.append(ref)
                numbers.append({"reference": ref,
                    "metric": fact.get("metric", ref.get("metric")),
                    "field": fact.get("field", ref.get("field")), "value": number,
                    "partial": fact.get("partial") if type(fact.get("partial")) is bool else None})
                identity = _identity(fact, binding, role)
                if ref.get("kind") == "promotion_keyword_sku" and identity is None:
                    unqualified_promotion = True
                if identity is not None: identities.add(identity)
            if unqualified_promotion or len(identities) != 1:
                object_type, object_id, status = "待核", None, "待核身份不可执行"
                pending.append(role + ":" + finding["id"])
            else:
                object_type, object_id = next(iter(identities))
                status = "建议待人工执行"
            values = [role, finding["id"], finding["title"], object_type, object_id, status,
                action["object"], action["change"], action["prerequisites"],
                action["successMetric"], action["observationDays"], action["rollback"],
                _PRIORITY[action["priority"]], action["ownerRole"],
                "待核（未与预算模型逐项绑定）", action["budgetImpact"],
                finding["explanation"], canonical(references), canonical(numbers)]
            for value in values:
                if type(value) is str: text(value)
            rows.append((*values, digest(values)))
    note = ("逐条列出输入合同标记已批准的行动建议；发布端须另行复核实际人审，全部为建议、未执行。"
            "引用数值保留来源指标字段名，未换算单位；预算影响保留批准原文，未测算时不得视为零。"
            "同一推广事实的词货视图费用不可相加。")
    if pending:
        note += " 身份缺失或多对象行动仍逐行保留、不可执行（待核）：" + "、".join(pending) + "。"
    text(note)
    return Table(KEY, "推广调整行动计划", note, _COLUMNS, tuple(rows), len(rows))
