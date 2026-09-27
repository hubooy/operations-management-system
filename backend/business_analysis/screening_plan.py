"""Fixed complete screening selection over supplied sealed-directory metadata.

No database, source traversal, authentication or model calls occur here. The
caller must obtain the complete directory, checkpoint info, analysis request
and optional mapping plan from an owner-authorized immutable report. A plan or
its digest is never an authority. canScreen means a bounded attempt is allowed,
not that source facts, output row counts or all requested capabilities exist.
"""
from copy import deepcopy

from . import diagnostic_screening as scanner, mapping_plan as mapping
from .contracts import AnalysisContractError, MAX_SAFE_INTEGER, canonical, digest
from .evidence_v2 import normalize_sources
from .planning import DIMENSIONS, WINDOWS, validate_analysis_request

SCHEMA_VERSION = "business-screening-plan-v1"
SELECTION_POLICY = "screen-selection-v1"


def _require(condition, message):
    if not condition:
        raise AnalysisContractError(message)


def _limits(value):
    result = dict(scanner.LIMITS)
    if value is not None:
        _require(type(value) is dict and len(value) <= len(result) and not set(value)-set(result), "筛查计划容量参数未知")
        for key, item in value.items():
            result[key] = scanner._integer(item, 1, scanner.LIMITS[key])
    return result


def _info(value):
    scanner._bounded(value, scanner.LIMITS["maxHeaderBytes"])
    scanner._fields(value, {"metadata", "expected", "pageCount"})
    _require(type(value["metadata"]) is dict, "筛查计划来源元信息无效")
    pages = scanner._integer(value["pageCount"], 1, 2000)
    expected = value["expected"]
    scanner._fields(expected, {"sourceRef", "rowCount", "metrics", "reconciled", "evidenceDigest"})
    scanner._sha(expected["sourceRef"]); scanner._sha(expected["evidenceDigest"])
    rows = scanner._integer(expected["rowCount"], 0, pages*100)
    _require(expected["reconciled"] is True and type(expected["metrics"]) is dict
        and len(expected["metrics"]) <= 128, "筛查计划检查点未完成或指标集合无效")
    for cell in expected["metrics"].values():
        scanner._fields(cell, {"value", "presentRows", "missingRows"})
        present, missing = scanner._integer(cell["presentRows"]), scanner._integer(cell["missingRows"])
        _require(present+missing == rows, "筛查计划来源指标计数不守恒")
        if cell["value"] is not None: scanner._integer(cell["value"], -MAX_SAFE_INTEGER)
        _require((cell["value"] is None) == (present == 0), "筛查计划来源指标空值不一致")
    return deepcopy(value)


def _family(entry):
    if entry["domain"] == "sales": return "erp"
    if entry["domain"] == "market": return "market"
    if entry["query"]["dataset"] == "master": return "master"
    return "promotion" if entry["query"]["dataset"] == "promotion" else "product"


def build(analysis_request, sources, source_infos, *, mapping_plan=None, limits=None):
    """Return all coverage/descriptors even when static admission fails.

    sources: the full Reader.sources list ({key, domain, query}). source_infos:
    exactly {sourceKey: Reader.info(sourceKey)}, not per-page tool metadata.
    Invalid contracts raise AnalysisContractError. Valid but unsupported/missing
    capabilities remain visible. Static capacity failures set canScreen=False,
    never shrink requested dimensions/windows or select a subset of sources.
    """
    bounds = _limits(limits)
    scanner._bounded(analysis_request, 8192)
    request = validate_analysis_request(analysis_request)
    entries = normalize_sources(sources)
    plain = [{key:entry[key] for key in ("key", "domain", "query")} for entry in entries]
    _require(type(source_infos) is dict and len(source_infos) == len(entries)
        and set(source_infos) == {e["key"] for e in entries},
        "筛查计划需要完整且仅对应目录的来源检查点")
    infos = {e["key"]:_info(source_infos[e["key"]]) for e in entries}
    _require(sum(info["pageCount"] for info in infos.values()) <= 2000,
        "筛查计划来源页总数超过封存事实额度")
    checked_mapping = mapping._checked_plan(mapping_plan, plain) if mapping_plan is not None else None
    by_sales = {pair["salesKey"]:pair for pair in checked_mapping["pairs"]} if checked_mapping else {}
    by_key = {entry["key"]:entry for entry in entries}
    mapping_digest = digest(checked_mapping) if checked_mapping else None
    windows = [window for window in WINDOWS if window in request["requestedWindows"]]
    dimensions = [dim for dim in DIMENSIONS if dim in request["requestedDimensions"]]
    grouped = {}
    for entry in entries:
        query = {key:value for key,value in entry["query"].items() if key != "window"}
        family_key = digest({"domain":entry["domain"], "query":query})
        family = grouped.setdefault(family_key, {"familyKey":family_key, "domain":entry["domain"],
            "query":query, "family":_family(entry), "sources":{}})
        _require(entry["query"]["window"] not in family["sources"], "筛查同范围窗口来源重复")
        family["sources"][entry["query"]["window"]] = entry["key"]
    families = [grouped[key] for key in sorted(grouped)]
    coverage, descriptors, table_keys = [], {}, {}

    def bound_source(source_key):
        entry, expected = by_key[source_key], infos[source_key]["expected"]
        return {**{key:deepcopy(entry[key]) for key in ("key", "domain", "query")},
            "sourceRef":expected["sourceRef"], "evidenceDigest":expected["evidenceDigest"]}

    def record(family, dimension, window, mode, kind, source_key, baseline_key=None,
               *, status="unavailable", reason=None):
        identity = [family["familyKey"],mode,dimension,window,kind]
        item = {"coverageKey":digest(identity), "familyKey":family["familyKey"], "domain":family["domain"],
            "mode":mode, "dimension":dimension, "window":window, "kind":kind,
            "sourceKey":source_key, "baselineKey":baseline_key, "status":status, "reason":reason, "tableKeys":[]}
        coverage.append(item)
        return item

    def availability(family, dimension, source_key, mode):
        if source_key is None: return "unavailable", "missing_source"
        if family["family"] == "market": return "unsupported", "unsupported_market_rules"
        if family["family"] == "erp":
            if dimension not in {"shop", "sku", "spu"}: return "unsupported", "unsupported_erp_dimension"
            if mode == "mapped" and source_key not in by_sales: return "unavailable", "missing_mapping_pair"
        if mode == "native" and not infos[source_key]["expected"]["metrics"]:
            return "unavailable", "no_additive_metrics"
        return "planned", None

    def add_table(source_key, baseline_key, dimension, mode, rules):
        mapped = None
        if mode == "mapped":
            pair = by_sales[source_key]
            mapped = {"planDigest":mapping_digest, "pairKey":pair["pairKey"],
                "baselinePairKey":by_sales[baseline_key]["pairKey"] if baseline_key else None,
                "algorithmVersion":checked_mapping["algorithmVersion"], "master":bound_source(pair["masterKey"])}
        desc = scanner._descriptor({"schemaVersion":scanner.DESCRIPTOR_SCHEMA, "mode":mode,
            "dimension":dimension, "source":bound_source(source_key),
            "baseline":bound_source(baseline_key) if baseline_key else None, "mapping":mapped,
            "ruleIds":sorted(rules)})
        key = digest({k:v for k,v in desc.items() if k != "ruleIds"})
        _require(key not in descriptors, "筛查同表重复枚举，不得静默合并规则")
        descriptors[key] = desc
        table_keys[key] = source_key, baseline_key
        return key

    for family in families:
        available = family["sources"]
        single_rules = [rule for rule,spec in scanner.RULES.items() if spec[0] == family["family"] and not spec[2]]
        compare_rules = [rule for rule,spec in scanner.RULES.items() if spec[0] == family["family"] and spec[2]]
        for dimension in dimensions:
            mode = "mapped" if family["family"] == "erp" and dimension in {"sku", "spu"} else "native"
            if family["family"] == "master":
                for window in windows:
                    record(family,dimension,window,"identity","dependency",available.get("current"),
                        status="dependency",reason="current_master_not_historical")
                continue
            singles = {}
            for window in windows:
                source_key = available.get(window)
                status, reason = availability(family,dimension,source_key,mode)
                item = record(family,dimension,window,mode,"single",source_key,status=status,reason=reason)
                singles[window] = item
                if status == "planned":
                    if single_rules:
                        item["tableKeys"] = [add_table(source_key,None,dimension,mode,single_rules)]
                    else:
                        item.update(status="not_applicable",reason="comparison_only_rule_family")
            for window in windows:
                if window == "current": continue
                source_key, baseline_key = available.get("current"), available.get(window)
                status, reason = availability(family,dimension,source_key,mode)
                if status == "planned":
                    status, reason = availability(family,dimension,baseline_key,mode)
                    if reason == "missing_source": reason = "missing_baseline_source"
                item = record(family,dimension,window,mode,"comparison",source_key,baseline_key,status=status,reason=reason)
                if status != "planned": continue
                if not compare_rules:
                    item.update(status="not_applicable",reason="no_comparison_rule")
                    continue
                if mode == "mapped":
                    try:
                        mapping.validate_baseline_pair(plain, checked_mapping,
                            by_sales[source_key]["pairKey"],by_sales[baseline_key]["pairKey"])
                    except AnalysisContractError:
                        item.update(status="unavailable",reason="incompatible_mapping_baseline")
                        continue
                key = add_table(source_key,baseline_key,dimension,mode,compare_rules)
                item["tableKeys"] = [key]
                if not single_rules:
                    for side in ("current",window):
                        singles[side]["tableKeys"].append(key)
                        singles[side].update(status="planned",reason="observed_in_comparison_table")
            if not single_rules:
                for item in singles.values():
                    if item["status"] == "not_applicable":
                        item.update(status="unavailable",reason="baseline_not_requested" if len(windows)==1 else "no_compatible_comparison")
            for window in WINDOWS:
                if window in available and window not in windows:
                    record(family,dimension,window,mode,"outside",available[window],
                        status="outside_fixed_request",reason="window_not_requested")

    coverage.sort(key=lambda item:item["coverageKey"])
    ordered = [descriptors[key] for key in sorted(descriptors)]
    descriptor_sizes = [len(canonical(desc).encode("utf-8")) for desc in ordered]
    coverage_bytes = len(canonical({"families":families,"requestedCoverage":coverage}).encode("utf-8"))
    partitions = sum(len(desc["ruleIds"]) for desc in ordered)
    upper_rows = sum(infos[key]["expected"]["rowCount"] for pair in table_keys.values() for key in pair if key)
    scanner._integer(upper_rows)
    capacity = {"tableCount":len(ordered), "maxTables":bounds["maxTables"],
        "partitionCount":partitions, "maxPartitions":bounds["maxPartitions"],
        "descriptorBytes":sum(descriptor_sizes), "maxDescriptorBytes":bounds["maxDescriptorBytes"],
        "largestDescriptorBytes":max(descriptor_sizes,default=0), "maxSingleDescriptorBytes":bounds["maxHeaderBytes"],
        "coverageBytes":coverage_bytes, "maxCoverageBytes":bounds["maxCoverageBytes"],
        "maxRetainedCandidates":partitions*bounds["candidatesPerPartition"],
        "candidatesPerPartition":bounds["candidatesPerPartition"],
        "rowVisitsUpperBound":upper_rows, "maxRowVisits":bounds["maxRowVisits"],
        "rowVisitsKnownWithinLimit":upper_rows<=bounds["maxRowVisits"],
        "actualTableRowsKnown":False, "actualCandidateBytesKnown":False}
    reasons=[]
    for actual, maximum, reason in (("tableCount","maxTables","table_limit"),
            ("partitionCount","maxPartitions","partition_limit"),
            ("descriptorBytes","maxDescriptorBytes","descriptor_bytes_limit"),
            ("largestDescriptorBytes","maxSingleDescriptorBytes","single_descriptor_bytes_limit"),
            ("coverageBytes","maxCoverageBytes","coverage_bytes_limit")):
        if capacity[actual]>capacity[maximum]:reasons.append({"reason":reason,"actual":capacity[actual],"limit":capacity[maximum]})
    if not ordered:reasons.append({"reason":"no_executable_rules","actual":0,"limit":None})
    source_proofs = [{"sourceKey":entry["key"],"infoDigest":digest(infos[entry["key"]]),
        "pageCount":infos[entry["key"]]["pageCount"],"rowCount":infos[entry["key"]]["expected"]["rowCount"]} for entry in entries]
    value = {"schemaVersion":SCHEMA_VERSION,"selectionPolicy":SELECTION_POLICY,"authorityVerified":False,
        "analysisRequest":request,"analysisRequestDigest":digest(request),"sourceCount":len(entries),
        "catalogDigest":digest({"schemaVersion":"business-evidence-directory-v2","entries":entries}),
        "sourceProofs":source_proofs,"mappingPlanDigest":mapping_digest,
        "families":families,"requestedCoverage":coverage,"descriptors":ordered,"limits":bounds,
        "capacity":capacity,"canScreen":not reasons,"admissionFailures":reasons,
        "requestedCoveragePlanned":all(item["status"] not in {"unsupported","unavailable"} for item in coverage),
        "limitations":["本计划没有读取事实或核验权限，canScreen只表示允许尝试，不保证筛查成功",
            "请求项与未执行缺口完整保留；不把市场未实现、缺映射、缺基期标成已扫描",
            "rowVisitsUpperBound来自检查点原始行数，不是实际分组数；超过上界预算时仍须按实际扫描计量",
            "单期和比较分别执行；历史窗口候选不自动变成本期操作，候选TopK不是完整明细阅读",
            "来源范围日期覆盖不证明每实体逐日完整；同比闰日区间不按天归一"]}
    value["planDigest"] = digest(value)
    return value
