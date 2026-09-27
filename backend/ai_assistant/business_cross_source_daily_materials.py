"""Internal sealed-v2 owning entry for unregistered cross-source daily columns."""
import json

from business_analysis import cross_source_daily_columns, cross_source_kpi_plan
from business_analysis.contracts import AnalysisContractError
from business_analysis.partitioned import Checkpoint
from . import business_erp_rollup_materials as erp_owning
from .business_sealed import Reader
from .policy import AiError, canonical, digest


SCHEMA = "business-cross-source-daily-owning-materials-candidate-v1"
_ERP_KINDS = ("shop_day", "sku_day", "unassigned_day")


def _need(ok, message="跨来源逐日报告、关联计划或封存证据已变化"):
    if not ok:
        raise AiError(message, "conflict", 409)


def _selection(value):
    _need(type(value) is dict and set(value) == {*cross_source_kpi_plan.FAMILIES, "master"},
        "逐日来源选择字段无效")
    _need(type(value["master"]) is str)
    for family in cross_source_kpi_plan.FAMILIES:
        windows = value[family]
        _need(type(windows) is dict and set(windows) == set(cross_source_kpi_plan.WINDOWS)
            and type(windows["current"]) is str
            and all(windows[window] is None or type(windows[window]) is str
                for window in cross_source_kpi_plan.WINDOWS[1:]),
            "逐日比较来源选择无效")
    _need(len(canonical(value).encode("utf-8")) <= 4096)
    return json.loads(canonical(value))


def prepare(report_id, source_keys, pair_key, window, principal, *,
            checkpoint=None, erp_scratch_bytes=None):
    """Return complete bounded data-only material after two live fences.

    This function does not register an Agent tool or authorize a file renderer.
    The underlying Reader proves each selected source page chain independently.
    """
    keys = _selection(source_keys)
    check = Checkpoint.wrap(checkpoint)
    if check is not None:
        check({"stage": "cross_source_daily", "phase": "before"})
    _, snapshot, evidence, sources, fixed = erp_owning._bound(report_id, principal)
    reader = Reader(evidence, principal)
    selected = {keys["master"]}
    selected.update(key for family in cross_source_kpi_plan.FAMILIES
        for key in keys[family].values() if key is not None)
    infos = {key: reader.info(key) for key in sorted(selected)}
    context = {"reportId": fixed["reportId"],
        "evidenceRunId": evidence.id, "evidenceVersion": evidence.version,
        "sealedDigest": snapshot["sealedDigest"],
        "ownerEmail": principal.email.lower(), "scope": principal.scope}
    try:
        plan = cross_source_kpi_plan.prepare_candidate(sources, infos, context, keys)
        _need(plan["mappingPlan"] == snapshot["mappingPlan"]
            and plan["mappingPlanDigest"] == snapshot["mappingPlanDigest"],
            "逐日固定mapping pair不属于报告工作流")
        _need(type(window) is str and window in cross_source_kpi_plan.WINDOWS,
            "逐日比较窗口无效")
        sales_key = keys["erpSales"][window]
        expected_pair = (plan["currentMappingPairKey"] if window == "current"
            else (plan["baselineMappingPairs"][window] or {}).get("baselinePairKey"))
        _need(pair_key == expected_pair,
            "ERP映射pair与当前比较窗口不一致")
        native = {key: reader.pages(key, checkpoint=check)
            for family in ("netshopSku", "netshopSpu", "promotion")
            if (key := keys[family][window]) is not None}
        if sales_key is None:
            material = cross_source_daily_columns.prepare_candidate(
                plan, sources, infos, context, keys, window, None, None, native)
        else:
            with erp_owning.prepare(report_id, pair_key, sales_key,
                    keys["master"], principal, checkpoint=check,
                    assignment_scratch_bytes=erp_scratch_bytes) as erp:
                erp_manifest = erp.manifest
                erp_pages = {kind: erp.ndjson_pages(kind) for kind in _ERP_KINDS}
                material = cross_source_daily_columns.prepare_candidate(
                    plan, sources, infos, context, keys, window,
                    erp_manifest, erp_pages, native)
        if check is not None:
            check({"stage": "cross_source_daily", "phase": "complete"})
        _need(erp_owning._bound(report_id, principal)[4] == fixed,
            "逐日材料返回前账号、报告或封存版本变化")
        result = {"schemaVersion": SCHEMA, "reportBinding": fixed,
            "planDigest": plan["planDigest"], "window": window,
            "material": material,
            "sealedSelectedSourcesFullyReplayed": True,
            "authorityVerified": False, "registeredAgentTool": False,
            "registeredRenderer": False}
        _need(len(canonical(result).encode("utf-8")) <=
            cross_source_daily_columns.MAX_OUTPUT_BYTES,
            "完整逐日材料超过固定响应容量")
        result["resultDigest"] = digest(result)
        return result
    except (AnalysisContractError, KeyError, TypeError, ValueError,
            UnicodeError, OverflowError) as error:
        raise AiError("跨来源逐日材料未通过完整封存来源与数值核验",
            "conflict", 409) from error
