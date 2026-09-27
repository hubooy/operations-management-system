"""Pure, versioned completed-content snapshot for future renderer 7.

The caller supplies already verified owning results. This module checks a
bounded detached shape and cross-field consistency; hashes are change fences,
never evidence of database authorization, Agent execution or human approval.
"""
from dataclasses import dataclass
import json
import math
import re

from business_analysis.contracts import MAX_SAFE_INTEGER, canonical, digest


SCHEMA = "business-promotion-completed-content-v1"
BINDING_SCHEMA = "business-promotion-completed-binding-v1"
SCREENING_SCHEMA = "business-promotion-completed-screening-v1"
PROFILE = "business-agent-screening-promotion-reference-v1"
ALGORITHM = "promotion-keyword-promoted-sku-v1"
ROLES = ("commerce", "promotion", "market_b2b", "independent_review", "report")
SPECIALISTS = ROLES[:3]
VIEWS = ("keyword_sku", "keyword_sku_context")
SECTIONS = ("范围与数据完整性", "店铺与商品诊断", "推广与搜索诊断", "市场与B端机会", "调整规划与观察指标")
MAX_BYTES = 4 * 1024 * 1024
MAX_NODES = 250_000
LIMITATIONS = (
    "两张词货表是同一推广事实的不同分组，费用不可相加。",
    "缺推广SKU的分组保留费用，但不能作为具体商品操作对象。",
    "平台归因成交不等于ERP净销售、利润或因果增量。",
    "来源日期覆盖不证明逐词逐SKU每天完整或归因最终成熟。",
)
_AGGREGATION_POLICY = {"views": list(VIEWS), "sameFactsDifferentGroups": True,
    "viewSpendAdditive": False, "missingSkuActionable": False,
    "reportedGmvIsErpNetSales": False}
_AGGREGATION_POLICY_RAW = canonical(_AGGREGATION_POLICY)
_TOKEN = object()
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")


class ContentContractError(ValueError):
    pass


def _require(condition, message="词货完整内容合同无效"):
    if not condition:
        raise ContentContractError(message)


def _fields(value, required, optional=()):
    _require(type(value) is dict and set(required) <= set(value)
             and not set(value)-set(required)-set(optional), "词货完整内容字段集合无效")
    return value


def _text(value, maximum=10000, *, nonempty=True):
    _require(type(value) is str and len(value) <= maximum
             and (not nonempty or bool(value.strip())), "词货完整内容文本无效")
    try:
        _require(len(value.encode("utf-8")) <= maximum * 4)
    except UnicodeError as error:
        raise ContentContractError("词货完整内容文本编码无效") from error
    return value


def _sha(value):
    _require(type(value) is str and _SHA.fullmatch(value) is not None, "词货完整内容摘要无效")
    return value


def _id(value):
    _require(type(value) is str and _ID.fullmatch(value) is not None, "词货完整内容身份无效")
    return value


def _detached(value, maximum=MAX_BYTES):
    nodes = MAX_NODES

    def visit(item, depth):
        nonlocal nodes
        nodes -= 1
        _require(nodes >= 0 and depth <= 32, "词货完整内容嵌套或节点超限")
        kind = type(item)
        if kind is dict:
            _require(len(item) <= 10000 and all(type(key) is str and 0 < len(key) <= 160 for key in item))
            for key, child in item.items():
                _text(key, 160)
                visit(child, depth + 1)
        elif kind in (list, tuple):
            _require(len(item) <= 100000, "词货完整内容数组超限")
            for child in item:
                visit(child, depth + 1)
        elif kind is str:
            _text(item, 65536, nonempty=False)
        elif kind is int:
            _require(abs(item) <= MAX_SAFE_INTEGER, "词货完整内容整数超出安全范围")
        elif kind is float:
            _require(math.isfinite(item), "词货完整内容数字非有限")
        else:
            _require(kind in (bool, type(None)), "词货完整内容包含不支持的类型")

    visit(value, 0)
    try:
        raw = canonical(value)
        _require(len(raw.encode("utf-8")) <= maximum, "词货完整内容超过容量")
        return json.loads(raw)
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise ContentContractError("词货完整内容无法规范编码") from error


def _selector(value):
    _fields(value, {"sourceKey", "views"}, {"baselineKey"})
    _id(value["sourceKey"])
    _require(type(value["views"]) is list and value["views"] == list(VIEWS), "词货两视图选择变化")
    if "baselineKey" in value:
        _id(value["baselineKey"])
        _require(value["baselineKey"] != value["sourceKey"], "词货基期不能等于当前来源")


def _binding(value):
    _fields(value, {"schemaVersion", "executionProfile", "reportId", "workflowId", "evidenceRunId",
        "evidenceVersion", "sealedDigest", "snapshotDigest", "workflowInputDigest", "ledgerDigest",
        "screeningId", "screeningRootDigest", "ownerEmail", "scope", "promotionSelector",
        "contextDigest", "promotionCatalogDigest", "promotionAlgorithmVersion", "jobs", "humanReview"},
        {"budgetPlanDigest"})
    _require(value["schemaVersion"] == BINDING_SCHEMA and value["executionProfile"] == PROFILE)
    for key in ("reportId", "workflowId", "evidenceRunId", "screeningId"):
        _id(value[key])
    _require(type(value["evidenceVersion"]) is int and 1 <= value["evidenceVersion"] <= MAX_SAFE_INTEGER)
    for key in ("sealedDigest", "snapshotDigest", "workflowInputDigest", "ledgerDigest",
                "screeningRootDigest", "contextDigest", "promotionCatalogDigest"):
        _sha(value[key])
    _require(value["scope"] is None, "词货报告仅允许无范围管理员")
    owner = _text(value["ownerEmail"], 320)
    _require(owner == owner.lower() and owner == owner.strip() and "@" in owner)
    _selector(value["promotionSelector"])
    _require(value["promotionAlgorithmVersion"] == ALGORITHM)
    _fields(value["jobs"], set(ROLES))
    job_ids = []
    for role in ROLES:
        item = _fields(value["jobs"][role], {"jobId", "outputDigest", "readProofDigest"})
        job_ids.append(_id(item["jobId"]))
        _sha(item["outputDigest"]); _sha(item["readProofDigest"])
    _require(len(set(job_ids)) == len(ROLES), "五个角色必须绑定不同的实际任务")
    review = _fields(value["humanReview"], {"status", "reviewDigest"})
    _require(review["status"] in ("pending", "approved"))
    _require((review["status"] == "pending" and review["reviewDigest"] is None)
             or (review["status"] == "approved" and _sha(review["reviewDigest"])))
    if "budgetPlanDigest" in value:
        _sha(value["budgetPlanDigest"])


def _diagnosis(value):
    _require(type(value) is dict and type(value.get("summary")) is str
             and type(value.get("findings")) is list)
    _text(value["summary"], 10000)
    _require(len(value["findings"]) <= 1000 and all(type(item) is dict for item in value["findings"]))


def _content(value, binding):
    _fields(value, {"sections", "diagnosis", "professionalAnalyses", "independentReview", "screening"}, {"budget"})
    sections = value["sections"]
    _require(type(sections) is list and len(sections) == len(SECTIONS))
    for row, title in zip(sections, SECTIONS):
        _fields(row, {"title", "body"})
        _require(row["title"] == title)
        _text(row["body"], 10000)
    _diagnosis(value["diagnosis"])
    _fields(value["professionalAnalyses"], set(SPECIALISTS))
    for role in SPECIALISTS:
        _diagnosis(value["professionalAnalyses"][role])
    review = _fields(value["independentReview"], {"approved", "conflicts", "limitations"})
    _require(review["approved"] is True and review["conflicts"] == [])
    _require(type(review["limitations"]) is list and len(review["limitations"]) <= 20)
    for item in review["limitations"]: _text(item, 1000)
    screening = _fields(value["screening"], {"schemaVersion", "coverage", "readProofs",
        "limitations", "candidateDisclosure"})
    _require(screening["schemaVersion"] == SCREENING_SCHEMA)
    _require(type(screening["coverage"]) is list and len(screening["coverage"]) <= 20000)
    _fields(screening["readProofs"], set(ROLES))
    for role in ROLES:
        proof = screening["readProofs"][role]
        _require(type(proof) is dict and proof.get("role") == role
                 and proof.get("jobId") == binding["jobs"][role]["jobId"])
        _require(digest(proof) == binding["jobs"][role]["readProofDigest"],
                 "角色读取证明与固定任务不一致")
    _require(type(screening["limitations"]) is list and len(screening["limitations"]) <= 24)
    for item in screening["limitations"]: _text(item, 1000)
    _require(len(set(screening["limitations"])) == len(screening["limitations"]))
    _require(set(LIMITATIONS) <= set(screening["limitations"]), "必要的词货口径与身份缺口说明缺失")
    disclosure = _fields(screening["candidateDisclosure"],
        {"fullCandidatesIncluded", "crossPartitionAmountsAdditive"})
    _require(disclosure == {"fullCandidatesIncluded": False, "crossPartitionAmountsAdditive": False})
    _require(("budget" in value) == ("budgetPlanDigest" in binding), "预算存在性与报告绑定不一致")
    if "budget" in value:
        _require(type(value["budget"]) is dict and value["budget"].get("planDigest") == binding["budgetPlanDigest"])


def check(value):
    """Validate a detached DTO; a matching digest does not confer authority."""
    item = _detached(value)
    _fields(item, {"schemaVersion", "binding", "bindingDigest", "content", "contentDigest",
        "aggregationPolicy", "authorityVerified", "registered", "dtoDigest"})
    _require(item["schemaVersion"] == SCHEMA and item["authorityVerified"] is False
             and item["registered"] is False)
    _binding(item["binding"])
    _content(item["content"], item["binding"])
    _require(canonical(item["aggregationPolicy"]) == _AGGREGATION_POLICY_RAW,
             "两种词货视图不可作为两份费用相加")
    _require(item["bindingDigest"] == digest(item["binding"])
             and item["contentDigest"] == digest(item["content"])
             and item["dtoDigest"] == digest({k: v for k, v in item.items() if k != "dtoDigest"}),
             "词货完整内容摘要不匹配")
    return item


@dataclass(frozen=True, slots=True, init=False)
class PreparedContent:
    _raw: str
    _digest: str

    def __init__(self, token, value):
        _require(token is _TOKEN, "词货完成内容只能由内部准备构造")
        raw = canonical(check(value))
        object.__setattr__(self, "_raw", raw)
        object.__setattr__(self, "_digest", digest(raw))

    @property
    def value(self):
        _require(digest(self._raw) == self._digest, "词货完成内容准备对象损坏")
        return check(json.loads(self._raw))


def prepare(binding, content):
    """Freeze already-verified inputs without granting database authority."""
    detached_binding = _detached(binding, 128 * 1024)
    detached_content = _detached(content)
    root = {"schemaVersion": SCHEMA, "binding": detached_binding,
        "bindingDigest": digest(detached_binding), "content": detached_content,
        "contentDigest": digest(detached_content), "aggregationPolicy": json.loads(_AGGREGATION_POLICY_RAW),
        "authorityVerified": False, "registered": False}
    root["dtoDigest"] = digest(root)
    return PreparedContent(_TOKEN, root)
