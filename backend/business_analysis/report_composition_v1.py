"""Pure, report-only composition of independently reconciled business materials.

This is a renderer input candidate, not a report publication or source authority.
The owning service must recheck the sealed report and supply a trusted proof
verifier. Finance, B2B and market remain separately attributed context.
"""
from __future__ import annotations

from . import (cross_source_category_spu_compare as category_spu,
               cross_source_kpi_plan as planning,
               cross_source_sku_window_compare as sku_compare,
               cross_source_window_compare as shop_compare,
               finance_b2b_source_proof as finance_b2b)
from .contracts import AnalysisContractError, canonical, digest, strict_date


SCHEMA = "business-report-composition-candidate-v1"
PROOF_SCHEMA = "business-report-composition-owning-proof-v1"
WINDOWS = planning.WINDOWS
MAX_OUTPUT_BYTES = 512 * 1024
TABLES = ("store_daily", "store_comparison", "erp_unassigned",
          "category", "spu_erp", "spu_native", "sku", "keyword_sku",
          "finance_month", "b2b_daily", "market_sample", "action_plan")


def _need(ok, message="报告组合材料与同报告证明不一致"):
    if not ok:
        raise AnalysisContractError(message)


def _digest_body(value, key):
    _need(type(value) is dict and value.get(key) == digest({name: part
        for name, part in value.items() if name != key}), "组合输入摘要无效")


def _primary(value, schema, plan, *, material_digests=None):
    _digest_body(value, "comparisonDigest")
    _need(value.get("schemaVersion") == schema
          and value.get("reportId") == plan["reportId"]
          and value.get("planDigest") == plan["planDigest"]
          and value.get("rowCount") == len(value.get("rows", []))
          and value.get("authorityVerified") is False
          and value.get("registeredRenderer") is False
          and value.get("crossDomainAmountsAdded") is False,
          "跨来源候选来源或权限状态不符")
    if material_digests is not None:
        _need(value.get("sourceMaterialDigests") == material_digests,
              "店铺与SKU三期未引用相同逐日材料")


def _market(value):
    if value is None:
        return None
    _digest_body(value, "resultDigest")
    _need(value.get("schemaVersion") == "business-market-v2-fifth-read-preview-v1"
          and value.get("serverFullMarketMaterialVerified") is True
          and value.get("persistedRead") is False
          and value.get("registeredTool") is False
          and value.get("authorityVerified") is False
          and value.get("mode") in {"summary", "page", "row"}
          and type(value.get("marketManifestDigest")) is str
          and len(value["marketManifestDigest"]) == 64,
          "市场样本不是未注册的完整来源预览")
    return value


def _historical_fence(rows, *, dimension_key, erp_values):
    for row in rows:
        if row[dimension_key] not in erp_values:
            continue
        _need(all(type(item) is dict
            and item.get("status") == "historical_identity_unverified"
            and item.get("difference") is None
            and item.get("growthRateBps") is None
            for item in row["comparisons"].values()),
            "当前主数据不能证明历史 ERP 品类、SPU 或 SKU 增长")


def _keyword(headers, plan):
    """Accept only owner-rechecked source table headers, never projected rows."""
    if headers is None:
        return None
    _need(type(headers) is dict and set(headers) == set(WINDOWS),
          "关键词三窗口表头须完整声明")
    for window, header in headers.items():
        if header is None:
            _need(plan["sourceKeys"]["promotion"][window] is None,
                  "已选推广来源不能冒充关键词缺源")
            continue
        _need(type(header) is dict
              and header.get("schemaVersion") == "business-promotion-keyword-sku-table-v1"
              and header.get("authorityVerified") is False
              and header.get("sourceWindow") == window
              and header.get("source", {}).get("key") ==
                  plan["sourceKeys"]["promotion"][window]
              and header.get("periods") == plan["periods"]
              and type(header.get("total")) is int and header["total"] >= 0
              and type(header.get("tableBindingDigest")) is str
              and len(header["tableBindingDigest"]) == 64,
              "关键词表头与同店三期推广来源不符")
    return headers


def _manifest(key, state, rows, source, *, note):
    _need(state in {"candidate_rows", "missing_source", "not_supplied",
                    "sample_only", "context_only"})
    return {"tableKey": key, "status": state, "rowCount": rows,
            "sourceDigest": source, "note": note}


def _actions(plan, shop, finance_proof, keyword_headers, market):
    """Evidence-led review tasks, never an automatic bid/budget instruction."""
    gaps = sorted({(row["column"], row["metric"], window,
                    row["windows"][window]["status"])
        for row in shop["rows"] for window in WINDOWS
        if row["windows"][window]["status"] != "observed_rows"})
    unassigned = next((row for row in shop["rows"] if
        row["column"] == "erpUnassigned" and row["metric"] == "netSalesCents"), None)
    pool = unassigned["windows"]["current"] if unassigned else None
    finance_missing = any(row["domain"] == "finance" and
        row["sourceStatus"] in {"not_supplied", "missing_month"}
        for row in finance_proof["rows"])
    b2b_missing = any(row["domain"] == "b2b" and
        row["sourceStatus"] in {"not_supplied", "catalogue_only_missing_source"}
        for row in finance_proof["rows"])
    tasks = [
        ("D01-D07", "source_coverage", "核对缺源、缺日与指标空值；取得拥有方补采或零日证明后重算。",
         {"gapCount": len(gaps), "gapExamples": gaps[:8]}, "data_owner"),
        ("D01-D07", "erp_identity", "复核 ERP 未分配池及退款身份；历史 SKU 归属未证实前不分摊。",
         {"currentUnassignedNetSalesCents": pool["value"] if pool else None,
          "status": pool["status"] if pool else "not_supplied"}, "erp_owner"),
        ("D08-D14", "promotion_keyword", "在完整关键词×明确推广 SKU 来源核验后，人工检查低效词与商品匹配；仅形成试验方案。",
         {"keywordStatus": "available_candidate" if keyword_headers else "not_supplied",
          "marketStatus": "sample_only" if market else "not_supplied"}, "ads_analyst"),
        ("D08-D14", "finance_b2b", "按自然月复核财报，并查证 B 端与 ERP/平台销售包含关系；不摊成每日或增量。",
         {"financeMissing": finance_missing, "b2bMissing": b2b_missing}, "finance_owner"),
        ("D15-D30", "controlled_review", "复核三期同口径指标与市场样本后，由人审批可回滚的投放试验和复盘指标。",
         {"comparisonRule": plan["comparisonRule"],
          "marketStatus": "sample_only" if market else "not_supplied"}, "business_owner"),
    ]
    return [{"id": digest([SCHEMA, plan["reportId"], code, trigger]),
             "window": period, "code": code, "ownerRole": owner,
             "recommendation": description, "trigger": trigger,
             "executionAllowed": False,
             "decisionGate": "owning_source_and_agent_citations_plus_human_approval"}
            for period, code, description, trigger, owner in tasks]


def compose_candidate(plan, sources, infos, context, source_keys, materials, *,
                      category_spu_result=None, keyword_headers=None,
                      finance=None, b2b=None, market_preview=None,
                      owning_proof=None, verify_owning_proof=None):
    """Build a bounded table/action manifest after trusted same-report recheck.

    ``verify_owning_proof`` is an internal owning-service callback, not a
    request-supplied boolean. It must re-read the current sealed report and
    return exactly True for this binding; the pure layer cannot grant authority.
    """
    plan = planning.check_candidate(sources, infos, context, source_keys, plan)
    _need(type(materials) is dict and set(materials) == set(WINDOWS))
    shop = shop_compare.prepare_candidate(plan, sources, infos, context,
                                          source_keys, materials)
    sku = sku_compare.prepare_candidate(plan, sources, infos, context,
                                        source_keys, materials)
    material_digests = {window: materials[window]["materialDigest"]
                        for window in WINDOWS}
    _primary(shop, shop_compare.SCHEMA, plan,
             material_digests=material_digests)
    _primary(sku, sku_compare.SCHEMA, plan,
             material_digests=material_digests)
    _need(sku.get("historicalErpSkuOwnershipVerified") is False
          and sku.get("erpUnassignedExcludedFromSku") is True
          and sku.get("missingPromotionSkuBucketSeparate") is True)
    _historical_fence(sku["rows"], dimension_key="column",
                      erp_values={"erpMatched"})
    category = category_spu_result
    if category is not None:
        _primary(category, category_spu.SCHEMA, plan)
        _need(category.get("historicalErpOwnershipVerified") is False
              and category.get("sourceMaterialProofs", {}).keys() ==
                  material_digests.keys(), "品类/SPU三期来源证明不完整")
        _historical_fence(category["rows"], dimension_key="dimension",
                          erp_values={"erpCategory", "erpSpu"})
        for window in WINDOWS:
            _need(category["sourceMaterialProofs"][window]
                  ["erpRollupManifestDigest"] ==
                  materials[window]["erpRollupManifestDigest"],
                  "品类/SPU与店铺ERP回卷不是同一材料")
            native_key = plan["sourceKeys"]["netshopSpu"][window]
            expected_native = (infos[native_key]["expected"]["evidenceDigest"]
                               if native_key else None)
            _need(category["sourceMaterialProofs"][window]
                  ["netshopSpuEvidenceDigest"] == expected_native,
                  "品类/SPU原生商智来源与店铺计划不一致")
    keyword = _keyword(keyword_headers, plan)
    market = _market(market_preview)
    finance_proof = finance_b2b.build_candidate(finance=finance, b2b=b2b)
    if b2b is not None:
        _need(b2b["shop"] == plan["shop"] and all(
            b2b["material"]["windows"][window]["period"] ==
                {key: plan["periods"][window][key]
                 for key in ("startDate", "endDate")}
            for window in WINDOWS), "B端店铺或三期窗口与报告不一致")
        for window in WINDOWS:
            coverage = b2b["sourceSummary"][window]["coverage"]
            if coverage is not None:
                first = strict_date(plan["periods"][window]["startDate"])
                last = strict_date(plan["periods"][window]["endDate"])
                _need(all(first <= strict_date(day) <= last for day in
                    coverage.get("missingDates", [])),
                    "B端缺日不属于报告比较窗口")
    component_digests = {"store": shop["comparisonDigest"],
                         "sku": sku["comparisonDigest"],
                         "categorySpu": category["comparisonDigest"] if category else None,
                         "keyword": digest(keyword) if keyword else None,
                         "financeB2b": finance_proof["candidateDigest"],
                         "market": market["resultDigest"] if market else None}
    binding = {"schemaVersion": PROOF_SCHEMA,
               "reportId": plan["reportId"],
               "planDigest": plan["planDigest"],
               "evidenceRunId": context["evidenceRunId"],
               "sealedDigest": context["sealedDigest"],
               "materialDigests": material_digests,
               "componentDigests": component_digests,
               "externalContextSameReportClaimed": False}
    _need(type(owning_proof) is dict and owning_proof ==
          {**binding, "bindingDigest": digest(binding)}
          and callable(verify_owning_proof),
          "缺少当前封存报告的拥有方绑定证明")
    _need(verify_owning_proof(owning_proof, binding) is True,
          "拥有方未确认同报告来源及当前版本")
    manifest = [
        _manifest("store_daily", "candidate_rows", sum(len(materials[w]["shopDayRows"])
            for w in WINDOWS), digest(material_digests), note="ERP/商智/推广逐来源日列"),
        _manifest("store_comparison", "candidate_rows", shop["rowCount"],
            component_digests["store"], note="同比与环比仅完整同来源指标"),
        _manifest("erp_unassigned", "candidate_rows", sum(
            row["column"] == "erpUnassigned" for row in shop["rows"]),
            component_digests["store"], note="ERP 未分配池是 ERP 子集，不与店铺相加"),
        _manifest("category", "candidate_rows" if category else "not_supplied",
            sum(row["dimension"] == "erpCategory" for row in category["rows"])
                if category else None, component_digests["categorySpu"],
            note="ERP 历史品类归属未核实"),
        _manifest("spu_erp", "candidate_rows" if category else "not_supplied",
            sum(row["dimension"] == "erpSpu" for row in category["rows"])
                if category else None, component_digests["categorySpu"],
            note="当前主数据映射不得当作历史归属"),
        _manifest("spu_native", "candidate_rows" if category else "not_supplied",
            sum(row["dimension"] == "netshopSpuNative" for row in category["rows"])
                if category else None, component_digests["categorySpu"],
            note="商智原生 SPU 与 SKU 汇卷分列"),
        _manifest("sku", "candidate_rows", sku["rowCount"],
            component_digests["sku"], note="缺明确推广 SKU 独立不可操作桶"),
        _manifest("keyword_sku", "candidate_rows" if keyword else "not_supplied",
            sum(item["total"] for item in keyword.values() if item)
                if keyword else None, component_digests["keyword"],
            note="仅表头覆盖，后续需拥有方全表分页与 Agent 引用"),
        _manifest("finance_month", "context_only" if finance else "not_supplied",
            sum(row["domain"] == "finance" for row in finance_proof["rows"]),
            component_digests["financeB2b"], note="财报自然月背景，不日摊"),
        _manifest("b2b_daily", "context_only" if b2b else "not_supplied", 3,
            component_digests["financeB2b"], note="B端重叠关系未知，不算增量"),
        _manifest("market_sample", "sample_only" if market else "not_supplied",
            None, component_digests["market"], note="市场 TOP 样本非全市场及本店销售"),
    ]
    _need(tuple(item["tableKey"] for item in manifest) == TABLES[:-1])
    coverage_rows = [{"family": family, "window": window,
                      "sourceKey": plan["sourceKeys"][family][window],
                      "sourceStatus": plan["sourceStatus"][family][window],
                      "coverage": plan["sourceBindings"][key]["coverage"] if key
                          else None}
        for family in planning.FAMILIES for window in WINDOWS
        for key in (plan["sourceKeys"][family][window],)]
    actions = _actions(plan, shop, finance_proof, keyword, market)
    manifest.append(_manifest("action_plan", "candidate_rows", len(actions),
        digest(actions), note="仅供人工复核，不自动调价、投放或预算执行"))
    value = {"schemaVersion": SCHEMA, "reportId": plan["reportId"],
             "planDigest": plan["planDigest"], "periods": plan["periods"],
             "comparisonRule": plan["comparisonRule"],
             "owningBindingDigest": owning_proof["bindingDigest"],
             "componentDigests": component_digests,
             "tableManifest": manifest, "coverageRows": coverage_rows,
             "financeB2bProof": finance_proof,
             "actions30Days": actions,
             "sameReportPrimaryBindingRechecked": True,
             "externalContextSameReportClaimed": False,
             "historicalErpIdentityVerified": False,
             "shopUniqueVisitorsAvailable": False,
             "crossDomainAmountsAdded": False,
             "agentReadPersisted": False,
             "htmlXlsxParityVerified": False,
             "authorityVerified": False, "registeredRenderer": False}
    _need(len(canonical(value).encode("utf-8")) <= MAX_OUTPUT_BYTES,
          "组合清单超过固定容量，拒绝截断")
    return {**value, "compositionDigest": digest(value)}
