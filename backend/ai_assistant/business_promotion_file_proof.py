"""Pure renderer-7 full-manifest fragment over completed content and materials.

Input digests are change fences. Neither this fragment nor its private wrapper
authorizes a report, verifies raw NDJSON bytes, registers a producer, or makes
an AiBusinessFileRun ready. The future owning writer must do those checks.
"""
from dataclasses import dataclass
import hashlib
import json

from business_analysis.contracts import MAX_SAFE_INTEGER, canonical, digest
from . import business_promotion_content_contract as completed


SCHEMA = "business-promotion-file-proof-v1"
MATERIAL_SCHEMA = "business-promotion-export-materials-v1"
BINDING_SCHEMA = "business-promotion-keyword-sku-binding-v1"
MAX_MATERIAL_BYTES = 512 * 1024
MAX_PROOF_BYTES = 64 * 1024
MAX_NDJSON_BYTES = 64 * 1024 * 1024
MAX_ROWS = 250_000
MAX_PAGES = 20_000
_TOKEN = object()


class FileProofError(ValueError):
    pass


def _require(condition, message="词货正式文件证明无效"):
    if not condition:
        raise FileProofError(message)


def _copy(value, maximum):
    try:
        return completed._detached(value, maximum)
    except completed.ContentContractError as error:
        raise FileProofError("词货文件证明输入超限或结构无效") from error


def _fields(value, required):
    _require(type(value) is dict and set(value) == set(required), "词货材料字段集合无效")
    return value


def _sha(value):
    try:
        return completed._sha(value)
    except completed.ContentContractError as error:
        raise FileProofError("词货材料摘要无效") from error


def _number(value, lo, hi):
    _require(type(value) is int and lo <= value <= hi, "词货材料计数或金额无效")
    return value


def _material(manifest, binding):
    _fields(manifest, {"schemaVersion", "reportBinding", "sourceKey", "baselineKey",
        "algorithmVersion", "tables", "rowCount", "ndjsonBytes", "tableExpensesAreAdditive",
        "registeredRenderer", "limitations", "manifestDigest"})
    _require(manifest["schemaVersion"] == MATERIAL_SCHEMA
             and manifest["registeredRenderer"] is False
             and manifest["tableExpensesAreAdditive"] is False,
             "内部材料不能声称已注册或可加总")
    _sha(manifest["manifestDigest"])
    _require(manifest["manifestDigest"] == digest({key: value for key, value in manifest.items()
        if key != "manifestDigest"}), "词货完整材料清单摘要不匹配")
    selector = binding["promotionSelector"]
    _require(manifest["sourceKey"] == selector["sourceKey"]
             and manifest["baselineKey"] == selector.get("baselineKey")
             and manifest["algorithmVersion"] == binding["promotionAlgorithmVersion"],
             "词货材料跨报告来源、基期或算法")
    report = manifest["reportBinding"]
    _require(type(report) is dict and report.get("role") == "admin"
             and report.get("scope") is None)
    for material_key, content_key in (
        ("reportId", "reportId"), ("workflowId", "workflowId"),
        ("ownerEmail", "ownerEmail"), ("evidenceRunId", "evidenceRunId"),
        ("evidenceVersion", "evidenceVersion"), ("sealedDigest", "sealedDigest"),
        ("snapshotDigest", "snapshotDigest"),
        ("workflowInputDigest", "workflowInputDigest"),
        ("executionProfile", "executionProfile"),
    ):
        _require(type(report.get(material_key)) is type(binding[content_key])
                 and report[material_key] == binding[content_key],
                 "词货材料与已完成报告的固定根不一致")
    expected_budget = binding.get("budgetPlanDigest")
    actual_budget = report.get("budgetRef")
    _require((actual_budget is None) == (expected_budget is None))
    if expected_budget is not None:
        _require(type(actual_budget) is dict and actual_budget.get("planDigest") == expected_budget)
    _require(type(manifest["tables"]) is list and len(manifest["tables"]) == len(completed.VIEWS))
    _require(type(manifest["limitations"]) is list and len(manifest["limitations"]) <= 20
             and all(type(item) is str and len(item) <= 1000 for item in manifest["limitations"]))
    _require(any("不可相加" in item for item in manifest["limitations"])
             and any("缺推广SKU" in item for item in manifest["limitations"]),
             "材料缺少同源费用或身份限制")
    rows, size, pages, proofs = 0, 0, 0, []
    totals = []
    for view, table in zip(completed.VIEWS, manifest["tables"]):
        _fields(table, {"view", "binding", "header", "rowCount", "pageCount",
            "ndjsonBytes", "ndjsonSha256", "spendTotals",
            "missingPromotedSkuGroups", "unqualifiedIdentityGroups"})
        _require(table["view"] == view)
        _sha(table["ndjsonSha256"])
        count = _number(table["rowCount"], 0, MAX_ROWS)
        page_count = _number(table["pageCount"], 1, MAX_PAGES)
        byte_count = _number(table["ndjsonBytes"], 0, MAX_NDJSON_BYTES)
        missing_sku = _number(table["missingPromotedSkuGroups"], 0, count)
        unqualified = _number(table["unqualifiedIdentityGroups"], missing_sku, count)
        if count == 0:
            _require(table["ndjsonSha256"] == hashlib.sha256(b"").hexdigest()
                     and byte_count == 0 and page_count == 1)
        else:
            _require(byte_count > 0 and page_count <= count)
        table_binding = _fields(table["binding"], {"schemaVersion", "reportBinding",
            "sourceKey", "baselineKey", "view", "algorithmVersion", "tableBindingDigest"})
        header = table["header"]
        _require(table_binding["schemaVersion"] == BINDING_SCHEMA
                 and table_binding["reportBinding"] == report
                 and table_binding["sourceKey"] == selector["sourceKey"]
                 and table_binding["baselineKey"] == selector.get("baselineKey")
                 and table_binding["view"] == view
                 and table_binding["algorithmVersion"] == binding["promotionAlgorithmVersion"]
                 and type(header) is dict and header.get("schemaVersion") ==
                    "business-promotion-keyword-sku-table-v1"
                 and header.get("view") == view
                 and header.get("algorithmVersion") == binding["promotionAlgorithmVersion"]
                 and header.get("authorityVerified") is False
                 and header.get("total") == count
                 and type(header.get("source")) is dict
                 and header["source"].get("key") == selector["sourceKey"]
                 and (header.get("baselineSource") or {}).get("key") == selector.get("baselineKey"),
                 "词货材料表头不属于固定报告选择")
        table_digest = _sha(table_binding["tableBindingDigest"])
        _require(header.get("tableBindingDigest") == table_digest)
        spends = _fields(table["spendTotals"], {"current", "baseline"})
        for side in ("current", "baseline"):
            cell = _fields(spends[side], {"value", "presentGroups", "missingFactRows"})
            if cell["value"] is not None:
                _number(cell["value"], -MAX_SAFE_INTEGER, MAX_SAFE_INTEGER)
            _number(cell["presentGroups"], 0, count)
            _number(cell["missingFactRows"], 0, MAX_ROWS)
            _require((cell["value"] is None) == (cell["presentGroups"] == 0))
        if selector.get("baselineKey") is None:
            _require(spends["baseline"] == {"value": None, "presentGroups": 0,
                "missingFactRows": 0})
        totals.append(spends)
        rows += count; size += byte_count; pages += page_count
        proofs.append({"view": view, "tableBindingDigest": table_digest,
            "rowCount": count, "pageCount": page_count, "ndjsonBytes": byte_count,
            "ndjsonSha256": table["ndjsonSha256"],
            "missingPromotedSkuGroups": missing_sku,
            "unqualifiedIdentityGroups": unqualified,
            "spendTotals": spends})
    _require(proofs[0]["tableBindingDigest"] != proofs[1]["tableBindingDigest"])
    for side in ("current", "baseline"):
        _require((totals[0][side]["value"], totals[0][side]["missingFactRows"]) ==
                 (totals[1][side]["value"], totals[1][side]["missingFactRows"]),
                 "两种词货分组费用或缺失事实未守恒")
    _require(rows == _number(manifest["rowCount"], 0, MAX_ROWS)
             and size == _number(manifest["ndjsonBytes"], 0, MAX_NDJSON_BYTES)
             and pages <= MAX_PAGES
             and size + len(canonical(manifest).encode("utf-8")) <= MAX_NDJSON_BYTES,
             "词货完整材料容量或行数不符")
    return proofs


def _fragment(content_dto, material_manifest):
    _require(type(content_dto) is completed.PreparedContent,
             "词货正式文件必须使用已冻结的五角色内容对象")
    content = content_dto.value
    binding = content["binding"]
    review = binding["humanReview"]
    _require(review["status"] == "approved" and type(review["reviewDigest"]) is str,
             "正式文件须先具有固定人审回执")
    material = _copy(material_manifest, MAX_MATERIAL_BYTES)
    proofs = _material(material, binding)
    return {"schemaVersion": SCHEMA, "rendererVersion": 7,
        "reportId": binding["reportId"], "executionProfile": binding["executionProfile"],
        "contentDtoDigest": content["dtoDigest"],
        "contentBindingDigest": content["bindingDigest"],
        "contentDigest": content["contentDigest"],
        "snapshotDigest": binding["snapshotDigest"],
        "workflowInputDigest": binding["workflowInputDigest"],
        "ledgerDigest": binding["ledgerDigest"],
        "humanReviewDigest": review["reviewDigest"],
        "screeningRootDigest": binding["screeningRootDigest"],
        "contextDigest": binding["contextDigest"],
        "promotionSelector": binding["promotionSelector"],
        "algorithmVersion": binding["promotionAlgorithmVersion"],
        "materialManifestDigest": material["manifestDigest"],
        "materialReportBindingDigest": digest(material["reportBinding"]),
        "tables": proofs,
        "rowCount": material["rowCount"], "ndjsonBytes": material["ndjsonBytes"],
        "tableExpensesAreAdditive": False,
        "requiredLimitations": list(completed.LIMITATIONS),
        "authorityVerified": False, "registered": False}


def check(value, content_dto, material_manifest):
    """Rebuild from the two inputs; a matching fragment is not authority."""
    actual = _copy(value, MAX_PROOF_BYTES)
    _sha(actual.get("proofDigest") if type(actual) is dict else None)
    expected = _fragment(content_dto, material_manifest)
    expected["proofDigest"] = digest(expected)
    _require(actual == expected, "词货文件证明与五角色内容或完整材料不一致")
    return actual


@dataclass(frozen=True, slots=True, init=False)
class PreparedFileProof:
    _raw: str
    _digest: str

    def __init__(self, token, value):
        _require(token is _TOKEN)
        raw = canonical(value)
        _require(len(raw.encode("utf-8")) <= MAX_PROOF_BYTES,
                 "词货文件证明超过完整清单容量")
        object.__setattr__(self, "_raw", raw)
        object.__setattr__(self, "_digest", digest(raw))

    @property
    def value(self):
        _require(digest(self._raw) == self._digest, "词货文件证明准备对象已变化")
        return json.loads(self._raw)


def prepare(content_dto, material_manifest):
    """Bind two complete inputs for a future owning renderer, never publish."""
    fragment = _fragment(content_dto, material_manifest)
    fragment["proofDigest"] = digest(fragment)
    result = PreparedFileProof(_TOKEN, fragment)
    check(result.value, content_dto, material_manifest)
    return result
