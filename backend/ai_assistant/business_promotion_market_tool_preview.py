"""Internal read-only market-v2 summary/page/row preview for one report.

No model tool registration or persisted Agent receipt. Every call fully
replays the three owning market materials before selecting bounded output.
"""
from business_analysis import market_report_tables_v2
from business_analysis.contracts import AnalysisContractError
from . import business_market_composite_export as composite
from . import business_market_dynamics as bands_reader
from . import business_market_observation as rank_reader
from . import business_promotion_market_admission as admission
from . import business_promotion_market_runtime_v2_contract as contract
from .policy import AiError, canonical, digest, identifier


SCHEMA = "business-promotion-market-tool-preview-v2"
MAX_RESPONSE_BYTES = 38_000


def _need(ok, message="市场v2内部工具与已准入封存报告不同"):
    if not ok:
        raise AiError(message, "conflict", 409)


def _roots(report_id, selector, expected_admission_digest, role, arguments,
           principal, checkpoint):
    report_id = identifier(report_id, "reportId")
    fixed = admission.require_observed(report_id, selector, principal,
        checkpoint=checkpoint)
    _need(type(expected_admission_digest) is str
        and fixed["bindingDigest"] == expected_admission_digest,
        "市场工具必须固定本次报告准入摘要")
    runtime = contract.prepare(fixed)
    try:
        args = contract.arguments(runtime, role, arguments)
    except AnalysisContractError as error:
        raise AiError("市场工具角色、模式或分页参数无效", "invalid_request", 400) from error
    return fixed, runtime, args


def _verified_material(report_id, fixed, selector, principal, checkpoint, limits):
    material = composite.prepare(report_id,
        selector["rankCurrentSourceKey"], selector["rankBaselineKey"],
        selector["currentObservationDate"], selector["baselineObservationDate"],
        principal, bands=selector["bands"], checkpoint=checkpoint, limits=limits)
    manifest = material.manifest
    candidate = fixed["candidate"]
    specs = {item["view"]:item for item in manifest["tables"]}
    _need(manifest["schemaVersion"] == composite.SCHEMA
        and manifest["manifestDigest"] == digest({key:value for key,value in
            manifest.items() if key != "manifestDigest"})
        and digest(manifest["reportBinding"]) ==
            fixed["binding"]["reportBindingDigest"]
        and manifest["priceBandSourceKey"] == selector["priceBandSourceKey"]
        and manifest["rankCurrentSourceKey"] == selector["rankCurrentSourceKey"]
        and manifest["rankBaselineKey"] == selector["rankBaselineKey"]
        and manifest["bands"] == selector["bands"]
        and manifest["observationDates"] == {"current":selector["currentObservationDate"],
            "baseline":selector["baselineObservationDate"]}
        and manifest["rankObservationCoverage"] ==
            candidate["observationCoverage"]
        and set(specs) == set(composite.VIEWS)
        and manifest["authority"] == {"selectedTopSampleReconciled":True,
            "wholeMarketCoverageVerified":False,
            "ownProductIdentityVerified":False,
            "priceSummaryAndMembersAdditive":False,
            "marketAndOwnSalesAdditive":False}
        and manifest["authorityVerified"] is False,
        "市场三表不是同一报告、观察日或样本口径")
    for name, expected in (("rankCurrent", "current"),
            ("rankBaseline", "baseline")):
        actual = manifest["sourceDescriptors"][name]
        selected = candidate["sources"][expected]
        _need(all(actual[key] == selected[key] for key in
            ("key", "domain", "query", "queryDigest")),
            "市场三表来源描述与准入目录不一致")
    try:
        with market_report_tables_v2.tables(manifest,
                {view:material.ndjson_pages(view) for view in composite.VIEWS}) as (summary, tables):
            _need(len(tables) == 3 and summary["authorityVerified"] is False)
            columns = {view:len(table.columns)
                for view,table in zip(composite.VIEWS,tables)}
    except AnalysisContractError as error:
        raise AiError("市场三张完整类型表未通过逐行与清单核验",
            "conflict", 409) from error
    return manifest, specs, columns


def _page_or_row(report_id, selector, args, specs, principal):
    view, mode = args["view"], args["mode"]
    if view == "price_band":
        if mode == "page":
            value = bands_reader.page(report_id, {"sourceKey":selector["priceBandSourceKey"],
                "view":"price_band", "bands":selector["bands"],
                "offset":args["offset"], "limit":20}, principal)
        else:
            value = bands_reader.read_row(report_id, selector["priceBandSourceKey"],
                "price_band", args["rowIndex"], args["rowId"], principal,
                bands=selector["bands"])
        spec = specs["price_band_summary"]
    else:
        if mode == "page":
            value = rank_reader.page(report_id, {
                "currentSourceKey":selector["rankCurrentSourceKey"],
                "baselineSourceKey":selector["rankBaselineKey"],
                "currentObservationDate":selector["currentObservationDate"],
                "baselineObservationDate":selector["baselineObservationDate"],
                "offset":args["offset"], "limit":20}, principal)
        else:
            value = rank_reader.read_row(report_id,
                selector["rankCurrentSourceKey"], selector["rankBaselineKey"],
                selector["currentObservationDate"],
                selector["baselineObservationDate"],
                args["rowIndex"], args["rowId"], principal)
        spec = specs["rank_entry_exit"]
    _need(value["binding"]["tableBindingDigest"] == spec["sourceTableDigest"]
        and value["bindingDigest"] == spec["bindingDigest"]
        and value["responseDigest"] == digest({key:item for key,item in value.items()
            if key != "responseDigest"}),
        "市场页/行与完整三表清单摘要不一致")
    if mode == "page":
        rows = value["table"]["rows"]
        _need(value["table"]["pagination"]["offset"] == args["offset"]
            and value["table"]["pagination"]["total"] == spec["rowCount"]
            and all(row["rowIndex"] == args["offset"]+index
                for index,row in enumerate(rows)))
        return {"rows":rows,"pagination":value["table"]["pagination"],
            "pageDigest":value["table"]["pageDigest"],
            "bindingDigest":value["bindingDigest"],
            "tableBindingDigest":spec["sourceTableDigest"]}
    row = value["row"]
    _need(row["rowIndex"] == args["rowIndex"]
        and row["rowId"] == args["rowId"])
    return {"row":row,"bindingDigest":value["bindingDigest"],
        "tableBindingDigest":spec["sourceTableDigest"]}


def read(report_id, selector, expected_admission_digest, role, arguments,
         principal, *, checkpoint=None, limits=None):
    """Return bounded preview only; role is a checked claim, not actual job."""
    fixed, runtime, args = _roots(report_id, selector,
        expected_admission_digest, role, arguments, principal, checkpoint)
    selector = runtime["marketSelector"]  # Defensive detached selection.
    manifest, specs, columns = _verified_material(report_id, fixed,
        selector, principal, checkpoint, limits)
    if args["mode"] == "summary":
        actor, _, _, infos, _ = admission._roots(report_id, principal)
        selected = {"current":selector["rankCurrentSourceKey"],
            "baseline":selector["rankBaselineKey"]}
        coverage = {side:infos[key]["metadata"]["coverage"]
            for side,key in selected.items()}
        payload = {"tables":[{"view":spec["view"],
            "rowCount":spec["rowCount"],"pageCount":spec["pageCount"],
            "ndjsonBytes":spec["ndjsonBytes"],
            "ndjsonSha256":spec["ndjsonSha256"],
            "sourceTableDigest":spec["sourceTableDigest"],
            "columnCount":columns[spec["view"]]}
            for spec in manifest["tables"]],
            "sourceCoverage":coverage,
            "observationCoverage":manifest["rankObservationCoverage"],
            "sourceDescriptorsDigest":manifest["sourceDescriptorsDigest"],
            "priceSummaryAndMembersAdditive":False,
            "marketAndOwnSalesAdditive":False,
            "ownProductIdentityVerified":False}
    else:
        payload = _page_or_row(report_id, selector, args, specs, principal)
    result = {"schemaVersion":SCHEMA,"mode":args["mode"],
        "reportId":report_id,"rolePolicyChecked":role,
        "admissionDigest":fixed["bindingDigest"],
        "marketContextDigest":runtime["marketContextDigest"],
        "marketManifestDigest":manifest["manifestDigest"],
        "payload":payload,
        "serverFullMarketMaterialVerified":True,
        "agentReadPersisted":False,"actualAgentBound":False,
        "authorityVerified":False,"registeredTool":False,
        "limitations":["市场TOP样本不归属本店、ERP或B端销售。",
            "价格带汇总与成员同源不可相加；缺日期与未入TOP不等于零。",
            "未记录同Agent dispatch/result，本输出不能作为正式Agent已读证明。"]}
    result["resultDigest"] = digest(result)
    _need(admission.require_observed(report_id, selector, principal)["bindingDigest"] ==
        fixed["bindingDigest"],
        "市场工具返回前账号、报告或来源根发生变化")
    if len(canonical(result).encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise AiError("完整市场内部工具响应超过固定容量", "payload_too_large", 413)
    return result
