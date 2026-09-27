"""Small-scale, unregistered Table projection for one sealed-v2 report candidate.

All rows are materialized before either output writer sees them. The resulting
Table tuple is the *same* input to report_files.write_pair for HTML and XLSX.
This bounded v2 candidate deliberately does not stream a v4 reference-scale run.
"""
from __future__ import annotations

import hashlib

from . import (cross_source_sku_window_compare as sku_compare,
               cross_source_window_compare as shop_compare,
               diagnostic_action_plan_table_v1 as diagnostic_table,
               report_composition_v1 as composition)
from .contracts import AnalysisContractError, canonical, digest
from .report_files import Column, Table


SCHEMA = "business-report-composition-table-delivery-candidate-v1"
DIAGNOSTIC_SCHEMA = "business-report-composition-table-delivery-candidate-v2"
AUDIT_KEY = "reconciliation"
TABLE_KEYS = (*composition.TABLES, AUDIT_KEY)
DIAGNOSTIC_TABLE_KEYS = (*composition.TABLES, diagnostic_table.KEY, AUDIT_KEY)
MAX_ROWS = 50_000
MAX_BYTES = 32 * 1024 * 1024
WINDOWS = composition.WINDOWS


def _need(ok, message="组合表格与已核材料不一致"):
    if not ok:
        raise AnalysisContractError(message)


def _json(value):
    return canonical(value) if type(value) in (list, dict) else value


def _table(key, title, note, columns, rows):
    typed = tuple(tuple(_json(cell) for cell in row) for row in rows)
    _need(len(typed) <= MAX_ROWS and all(len(row) == len(columns)
        for row in typed), "组合表行数或列宽超过候选容量")
    return Table(key, title, note, tuple(columns), typed, len(typed))


def _row_digest(table):
    sha = hashlib.sha256()
    for row in table.rows:
        # Mirror report_files.write_pair's Excel precision preservation.
        values = [str(value) if type(value) is int and abs(value) >= 10**15
                  else value for value in row]
        sha.update((canonical(values) + "\n").encode("utf-8"))
    return sha.hexdigest()


def _compare_rows(rows, *, identity_field):
    for entry in rows:
        for baseline in WINDOWS[1:]:
            current = entry["windows"]["current"]
            earlier = entry["windows"][baseline]
            compare = entry["comparisons"][baseline]
            yield (identity_field(entry), entry["sourceFamily"], entry["metric"],
                   baseline, current["sourceKey"], current["status"],
                   current["value"], earlier["sourceKey"], earlier["status"],
                   earlier["value"], compare["status"],
                   compare["difference"], compare["growthRateBps"])


COMPARE_COLUMNS = (
    Column("identity", "维度身份"), Column("sourceFamily", "来源域"),
    Column("metric", "指标"), Column("baseline", "比较期"),
    Column("currentSourceKey", "本期来源键"),
    Column("currentStatus", "本期状态"),
    Column("currentValue", "本期原值", "integer"),
    Column("baselineSourceKey", "比较期来源键"),
    Column("baselineStatus", "比较期状态"),
    Column("baselineValue", "比较期原值", "integer"),
    Column("comparisonStatus", "比较状态"),
    Column("difference", "差额", "integer"),
    Column("growthRateBps", "增长率基点", "integer"),
)


def prepare(plan, sources, infos, context, source_keys, materials, *,
            category_spu_result=None, keyword_headers=None,
            finance=None, b2b=None, market_preview=None,
            owning_proof=None, verify_owning_proof=None,
            diagnostic_action_candidate=None,
            diagnostic_market_result=None,
            verify_diagnostic_market_result=None):
    """Return an immutable Table tuple plus per-table expected writer proofs."""
    summary = composition.compose_candidate(plan, sources, infos, context,
        source_keys, materials, category_spu_result=category_spu_result,
        keyword_headers=keyword_headers, finance=finance, b2b=b2b,
        market_preview=market_preview, owning_proof=owning_proof,
        verify_owning_proof=verify_owning_proof)
    shop = shop_compare.prepare_candidate(plan, sources, infos, context,
                                          source_keys, materials)
    sku = sku_compare.prepare_candidate(plan, sources, infos, context,
                                        source_keys, materials)
    _need(shop["comparisonDigest"] == summary["componentDigests"]["store"]
        and sku["comparisonDigest"] == summary["componentDigests"]["sku"],
        "组合后来源材料发生变化")
    if category_spu_result is not None:
        _need(category_spu_result["comparisonDigest"] ==
              summary["componentDigests"]["categorySpu"])
    tables = []
    daily_rows = []
    for window in WINDOWS:
        for row in materials[window]["shopDayRows"]:
            for family, field in (("erpSales", "erpSales"),
                                  ("erpUnassigned", "erpUnassigned"),
                                  ("netshopSku", "netshopSku"),
                                  ("netshopSpuNative", "netshopSpuNative"),
                                  ("promotion", "promotion")):
                status = (row["sourceDayStatus"]["erpSales"] if family ==
                          "erpUnassigned" else row["sourceDayStatus"][
                              "netshopSpu" if family == "netshopSpuNative"
                              else family])
                for metric, cell in sorted(row[field].items()):
                    daily_rows.append((window, row["date"], family, metric,
                        status, cell["value"], cell["presentRows"],
                        cell["missingRows"], row["id"]))
    tables.append(_table("store_daily", "店铺逐日来源列",
        "ERP、商智和推广分别列示；ERP未分配池为ERP子集。",
        (Column("window", "窗口"), Column("date", "日期"),
         Column("sourceFamily", "来源域"), Column("metric", "指标"),
         Column("status", "覆盖状态"), Column("value", "原值", "integer"),
         Column("presentRows", "有值行", "integer"),
         Column("missingRows", "缺值行", "integer"),
         Column("dayRowId", "逐日行摘要")), daily_rows))
    tables.append(_table("store_comparison", "店铺三期指标比较",
        "只有同来源同指标完整覆盖且基期为正时提供增长率。",
        COMPARE_COLUMNS, _compare_rows((row for row in shop["rows"]
            if row["column"] != "erpUnassigned"),
            identity_field=lambda row: row["column"])))
    tables.append(_table("erp_unassigned", "ERP未分配池核对",
        "此表为ERP净销售的未分配子集，不得与店铺销售再次相加。",
        COMPARE_COLUMNS, _compare_rows((row for row in shop["rows"]
            if row["column"] == "erpUnassigned"),
            identity_field=lambda row: "ERP未分配")))
    dimensions = (("category", "erpCategory", "ERP品类"),
                  ("spu_erp", "erpSpu", "ERP SPU"),
                  ("spu_native", "netshopSpuNative", "商智原生SPU"))
    for key, dimension, title in dimensions:
        rows = (row for row in category_spu_result["rows"]
                if row["dimension"] == dimension) if category_spu_result else ()
        tables.append(_table(key, title + "三期比较",
            "ERP历史身份未核实，历史差额和增长率为缺值；原生SPU独立列示。",
            COMPARE_COLUMNS, _compare_rows(rows,
                identity_field=lambda row: canonical(row["identity"]))))
    tables.append(_table("sku", "SKU三期逐来源比较",
        "缺明确推广SKU为独立不可操作桶；ERP历史归属未证明。",
        COMPARE_COLUMNS, _compare_rows(sku["rows"],
            identity_field=lambda row: row["skuId"] if row["skuId"] is not None
                else "[缺明确推广SKU]")))
    keyword_rows = []
    for window in WINDOWS:
        header = keyword_headers[window] if keyword_headers else None
        keyword_rows.append((window, "header_only" if header else "not_supplied",
            header["source"]["key"] if header else None,
            header["total"] if header else None,
            header["tableBindingDigest"] if header else None))
    tables.append(_table("keyword_sku", "关键词×推广SKU来源覆盖",
        "仅表头证据；尚无完整关键词行流及同job Agent已读，不展示推断数值。",
        (Column("window", "窗口"), Column("status", "状态"),
         Column("sourceKey", "推广来源键"),
         Column("expectedRows", "表头行数", "integer"),
         Column("tableBindingDigest", "表头绑定摘要")), keyword_rows))
    for key, domain, title in (("finance_month", "finance", "财报自然月来源"),
                               ("b2b_daily", "b2b", "B端三期来源")):
        rows = ((row["periodRole"], row["sourceStatus"], row["coverageStatus"],
                 row["sourceKey"], row["owningResultDigest"], row["note"])
                for row in summary["financeB2bProof"]["rows"]
                if row["domain"] == domain)
        tables.append(_table(key, title,
            "仅来源及缺口。财报不日摊，B端与ERP/商智重叠未知，金额不相加。",
            (Column("periodRole", "自然月或窗口"),
             Column("sourceStatus", "来源状态"),
             Column("coverageStatus", "覆盖状态"),
             Column("sourceKey", "来源键"),
             Column("owningResultDigest", "拥有方结果摘要"),
             Column("note", "口径说明")), rows))
    tables.append(_table("market_sample", "市场TOP样本来源",
        "样本不是本店销售或全市场销量；当前无持久Agent已读。",
        (Column("status", "状态"), Column("sourceReportId", "来源报告"),
         Column("manifestDigest", "市场材料摘要")),
        [("sample_only" if market_preview else "not_supplied",
          market_preview["sourceReportId"] if market_preview else None,
          market_preview["marketManifestDigest"] if market_preview else None)]))
    tables.append(_table("action_plan", "30天复核行动",
        "建议均不可自动执行；投放调整需同job引用、负责人和人工审批。",
        (Column("window", "计划窗口"), Column("code", "任务代号"),
         Column("ownerRole", "责任角色"),
         Column("recommendation", "建议"),
         Column("trigger", "触发证据"),
         Column("executionAllowed", "自动执行许可"),
         Column("decisionGate", "决策门槛")),
        ((item["window"], item["code"], item["ownerRole"],
          item["recommendation"], item["trigger"], item["executionAllowed"],
          item["decisionGate"]) for item in summary["actions30Days"])))
    _need(tuple(table.key for table in tables) == composition.TABLES)
    manifests = list(summary["tableManifest"])
    table_keys, schema = TABLE_KEYS, SCHEMA
    if diagnostic_action_candidate is not None:
        diagnostic = diagnostic_table.project(diagnostic_action_candidate,
            summary, verify_current_composition=lambda current: current is summary,
            market_result=diagnostic_market_result,
            verify_market_result=verify_diagnostic_market_result)
        tables.append(diagnostic)
        manifests.append({"tableKey": diagnostic.key,
            "rowCount": diagnostic.row_count,
            "sourceDigest": diagnostic_action_candidate["candidateDigest"],
            "status": "candidate_rows"})
        table_keys, schema = DIAGNOSTIC_TABLE_KEYS, DIAGNOSTIC_SCHEMA
    else:
        _need(diagnostic_market_result is None
              and verify_diagnostic_market_result is None,
              "未启用调整计划时不得单独附带市场Agent候选")
    audit = [{"tableKey": table.key, "rowCount": table.row_count,
              "columnCount": len(table.columns), "rowDigest": _row_digest(table),
              "sourceRowCount": manifest["rowCount"],
              "sourceDigest": manifest["sourceDigest"],
              "tableStatus": manifest["status"]}
             for table, manifest in zip(tables, manifests)]
    audit_table = _table(AUDIT_KEY, "逐表可重算核对",
        "行摘要与HTML及XLSX共享写入器回执逐表核对；来源摘要不等于权威。",
        (Column("tableKey", "表身份"), Column("rowCount", "实际行数", "integer"),
         Column("columnCount", "列数", "integer"),
         Column("rowDigest", "规范行摘要"),
         Column("sourceRowCount", "上游清单行数", "integer"),
         Column("sourceDigest", "来源材料摘要"),
         Column("tableStatus", "来源状态")),
        ((row["tableKey"], row["rowCount"], row["columnCount"],
          row["rowDigest"], row["sourceRowCount"],
          row["sourceDigest"], row["tableStatus"])
          for row in audit))
    tables.append(audit_table)
    _need(tuple(table.key for table in tables) == table_keys
          and sum(table.row_count for table in tables) <= MAX_ROWS
          and sum(len(canonical(list(row)).encode("utf-8")) for table in tables
                  for row in table.rows) <= MAX_BYTES,
          "表格候选总行数或字节超过v2小规模容量")
    body = {"schemaVersion": schema,
            "reportId": summary["reportId"],
            "compositionDigest": summary["compositionDigest"],
            "tableKeys": list(table_keys), "tableAudit": audit,
            "auditTableDigest": _row_digest(audit_table),
            "authorityVerified": False, "registeredRenderer": False,
            "agentReadPersisted": False, "htmlXlsxParityVerified": False,
            "limitations": ["本候选仅v2小规模完整材料，v4全量须另做分片流式核验。",
                "财报、B端和市场只列上下文与缺口，不进入销售数值汇总。",
                "同一Table流由write_pair写HTML/XLSX；只有回执复核后才可声明文件一致。"]}
    if diagnostic_action_candidate is not None:
        body["diagnosticActionCandidateDigest"] = diagnostic_action_candidate[
            "candidateDigest"]
        body["limitations"].append("深度诊断动作仅为同报告表级证据的人审候选；无行级数值、正式Agent或自动执行许可。")
    return {**body, "deliveryDigest": digest(body)}, tuple(tables)
