"""Internal deterministic screening of complete result-table streams.

prepare() owns each opener context and returns only after normal exhaustion AND
normal context exit. Its result is deliberately prepared_unpublished: caller
descriptors, expected row counts and callbacks are NOT evidence authorities.
A later owning service must authenticate the principal, freeze the full plan,
reconcile sealed sources and revalidate after all contexts. There is no publish
API, Django dependency, model execution, filesystem use or legacy integration.

Limits bound counts and serialized UTF-8 payloads, not Python process RSS. Only
one source context/row is active. Row-ID tracking is bounded per table; retained
candidates, descriptors and coverage have separate byte limits. Nothing is
silently dropped except explicitly counted ranking candidates beyond top K.
"""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import heapq
import math
import re

from .contracts import AnalysisContractError, MAX_SAFE_INTEGER, canonical, comparison_periods, digest
from .evidence_v2 import normalize_sources
from .results import VIEWS

SCHEMA_VERSION = "business-diagnostic-screen-prepared-v1"
ALGORITHM_VERSION = "diagnostic-signs-v1"
DESCRIPTOR_SCHEMA = "business-diagnostic-table-v1"
LIMITS = {"maxTables": 256, "maxRowsPerTable": 250000, "maxRowVisits": 2000000,
    "maxPartitions": 64, "candidatesPerPartition": 16, "maxCandidateBytes": 8*1024*1024,
    "maxDescriptorBytes": 1024*1024, "maxCoverageBytes": 1024*1024,
    "maxRowBytes": 128*1024, "maxHeaderBytes": 128*1024,
    "maxRowIdBytes": 16*1024*1024}
_NETSHOP = {("京东", "promotion"): ("jd_promotion", "ad"),
    ("天猫", "promotion"): ("tmall_promotion", "promotion_daily"),
    ("京东", "sku"): ("jd_sku_daily", "sku_daily"),
    ("京东", "spu"): ("jd_sku_daily", "spu_daily"),
    ("天猫", "spu"): ("tmall_product_daily", "spu_daily"),
    ("京东", "b2b"): ("jd_b2b", "b2b")}
# family, required integer metrics, needs baseline
RULES = {
    "promotion_spend_up_gmv_down": ("promotion", ("spendCents", "reportedGmvCents"), True),
    "promotion_spend_without_reported_gmv": ("promotion", ("spendCents", "reportedGmvCents"), False),
    "erp_refund_present": ("erp", ("refundCents",), False),
    "erp_refund_up_sales_not_up": ("erp", ("refundCents", "positiveSalesCents"), True),
    "erp_gross_profit_negative": ("erp", ("grossProfitCents",), False),
    "erp_fee_up_net_sales_down": ("erp", ("feeCents", "netSalesCents"), True),
    "product_traffic_up_payment_down": ("product", ("productDayVisitors", "paymentCents"), True),
}


def _require(ok, message="诊断筛查合同无效"):
    if not ok:
        raise AnalysisContractError(message)


def _fields(value, fields):
    _require(type(value) is dict and len(value) == len(fields) and set(value) == set(fields), "筛查字段集合无效")


def _integer(value, minimum=0, maximum=MAX_SAFE_INTEGER):
    _require(type(value) is int and minimum <= value <= maximum, "筛查整数超出精确范围")
    return value


def _sha(value):
    _require(type(value) is str and len(value) == 64 and re.fullmatch(r"[a-f0-9]{64}", value) is not None, "筛查摘要格式无效")
    return value


def _identifier(value):
    _require(type(value) is str and 1 <= len(value) <= 160 and re.fullmatch(r"[A-Za-z0-9_-]+", value) is not None, "筛查身份格式无效")
    return value


def _bounded(value, maximum):
    """Bound traversal before serializing an untrusted nested object."""
    state = [0, 0]
    def visit(item, depth=0):
        state[0] += 1
        _require(depth <= 16 and state[0] <= 20000, "筛查结构超过容量")
        if type(item) is dict:
            _require(len(item) <= 512, "筛查对象字段过多")
            for key, child in item.items():
                _require(type(key) is str and len(key) <= 200, "筛查键无效")
                visit(key, depth+1); visit(child, depth+1)
        elif type(item) is list:
            _require(len(item) <= 1024, "筛查数组过长")
            for child in item: visit(child, depth+1)
        else:
            _require(item is None or type(item) in (str, int, bool, float), "筛查只接受JSON标量")
            if type(item) is str: _require(len(item) <= maximum, "筛查文本超过容量")
            if type(item) is int: _integer(item, -MAX_SAFE_INTEGER)
            if type(item) is float: _require(math.isfinite(item), "筛查数值非有限")
            try:
                state[1] += len(canonical(item).encode("utf-8"))
            except (UnicodeError, ValueError) as error:
                raise AnalysisContractError("筛查文本或数值无效") from error
            _require(state[1] <= maximum, "筛查内容超过字节容量")
    visit(value)
    raw = canonical(value).encode("utf-8")
    _require(len(raw) <= maximum, "筛查内容超过字节容量")
    return raw


def _source(value):
    _fields(value, {"key", "domain", "query", "sourceRef", "evidenceDigest"})
    _sha(value["sourceRef"]); _sha(value["evidenceDigest"])
    entry = normalize_sources([{k: value[k] for k in ("key", "domain", "query")}])[0]
    return {**{k:entry[k] for k in ("key", "domain", "query")}, "sourceRef": value["sourceRef"], "evidenceDigest": value["evidenceDigest"]}


def _descriptor(value):
    _fields(value, {"schemaVersion", "mode", "dimension", "source", "baseline", "mapping", "ruleIds"})
    _require(value["schemaVersion"] == DESCRIPTOR_SCHEMA and type(value["mode"]) is str
        and value["mode"] in {"native", "mapped"} and type(value["dimension"]) is str
        and value["dimension"] in VIEWS, "筛查描述版本或维度无效")
    source = _source(value["source"])
    baseline = _source(value["baseline"]) if value["baseline"] is not None else None
    if baseline:
        a, b = source["query"], baseline["query"]
        _require(source["key"] != baseline["key"] and source["domain"] == baseline["domain"]
            and a["window"] == "current" and b["window"] in {"previous", "yearAgo"}
            and {k:v for k,v in a.items() if k != "window"} == {k:v for k,v in b.items() if k != "window"}
            and value["dimension"] != "daily", "筛查比较范围、来源或窗口不相容")
    mapping = value["mapping"]
    if value["mode"] == "mapped":
        _require(source["domain"] == "sales" and value["dimension"] in {"sku", "spu"}, "映射筛查仅支持ERP SKU/SPU")
        _fields(mapping, {"planDigest", "pairKey", "baselinePairKey", "algorithmVersion", "master"})
        _sha(mapping["planDigest"]); _sha(mapping["pairKey"])
        _require(mapping["algorithmVersion"] == "exact-product-partition-v1", "映射算法版本无效")
        master = _source(mapping["master"])
        _require(master["domain"] == "netshop" and master["query"]["dataset"] == "master"
            and master["query"]["window"] == "current"
            and all(master["query"][k] == source["query"][k] for k in ("platform", "shop", "startDate", "endDate")), "映射主数据范围无效")
        _require(mapping["pairKey"] == digest([mapping["algorithmVersion"], source["key"], master["key"]]), "映射pairKey无效")
        expected = digest([mapping["algorithmVersion"], baseline["key"], master["key"]]) if baseline else None
        _require(mapping["baselinePairKey"] == expected, "映射基期须使用相同主数据")
        mapping = {**mapping, "master": master}
    else:
        _require(mapping is None, "原生筛查不得带映射绑定")
    rules = value["ruleIds"]
    _require(type(rules) is list and 1 <= len(rules) <= len(RULES)
        and all(type(rule) is str and rule in RULES for rule in rules)
        and len(set(rules)) == len(rules), "筛查规则未知或重复")
    return {**value, "source": source, "baseline": baseline, "mapping": mapping, "ruleIds": sorted(rules)}


def _metadata(metadata, source, *, mapped=False):
    _require(type(metadata) is dict, "筛查来源元信息缺失")
    coverage = metadata.get("coverage")
    _require(type(coverage) is dict and type(coverage.get("status")) is str
        and coverage["status"] in {"dates_present", "missing_dates", "no_records"}, "筛查来源日期覆盖缺失或无效")
    if mapped:
        # Mapped headers retain the sealed checkpoint metadata, which does not
        # contain raw page filters. The exact query is verified below through
        # the mapping binding; never manufacture missing page metadata here.
        _require(source["domain"] == "sales" and type(metadata.get("sourceRevision")) is str
            and 1 <= len(metadata["sourceRevision"]) <= 128, "筛查映射来源版本缺失")
        return
    _require(type(metadata.get("filters")) is dict, "筛查来源元信息缺失")
    filters = metadata["filters"]
    domain, query = source["domain"], source["query"]
    checked_query = query if domain == "sales" else {k:v for k,v in query.items() if k not in {"startDate", "endDate"}}
    _require(all(filters.get(k, "current" if k == "window" else None) == v for k,v in checked_query.items())
        and canonical(filters.get("periods")) == canonical(comparison_periods(query["startDate"], query["endDate"])), "筛查来源查询被替换")
    if domain == "sales": _require(metadata.get("source") == "erp_sales", "筛查ERP来源身份无效")
    elif domain == "market": _require(metadata.get("source") == "market_daily_top" and filters.get("shop") == "", "筛查市场来源身份无效")
    else:
        expected = _NETSHOP.get((query["platform"], query["dataset"]))
        _require(expected is not None and (metadata.get("source"), metadata.get("sourceDataset")) == expected, "筛查网店来源身份无效")


def _header(header, descriptor, binding, limits):
    _bounded(header, limits["maxHeaderBytes"])
    _require(type(header) is dict and header.get("dimension") == descriptor["dimension"], "筛查表维度被替换")
    mapped = descriptor["mode"] == "mapped"
    _require(header.get("schemaVersion") == ("business-mapped-result-table-v1" if mapped else "business-result-table-v1"), "筛查表schema无效")
    _integer(header.get("total"), maximum=limits["maxRowsPerTable"])
    _require(header.get("rows", []) == [], "筛查header不得夹带不完整数据页")
    for side, proof_key, metadata_key in (("source", "source", "sourceMetadata"), ("baseline", "baselineSource", "baselineMetadata")):
        source, proof, metadata = descriptor[side], header.get(proof_key), header.get(metadata_key)
        if source is None:
            _require(proof is None and metadata is None, "筛查基期被添加")
        else:
            _require(type(proof) is dict and all(proof.get(k) == source[k] for k in ("sourceRef", "evidenceDigest")), "筛查来源证明被替换")
            _metadata(metadata, source, mapped=mapped)
    baseline = descriptor["baseline"]
    _require(header.get("comparisonWindow") == (baseline["query"]["window"] if baseline else None), "筛查比较窗口被替换")
    comparable = bool(baseline and all(type(header[key].get("coverage")) is dict
        and header[key]["coverage"].get("status") == "dates_present" for key in ("sourceMetadata", "baselineMetadata")))
    _require(type(header.get("dateCoverageComparable")) is bool and header["dateCoverageComparable"] == comparable, "筛查日期比较标志无效")
    if mapped:
        _require(header.get("sourceWindow") == descriptor["source"]["query"]["window"], "筛查映射本期窗口被替换")
        _require(header.get("algorithmVersion") == "business-mapped-results-v1"
            and header.get("mappingAlgorithmVersion") == "exact-product-partition-v1"
            and header.get("historicalMapping") is False, "筛查映射算法/历史口径无效")
        _sha(header.get("bindingDigest"))
        for source_key, key in (("source", "binding"), ("baseline", "baselineBinding")):
            source, actual = descriptor[source_key], header.get(key)
            if source is None: _require(actual is None, "筛查映射基期被添加"); continue
            _require(type(actual) is dict, "筛查映射绑定无效")
            _integer(actual.get("evidenceVersion"), 1)
            _require(type(actual) is dict and actual.get("schemaVersion") == "business-product-mapping-binding-v1"
                and actual.get("algorithmVersion") == descriptor["mapping"]["algorithmVersion"]
                and all(actual.get(k) == binding[k] for k in ("evidenceRunId", "evidenceVersion", "evidencePlanDigest", "catalogDigest", "sealedDigest")), "筛查映射证据绑定无效")
            for role, entry in (("sales", source), ("master", descriptor["mapping"]["master"])):
                _require(actual.get(role) == {"sourceKey":entry["key"], "sourceRef":entry["sourceRef"], "queryDigest":digest(entry["query"])}, "筛查映射来源绑定无效")
    return comparable


def _family(descriptor):
    source = descriptor["source"]
    if source["domain"] == "sales":
        return "erp" if descriptor["mode"] == "mapped" or descriptor["dimension"] == "shop" else None
    if source["domain"] == "netshop":
        if source["query"]["dataset"] == "promotion": return "promotion"
        if source["query"]["dataset"] in {"sku", "spu", "b2b"}: return "product"
    return None


def _row(row, index, descriptor, ids, limits):
    raw = _bounded(row, limits["maxRowBytes"])
    _require(type(row) is dict and type(row.get("rowIndex")) is int and row["rowIndex"] == index, "筛查行序不连续")
    row_id = _sha(row.get("id"))
    _require(row_id not in ids, "筛查行身份重复")
    _require((len(ids)+1)*64 <= limits["maxRowIdBytes"], "筛查行身份跟踪超过容量")
    ids.add(row_id)
    entity = row.get("entity")
    _require(type(entity) is dict and entity.get("platform") == descriptor["source"]["query"]["platform"], "筛查行平台不一致")
    query = descriptor["source"]["query"]
    _require(entity.get("shopName") == query.get("shop", ""), "筛查行店铺不一致")
    _require(type(row.get("dimensionMissing")) is bool, "筛查维度缺失标志无效")
    if descriptor["mode"] == "mapped":
        _require(type(entity.get("mappingStatus")) is str and entity["mappingStatus"] in {"matched", "ambiguous", "unmatched"}, "筛查映射状态无效")
    _require(all(entity.get(key) is None or type(entity[key]) is str for key in VIEWS[descriptor["dimension"]]), "筛查维度身份须为文本或空值")
    if descriptor["baseline"] is None:
        _require(row.get("baselineRowCount") is None and row.get("baselineMetrics") is None, "筛查未请求基期却出现基期数据")
    for name, metrics_key in (("currentRowCount", "metrics"), ("baselineRowCount", "baselineMetrics")):
        count = row.get(name)
        if count is not None: _integer(count, 1)
        metrics = row.get(metrics_key)
        _require(metrics is None or type(metrics) is dict and len(metrics) <= 128, "筛查指标集合无效")
        for metric in (metrics or {}).values():
            if metric is None: continue
            _fields(metric, {"value", "presentRows", "missingRows"})
            present, missing = _integer(metric["presentRows"]), _integer(metric["missingRows"])
            _require(count is not None and present+missing == count, "筛查指标行数不守恒")
            value = metric["value"]
            if value is not None: _integer(value, -MAX_SAFE_INTEGER)
            _require((value is None) == (present == 0), "筛查指标空值与有效行数不一致")
    _require(row.get("currentRowCount") is not None or row.get("baselineRowCount") is not None, "筛查两侧行都缺失")
    return raw


def _evaluate(rule, descriptor, header, row):
    family, metrics, comparison = RULES[rule]
    if _family(descriptor) != family: return "unsupported_source", None
    entity = row["entity"]
    missing = row["dimensionMissing"] or any(entity.get(k) in (None, "") for k in VIEWS[descriptor["dimension"]])
    unresolved = descriptor["mode"] == "mapped" and entity["mappingStatus"] != "matched"
    # Single-period ERP queues retain unresolved amounts, clearly labelled below.
    if (missing or unresolved) and not (descriptor["mode"] == "mapped" and family == "erp" and not comparison):
        return "identity_incomplete", None
    if row.get("currentRowCount") is None: return "current_row_missing", None
    if comparison and descriptor["baseline"] is None: return "baseline_not_requested", None
    if comparison and row.get("baselineRowCount") is None: return "baseline_row_missing", None
    if comparison and not header["dateCoverageComparable"]: return "dates_incomparable", None
    values = []
    for key in (("metrics", "baselineMetrics") if comparison else ("metrics",)):
        result = {}
        for metric in metrics:
            cell = (row.get(key) or {}).get(metric)
            if cell is None or cell["value"] is None or cell["missingRows"]: return "metric_incomplete", None
            value = cell["value"]
            if metric in {"refundCents", "positiveSalesCents", "productDayVisitors"}:
                _require(value >= 0, "退款绝对额、正向销售或访客不能为负")
            result[metric] = value
        values.append(result)
    a, b = values[0], values[1] if comparison else {}
    differences = {key: _integer(a[key]-b[key], -MAX_SAFE_INTEGER) for key in metrics} if comparison else {}
    if rule == "promotion_spend_up_gmv_down": match, score = differences["spendCents"] > 0 and differences["reportedGmvCents"] < 0, differences["spendCents"]
    elif rule == "promotion_spend_without_reported_gmv": match, score = a["spendCents"] > 0 and a["reportedGmvCents"] == 0, a["spendCents"]
    elif rule == "erp_refund_present": match, score = a["refundCents"] > 0, a["refundCents"]
    elif rule == "erp_refund_up_sales_not_up": match, score = differences["refundCents"] > 0 and differences["positiveSalesCents"] <= 0, differences["refundCents"]
    elif rule == "erp_gross_profit_negative": match, score = a["grossProfitCents"] < 0, -a["grossProfitCents"]
    elif rule == "erp_fee_up_net_sales_down": match, score = differences["feeCents"] > 0 and differences["netSalesCents"] < 0, differences["feeCents"]
    else: match, score = differences["productDayVisitors"] > 0 and differences["paymentCents"] < 0, -differences["paymentCents"]
    return None, {"matched":match, "score":score, "current":a, "baseline":b if comparison else None,
        "differences":differences, "identityQualified":not (missing or unresolved)}


@dataclass
class _Candidate:
    # heap root is the worst candidate, so replacing it preserves exact top K.
    score: int
    row_id: str
    data: dict
    size: int

    def __lt__(self, other):
        return self.score < other.score or self.score == other.score and self.row_id > other.row_id


def prepare(binding, descriptors, open_table, *, limits=None, checkpoint=None):
    """Return untrusted-authority prepared JSON after every source context exits.

    open_table receives a fresh descriptor copy and yields (header, rows) from a
    context manager. The rows must be the full native/mapped result stream, not
    a tool page. Even a caller that fabricates every input can obtain only an
    explicitly unpublished result, never a factual authorization certificate.
    """
    from .partitioned import Checkpoint
    checkpoint = Checkpoint.wrap(checkpoint)
    bounds = dict(LIMITS)
    _require(callable(open_table), "筛查须使用内部表context opener")
    if limits is not None:
        _require(type(limits) is dict and not set(limits)-set(bounds), "筛查容量参数未知")
        for key, value in limits.items(): bounds[key] = _integer(value, 1, LIMITS[key])
    _bounded(binding, 4096)
    _fields(binding, {"reportId", "evidenceRunId", "evidenceVersion", "evidencePlanDigest", "catalogDigest", "sealedDigest"})
    _identifier(binding["reportId"]); _identifier(binding["evidenceRunId"]); _integer(binding["evidenceVersion"], 1)
    for key in ("evidencePlanDigest", "catalogDigest", "sealedDigest"): _sha(binding[key])
    binding = deepcopy(binding)
    _require(type(descriptors) is list and 1 <= len(descriptors) <= bounds["maxTables"], "筛查完整表计划超过容量或为空")
    entries, plan_bytes, identities, source_keys = [], 0, set(), {}
    for descriptor_index,value in enumerate(descriptors):
        if checkpoint is not None: checkpoint({"stage":"screen_descriptor","descriptorIndex":descriptor_index,"phase":"before"})
        plan_bytes += len(_bounded(value, bounds["maxHeaderBytes"]))
        _require(plan_bytes <= bounds["maxDescriptorBytes"], "筛查完整描述超过容量")
        entry = _descriptor(deepcopy(value))
        identity = digest({k:v for k,v in entry.items() if k != "ruleIds"})
        _require(identity not in identities, "筛查重复表不得合并")
        identities.add(identity)
        for source in [entry["source"], entry["baseline"], (entry["mapping"] or {}).get("master")]:
            if source:
                raw = canonical(source)
                _require(source_keys.get(source["key"], raw) == raw, "筛查同名来源绑定不一致")
                source_keys[source["key"]] = raw
        entries.append((identity, entry))
        if checkpoint is not None: checkpoint({"stage":"screen_descriptor","descriptorIndex":descriptor_index,"phase":"after"})
    _require(len(source_keys) <= 48, "筛查来源超过完整目录容量")
    entries.sort(key=lambda item:item[0])
    _require(sum(len(entry["ruleIds"]) for _,entry in entries) <= bounds["maxPartitions"], "筛查候选分区超过容量")
    plan = {"algorithmVersion":ALGORITHM_VERSION, "binding":binding, "descriptors":[entry for _,entry in entries], "limits":bounds}
    plan_digest = digest(plan)
    tables, partitions, visits, candidate_bytes = [], [], 0, 0
    for table_key, entry in entries:
        if checkpoint is not None: checkpoint({"stage":"screen_table","tableKey":table_key,"phase":"before"})
        states = {rule:{"ruleId":rule, "eligibleRows":0, "matchedRows":0, "ineligibleReasons":{}, "heap":[]} for rule in entry["ruleIds"]}
        row_hash, ids, scanned, exhausted = hashlib.sha256(), set(), 0, False
        with open_table(deepcopy(entry)) as opened:
            _require(type(opened) in (tuple, list) and len(opened) == 2, "筛查来源context返回无效")
            header, rows = opened
            _header(header, entry, binding, bounds)
            header = deepcopy(header)
            for row in rows:
                if checkpoint is not None and scanned % 100 == 0:
                    checkpoint({"stage":"screen_candidates","tableKey":table_key,"rowOffset":scanned})
                _require(scanned < header["total"] and visits < bounds["maxRowVisits"], "筛查行数超过完整计划或访问容量")
                raw = _row(row, scanned, entry, ids, bounds)
                row_hash.update(len(raw).to_bytes(8, "big")); row_hash.update(raw)
                for rule, state in states.items():
                    reason, value = _evaluate(rule, entry, header, row)
                    if reason:
                        state["ineligibleReasons"][reason] = state["ineligibleReasons"].get(reason, 0)+1
                        continue
                    state["eligibleRows"] += 1
                    if not value["matched"]: continue
                    state["matchedRows"] += 1
                    ref = {"dimension":entry["dimension"], "rowIndex":row["rowIndex"], "rowId":row["id"]}
                    if entry["mode"] == "native": ref.update(sourceKey=entry["source"]["key"], baselineKey=entry["baseline"]["key"] if entry["baseline"] else None)
                    else: ref.update(pairKey=entry["mapping"]["pairKey"], baselinePairKey=entry["mapping"]["baselinePairKey"])
                    data = {"candidateId":digest([plan_digest, table_key, rule, row["id"]]), "ruleId":rule,
                        "tableKey":table_key, "reference":ref, "entity":deepcopy(row["entity"]),
                        **value, "meaning":"numeric_pattern_not_causality_or_final_profit"}
                    size = len(_bounded(data, bounds["maxRowBytes"]))
                    item = _Candidate(value["score"], row["id"], data, size)
                    heap = state["heap"]
                    if len(heap) < bounds["candidatesPerPartition"]:
                        candidate_bytes += size; heapq.heappush(heap, item)
                    elif heap[0] < item:
                        candidate_bytes += size-heap[0].size; heapq.heapreplace(heap, item)
                    _require(candidate_bytes <= bounds["maxCandidateBytes"], "筛查候选超过字节容量，不得减少计划范围")
                scanned += 1; visits += 1
            _require(scanned == header["total"], "筛查未消费完整行数")
            exhausted = True
        # Also catches a hostile context manager suppressing an iterator error.
        if checkpoint is not None: checkpoint.raise_if_failed()
        _require(exhausted, "筛查context吞掉未完成流错误")
        counts = []
        for rule, state in states.items():
            if checkpoint is not None: checkpoint({"stage":"screen_partition","tableKey":table_key,"ruleId":rule})
            candidates = [item.data for item in sorted(state.pop("heap"), key=lambda item:(-item.score, item.row_id))]
            ineligible = sum(state["ineligibleReasons"].values())
            _require(state["eligibleRows"]+ineligible == scanned, "筛查规则计数不守恒")
            count = {**state, "ineligibleRows":ineligible, "scannedRows":scanned,
                "retainedRows":len(candidates), "omittedRows":state["matchedRows"]-len(candidates),
                "supported":_family(entry) == RULES[rule][0],
                "unavailableReason":None if _family(entry) == RULES[rule][0] else "unsupported_source"}
            counts.append(count)
            partitions.append({"partitionKey":digest([table_key, rule]), "tableKey":table_key,
                **count, "candidates":candidates})
        source_metadata, baseline_metadata = header["sourceMetadata"], header.get("baselineMetadata")
        tables.append({"tableKey":table_key, "headerDigest":digest(header), "expectedRows":header["total"],
            "scannedRows":scanned, "rowDigest":row_hash.hexdigest(), "rules":counts,
            "sourceCoverage":deepcopy(source_metadata.get("coverage")),
            "baselineCoverage":deepcopy(baseline_metadata.get("coverage")) if baseline_metadata else None,
            "sourcePeriod":comparison_periods(entry["source"]["query"]["startDate"], entry["source"]["query"]["endDate"])[entry["source"]["query"]["window"]],
            "baselinePeriod":comparison_periods(entry["baseline"]["query"]["startDate"], entry["baseline"]["query"]["endDate"])[entry["baseline"]["query"]["window"]] if baseline_metadata else None,
            "dateCoverageComparable":header["dateCoverageComparable"]})
        _require(len(canonical(tables).encode("utf-8")) <= bounds["maxCoverageBytes"], "筛查覆盖证明超过容量")
        if checkpoint is not None: checkpoint({"stage":"screen_table","tableKey":table_key,"phase":"after"})
    result = {"schemaVersion":SCHEMA_VERSION, "algorithmVersion":ALGORITHM_VERSION,
        "status":"prepared_unpublished", "authorityVerified":False, "binding":binding,
        "plan":plan, "planDigest":plan_digest, "coverage":{"streamScanComplete":True,
            "sourceAuthorityVerified":False, "rowVisits":visits, "tableCount":len(tables), "tables":tables},
        "partitions":partitions, "candidateBytes":candidate_bytes,
        "limitations":["仅传入完整表流与固定规则的计算证明；未验证来源授权，不能直接发布报告",
            "候选排名不是全部明细阅读证明；保留、遗漏和完整命中总数分别展示",
            "同一事实跨维度和跨规则可能重复，不得相加损失；数值现象不证明因果",
            "退款为绝对额；毛利为净销售减来源成本未扣费用；费用不是推广费",
            "日期可比仅为所选来源范围有记录，不证明单个SKU逐日完整；单期现象不证明缺日周期完整",
            "单期条件只描述已观察记录，缺日来源零归因成交不代表完整周期零成交；覆盖状态逐表披露",
            "同比是原区间总量比较，闰日夹止可能使两侧天数不同；未按天归一",
            "当前主数据不证明历史归属；未归属单期金额是核查队列而非已识别商品风险"]}
    result["resultDigest"] = digest(result)
    return result
