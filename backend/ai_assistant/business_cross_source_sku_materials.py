"""Default-closed sealed-v2 owning three-window SKU comparison candidate."""
from business_analysis import cross_source_kpi_plan, cross_source_sku_window_compare
from business_analysis.contracts import AnalysisContractError
from business_analysis.partitioned import Checkpoint

from . import business_cross_source_daily_materials as daily_owning
from . import business_erp_rollup_materials as erp_owning
from .business_sealed import Reader
from .policy import AiError, canonical, digest


SCHEMA = "business-cross-source-sku-owning-candidate-v1"
MAX_THREE_MATERIAL_BYTES = 64 * 1024 * 1024
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
WINDOWS = cross_source_kpi_plan.WINDOWS


def _need(ok, message="SKU三期拥有方材料、报告或来源修订已变化"):
    if not ok:
        raise AiError(message, "conflict", 409)


def _selected(keys):
    selected = {keys["master"]}
    selected.update(key for family in cross_source_kpi_plan.FAMILIES
        for key in keys[family].values() if key is not None)
    return sorted(selected)


def _pair(plan, window):
    return (plan["currentMappingPairKey"] if window == "current" else
        (plan["baselineMappingPairs"][window] or {}).get("baselinePairKey"))


def prepare(report_id, source_keys, principal, *, enabled=False,
            checkpoint=None, erp_scratch_bytes=None):
    """Return data-only SKU columns from all three exact sealed-v2 windows."""
    _need(enabled is True, "SKU三期拥有方入口默认关闭")
    keys = daily_owning._selection(source_keys)
    check = Checkpoint.wrap(checkpoint)
    if check is not None:
        check({"stage":"cross_source_sku_owning", "phase":"before"})
    _, snapshot, evidence, sources, fixed = erp_owning._bound(report_id,
        principal)
    selected = _selected(keys)
    reader = Reader(evidence, principal)
    infos = {key:reader.info(key) for key in selected}
    context = {"reportId":fixed["reportId"], "evidenceRunId":evidence.id,
        "evidenceVersion":evidence.version,
        "sealedDigest":snapshot["sealedDigest"],
        "ownerEmail":principal.email.lower(), "scope":principal.scope}
    try:
        plan = cross_source_kpi_plan.prepare_candidate(
            sources, infos, context, keys)
        _need(plan["mappingPlan"] == snapshot["mappingPlan"]
            and plan["mappingPlanDigest"] == snapshot["mappingPlanDigest"],
            "SKU三期映射不属于固定报告")
        materials = {}
        total_bytes = 0
        for window in WINDOWS:
            if check is not None:
                check({"stage":"cross_source_sku_owning", "phase":"window",
                    "window":window})
            selected_erp = keys["erpSales"][window]
            pair = _pair(plan, window)
            _need((selected_erp is None) == (pair is None),
                "SKU三期ERP来源与固定mapping pair不一致")
            result = daily_owning.prepare(report_id, keys, pair, window,
                principal, checkpoint=check,
                erp_scratch_bytes=erp_scratch_bytes)
            _need(result["schemaVersion"] == daily_owning.SCHEMA
                and result["reportBinding"] == fixed
                and result["planDigest"] == plan["planDigest"]
                and result["window"] == window
                and result["sealedSelectedSourcesFullyReplayed"] is True
                and result["authorityVerified"] is False
                and result["registeredAgentTool"] is False
                and result["registeredRenderer"] is False
                and result["resultDigest"] == digest({key:value for key,value
                    in result.items() if key != "resultDigest"}),
                "SKU三期日材料不属于同一已封存报告")
            material = result["material"]
            total_bytes += len(canonical(material).encode("utf-8"))
            _need(total_bytes <= MAX_THREE_MATERIAL_BYTES,
                "SKU三期完整材料超过候选容量")
            materials[window] = material
        compared = cross_source_sku_window_compare.prepare_candidate(
            plan, sources, infos, context, keys, materials)
        _need(compared["schemaVersion"] == cross_source_sku_window_compare.SCHEMA
            and compared["sourceMaterialDigests"] == {window:
                materials[window]["materialDigest"] for window in WINDOWS}
            and compared["historicalErpSkuOwnershipVerified"] is False
            and compared["crossDomainAmountsAdded"] is False
            and compared["authorityVerified"] is False,
            "SKU三期比较口径或材料摘要无效")
        if check is not None:
            check({"stage":"cross_source_sku_owning", "phase":"complete"})
        _need(erp_owning._bound(report_id, principal)[4] == fixed,
            "SKU三期返回前报告、封存或账号发生变化")
        current_reader = Reader(evidence, principal)
        _need({key:current_reader.info(key) for key in selected} == infos,
            "SKU三期返回前来源修订或控制摘要发生变化")
        binding = {"schemaVersion":
                "business-cross-source-sku-owning-binding-v1",
            "reportBinding":fixed, "planDigest":plan["planDigest"],
            "sourceKeys":keys, "sourceRevisions":{key:
                infos[key]["metadata"]["sourceRevision"] for key in selected},
            "sourceEvidenceDigests":{key:
                infos[key]["expected"]["evidenceDigest"] for key in selected},
            "materialDigests":compared["sourceMaterialDigests"],
            "comparisonDigest":compared["comparisonDigest"],
            "historicalErpSkuOwnershipVerified":False,
            "erpUnassignedPoolPreserved":True,
            "crossDomainAmountsAdded":False,
            "registeredAgentTool":False, "registeredRenderer":False}
        value = {"schemaVersion":SCHEMA, "binding":binding,
            "bindingDigest":digest(binding), "comparison":compared,
            "sealedSelectedSourcesFullyReplayed":True,
            "sameReportAuthorityVerified":True,
            "historicalErpSkuOwnershipVerified":False,
            "agentReadPersisted":False,
            "registeredRenderer":False, "authorityVerified":False}
        _need(len(canonical(value).encode("utf-8")) <= MAX_RESPONSE_BYTES,
            "SKU三期拥有方结果超过固定响应容量")
        return {**value, "resultDigest":digest(value)}
    except (AnalysisContractError, KeyError, TypeError, ValueError,
            UnicodeError, OverflowError) as error:
        raise AiError("SKU三期完整封存来源或逐行比较未通过核验",
            "conflict", 409) from error
