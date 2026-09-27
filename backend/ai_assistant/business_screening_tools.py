"""Internal ready-screening readers; no route, tool or runtime registration.

Preparation builds the five persisted role packages once. Reusing that object
never skips current ownership, report/intent or immutable publication checks.
Only analysis and explicitly requested budget preparation traverse fact pages.
"""
from copy import deepcopy
from dataclasses import dataclass
import json
import re
from types import SimpleNamespace

from business_analysis import budget_reference, screening_package
from business_analysis.contracts import AnalysisContractError
from business_analysis.results import VIEWS, build_table
from . import business_screening_runtime as runtime, business_screening_runtime_contract as contract
from . import business_screening_packages as packages, business_screening_store as store
from . import business_budget_store, business_mapped_analysis
from .business_sealed import Reader
from .policy import AiError, canonical, digest, fields, identifier

MAX_RESPONSE_BYTES = contract.MAX_TOOL_BYTES
LIMIT = 20
ANALYSIS_SCHEMA = "business-screening-analysis-v1"
_TOKEN = object()


def _conflict(message="筛查工具准备对象或固定结果已变化"):
    raise AiError(message, "conflict", 409)


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except (AnalysisContractError, ValueError, TypeError, KeyError, IndexError, AttributeError,
            UnicodeError, RecursionError) as error:
        raise AiError("筛查工具数据未通过完整核验", "conflict", 409) from error


def _budget_digest(value):
    if value is None: return None
    if type(value) is not business_budget_store.PreparedBudget: _conflict()
    return digest([value.id, value.plan_json, value.binding_json, value.result_json])


def _package_digests(ready):
    if type(ready) is not packages.PreparedPackages: _conflict()
    entries = ready._packages
    if type(entries) is not tuple or len(entries)!=len(screening_package.ROLES): _conflict()
    result = []
    for role,entry in zip(screening_package.ROLES,entries):
        if (type(entry) is not tuple or len(entry)!=3 or type(entry[0]) is not str or entry[0]!=role
                or type(entry[1]) is not screening_package.Package or type(entry[2]) is not str): _conflict()
        actual = entry[1].package_digest  # Rehash actual bounded canonical bytes.
        if actual!=entry[2]: _conflict()
        result.append([role,actual])
    return canonical(result)


@dataclass(frozen=True, slots=True, init=False)
class Prepared:
    _report: object
    _evidence: object
    _snapshot_json: str
    _reference_json: str
    _sources_json: str
    _packages: object
    _package_digests_json: str
    _storage_reference_json: str
    _budget: object
    _budget_digest: object

    def __init__(self, token, report, snapshot, reference, evidence, sources, ready, saved, budget):
        if token is not _TOKEN: raise AiError("筛查工具准备对象只能由内部构造")
        for key, value in (("_report",deepcopy(report)),("_evidence",deepcopy(evidence)),
                ("_snapshot_json",canonical(snapshot)),("_reference_json",canonical(reference)),
                ("_sources_json",canonical(sources)),("_packages",ready),
                ("_package_digests_json",_call(_package_digests,ready)),
                ("_storage_reference_json",canonical(saved)),("_budget",deepcopy(budget)),
                ("_budget_digest",_budget_digest(budget))):
            object.__setattr__(self,key,value)

    @property
    def report_id(self): return self.reference["reportId"]
    @property
    def actual(self): return deepcopy(self._report)
    @property
    def report(self): return self.actual
    @property
    def evidence(self): return deepcopy(self._evidence)
    @property
    def sources(self): return json.loads(self._sources_json)
    @property
    def snapshot(self): return json.loads(self._snapshot_json)
    @property
    def reference(self): return json.loads(self._reference_json)
    @property
    def packages(self): return self._packages
    @property
    def budget(self): return deepcopy(self._budget)


def _checked(prepared, principal):
    if type(prepared) is not Prepared: raise AiError("不能从公开 JSON 恢复筛查工具准备对象")
    snapshot = _call(lambda:prepared.snapshot)
    actual, fixed, reference, evidence, sources, _ = runtime.bound(
        SimpleNamespace(id=snapshot.get("reportId"),snapshot_json=prepared._snapshot_json),principal)
    if (canonical(fixed)!=prepared._snapshot_json or canonical(reference)!=prepared._reference_json
            or canonical(sources)!=prepared._sources_json): _conflict()
    if _call(lambda:(prepared._report.id,prepared._report.owner_email,prepared._report.scope_json,prepared._report.snapshot_json,
            prepared._evidence.id,prepared._evidence.version,prepared._evidence.plan_json,prepared._evidence.state_json)) != (
            actual.id,actual.owner_email,actual.scope_json,actual.snapshot_json,
            evidence.id,evidence.version,evidence.plan_json,evidence.state_json): _conflict()
    row = packages._checked(prepared._packages,principal)
    if (row.id!=fixed["screeningIntent"]["id"] or row.report_id!=actual.id or row.evidence_id!=evidence.id
            or canonical(store._reference(row))!=prepared._storage_reference_json): _conflict()
    if _call(_package_digests,prepared._packages)!=prepared._package_digests_json: _conflict()
    if _call(_budget_digest,prepared._budget)!=prepared._budget_digest: _conflict()
    if prepared._budget is not None:
        bound = business_budget_store.binding_for_report(actual,principal)
        if ((prepared._budget.id,prepared._budget.plan_json,prepared._budget.binding_json)
                != (bound.id,bound.plan_json,bound.binding_json)
                or prepared._budget.reference!=fixed.get("budgetRef")): _conflict()
    return actual,fixed,reference,evidence,sources


def prepare_for_report(report, principal, *, resolve_budget=False):
    if type(resolve_budget) is not bool: raise AiError("预算解析选项须为布尔值")
    actual,snapshot,reference,evidence,sources,_ = runtime.bound(report,principal)
    # A fixed intent is not a ready publication. The owning package constructor
    # validates every persisted page before issuing a process-local container.
    try:
        ready = packages.prepare(snapshot["screeningIntent"]["id"],principal)
    except AiError as error:
        if error.status == 404:
            raise AiError("固定筛查结果尚未完整发布", "conflict", 409) from error
        raise
    saved = packages.describe(ready,principal)["reference"]
    if saved["reportId"]!=actual.id or saved["id"]!=snapshot["screeningIntent"]["id"]: _conflict()
    budget = business_budget_store.load(actual,principal) if resolve_budget and actual.budget_plan_id else None
    prepared = Prepared(_TOKEN,actual,snapshot,reference,evidence,sources,ready,saved,budget)
    _checked(prepared,principal)
    return prepared


def package_from(prepared, role, principal, *, offset=0):
    _checked(prepared,principal)
    value = packages.page(prepared._packages,role,principal,offset=offset)
    # No wrapper: the role page itself owns the entire 38,000 byte allowance.
    if not _sized(value): raise AiError("完整角色包页超过工具容量", "payload_too_large", 413)
    _checked(prepared,principal)
    return value


def _sized(value):
    return len(canonical(value).encode("utf-8")) <= MAX_RESPONSE_BYTES


def _offset(value, maximum):
    if (type(value) is not str or len(value)>6 or re.fullmatch(r"0|[1-9][0-9]*",value) is None
            or int(value)>maximum): raise AiError("筛查工具分页偏移无效")
    return int(value)


def _integer_offset(value, maximum):
    if type(value) is not int or not 0 <= value <= maximum: raise AiError("筛查工具分页偏移无效")
    return value


def budget_from(prepared, principal, *, offset=0):
    _checked(prepared,principal)
    _integer_offset(offset,99)
    fixed = prepared._budget
    if fixed is None:
        _conflict("本报告没有已解析的固定预算；须显式准备预算，不能返回空成功")
    value = None
    for limit in range(LIMIT,0,-1):
        page = _call(budget_reference.page,fixed.result,fixed.binding,budget_ref=fixed.reference,
            report_id=prepared.report_id,offset=offset,limit=limit)
        candidate = {"schemaVersion":contract.BUDGET_PAGE_SCHEMA,"reference":prepared.reference,"budget":page}
        candidate["pageDigest"] = digest(candidate)
        if _sized(candidate):
            value = candidate
            break
    if value is None: raise AiError("单个完整预算对象超过筛查工具容量", "payload_too_large", 413)
    _checked(prepared,principal)
    return value


def _table_page(prepared, mode, selector, table, offset):
    rows = table["rows"]
    for count in range(len(rows),-1,-1):
        if not count and rows: break
        end = offset+count
        page = {**table,"rows":rows[:count],"pagination":{"offset":offset,"limit":LIMIT,
            "total":table["total"],"hasMore":end<table["total"],"nextOffset":end if end<table["total"] else None}}
        if "pageDigest" in page: page["pageDigest"] = digest({k:v for k,v in page.items() if k!="pageDigest"})
        result = {"schemaVersion":ANALYSIS_SCHEMA,"reference":prepared.reference,"mode":mode,"selector":selector,"table":page}
        result["pageDigest"] = digest(result)
        if _sized(result): return result
    raise AiError("单个完整分析行超过筛查工具容量", "payload_too_large", 413)


def analysis_from(prepared, arguments, principal):
    _,snapshot,_,evidence,sources = _checked(prepared,principal)
    fields(arguments,{"runId","reportId","screeningId","mode","dimension","offset","sourceKey","baselineKey","pairKey","baselinePairKey"},
        {"runId","reportId","screeningId","mode","dimension"})
    if (arguments["runId"]!=evidence.id or arguments["reportId"]!=prepared.report_id
            or arguments["screeningId"]!=snapshot["screeningIntent"]["id"]):
        raise AiError("分析工具跨报告、封存证据或筛查意图", "access_denied", 403)
    offset = _integer_offset(arguments.get("offset",0),250000)
    mode,dimension = arguments["mode"],arguments["dimension"]
    if type(dimension) is not str: raise AiError("分析维度无效")
    if mode == "native":
        if "sourceKey" not in arguments or {"pairKey","baselinePairKey"}&set(arguments) or dimension not in VIEWS:
            raise AiError("原生分析参数无效")
        keys = [identifier(arguments["sourceKey"])]+([identifier(arguments["baselineKey"])] if "baselineKey" in arguments else [])
        if not set(keys)<={source["key"] for source in sources}: _conflict("分析来源不在固定封存目录")
        reader = Reader(evidence,principal)
        table = _call(build_table,reader.pages(keys[0]),dimension,reader.info(keys[0])["expected"],offset=offset,limit=LIMIT,
            **({"baseline_pages":reader.pages(keys[1]),"baseline_expected":reader.info(keys[1])["expected"]} if len(keys)>1 else {}))
        if offset>table["total"]: raise AiError("分析偏移超过完整范围")
        selector = {key:arguments[key] for key in ("sourceKey","baselineKey","dimension") if key in arguments}
        result = _table_page(prepared,mode,selector,table,offset)
    elif mode == "mapped":
        if "pairKey" not in arguments or {"sourceKey","baselineKey"}&set(arguments): raise AiError("关联分析参数无效")
        if "mappingPlan" not in snapshot: _conflict("本报告没有固定商品关联计划")
        selector = {key:arguments[key] for key in ("pairKey","baselinePairKey","dimension") if key in arguments}
        with business_mapped_analysis.table(evidence.id,snapshot["mappingPlan"],arguments["pairKey"],dimension,principal,
                baseline_pair_key=arguments.get("baselinePairKey")) as table:
            result = _table_page(prepared,mode,selector,table.page(offset=offset,limit=LIMIT),offset)
    else: raise AiError("分析模式无效")
    _checked(prepared,principal)
    return result


def expected_pages(prepared, role, principal):
    """Enumerate exact pages once for future admission/receipts, not fact reads."""
    _checked(prepared,principal)
    role_pages,budget_pages = {},{}
    offset = 0
    while offset is not None:
        page = package_from(prepared,role,principal,offset=offset)
        role_pages[offset] = page
        next_offset = page["pagination"]["nextOffset"]
        if next_offset is not None and (type(next_offset) is not int or not offset<next_offset<screening_package.MAX_RECORDS): _conflict()
        offset = next_offset
        if len(role_pages)>screening_package.MAX_RECORDS: _conflict()
    if prepared._budget is not None:
        offset = 0
        while offset is not None:
            page = budget_from(prepared,principal,offset=offset)
            budget_pages[offset] = page
            next_offset = page["budget"]["pagination"]["nextOffset"]
            if next_offset is not None and (type(next_offset) is not int or not offset<next_offset<=99): _conflict()
            offset = next_offset
            if len(budget_pages)>100: _conflict()
    _checked(prepared,principal)
    return role_pages,budget_pages


def read(report_id, operation, params, principal):
    from .reports import get
    if operation not in ("package","budget","analysis"): raise AiError("筛查工具操作无效")
    allowed = {"runId","screeningId","offset"}
    required = {"runId","screeningId"}
    if operation == "package": allowed.add("role"); required.add("role")
    if operation == "analysis":
        allowed |= {"mode","dimension","sourceKey","baselineKey","pairKey","baselinePairKey"}
        required |= {"mode","dimension"}
    fields(params,allowed,required)
    offset = _offset(params.get("offset","0"),screening_package.MAX_RECORDS-1 if operation=="package" else 99 if operation=="budget" else 250000)
    prepared = prepare_for_report(get(report_id,principal),principal,resolve_budget=operation=="budget")
    if params["runId"]!=prepared.reference["evidenceRunId"] or params["screeningId"]!=prepared.reference["screeningIntent"]["id"]:
        raise AiError("工具只能读取本报告的封存证据和固定筛查", "access_denied", 403)
    if operation == "package": result = package_from(prepared,params["role"],principal,offset=offset)
    elif operation == "budget": result = budget_from(prepared,principal,offset=offset)
    else: result = analysis_from(prepared,{**params,"reportId":report_id,"offset":offset},principal)
    _checked(prepared,principal)
    return result
