"""Byte-bounded tools for one fixed integrated report, without model I/O."""
import json
import re

from business_analysis import budget_reference, evidence_v2
from business_analysis.contracts import AnalysisContractError
from business_analysis.results import VIEWS, build_table
from . import business_integrated as contract, business_mapped_analysis, business_budget_store
from .business_sealed import Reader
from .policy import AiError, canonical, digest, fields, identifier

MAX_RESPONSE_BYTES = 38000
LIMIT = 20


def _offset(value, maximum):
    if (type(value) is not str or len(value) > 6 or re.fullmatch(r"0|[1-9][0-9]*", value) is None
            or int(value) > maximum):
        raise AiError("集成分析分页偏移无效")
    return int(value)


def _sized(value):
    return len(canonical(value).encode("utf-8")) <= MAX_RESPONSE_BYTES


def directory_from(prepared, evidence, sources, offset=0):
    """Prepared inputs must already be verified by admission or report binding."""
    catalog = evidence_v2.build_catalog(sources, analysis_request=json.loads(evidence.plan_json).get("analysisRequest"))
    if type(offset) is not int or not 0 <= offset < len(sources):
        raise AiError("集成目录偏移无效")
    pairs = {p["salesKey"]:p for p in prepared.plan["pairs"]}
    def assemble(items):
        end = offset+len(items)
        result = {"schemaVersion":"business-integrated-directory-v1", "reference":prepared.reference,
            "offset":offset, "requestedLimit":LIMIT, "total":len(sources), "returned":len(items),
            "nextOffset":end if end < len(sources) else None, "items":items,
            "budgetMode":"fixed" if "budgetRef" in prepared.snapshot else "none"}
        return {**result, "pageDigest":digest(result)}
    items = []
    for entry in catalog["entries"][offset:offset+LIMIT]:
        candidate = {**entry, "mappingPair":pairs.get(entry["key"])}
        if not _sized(assemble([*items,candidate])):
            if not items: raise AiError("单个集成目录来源超过完整页容量", "payload_too_large", 413)
            break
        items.append(candidate)
    return assemble(items)


def budget_from(prepared, offset=0):
    if prepared.budget is None:
        raise AiError("本报告没有固定推广预算参数", "conflict", 409)
    for limit in range(LIMIT, 0, -1):
        value = business_budget_store._call(budget_reference.page, prepared.budget.result, prepared.budget.binding,
            budget_ref=prepared.budget.reference, report_id=prepared.snapshot["reportId"], offset=offset, limit=limit)
        result = {"schemaVersion":"business-integrated-budget-v1", "reference":prepared.reference, "budget":value}
        result["pageDigest"] = digest(result)
        if _sized(result): return result
    raise AiError("单个完整预算对象超过集成页容量", "payload_too_large", 413)


def expected_pages(prepared, evidence, sources):
    directories, budgets, offset = {}, {}, 0
    while offset is not None:
        result = directory_from(prepared, evidence, sources, offset)
        directories[offset] = result; offset = result["nextOffset"]
    if prepared.budget is not None:
        offset = 0
        while offset is not None:
            result = budget_from(prepared, offset)
            budgets[offset] = result; offset = result["budget"]["pagination"]["nextOffset"]
            if len(budgets) > 40:
                raise AiError("完整预算超过工具调用容量", "tool_limit_exceeded", 409)
    return directories, budgets


def prepare_for_report(report, principal, *, resolve_budget=False, _reuse=None):
    actual, snapshot, reference, evidence, sources = contract.bound(report, principal)
    budget = ((_reuse.budget(actual, principal) if _reuse is not None else business_budget_store.load(actual, principal))
        if resolve_budget and actual.budget_plan_id else None)
    prepared = contract.Prepared(actual.owner_email, actual.scope_json, actual.snapshot_json, canonical(reference), budget)
    return actual, prepared, evidence, sources


def _table_page(prepared, mode, selector, table, offset):
    rows = table["rows"]
    # Keep a complete leading row prefix; only the next offset changes. No
    # metric, provenance, or identity field is truncated to fit the tool cap.
    for count in range(len(rows), -1, -1):
        if not count and rows: break
        end = offset+count
        page = {**table, "rows":rows[:count], "pagination":{"offset":offset, "limit":LIMIT,
            "total":table["total"], "hasMore":end < table["total"], "nextOffset":end if end < table["total"] else None}}
        if "pageDigest" in page:
            page["pageDigest"] = digest({k:v for k,v in page.items() if k != "pageDigest"})
        result = {"schemaVersion":"business-integrated-analysis-v1", "reference":prepared.reference,
            "mode":mode, "selector":selector, "table":page}
        result["pageDigest"] = digest(result)
        if _sized(result): return result
    raise AiError("单个完整分析行超过集成工具容量", "payload_too_large", 413)


def analysis_from(prepared, evidence, sources, arguments, principal, *, _reuse=None):
    if _reuse is not None:
        return _reuse.analysis(prepared, evidence, sources, arguments, principal,
            lambda: _analysis_from(prepared, evidence, sources, arguments, principal))
    return _analysis_from(prepared, evidence, sources, arguments, principal)


def _analysis_from(prepared, evidence, sources, arguments, principal):
    fields(arguments, {"runId", "reportId", "mode", "dimension", "offset", "sourceKey", "baselineKey", "pairKey", "baselinePairKey"},
        {"runId", "reportId", "mode", "dimension"})
    if arguments["runId"] != evidence.id or arguments["reportId"] != prepared.snapshot["reportId"]:
        raise AiError("分析工具跨报告或证据范围", "access_denied", 403)
    offset = arguments.get("offset", 0)
    if type(offset) is not int or not 0 <= offset <= 250000:
        raise AiError("分析偏移无效")
    mode, dimension = arguments["mode"], arguments["dimension"]
    if mode == "native":
        if "sourceKey" not in arguments or {"pairKey","baselinePairKey"} & set(arguments):
            raise AiError("原生分析来源参数无效")
        if type(dimension) is not str or dimension not in VIEWS:
            raise AiError("原生分析维度无效")
        keys = [identifier(arguments["sourceKey"])]+([identifier(arguments["baselineKey"])] if "baselineKey" in arguments else [])
        if not set(keys) <= {s["key"] for s in sources}:
            raise AiError("分析来源不在固定目录", "conflict", 409)
        reader = Reader(evidence, principal)
        try:
            table = build_table(reader.pages(keys[0]), dimension, reader.info(keys[0])["expected"], offset=offset, limit=LIMIT,
                **({"baseline_pages":reader.pages(keys[1]), "baseline_expected":reader.info(keys[1])["expected"]} if len(keys)>1 else {}))
        except (AnalysisContractError, KeyError, TypeError, ValueError) as error:
            raise AiError("原生分析未通过完整核验", "conflict", 409) from error
        if offset > table["total"]:
            raise AiError("分析偏移超过完整范围")
        selector = {k:arguments[k] for k in ("sourceKey","baselineKey","dimension") if k in arguments}
        return _table_page(prepared, mode, selector, table, offset)
    if mode == "mapped":
        if "pairKey" not in arguments or {"sourceKey","baselineKey"} & set(arguments):
            raise AiError("映射分析来源参数无效")
        selector = {k:arguments[k] for k in ("pairKey","baselinePairKey","dimension") if k in arguments}
        with business_mapped_analysis.table(evidence.id, prepared.plan, arguments["pairKey"], dimension, principal,
                baseline_pair_key=arguments.get("baselinePairKey")) as table:
            result = _table_page(prepared, mode, selector, table.page(offset=offset, limit=LIMIT), offset)
        return result
    raise AiError("分析模式无效")


def read(report_id, operation, params, principal):
    from .reports import get
    allowed = {"runId", "offset"}
    if operation == "analysis": allowed |= {"mode","dimension","sourceKey","baselineKey","pairKey","baselinePairKey"}
    fields(params, allowed, {"runId"} | ({"mode","dimension"} if operation == "analysis" else set()))
    report, prepared, evidence, sources = prepare_for_report(get(report_id, principal), principal, resolve_budget=operation == "budget")
    if params["runId"] != evidence.id:
        raise AiError("工具只能读取本报告封存证据", "access_denied", 403)
    offset = _offset(params.get("offset", "0"), 47 if operation == "directory" else 99 if operation == "budget" else 250000)
    if operation == "directory": result = directory_from(prepared, evidence, sources, offset)
    elif operation == "budget": result = budget_from(prepared, offset)
    elif operation == "analysis":
        result = analysis_from(prepared, evidence, sources, {**params,"reportId":report_id,"offset":offset}, principal)
    else: raise AiError("集成工具操作无效")
    contract.bound(report, principal)
    return result
