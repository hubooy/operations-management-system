"""Append the existing complete sealed source and native dimension tables.

The caller owns its verified Reader, spool, source order and report policy.
This helper keeps the original v4/v6 table identity, ordering, titles, notes,
and one-pass row traversal for later versioned report composition.
"""
from business_analysis.contracts import comparison_periods
from business_analysis.results import stream_table
from .policy import digest


def append(spool, sources, expected, pages, source_by_key, views, dimension_names):
    for source in sources:
        key = source["key"]
        def records(key=key):
            for page in pages(key):
                yield from page["items"]
        query = source["query"]
        window = query.get("window", "current")
        period = comparison_periods(query["startDate"], query["endDate"])[window]
        period_name = {"current": "本期", "previous": "环比基期", "yearAgo": "同比基期"}[window]
        note = f'{period_name}：{period["startDate"]} 至 {period["endDate"]}。完整规范明细；金额字段单位为分；推广、ERP和B端口径分别保留。'
        spool.add("raw-"+key, "来源_"+key, note, records(), expected[key]["rowCount"])
    for source in sources:
        key, query = source["key"], source["query"]
        if query.get("window", "current") != "current" or not expected[key]["metrics"]:
            continue
        bases = [None]
        for candidate in sources:
            q = candidate["query"]
            if candidate["domain"] == source["domain"] and q.get("window") in {"previous", "yearAgo"} and {k: v for k, v in q.items() if k != "window"} == {k: v for k, v in query.items() if k != "window"}:
                bases.append(candidate["key"])
        for dimension in views:
            for base in ([None] if dimension == "daily" else bases):
                args = {"baseline_pages": pages(base), "baseline_expected": expected[base]} if base else {}
                with stream_table(pages(key), dimension, expected[key], **args) as (table, rows):
                    period = "环比" if base and source_by_key[base]["query"]["window"] == "previous" else "同比" if base else "本期"
                    note = "；".join(table["limitations"])+"。来源="+key+("，基期="+base if base else "")
                    spool.add("analysis-"+digest([key, dimension, base])[:24], f"{key}_{dimension_names[dimension]}_{period}", note,
                        ({"sourceKey": key, "baselineKey": base, **row} for row in rows), table["total"])
