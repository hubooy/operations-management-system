"""Resolve report claims to immutable calculated facts; never trust supplied numbers."""
from . import business_evidence
from .policy import AiError, canonical, fields, identifier, integer, passive, text
from business_analysis.partitioned import MAX_RESULT_GROUPS

REFERENCE_FIELDS = {"sourceKey", "dimension", "baselineKey", "rowIndex", "rowId", "metric", "field"}
VALUE_FIELDS = {"value", "ratio", "baseline", "difference", "changeRate", "percentagePoints"}
ACTION_FIELDS = {"object", "change", "prerequisites", "successMetric", "observationDays", "rollback", "priority", "ownerRole", "budgetImpact"}


def validate(value, evidence_id, principal, *, fixed_mapping_plan=None):
    fields(value, {"summary", "findings"}, {"summary", "findings"})
    passive(value, 48000)
    summary = text(value["summary"], "summary", 2000)
    findings = value["findings"]
    if not isinstance(findings, list) or not 1 <= len(findings) <= 12:
        raise AiError("诊断须包含 1—12 条结论")
    if business_evidence.get_run(evidence_id, principal).status != "sealed":
        raise AiError("诊断须引用封存证据", "conflict", 409)
    cache, output, ids, count = {}, [], set(), 0
    for finding in findings:
        fields(finding, {"id", "kind", "title", "explanation", "references", "action"}, {"id", "kind", "title", "explanation", "references"})
        key = identifier(finding["id"])
        if key in ids or finding["kind"] not in ("observation", "hypothesis", "action", "gap"):
            raise AiError("结论身份或类型无效")
        ids.add(key)
        references = finding["references"]
        if not isinstance(references, list) or not (0 if finding["kind"] == "gap" else 1) <= len(references) <= 6:
            raise AiError("结论缺少证据引用或引用过多")
        facts = []
        for reference in references:
            count += 1
            if count > 32:
                raise AiError("单份诊断引用超过 32 项", "payload_too_large", 413)
            if fixed_mapping_plan is not None and type(reference) is dict and "pairKey" in reference:
                from .business_mapped_claims import resolve
                facts.append(resolve(reference, evidence_id, fixed_mapping_plan, principal))
                continue
            fields(reference, REFERENCE_FIELDS, REFERENCE_FIELDS - {"baselineKey"})
            for field in ("sourceKey", "baselineKey", "rowId", "metric"):
                if field in reference:
                    identifier(reference[field], field)
            integer(reference["rowIndex"], "rowIndex", lo=0, hi=MAX_RESULT_GROUPS-1)
            if not isinstance(reference["field"], str) or reference["field"] not in VALUE_FIELDS:
                raise AiError("引用数值类型无效")
            query = {key: reference[key] for key in ("sourceKey", "dimension", "baselineKey") if key in reference}
            query.update(offset=str(reference["rowIndex"]), limit="1")
            locator = canonical(query)
            if locator not in cache:
                cache[locator] = business_evidence.analysis_table(evidence_id, query, principal)
            table = cache[locator]
            if len(table["rows"]) != 1 or table["rows"][0]["id"] != reference["rowId"]:
                raise AiError("结论引用的分析行不存在或身份不符", "conflict", 409)
            row, field, metric = table["rows"][0], reference["field"], reference["metric"]
            if field == "value":
                number = (row["metrics"].get(metric) or {}).get("value")
                partial = bool((row["metrics"].get(metric) or {}).get("missingRows"))
            elif field == "ratio":
                number, partial = row["ratios"].get(metric), False
            else:
                number = row["comparisons"].get(metric, {}).get(field)
                partial = bool(((row.get("baselineMetrics") or {}).get(metric) or {}).get("missingRows")) if field == "baseline" else False
            if number is None:
                raise AiError("结论引用的数值不可用，须改为数据缺口", "conflict", 409)
            facts.append({"reference": reference, "value": number, "partial": partial,
                "sourceRef": table["source"]["sourceRef"], "evidenceDigest": table["source"]["evidenceDigest"],
                "entity": row["entity"], "coverage": table["sourceMetadata"]["coverage"]})
        item = {"id": key, "kind": finding["kind"], "title": text(finding["title"], "title", 160),
            "explanation": text(finding["explanation"], "explanation", 1200), "facts": facts}
        if finding["kind"] == "action":
            action = finding.get("action")
            fields(action, ACTION_FIELDS, ACTION_FIELDS)
            item["action"] = {key: text(action[key], key, 600) for key in ACTION_FIELDS - {"observationDays", "priority"}}
            item["action"]["observationDays"] = integer(action["observationDays"], "observationDays", hi=90)
            if action["priority"] not in ("high", "medium", "low"):
                raise AiError("动作优先级无效")
            item["action"]["priority"] = action["priority"]
        elif "action" in finding:
            raise AiError("仅动作结论可以含执行规划")
        output.append(item)
    return {"schemaVersion": "business-diagnosis-v1", "evidenceRunId": evidence_id,
        "summary": summary, "findings": output, "factsVerified": True, "humanReviewRequired": True,
        "limitations": ["只自动核验结构化引用数值，文字解释、缺口声明及因果推测仍须独立和人工复核", "调整规划不会自动修改推广预算、商品或业务数据"]}
