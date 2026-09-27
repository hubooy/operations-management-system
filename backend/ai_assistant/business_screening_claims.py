"""Internal exact retained-candidate numbers; no diagnosis/runtime registration.

Process-local objects are not authorization credentials. Every resolution
reloads current owning metadata and decodes the actual fixed role package.
Neither an index nor a caller-supplied number can replace that evidence.
"""
from dataclasses import dataclass
import json
import re

from business_analysis import screening_package as contract
from business_analysis.contracts import AnalysisContractError, MAX_SAFE_INTEGER
from . import business_screening_packages as packages
from .policy import AiError, canonical, digest, fields

SCHEMA = "business-screening-candidate-value-v1"
MAX_INDEX_BYTES, MAX_RESPONSE_BYTES = 16*1024*1024, 38000
FIELDS = {"value":"current", "baseline":"baseline", "difference":"differences"}
_TOKEN = object()


def _conflict(message="候选引用与固定角色筛查包不一致"):
    raise AiError(message,"conflict",409)


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except (AnalysisContractError, ValueError, TypeError, KeyError, IndexError, UnicodeError, RecursionError) as error:
        raise AiError("候选引用未通过完整核验","conflict",409) from error


def _role(value):
    if type(value) is not str or value not in contract.ROLES:
        raise AiError("候选筛查角色无效")
    return value


def _selected(prepared, role, row, expected=None):
    entries = prepared._packages
    if (type(entries) is not tuple or len(entries) != len(contract.ROLES)
            or any(type(entry) is not tuple or len(entry) != 3 for entry in entries)
            or tuple(entry[0] for entry in entries) != contract.ROLES):
        _conflict()
    _, package, fixed_digest = entries[contract.ROLES.index(role)]
    if (type(package) is not contract.Package or type(fixed_digest) is not str
            or _call(lambda:package.package_digest) != fixed_digest
            or expected is not None and fixed_digest != expected):
        _conflict()
    first = _call(package.page)
    packages._page_binding(first,row,role,fixed_digest)
    return package, fixed_digest


def _index(decoded):
    coverage = decoded["coverage"]
    tables = {r["value"]["tableKey"]:r["value"] for r in coverage if r["kind"] == "table"}
    partitions = {r["value"]["partitionKey"]:r["value"] for r in coverage if r["kind"] == "partition"}
    candidates = {}
    for group in decoded["candidates"]:
        for candidate in group["items"]:
            key = candidate["candidateId"]
            if key in candidates: _conflict("候选身份跨分区重复")
            candidates[key] = {"partitionKey":group["partitionKey"],"candidate":candidate}
    result = {"candidates":candidates,"tables":tables,"partitions":partitions,
        "tableBindings":{r["tableKey"]:r for r in decoded["tableBindings"]},
        "evidenceBinding":{key:decoded["binding"][key] for key in ("reportId","evidenceRunId","evidenceVersion",
            "evidencePlanDigest","catalogDigest","sealedDigest")},
        "sources":{s["key"]:{**s,**{key:decoded["sourceInfos"][s["key"]]["expected"][key]
            for key in ("sourceRef","evidenceDigest")}} for s in decoded["sources"]}}
    raw = canonical(result)
    if len(raw.encode("utf-8")) > MAX_INDEX_BYTES:
        raise AiError("完整候选引用索引超过容量","payload_too_large",413)
    return raw


@dataclass(frozen=True, slots=True, init=False)
class VerifiedClaims:
    _prepared: object
    _role: str
    _package_digest: str
    _identity_json: str
    _reference_json: str
    _index_json: str

    def __init__(self, token, prepared, role, package_digest, row, index_json):
        if token is not _TOKEN:
            raise AiError("候选引用只能从内部已发布角色包构造")
        for key,value in {"_prepared":prepared,"_role":role,"_package_digest":package_digest,
                "_identity_json":packages._identity(row),"_reference_json":canonical(packages.store._reference(row)),
                "_index_json":index_json}.items():
            object.__setattr__(self,key,value)


def _checked(verified, principal):
    if type(verified) is not VerifiedClaims:
        raise AiError("不能从公开JSON恢复候选引用")
    role = _role(verified._role)
    row = _call(packages._checked,verified._prepared,principal)
    if (packages._identity(row) != verified._identity_json
            or canonical(packages.store._reference(row)) != verified._reference_json):
        _conflict()
    package, _ = _selected(verified._prepared,role,row,verified._package_digest)
    return row, package


def prepare(prepared_packages, role, principal):
    """Bind one full role to actual ready storage, without reading fact pages.

    A fresh bounded build is used once to reject a substituted input container.
    Subsequent resolve calls only decode this role's already-held package.
    """
    return prepare_many(prepared_packages, (role,), principal)[role]


def prepare_many(prepared_packages, roles, principal):
    """One actual storage rebuild, separate fixed index per explicitly named role.

    This saves mathematical reconstruction only; it supplies no Agent ledger
    proof and every subsequent resolve still reloads current owning authority.
    """
    if type(roles) not in (list,tuple) or not 1 <= len(roles) <= len(contract.ROLES):
        raise AiError("候选角色列表无效")
    roles = tuple(_role(role) for role in roles)
    if len(set(roles)) != len(roles): raise AiError("候选角色不能重复")
    row = _call(packages._checked,prepared_packages,principal)
    fresh = packages.prepare(row.id,principal)
    values = {}
    for role in roles:
        _, original_digest = _selected(prepared_packages,role,row)
        actual, actual_digest = _selected(fresh,role,row)
        if original_digest != actual_digest:
            _conflict("传入角色包与实际已发布筛查不一致")
        decoded = _call(contract.decode_pages,actual.pages())
        if decoded["role"] != role: _conflict()
        index_json = _call(_index,decoded)
        value = VerifiedClaims(_TOKEN,prepared_packages,role,actual_digest,row,index_json)
        _checked(value,principal)
        values[role] = value
    for value in values.values(): _checked(value,principal)
    return values


def resolve(verified, request, principal):
    """Resolve only an existing exact metric, never a caller's proposed value."""
    if type(request) is not dict:
        raise AiError("候选引用请求必须为对象")
    fields(request,{"candidateId","metric","field"},{"candidateId","metric","field"})
    candidate_id, metric, field = (request[key] for key in ("candidateId","metric","field"))
    if type(candidate_id) is not str or re.fullmatch(r"[a-f0-9]{64}",candidate_id) is None:
        raise AiError("候选身份格式无效")
    if type(metric) is not str or not 1 <= len(metric) <= 128:
        raise AiError("候选指标格式无效")
    if type(field) is not str or field not in FIELDS:
        raise AiError("候选数值字段无效")
    row, package = _checked(verified,principal)
    # Private JSON/digest fields are not trust anchors. Reconstruct the index
    # from the actual package bytes even when the caller replaces a whole index.
    decoded = _call(contract.decode_pages,package.pages())
    actual_index = _call(_index,decoded)
    if type(verified._index_json) is not str or actual_index != verified._index_json:
        _conflict("候选索引与实际角色包记录不一致")
    index = json.loads(actual_index)
    item = index["candidates"].get(candidate_id)
    if item is None:
        raise AiError("候选不属于此角色的固定留存集合","not_found",404)
    candidate = item["candidate"]
    values = candidate[FIELDS[field]]
    if type(values) is not dict or metric not in values or values[metric] is None:
        raise AiError("此候选没有所请求的指标或基期数值","unavailable",409)
    number = values[metric]
    if type(number) is not int or not -MAX_SAFE_INTEGER <= number <= MAX_SAFE_INTEGER:
        _conflict("候选数值不是精确安全整数")
    table_key = candidate["tableKey"]
    table, fixed = index["tables"][table_key], index["tableBindings"][table_key]
    reference = {"screening":packages.store._reference(row),"evidenceBinding":index["evidenceBinding"],"role":verified._role,
        "packagePolicy":contract.POLICY,"packageDigest":verified._package_digest,
        "candidateId":candidate_id,"partitionKey":item["partitionKey"],"ruleId":candidate["ruleId"],"tableKey":table_key,
        "row":candidate["reference"],"tableBinding":fixed,"source":index["sources"][fixed["sourceKey"]],
        "baselineSource":index["sources"][fixed["baselineKey"]] if fixed["baselineKey"] is not None else None,
        "masterSource":index["sources"][fixed["masterKey"]] if fixed["masterKey"] is not None else None}
    value = {"schemaVersion":SCHEMA,"reference":reference,"metric":metric,"field":field,"value":number,
        "entity":candidate["entity"],"identityQualified":candidate["identityQualified"],
        "dateCoverage":{**{key:table[key] for key in ("sourceCoverage","baselineCoverage","sourcePeriod","baselinePeriod","dateCoverageComparable")},
            "entityDailyCoverageVerified":False},
        "verification":{"candidateNumberVerified":True,"causalityVerified":False,
            "humanReviewRequired":True,"agentReadVerified":False},
        "limitations":["仅核验固定留存候选的原始数值；不证明因果、增量收益或最终利润",
            "来源日期有记录不证明每个实体每日完整；同比环比按原查询区间总量，未按天归一",
            "不同维度、分区或规则可能引用同一事实，不得把候选金额相加为总损失",
            "内部读取不构成专业Agent已读证明，正式结论仍需人工复核"]}
    value["responseDigest"] = digest(value)
    if len(canonical(value).encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise AiError("完整候选引用超过响应容量","payload_too_large",413)
    _checked(verified,principal)
    return value
