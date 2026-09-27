"""Internal authorized transport of published screening; no Agent admission.

The process-local container is not a credential. Every read reloads the actual
report, workflow, sealed metadata, fixed selector and immutable storage root.
Pure pages deliberately retain authorityVerified=False: neither preparation nor
reading this service proves that a future model/job has consumed its records.
"""
from dataclasses import dataclass
import json

from business_analysis import screening_package as contract
from business_analysis.contracts import AnalysisContractError
from . import business_diagnostic_screening as screening, business_screening_store as store
from .policy import AiError, canonical, digest

_TOKEN = object()


def _conflict():
    raise AiError("角色筛查包与固定持久结果不一致", "conflict", 409)


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except (AnalysisContractError, ValueError, TypeError, KeyError, IndexError, UnicodeError, RecursionError) as error:
        raise AiError("角色筛查包未通过完整校验", "conflict", 409) from error


def _identity(row):
    return canonical({"reference":store._reference(row), "bindingJson":row.binding_json,
        "manifestJson":row.manifest_json, "storedBytes":row.stored_bytes})


@dataclass(frozen=True, slots=True, init=False)
class PreparedPackages:
    """Bound immutable packages, created only after trusted metadata rechecks."""
    _screening_id: str
    _identity_json: str
    _packages: tuple
    _description_json: str

    def __init__(self, token, screening_id, identity_json, packages, description):
        if token is not _TOKEN:
            raise AiError("角色筛查包只能从内部持久结果构造")
        object.__setattr__(self, "_screening_id", screening_id)
        object.__setattr__(self, "_identity_json", identity_json)
        object.__setattr__(self, "_packages", tuple((role, packages[role], _call(lambda:packages[role].package_digest))
            for role in contract.ROLES))
        object.__setattr__(self, "_description_json", canonical(description))


def _checked(prepared, principal):
    if type(prepared) is not PreparedPackages:
        raise AiError("不能从公开 JSON 恢复角色筛查包")
    row, _, _ = store._loaded(prepared._screening_id, principal)
    if _identity(row) != prepared._identity_json:
        _conflict()
    return row


def prepare(screening_id, principal):
    """Build all five roles losslessly from already-published pages, not facts."""
    row, binding, _ = store._loaded(screening_id, principal)
    identity_json = _identity(row)
    bundle = store._all(row)
    # Reconstruct metadata from the owning Reader, never from a client request
    # or the public serialized package. This path only reads sealed metadata.
    loaded = screening._load(row.report_id, principal)
    plan = screening._describe(loaded)["plan"]
    if (canonical(loaded[0]) != canonical(binding) or not plan["canScreen"]
            or plan["planDigest"] != row.selection_plan_digest):
        _conflict()
    packages = _call(contract.build, bundle, sources=loaded[4], source_infos=loaded[5], selection_plan=plan)
    if type(packages) is not dict or set(packages) != set(contract.ROLES):
        _conflict()
    summaries = {}
    for role in contract.ROLES:
        package = packages[role]
        if type(package) is not contract.Package:
            _conflict()
        first = _call(package.page)
        _page_binding(first, row, role, _call(lambda:package.package_digest))
        summaries[role] = {**{key:first[key] for key in ("packageDigest", "totalRecords")},
            **{key:first["directory"][key] for key in ("recordCounts", "requiredPartitionKeys")}}
    value = PreparedPackages(_TOKEN, row.id, identity_json, packages,
        {"schemaVersion":"business-screening-packages-reference-v1", "reference":store._reference(row),
         "policy":contract.POLICY, "agentReadVerified":False, "roles":summaries})
    _checked(value, principal)
    return value


def _page_binding(value, row, role, package_digest):
    if (value.get("schemaVersion") != contract.SCHEMA or value.get("policy") != contract.POLICY
            or value.get("authorityVerified") is not False or value.get("role") != role
            or value.get("reportId") != row.report_id or value.get("evidenceRunId") != row.evidence_id
            or value.get("bindingDigest") != row.binding_digest
            or value.get("selectionPlanDigest") != row.selection_plan_digest
            or value.get("serviceResultDigest") != row.service_result_digest
            or value.get("storageManifestDigest") != row.manifest_digest
            or value.get("packageDigest") != package_digest
            or value.get("pageDigest") != digest({key:item for key,item in value.items() if key != "pageDigest"})
            or len(canonical(value).encode("utf-8")) > contract.MAX_PAGE_BYTES):
        _conflict()


def page(prepared, role, principal, *, offset=0):
    """Read a precise role and record offset; no persistence or model receipt."""
    if type(role) is not str or role not in contract.ROLES:
        raise AiError("筛查角色无效")
    if type(offset) is not int or not 0 <= offset < contract.MAX_RECORDS:
        raise AiError("筛查角色包偏移无效")
    row = _checked(prepared, principal)
    _, package, expected_digest = next(item for item in prepared._packages if item[0] == role)
    if type(package) is not contract.Package or _call(lambda:package.package_digest) != expected_digest:
        _conflict()
    value = _call(package.page, offset)
    _page_binding(value, row, role, expected_digest)
    _checked(prepared, principal)
    return value


def describe(prepared, principal):
    """Small reference for future admission; not proof that an Agent read it."""
    _checked(prepared, principal)
    value = json.loads(prepared._description_json)
    _checked(prepared, principal)
    return value
