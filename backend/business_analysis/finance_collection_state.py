"""Pure verification of complete signed finance page sequences.

Checkpoints are bounded summaries, not authorization or durable evidence.
The future AI owner must bind them to real immutable chunks and a CAS version.
"""
from __future__ import annotations

import json
import re

from . import evidence_v3, finance_source
from .contracts import AnalysisContractError, MAX_SAFE_INTEGER, canonical, comparison_periods, digest

SCHEMA = "business-finance-collection-checkpoint-v1"
RESULT_SCHEMA = "business-finance-collection-result-v1"
PAGE_SCHEMA = "business-finance-owned-page-v1"
PAGE_BYTES = 38_000
CHECKPOINT_BYTES = 32_768
TOTAL_BYTES = 64 * 1024 * 1024
MAX_DATA_PAGES = 1_999  # Reserve one immutable final header within the 2,000-chunk ledger.
FINAL_HEADER_RESERVE = 38_000
ZERO = "0" * 64
COUNT_FIELDS = ("scopeRows", "summaryRows", "kingdeeRows", "totalRows", "detailRows", "mergedRows")
STATE_FIELDS = frozenset(("schemaVersion", "queryDigest", "sourceRef", "sourceRevision", "publication",
    "periodAlignment", "totalRows", "rowsRead", "lastId", "nextOffset", "pageCount", "storedBytes",
    "lastPageDigest", "pageChainDigest", "rowChainDigest", "monthCounts", "metricStates", "finished",
    "persistentEvidenceVerified", "checkpointDigest"))
PAGE_FIELDS = frozenset(("schemaVersion", "sourceRef", "sourceRevision", "query", "periodAlignment",
    "publication", "rows", "pageEvidence", "pagination", "sourceAuthorityVerified",
    "persistentEvidenceVerified", "pageDigest"))


def _require(ok, reason):
    if not ok:
        raise AnalysisContractError(reason)


def _fields(value, names, reason):
    _require(type(value) is dict and set(value) == set(names), reason)


def _integer(value, lo, hi, reason):
    _require(type(value) is int and lo <= value <= hi, reason)


def _sha(value, reason):
    _require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None, reason)


def _query(trusted_query):
    _fields(trusted_query, {"months", "scope", "analysisPeriod"}, "可信财报查询字段无效")
    period = trusted_query["analysisPeriod"]
    _fields(period, {"startDate", "endDate"}, "可信财报日区间无效")
    comparison_periods(period["startDate"], period["endDate"])
    query, alignment = evidence_v3._finance_query(trusted_query, (period["startDate"], period["endDate"]))
    return query, alignment


def _revision(value):
    _require(type(value) is str and re.fullmatch(r"(?:0|[1-9][0-9]*):[0-9a-f]{64}", value) is not None,
             "财报页完整版本无效")
    number, source_digest = value.split(":", 1)
    _integer(int(number), 0, MAX_SAFE_INTEGER, "财报页版本超出无损范围")
    return {"revision": int(number), "source_digest": source_digest}


def _publication(value, query, revision):
    _fields(value, {"months", "batches", "missingMonths"}, "财报发布批次字段无效")
    _require(type(value["months"]) is list and type(value["batches"]) is list
             and type(value["missingMonths"]) is list, "财报发布批次类型无效")
    # The original source contract rejects incomplete, duplicated, foreign and
    # non-completed month/batch relationships without inventing absent months.
    finance_source.build([], query={key: query[key] for key in ("months", "scope")},
        revision=revision, months=value["months"], batches=value["batches"],
        analysis_period=query["analysisPeriod"])
    missing = [month for month in query["months"] if month not in {item["month"] for item in value["months"]}]
    _require(value["missingMonths"] == missing, "财报缺月与真实发布关系不一致")
    return json.loads(canonical(value))


def _state(value, query):
    _fields(value, STATE_FIELDS, "财报检查点字段集合无效")
    _require(value["schemaVersion"] == SCHEMA and value["persistentEvidenceVerified"] is False,
             "财报检查点版本或证明标志无效")
    _sha(value["checkpointDigest"], "财报检查点摘要无效")
    expected = digest({key: item for key, item in value.items() if key != "checkpointDigest"})
    _require(value["checkpointDigest"] == expected, "财报检查点内容摘要无效")
    _require(len(canonical(value).encode("utf-8")) <= CHECKPOINT_BYTES,
             "财报检查点超过持久容量")
    _require(value["queryDigest"] == digest(query), "财报检查点查询身份变化")
    _sha(value["sourceRef"], "财报检查点来源身份无效")
    revision = _revision(value["sourceRevision"])
    _publication(value["publication"], query, revision)
    _require(value["periodAlignment"] == finance_source._period(
        {key: query[key] for key in ("months", "scope")}, query["analysisPeriod"]),
        "财报检查点自然月与日区间关系变化")
    for key, lo, hi in (("totalRows", 0, finance_source.MAX_ROWS), ("rowsRead", 0, finance_source.MAX_ROWS),
                        ("lastId", 0, MAX_SAFE_INTEGER), ("pageCount", 1, MAX_DATA_PAGES),
                        ("storedBytes", 1, TOTAL_BYTES - FINAL_HEADER_RESERVE)):
        _integer(value[key], lo, hi, "财报检查点计数无效")
    _require(value["rowsRead"] <= value["totalRows"] and (value["rowsRead"] == 0) == (value["lastId"] == 0)
             and type(value["finished"]) is bool, "财报检查点进度不一致")
    _require(value["rowsRead"] > 0 or value["totalRows"] == 0 and value["finished"],
             "财报检查点不可在有事实时保持空页")
    for key in ("lastPageDigest", "pageChainDigest", "rowChainDigest"):
        _sha(value[key], "财报检查点链摘要无效")
    _require(value["nextOffset"] == (None if value["finished"] else value["rowsRead"])
             and (not value["finished"] or value["rowsRead"] == value["totalRows"]),
             "财报检查点后续偏移无效")
    _fields(value["monthCounts"], query["months"], "财报月份计数字段无效")
    _fields(value["metricStates"], query["months"], "财报核心指标字段无效")
    for month in query["months"]:
        counts = value["monthCounts"][month]
        _fields(counts, COUNT_FIELDS, "财报月份计数结构无效")
        for number in counts.values(): _integer(number, 0, value["rowsRead"], "财报月份计数范围无效")
        _require(counts["summaryRows"] + counts["kingdeeRows"] == counts["scopeRows"]
                 and counts["totalRows"] + counts["detailRows"] == counts["scopeRows"]
                 and counts["mergedRows"] <= counts["scopeRows"],
                 "财报月份区段或合计计数不一致")
        metrics = value["metricStates"][month]
        _fields(metrics, finance_source.CORE_METRICS, "财报核心指标集合无效")
        for state in metrics.values():
            _require(type(state) is list and len(state) == 3 and type(state[0]) is int and 0 <= state[0] <= 2,
                     "财报核心指标状态无效")
            _require(state[0] <= counts["summaryRows"], "财报核心指标超过经营汇总行数")
            _require((state[0] == 0 and state[1:] == [None, None])
                     or (state[0] == 2 and state[1:] == [None, None])
                     or (state[0] == 1 and state[1] in ("amount", "rate", "number", "text")
                         and (state[2] is None or type(state[2]) is int and -MAX_SAFE_INTEGER <= state[2] <= MAX_SAFE_INTEGER)),
                     "财报核心指标原值状态无效")
    _require(sum(item["scopeRows"] for item in value["monthCounts"].values()) == value["rowsRead"],
             "财报检查点月份行数不一致")
    return json.loads(canonical(value))


def _pack(value):
    result = {**value, "checkpointDigest": digest(value)}
    _require(len(canonical(result).encode("utf-8")) <= CHECKPOINT_BYTES,
             "财报检查点超过持久容量")
    return json.loads(canonical(result))


def consume(checkpoint, page, *, trusted_query):
    """Advance one page; the trusted query must come from the owner directory."""
    query, alignment = _query(trusted_query)
    state = _state(checkpoint, query) if checkpoint is not None else None
    _require(state is None or state["finished"] is False, "完整财报来源不可再消费页面")
    _fields(page, PAGE_FIELDS, "财报页字段集合无效")
    _require(page["schemaVersion"] == PAGE_SCHEMA
             and page["sourceAuthorityVerified"] is False
             and page["persistentEvidenceVerified"] is False
             and page["query"] == query and page["periodAlignment"] == alignment,
             "财报页来源协议或自然月口径不一致")
    _sha(page["sourceRef"], "财报页来源身份无效")
    revision = _revision(page["sourceRevision"])
    publication = _publication(page["publication"], query, revision)
    _sha(page["pageDigest"], "财报页摘要格式无效")
    _require(page["pageDigest"] == digest({key: item for key, item in page.items() if key != "pageDigest"}),
             "财报页内容摘要不一致")
    page_bytes = len(canonical(page).encode("utf-8"))
    _require(page_bytes <= PAGE_BYTES, "财报页超过完整UTF-8容量")
    _fields(page["pageEvidence"], {"rowCount", "sha256"}, "财报页行证明无效")
    _fields(page["pagination"], {"offset", "returned", "total", "nextOffset", "nextLastId"},
            "财报页分页字段无效")
    rows, paging = page["rows"], page["pagination"]
    _require(type(rows) is list and len(rows) <= 100, "财报页事实行数无效")
    _integer(page["pageEvidence"]["rowCount"], 0, 100, "财报页行证明计数无效")
    _require(page["pageEvidence"]["rowCount"] == len(rows)
             and page["pageEvidence"]["sha256"] == digest(rows), "财报页行摘要不一致")
    _integer(paging["offset"], 0, finance_source.MAX_ROWS, "财报页偏移无效")
    _integer(paging["returned"], 0, 100, "财报页返回行数无效")
    _integer(paging["total"], 0, finance_source.MAX_ROWS, "财报页总行数无效")
    _require(paging["returned"] == len(rows) and paging["offset"] + len(rows) <= paging["total"],
             "财报页计数不一致")
    expected_offset = 0 if state is None else state["rowsRead"]
    _require(paging["offset"] == expected_offset and (rows or paging["total"] == 0 and state is None),
             "财报页缺行、重复或乱序")
    next_offset = paging["offset"] + len(rows)
    _require(paging["nextOffset"] == (next_offset if next_offset < paging["total"] else None)
             and paging["nextLastId"] == (rows[-1]["id"] if next_offset < paging["total"] else None),
             "财报页后继行边界无效")
    if state is not None:
        _require(page["sourceRef"] == state["sourceRef"]
                 and page["sourceRevision"] == state["sourceRevision"]
                 and publication == state["publication"]
                 and paging["total"] == state["totalRows"],
                 "财报续页来源、完成批次或总行数变化")
    counts = (json.loads(canonical(state["monthCounts"])) if state else
              {month: {key: 0 for key in COUNT_FIELDS} for month in query["months"]})
    metrics = (json.loads(canonical(state["metricStates"])) if state else
               {month: {metric: [0, None, None] for metric in finance_source.CORE_METRICS}
                for month in query["months"]})
    available = {item["month"]: item for item in publication["months"]}
    last_id = state["lastId"] if state else 0
    row_chain = state["rowChainDigest"] if state else ZERO
    for record in rows:
        _fields(record, finance_source.ROW_FIELDS | {"rowId"}, "财报行字段集合无效")
        raw = {key: record[key] for key in finance_source.ROW_FIELDS}
        row = finance_source._row(raw, {key: query[key] for key in ("months", "scope")}, available)
        _require(row["id"] > last_id and record["rowId"] == digest({"revision": revision, "row": row}),
                 "财报真实行ID或原值摘要不连续")
        last_id = row["id"]
        row_chain = digest({"previous": row_chain, "row": record})
        counted = counts[row["month"]]
        counted["scopeRows"] += 1
        counted["summaryRows" if row["section"] == "summary" else "kingdeeRows"] += 1
        counted["totalRows" if row["is_total"] else "detailRows"] += 1
        if row["source_row_count"] > 1: counted["mergedRows"] += 1
        if row["section"] == "summary" and row["metric_key"] in finance_source.CORE_METRICS:
            metric = row["metric_key"]
            existing = metrics[row["month"]][metric]
            field = "amount_cents" if finance_source.CORE_METRICS[metric] == "amount" else "rate_bps"
            metrics[row["month"]][metric] = ([1, row["value_type"], row[field]] if existing[0] == 0
                                             else [2, None, None])
    previous_page = state["pageChainDigest"] if state else ZERO
    result = {"schemaVersion": SCHEMA, "queryDigest": digest(query), "sourceRef": page["sourceRef"],
              "sourceRevision": page["sourceRevision"], "publication": publication,
              "periodAlignment": alignment, "totalRows": paging["total"], "rowsRead": next_offset,
              "lastId": last_id, "nextOffset": paging["nextOffset"],
              "pageCount": (state["pageCount"] if state else 0) + 1,
              "storedBytes": (state["storedBytes"] if state else 0) + page_bytes,
              "lastPageDigest": page["pageDigest"],
              "pageChainDigest": digest({"previous": previous_page, "page": page["pageDigest"]}),
              "rowChainDigest": row_chain, "monthCounts": counts, "metricStates": metrics,
              "finished": paging["nextOffset"] is None, "persistentEvidenceVerified": False}
    _require(result["pageCount"] <= MAX_DATA_PAGES
             and result["storedBytes"] <= TOTAL_BYTES - FINAL_HEADER_RESERVE,
             "财报来源超过完整持久容量，不得截断完成")
    return _state(_pack(result), query)


def next_arguments(checkpoint, *, trusted_query):
    query, _ = _query(trusted_query)
    state = _state(checkpoint, query)
    _require(not state["finished"], "完整财报来源没有下一页")
    return {"query": query, "offset": state["nextOffset"], "afterId": state["lastId"],
            "expectedSourceRef": state["sourceRef"], "expectedRevision": state["sourceRevision"]}


def result(checkpoint, *, trusted_query):
    query, _ = _query(trusted_query)
    state = _state(checkpoint, query)
    _require(state["finished"], "财报页面尚未完整读取")
    published = {item["month"] for item in state["publication"]["months"]}
    coverage = []
    for month in query["months"]:
        statuses = {}
        for metric, kind in finance_source.CORE_METRICS.items():
            found, value_type, value = state["metricStates"][month][metric]
            status = ("missing_month" if month not in published else "missing_subject" if found == 0
                      else "ambiguous_subject" if found == 2 else "non_numeric" if value_type != kind
                      else "missing_value" if value is None else "present")
            statuses[metric] = {"status": status, "value": value if status == "present" else None,
                                "unit": "CNY_cent" if kind == "amount" else "basis_point"}
        coverage.append({"month": month, "published": month in published,
                         **state["monthCounts"][month], "metrics": statuses})
    return {"schemaVersion": RESULT_SCHEMA, "sourceRef": state["sourceRef"],
            "sourceRevision": state["sourceRevision"], "publication": state["publication"],
            "periodAlignment": state["periodAlignment"], "rowCount": state["rowsRead"],
            "pageCount": state["pageCount"], "storedBytes": state["storedBytes"],
            "rowChainDigest": state["rowChainDigest"], "pageChainDigest": state["pageChainDigest"],
            "coverage": coverage, "persistentEvidenceVerified": False,
            "businessCoverageVerified": False, "dailyProrationAllowed": False,
            "sumSourceRates": False, "inferSkuProfit": False}
