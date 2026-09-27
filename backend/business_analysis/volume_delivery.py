"""Pure renderer-4 persistence receipts, with no database or publication effects.

The compact receipt is an index, not evidence that any file has been persisted.
Callers must independently verify each stored stream's SHA, size and chunks,
hold their owner/attempt fence, and publish atomically. Historical-attempt quota
is also a persistence concern; this contract bounds the current delivery only.
"""
import hashlib
import json
import re

from . import volume_plan
from .contracts import AnalysisContractError, MAX_SAFE_INTEGER, canonical, digest


SCHEMA_VERSION = "business-file-delivery-v2"
CHUNK_BYTES = 512 * 1024
MAX_ROOT_BYTES = 128 * 1024
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_DELIVERY_BYTES = 1024 * 1024 * 1024
ROOT_FIELDS = {"schemaVersion", "rendererVersion", "bindingDigest", "attempt", "draft", "volumeCount", "files", "manifestFile"}
FILE_FIELDS = {"volumeIndex", "format", "bytes", "sha256", "chunkCount"}
DESCRIPTOR_FIELDS = {"key", "title", "rowCount", "columnCount"}
PART_FIELDS = DESCRIPTOR_FIELDS | {"fragmentIndex", "fragmentCount", "rowOffset", "rowLimit", "fragmentKey"}
FULL_FIELDS = {"schemaVersion", "status", "reportId", "evidenceDigest", "rendererVersion", "planDigest", "sourceDescriptorDigest",
               "volumeCount", "sourceTableCount", "fragmentCount", "totalRows", "byteCapacity", "tables", "volumes", "manifestDigest"}


def _renderer(value):
    if type(value) is not int or value not in (4, 6, 7, 9, 10, 11):
        _fail("多卷持久渲染版本不受支持")


def _fail(message):
    raise AnalysisContractError(message)


def _fields(value, required, optional=()):
    if type(value) is not dict or not required <= value.keys() or value.keys() - required - set(optional):
        _fail("多卷交付字段集合无效")


def _integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        _fail("多卷交付整数或容量无效")
    return value


def _sha(value):
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
        _fail("多卷交付摘要须为小写SHA-256")
    return value


def _equal(actual, expected):
    if not volume_plan._same_bounded(actual, expected):
        _fail("多卷交付与可信绑定、顺序或完整证明不一致")


def _snapshot(value, maximum):
    """Bound passive input before encoding; no arbitrary object hooks or floats."""
    remaining, nodes = maximum, 1_000_000

    def charge(amount):
        nonlocal remaining
        remaining -= amount
        if remaining < 0:
            _fail("多卷交付JSON字节容量超限")

    def visit(item, depth):
        nonlocal nodes
        nodes -= 1
        if nodes < 0 or depth > 24:
            _fail("多卷交付JSON嵌套或节点超限")
        kind = type(item)
        if kind is str:
            if len(item) > remaining:
                _fail("多卷交付文本容量超限")
            try:
                charge(len(canonical(item).encode("utf-8")))
            except UnicodeError as error:
                raise AnalysisContractError("多卷交付文本编码无效") from error
            return item
        if kind is bool or item is None:
            charge(len(canonical(item)))
            return item
        if kind is int:
            if abs(item) > MAX_SAFE_INTEGER:
                _fail("多卷交付整数超出安全范围")
            charge(len(str(item)))
            return item
        if kind is list:
            if len(item) > nodes:
                _fail("多卷交付数组容量超限")
            charge(2 + max(0, len(item)-1))
            return [visit(child, depth+1) for child in item]
        if kind is dict:
            if len(item) > nodes:
                _fail("多卷交付对象容量超限")
            charge(2 + max(0, len(item)-1) + len(item))
            result = {}
            for key, child in item.items():
                if type(key) is not str:
                    _fail("多卷交付对象键须为文本")
                result[visit(key, depth+1)] = visit(child, depth+1)
            return result
        _fail("多卷交付只允许被动JSON值及精确整数")

    return visit(value, 0)


def _file(value, index, format, maximum):
    _fields(value, FILE_FIELDS)
    _equal(value["volumeIndex"], index)
    _equal(value["format"], format)
    size = _integer(value["bytes"], 1, maximum)
    _sha(value["sha256"])
    _equal(value["chunkCount"], (size + CHUNK_BYTES - 1) // CHUNK_BYTES)
    return size


def validate(compact, *, binding_digest, attempt, draft, renderer_version=4):
    """Return an independent bounded compact receipt, or fail closed."""
    _renderer(renderer_version)
    _sha(binding_digest)
    _integer(attempt, 1, 5)
    if type(draft) is not bool:
        _fail("多卷交付草稿标记须为布尔值")
    root = _snapshot(compact, MAX_ROOT_BYTES)
    _fields(root, ROOT_FIELDS)
    for key, expected in (("schemaVersion", SCHEMA_VERSION), ("rendererVersion", renderer_version),
                          ("bindingDigest", binding_digest), ("attempt", attempt), ("draft", draft)):
        _equal(root[key], expected)
    count = _integer(root["volumeCount"], 1, 100)
    if type(root["files"]) is not list or len(root["files"]) != 2 * count:
        _fail("多卷交付必须包含每卷完整HTML和XLSX")
    total = _file(root["manifestFile"], 0, "json", MAX_MANIFEST_BYTES)
    for position, item in enumerate(root["files"]):
        total += _file(item, position // 2 + 1, ("html", "xlsx")[position % 2], MAX_FILE_BYTES)
    if total > MAX_DELIVERY_BYTES:
        _fail("完整多卷交付含JSON清单超过1GiB")
    return root


def _budget(value, report_id, plan_digest):
    _fields(value, {"schemaVersion", "reportId", "planDigest", "sheets", "activeScenario", "initialUnmeasurableTargets", "integerProductLimit", "reviewStatus"})
    for key, expected in (("schemaVersion", "business-excel-budget-v1"), ("reportId", report_id), ("planDigest", plan_digest),
                          ("activeScenario", 1), ("integerProductLimit", 99999999999999), ("reviewStatus", "unreviewed_local_scenario")):
        _equal(value[key], expected)
    sheets = value["sheets"]
    if (type(sheets) is not list or len(sheets) != 3 or
            any(type(s) is not str or not 1 <= len(s) <= 31 for s in sheets) or len(set(s.casefold() for s in sheets)) != 3):
        _fail("原生预算工作表证明无效")
    _integer(value["initialUnmeasurableTargets"], 0, 10000)


SCREENING_KEYS = {"screeningRef", "screeningPackagePolicy", "screeningPackageDigests"}
PROMOTION_KEY = "promotionFileProof"
TRIAL_KEY = "promotionTrialProof"
BUDGET_KEY = "promotionBudgetProof"
SLIM_KEY = "promotionSlimProof"
SLIM_BROWSER_REQUIREMENTS = ["DecompressionStream:gzip", "SubtleCrypto:SHA-256"]


def _html_payload_v11(payload, volume):
    """Bind every compressed table descriptor to its exact volume fragment."""
    _fields(payload, {"schemaVersion", "htmlPayloadVersion",
        "browserRequirements", "tables", "proofDigest"})
    _equal(payload["schemaVersion"], "business-html-compressed-rows-v1")
    _equal(payload["htmlPayloadVersion"], 2)
    _equal(payload["browserRequirements"], SLIM_BROWSER_REQUIREMENTS)
    _equal(payload["proofDigest"], digest({key: child for key, child in
        payload.items() if key != "proofDigest"}))
    if type(payload["tables"]) is not list or len(payload["tables"]) != len(volume["tables"]):
        _fail("renderer 11 压缩行表片数量无效")
    for table, part in zip(payload["tables"], volume["tables"]):
        _fields(table, {"key", "rowCount", "rowDigest", "rowsNdjsonBytes",
            "rowsGzipBytes", "rowsGzipSha256"})
        _equal(table["key"], part["fragmentKey"])
        _equal(table["rowCount"], part["rowLimit"])
        _equal(table["rowDigest"], part["rowDigest"])
        _sha(table["rowsGzipSha256"])
        _integer(table["rowsNdjsonBytes"], 0, MAX_FILE_BYTES)
        _integer(table["rowsGzipBytes"], 1, MAX_FILE_BYTES)
        if table["rowCount"] == 0:
            _equal(table["rowsNdjsonBytes"], 0)
            _equal(table["rowDigest"], hashlib.sha256(b"").hexdigest())
        elif table["rowsNdjsonBytes"] < table["rowCount"] * 3:
            _fail("renderer 11 压缩行原文字节不足")


def slim_proof_v11(value):
    """Pure manifest-bound v11 sidecar, never a source or publish authority."""
    _equal(value["rendererVersion"], 11)
    _sha(value[BUDGET_KEY]["proofDigest"])
    payloads, files = [], []
    for volume in value["volumes"]:
        _html_payload_v11(volume["htmlPayload"], volume)
        payloads.append(volume["htmlPayload"])
        files.append({"volumeIndex": volume["volumeIndex"],
            "htmlSha256": _sha(volume["files"]["html"]["sha256"]),
            "htmlBytes": _integer(volume["files"]["html"]["bytes"], 1, MAX_FILE_BYTES),
            "xlsxSha256": _sha(volume["files"]["xlsx"]["sha256"]),
            "xlsxBytes": _integer(volume["files"]["xlsx"]["bytes"], 1, MAX_FILE_BYTES)})
    base = {"schemaVersion": "business-promotion-budget-v11-slim-proof-v1",
        "rendererVersion": 11, "sourceBudgetProofDigest": value[BUDGET_KEY]["proofDigest"],
        "htmlPayloadVersion": 2,
        "browserRequirements": SLIM_BROWSER_REQUIREMENTS,
        "volumePayloadDigest": digest(payloads), "fileBindingsDigest": digest(files),
        "volumeCount": len(payloads), "candidateOnly": True,
        "publicationStatus": "unpublished"}
    return {**base, "proofDigest": digest(base)}


def trial_proof(value, manifest):
    """Validate the v9 approval/source/table fence; no publication authority."""
    _fields(value, {"schemaVersion", "rendererVersion", "reportId", "contentDtoDigest",
        "humanReviewDigest", "promotionFileProofDigest", "sealedSourcesDigest",
        "sourceDescriptorDigest", "tableSchemaDigest", "actionTableKey", "actionRowCount", "actionRowDigest",
        "scopeTableKeys", "promotionTableKeys", "budgetDelivered", "proofDigest"})
    _equal(value["schemaVersion"], "business-promotion-trial-file-proof-v2")
    _equal(value["rendererVersion"], 9)
    _equal(value["reportId"], manifest["reportId"])
    _equal(value["contentDtoDigest"], manifest[PROMOTION_KEY]["contentDtoDigest"])
    _equal(value["humanReviewDigest"], manifest[PROMOTION_KEY]["humanReviewDigest"])
    _equal(value["promotionFileProofDigest"], manifest[PROMOTION_KEY]["proofDigest"])
    _sha(value["sealedSourcesDigest"])
    _equal(value["sourceDescriptorDigest"], manifest["sourceDescriptorDigest"])
    _equal(value["tableSchemaDigest"], manifest["tableSchemaDigest"])
    _sha(value["tableSchemaDigest"])
    _equal(value["actionTableKey"], "promotion-approved-actions-v1")
    _equal(value["scopeTableKeys"], ["promotion-trial-source-scope", "promotion-trial-boundaries"])
    _equal(value["promotionTableKeys"], ["promotion-keyword_sku", "promotion-keyword_sku_context"])
    _equal(value["budgetDelivered"], False)
    _sha(value["actionRowDigest"])
    keys = [item["key"] for item in manifest["tables"]]
    _equal(keys[-2:], value["promotionTableKeys"])
    for key in [value["actionTableKey"], *value["scopeTableKeys"]]:
        _equal(keys.count(key), 1)
    action = next(item for item in manifest["tables"] if item["key"] == value["actionTableKey"])
    _equal(action["rowCount"], _integer(value["actionRowCount"], 0, 1_000_000))
    _equal(action["rowDigest"], value["actionRowDigest"])
    _equal(value["proofDigest"], digest({key: child for key, child in value.items() if key != "proofDigest"}))
    for volume in manifest["volumes"]:
        _equal(volume["nativeBudgetSheets"], 0)
        _equal(volume["offlineBudgetEnabled"], False)
    if "budgetPlanDigest" in manifest:
        _fail("试用报告不能宣称预算工作表已交付")


def budget_candidate_proof(value, manifest):
    """Check v10's bounded lineage and exact source-row hooks, not DB authority."""
    trial = manifest[TRIAL_KEY]
    _fields(trial, {"schemaVersion", "rendererVersion", "reportId", "contentDtoDigest",
        "humanReviewDigest", "promotionFileProofDigest", "sealedSourcesDigest",
        "sourceDescriptorDigest", "tableSchemaDigest", "actionTableKey", "actionRowCount",
        "actionRowDigest", "scopeTableKeys", "promotionTableKeys", "budgetDelivered", "proofDigest"})
    _equal(trial["schemaVersion"], "business-promotion-trial-file-proof-v2")
    _equal(trial["rendererVersion"], 9)
    _equal(trial["reportId"], manifest["reportId"])
    _equal(trial["budgetDelivered"], False)
    _equal(trial["contentDtoDigest"], manifest[PROMOTION_KEY]["contentDtoDigest"])
    _equal(trial["humanReviewDigest"], manifest[PROMOTION_KEY]["humanReviewDigest"])
    _equal(trial["promotionFileProofDigest"], manifest[PROMOTION_KEY]["proofDigest"])
    for key in ("sourceDescriptorDigest", "tableSchemaDigest", "sealedSourcesDigest",
            "actionRowDigest", "proofDigest"):
        _sha(trial[key])
    _equal(trial["proofDigest"], digest({key: child for key, child in trial.items()
        if key != "proofDigest"}))
    _equal(trial["actionTableKey"], "promotion-approved-actions-v1")
    _equal(trial["scopeTableKeys"], ["promotion-trial-source-scope", "promotion-trial-boundaries"])
    _equal(trial["promotionTableKeys"], ["promotion-keyword_sku", "promotion-keyword_sku_context"])
    tables = {item["key"]: item for item in manifest["tables"]}
    _equal([item["key"] for item in manifest["tables"][-2:]], trial["promotionTableKeys"])
    if trial["actionTableKey"] not in tables:
        _fail("推广行动表缺失")
    _equal(tables[trial["actionTableKey"]]["rowCount"],
        _integer(trial["actionRowCount"], 0, 1_000_000))
    _equal(tables[trial["actionTableKey"]]["rowDigest"], trial["actionRowDigest"])
    for key in trial["scopeTableKeys"]:
        if key not in tables: _fail("试用来源与边界表缺失")

    _fields(value, {"schemaVersion", "rendererVersion", "reportId", "status",
        "promotionTrialProofDigest", "approvedContentDigest", "humanReviewDigest",
        "sealedDigest", "budgetBindingDigest", "budgetReferenceDigest",
        "budgetResultDigest", "budgetPlanDigest", "offlinePayloadDigest",
        "nativeModelProofDigest", "excelFormulaVersion", "tableSchemaDigest",
        "tableRowsDigest", "tableKeys", "tableRowCounts", "tableRowDigests",
        "nativeBudgetSheets", "offlineBudgetEnabled", "editableAllocation",
        "candidateOnly", "proofDigest"})
    _equal(value["schemaVersion"], "business-promotion-budget-renderer10-candidate-v1")
    _equal(value["rendererVersion"], 10)
    _equal(value["reportId"], manifest["reportId"])
    _equal(value["promotionTrialProofDigest"], trial["proofDigest"])
    _equal(value["approvedContentDigest"], trial["contentDtoDigest"])
    _equal(value["humanReviewDigest"], trial["humanReviewDigest"])
    _equal(value["sealedDigest"], manifest["evidenceDigest"])
    _equal(value["candidateOnly"], True)
    for key in ("promotionTrialProofDigest", "approvedContentDigest",
            "humanReviewDigest", "sealedDigest", "tableSchemaDigest",
            "tableRowsDigest", "proofDigest"):
        _sha(value[key])
    _sha(manifest["tableSchemaDigest"])
    _equal(value["proofDigest"], digest({key: child for key, child in value.items()
        if key != "proofDigest"}))
    present = value["status"] == "reconciled_fixed_budget_candidate"
    if not present and value["status"] != "missing_fixed_budget":
        _fail("预算候选状态无效")
    expected_keys = (["promotion-budget-allocation-v1", "promotion-budget-scenarios-v1",
        "promotion-budget-scenario-summary-v1"] if present else ["promotion-budget-gap-v1"])
    _equal(value["tableKeys"], expected_keys)
    count = len(expected_keys)
    if (type(value["tableRowCounts"]) is not list or len(value["tableRowCounts"]) != count
            or type(value["tableRowDigests"]) is not list or len(value["tableRowDigests"]) != count):
        _fail("预算候选完整表证明数量无效")
    _equal([item["key"] for item in manifest["tables"][-count-2:-2]], expected_keys)
    for key, rows, sha in zip(expected_keys, value["tableRowCounts"], value["tableRowDigests"]):
        _sha(sha)
        _equal(tables[key]["rowCount"], _integer(rows, 0, 1_000_000))
        _equal(tables[key]["rowDigest"], sha)
    _equal(value["nativeBudgetSheets"], 3 if present else 0)
    _equal(value["offlineBudgetEnabled"], present)
    _equal(value["editableAllocation"], present)
    _equal(manifest["volumes"][0]["nativeBudgetSheets"], value["nativeBudgetSheets"])
    _equal(manifest["volumes"][0]["offlineBudgetEnabled"], present)
    if present:
        _equal(value["budgetPlanDigest"], manifest["budgetPlanDigest"])
        _equal(value["excelFormulaVersion"], 2)
        for key in ("budgetPlanDigest", "budgetBindingDigest", "budgetReferenceDigest",
                "budgetResultDigest", "offlinePayloadDigest", "nativeModelProofDigest"):
            _sha(value[key])
        from . import budget_excel
        model = manifest["volumes"][0].get("budgetCalculator")
        if type(model) is not dict:
            _fail("原生预算计算证明缺失")
        _equal(digest({**model, "sheets": list(budget_excel.TITLES)}),
            value["nativeModelProofDigest"])
    else:
        if "budgetPlanDigest" in manifest:
            _fail("无固定预算候选不得携带预算计划摘要")
        for key in ("budgetPlanDigest", "budgetBindingDigest", "budgetReferenceDigest",
                "budgetResultDigest", "offlinePayloadDigest", "nativeModelProofDigest",
                "excelFormulaVersion"):
            _equal(value[key], None)


def promotion_proof(value, report_id):
    """Bound shape only; the owning writer must compare actual content/materials."""
    _fields(value, {"schemaVersion", "rendererVersion", "reportId", "executionProfile",
        "contentDtoDigest", "contentBindingDigest", "contentDigest", "snapshotDigest",
        "workflowInputDigest", "ledgerDigest", "humanReviewDigest", "screeningRootDigest",
        "contextDigest", "promotionSelector", "algorithmVersion", "materialManifestDigest",
        "materialReportBindingDigest", "tables", "rowCount", "ndjsonBytes",
        "tableExpensesAreAdditive", "requiredLimitations", "authorityVerified", "registered",
        "proofDigest"})
    _equal(value["schemaVersion"], "business-promotion-file-proof-v1")
    _equal(value["rendererVersion"], 7)
    _equal(value["reportId"], report_id)
    _equal(value["executionProfile"], "business-agent-screening-promotion-reference-v1")
    _equal(value["algorithmVersion"], "promotion-keyword-promoted-sku-v1")
    for key in ("contentDtoDigest", "contentBindingDigest", "contentDigest", "snapshotDigest",
                "workflowInputDigest", "ledgerDigest", "humanReviewDigest", "screeningRootDigest",
                "contextDigest", "materialManifestDigest", "materialReportBindingDigest", "proofDigest"):
        _sha(value[key])
    _equal(value["authorityVerified"], False)
    _equal(value["registered"], False)
    _equal(value["tableExpensesAreAdditive"], False)
    selector = value["promotionSelector"]
    _fields(selector, {"sourceKey", "views"}, {"baselineKey"})
    _equal(selector["views"], ["keyword_sku", "keyword_sku_context"])
    if type(selector["sourceKey"]) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", selector["sourceKey"]):
        _fail("词货文件来源身份无效")
    if "baselineKey" in selector and (type(selector["baselineKey"]) is not str
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", selector["baselineKey"])
            or selector["baselineKey"] == selector["sourceKey"]):
        _fail("词货文件基期身份无效")
    if type(value["tables"]) is not list or len(value["tables"]) != 2:
        _fail("词货双视图证明缺失")
    rows = size = 0
    for view, table in zip(("keyword_sku", "keyword_sku_context"), value["tables"]):
        _fields(table, {"view", "tableBindingDigest", "rowCount", "pageCount", "ndjsonBytes",
            "ndjsonSha256", "missingPromotedSkuGroups", "unqualifiedIdentityGroups", "spendTotals"})
        _equal(table["view"], view)
        _sha(table["tableBindingDigest"]); _sha(table["ndjsonSha256"])
        rows += _integer(table["rowCount"], 0, 250_000)
        size += _integer(table["ndjsonBytes"], 0, 64*1024*1024)
        _integer(table["pageCount"], 1, 20_000)
        _integer(table["missingPromotedSkuGroups"], 0, table["rowCount"])
        _integer(table["unqualifiedIdentityGroups"], table["missingPromotedSkuGroups"], table["rowCount"])
        _fields(table["spendTotals"], {"current", "baseline"})
        for side in ("current", "baseline"):
            _fields(table["spendTotals"][side], {"value", "presentGroups", "missingFactRows"})
    _equal(value["rowCount"], rows)
    _equal(value["ndjsonBytes"], size)
    if rows > 250_000 or size > 64*1024*1024:
        _fail("词货材料超过固定容量")
    for side in ("current", "baseline"):
        left, right = [table["spendTotals"][side] for table in value["tables"]]
        _equal((left["value"], left["missingFactRows"]),
               (right["value"], right["missingFactRows"]))
    if (type(value["requiredLimitations"]) is not list
            or len(value["requiredLimitations"]) < 4
            or not any(type(item) is str and "不可相加" in item for item in value["requiredLimitations"])):
        _fail("词货文件必要口径说明缺失")
    _equal(value["proofDigest"], digest({key: child for key, child in value.items() if key != "proofDigest"}))
    return value


def screening_fields(value, report_id):
    """Shape only; owning publication separately compares actual stored roots."""
    if not SCREENING_KEYS & value.keys():
        return {}
    if not SCREENING_KEYS <= value.keys():
        _fail("筛查完整清单绑定缺失")
    ref = value["screeningRef"]
    _fields(ref, {"schemaVersion", "id", "reportId", "bindingDigest", "selectionPlanDigest", "resultDigest", "contentRootDigest", "manifestDigest"})
    _equal(ref["schemaVersion"], "business-screening-storage-reference-v1")
    _equal(ref["reportId"], report_id)
    if type(ref["id"]) is not str or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", ref["id"]):
        _fail("筛查持久身份无效")
    for key in ("bindingDigest", "selectionPlanDigest", "resultDigest", "contentRootDigest", "manifestDigest"):
        _sha(ref[key])
    _equal(value["screeningPackagePolicy"], "screening-role-package-policy-v1")
    roles = {"commerce", "promotion", "market_b2b", "independent_review", "report"}
    _fields(value["screeningPackageDigests"], roles)
    for item in value["screeningPackageDigests"].values():
        _sha(item)
    return {key:value[key] for key in SCREENING_KEYS}


def _full(value, *, max_tables, max_rows, max_volumes, renderer_version):
    _renderer(renderer_version)
    mapping_keys = {"mappingPlanDigest", "mappingAlgorithmVersion", "mappedTableAlgorithmVersion"}
    _fields(value, FULL_FIELDS | ({PROMOTION_KEY} if renderer_version in (7, 9, 10, 11) else set()) |
            ({TRIAL_KEY, "tableSchemaDigest"} if renderer_version == 9 else set()) |
            ({TRIAL_KEY, BUDGET_KEY, "tableSchemaDigest"} if renderer_version in (10, 11) else set()) |
            ({SLIM_KEY} if renderer_version == 11 else set()),
            {"budgetPlanDigest"} | mapping_keys | SCREENING_KEYS)
    if renderer_version in (7, 9, 10, 11):
        promotion_proof(value[PROMOTION_KEY], value["reportId"])
    screening_fields(value, value["reportId"])
    if mapping_keys & value.keys():
        if not mapping_keys <= value.keys():
            _fail("商品关联完整清单绑定缺失")
        _sha(value["mappingPlanDigest"])
        _equal(value["mappingAlgorithmVersion"], "exact-product-partition-v1")
        _equal(value["mappedTableAlgorithmVersion"], "business-mapped-results-v1")
    _equal(value["schemaVersion"], "business-volume-files-v1")
    _equal(value["status"], "complete")
    _equal(value["rendererVersion"], renderer_version)
    count = _integer(value["volumeCount"], 1, 100)
    for key in ("planDigest", "sourceDescriptorDigest", "manifestDigest", "evidenceDigest"):
        _sha(value[key])
    _fields(value["byteCapacity"], {"verified", "maxFileBytes", "dynamicByteSplitting"})
    _equal(value["byteCapacity"]["verified"], True)
    _equal(value["byteCapacity"]["dynamicByteSplitting"], False)
    max_file = _integer(value["byteCapacity"]["maxFileBytes"], 1, MAX_FILE_BYTES)
    if type(value["tables"]) is not list or not 1 <= len(value["tables"]) <= 12000:
        _fail("完整来源表证明数量无效")
    descriptors = []
    for source in value["tables"]:
        _fields(source, DESCRIPTOR_FIELDS | {"rowDigest"})
        _sha(source["rowDigest"])
        descriptors.append({key: source[key] for key in DESCRIPTOR_FIELDS})
    if type(value["volumes"]) is not list or len(value["volumes"]) != count:
        _fail("完整卷证明缺失")
    first = value["volumes"][0]
    if type(first) is not dict or type(first.get("nativeBudgetSheets")) is not int or first["nativeBudgetSheets"] not in (0, 3):
        _fail("首卷预算预留无效")
    request = {"schemaVersion": volume_plan.REQUEST_SCHEMA, "reportId": value["reportId"], "evidenceDigest": value["evidenceDigest"],
               "rendererVersion": renderer_version, "tables": descriptors}
    plan = volume_plan.build(request, max_tables=max_tables, max_rows=max_rows, max_volumes=max_volumes, native_budget_sheets=first["nativeBudgetSheets"])
    for key in ("planDigest", "sourceDescriptorDigest", "volumeCount", "sourceTableCount", "fragmentCount", "totalRows"):
        _equal(value[key], plan[key])
    has_budget = first["nativeBudgetSheets"] == 3 or first.get("offlineBudgetEnabled") is True
    if has_budget != ("budgetPlanDigest" in value):
        _fail("预算存在性与完整清单不一致")
    if has_budget:
        _sha(value["budgetPlanDigest"])
    source_by_key = {source["key"]: source for source in value["tables"]}
    files = []
    for actual, expected in zip(value["volumes"], plan["volumes"]):
        _fields(actual, {"volumeIndex", "volumeCount", "kind", "nativeBudgetSheets", "rowCount", "offlineBudgetEnabled", "tables", "files"},
            {"budgetCalculator", "htmlPayload"} if renderer_version == 11 else {"budgetCalculator"})
        for key in ("volumeIndex", "volumeCount", "kind", "nativeBudgetSheets"):
            _equal(actual[key], expected[key])
        if type(actual["offlineBudgetEnabled"]) is not bool or actual["volumeIndex"] != 1 and actual["offlineBudgetEnabled"]:
            _fail("离线预算只能出现在首卷")
        if expected["kind"] == "budget_only" or ("budgetCalculator" in actual) != (expected["nativeBudgetSheets"] == 3):
            _fail("原生预算证明与分卷计划不一致")
        if "budgetCalculator" in actual:
            _budget(actual["budgetCalculator"], value["reportId"], value["budgetPlanDigest"])
        if type(actual["tables"]) is not list or len(actual["tables"]) != len(expected["tables"]):
            _fail("分卷表片段数量不一致")
        sheets = set()
        for part, planned in zip(actual["tables"], expected["tables"]):
            _fields(part, PART_FIELDS | {"sheet", "rowDigest", "precisionTextCells"})
            _equal({key: part[key] for key in PART_FIELDS}, planned)
            _sha(part["rowDigest"])
            sheet = part["sheet"]
            if type(sheet) is not str or not 1 <= len(sheet) <= 31 or sheet.casefold() in sheets:
                _fail("分卷工作表名称或顺序无效")
            sheets.add(sheet.casefold())
            _integer(part["precisionTextCells"], 0, part["rowLimit"] * part["columnCount"])
            if part["fragmentCount"] == 1:
                _equal(part["rowDigest"], source_by_key[part["key"]]["rowDigest"])
            if part["rowLimit"] == 0:
                _equal(part["rowDigest"], hashlib.sha256(b"").hexdigest())
        _equal(actual["rowCount"], sum(part["rowLimit"] for part in expected["tables"]))
        if renderer_version == 11:
            _html_payload_v11(actual["htmlPayload"], actual)
        _fields(actual["files"], {"html", "xlsx"})
        for format in ("html", "xlsx"):
            proof = actual["files"][format]
            _fields(proof, {"filename", "bytes", "sha256"})
            _equal(proof["filename"], f"{value['reportId']}-volume-{actual['volumeIndex']:03}-of-{count:03}.{format}")
            size = _integer(proof["bytes"], 1, max_file)
            files.append({"volumeIndex": actual["volumeIndex"], "format": format, "bytes": size, "sha256": _sha(proof["sha256"]),
                          "chunkCount": (size + CHUNK_BYTES - 1) // CHUNK_BYTES})
    if renderer_version == 9:
        trial_proof(value[TRIAL_KEY], value)
    if renderer_version in (10, 11):
        budget_candidate_proof(value[BUDGET_KEY], value)
    if renderer_version == 11:
        _equal(value[SLIM_KEY], slim_proof_v11(value))
    _equal(value["manifestDigest"], digest({key: child for key, child in value.items() if key != "manifestDigest"}))
    return files


def make(full_manifest, *, binding_digest, attempt, draft, max_tables=120, max_rows=1_000_000, max_volumes=100, renderer_version=4):
    """Return (compact receipt, exact canonical full-JSON artifact bytes).

    Optional capacity arguments are trusted policy and can only tighten defaults;
    they support synthetic splitting. Production and recovery use defaults.
    """
    full = _snapshot(full_manifest, MAX_MANIFEST_BYTES)
    files = _full(full, max_tables=max_tables, max_rows=max_rows, max_volumes=max_volumes, renderer_version=renderer_version)
    data = canonical(full).encode("utf-8")
    compact = {"schemaVersion": SCHEMA_VERSION, "rendererVersion": renderer_version, "bindingDigest": binding_digest, "attempt": attempt, "draft": draft,
               "volumeCount": full["volumeCount"], "files": files,
               "manifestFile": {"volumeIndex": 0, "format": "json", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                                "chunkCount": (len(data) + CHUNK_BYTES - 1) // CHUNK_BYTES}}
    return validate(compact, binding_digest=binding_digest, attempt=attempt, draft=draft, renderer_version=renderer_version), data


def verify_full(compact, manifest_bytes, *, binding_digest, attempt, draft, report_id, evidence_digest, plan_digest=None,
                max_tables=120, max_rows=1_000_000, max_volumes=100, renderer_version=4):
    """Cross-check the stored JSON artifact with compact and trusted bindings.

    The deterministic plan is always rebuilt, including when plan_digest is not
    separately available during recovery. A SHA alone is not source authority.
    This does not re-read facts or concatenate fragment digests into source SHA.
    """
    root = validate(compact, binding_digest=binding_digest, attempt=attempt, draft=draft, renderer_version=renderer_version)
    if type(manifest_bytes) is not bytes or not 1 <= len(manifest_bytes) <= MAX_MANIFEST_BYTES:
        _fail("完整多卷清单须为有界UTF-8字节")
    _equal(len(manifest_bytes), root["manifestFile"]["bytes"])
    _equal(hashlib.sha256(manifest_bytes).hexdigest(), root["manifestFile"]["sha256"])

    def pairs(items):
        result = {}
        for key, child in items:
            if key in result:
                _fail("完整多卷清单包含重复JSON键")
            result[key] = child
        return result

    try:
        parsed = json.loads(manifest_bytes.decode("utf-8"), object_pairs_hook=pairs)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise AnalysisContractError("完整多卷清单JSON无效") from error
    full = _snapshot(parsed, MAX_MANIFEST_BYTES)
    files = _full(full, max_tables=max_tables, max_rows=max_rows, max_volumes=max_volumes, renderer_version=renderer_version)
    _equal(full["reportId"], report_id)
    _equal(full["evidenceDigest"], evidence_digest)
    if plan_digest is not None:
        _sha(plan_digest)
        _equal(full["planDigest"], plan_digest)
    _equal(full["volumeCount"], root["volumeCount"])
    _equal(files, root["files"])
    if canonical(full).encode("utf-8") != manifest_bytes:
        _fail("完整多卷清单不是规范JSON字节")
    return full
