import { officialPriceBandSql } from "@/lib/market/schema-core";

type MarketOverviewSqlOptions = {
  factWhere?: string;
  where?: string;
  priceBandWhere?: string;
  materialized?: boolean;
  confirmedOnlyPriceBands?: boolean;
  useEffectiveMetricsCache?: boolean;
  rankingLimit?: number;
  rankingOffset?: number;
};

type MarketMonthlyCoverageSqlOptions = {
  source: string;
  selectionSource?: string;
  gmvColumn?: string;
  quantityColumn?: string;
  pageViewsColumn?: string;
  visitorsColumn?: string;
  conversionColumn?: string;
  includeConversion?: boolean;
};

function overviewPriceBandSql(confirmedOnly = false) {
  return officialPriceBandSql("ps.confirmed_market_price_cents", {
    confirmationStatusSql: "ps.confirmation_status",
    aiPriceTypeSql: "ps.ai_price_type",
    categorySql: "m.category",
    periodEndSql: "m.period_end",
    fallbackPriceSql: confirmedOnly ? undefined : "NULLIF(m.price_cents, 0)",
  });
}

/**
 * Selects one non-overlapping fact per product identity and month.
 * A latest full-month snapshot competes with deduplicated daily facts by
 * coverage days; equal coverage prefers the full-month snapshot. Rolling
 * windows are intentionally excluded because they cannot be added safely.
 */
export function marketMonthlyCoverageCtes(options: MarketMonthlyCoverageSqlOptions) {
  const gmv = options.gmvColumn ?? "effective_gmv_cents";
  const quantity = options.quantityColumn ?? "effective_quantity";
  const pageViews = options.pageViewsColumn ?? "page_views";
  const visitors = options.visitorsColumn ?? "visitors";
  const conversion = options.conversionColumn ?? "effective_conversion_bps";
  const monthlyConversionSql = options.includeConversion === false
    ? "NULL"
    : `CASE WHEN selected.source_priority=0 THEN MAX(fact.${conversion})
        WHEN SUM(fact.${visitors})>0
        THEN MIN(10000,MAX(0,CAST(ROUND(SUM(fact.${quantity})*10000.0/SUM(fact.${visitors})) AS INTEGER)))
        ELSE NULL END`;
  const selectionSource = options.selectionSource ?? options.source;
  const sourceIdentity = "source.category, source.scope, source.ranking_dimension, source.sku_code, substr(source.period_end,1,7)";
  const identity = "category, scope, ranking_dimension, sku_code, coverage_month";
  const selectedIdentity = "selected.category, selected.scope, selected.ranking_dimension, selected.sku_code, selected.coverage_month";
  const optionJoin = ["category", "scope", "ranking_dimension", "sku_code", "coverage_month"]
    .map((column) => `choice.${column}=selected.${column}`).join(" AND ");
  return `market_monthly_source AS MATERIALIZED (
    SELECT source.id, source.updated_at, source.period_start, source.period_end, source.category, source.scope,
      source.price_band_filter, source.ranking_dimension, source.sku_code, substr(source.period_end,1,7) coverage_month,
      CASE WHEN COALESCE(source.price_band_filter,'') IN ('','全部') THEN 0 ELSE 1 END coverage_band_priority,
      MAX(CASE WHEN COALESCE(source.price_band_filter,'') IN ('','全部') THEN 1 ELSE 0 END) OVER (
        PARTITION BY ${sourceIdentity}
      ) coverage_month_has_basis,
      source.period_end || '|' || source.updated_at || '|' || printf('%020d', source.id) coverage_source_key
    FROM ${selectionSource} source
  ), market_monthly_eligible AS MATERIALIZED (
    SELECT source.*,
      CASE
        WHEN source.period_start=source.period_end THEN 'daily'
        WHEN source.period_start=date(source.period_start,'start of month')
          AND source.period_end=date(source.period_start,'start of month','+1 month','-1 day') THEN 'monthly'
        ELSE 'rolling'
      END coverage_period_kind
    FROM market_monthly_source source
    WHERE coverage_band_priority=0 OR coverage_month_has_basis=0
  ), market_monthly_ranked AS MATERIALIZED (
    SELECT source.*, ROW_NUMBER() OVER (
      PARTITION BY ${identity}, coverage_period_kind,
        CASE WHEN coverage_period_kind='daily' THEN period_end ELSE '' END
      ORDER BY period_end DESC, updated_at DESC, id DESC
    ) coverage_rank
    FROM market_monthly_eligible source WHERE coverage_period_kind IN ('monthly','daily')
  ), market_monthly_choices AS MATERIALIZED (
    SELECT ${identity},
      CAST(substr(MAX(CASE WHEN coverage_period_kind='monthly' AND coverage_rank=1 THEN coverage_source_key END),-20) AS INTEGER) full_source_id,
      MAX(CASE WHEN coverage_period_kind='monthly' AND coverage_rank=1
        THEN CAST(julianday(period_end)-julianday(period_start)+1 AS INTEGER) END) full_coverage_days,
      CAST(substr(MAX(CASE WHEN coverage_period_kind='daily' AND coverage_rank=1 THEN coverage_source_key END),-20) AS INTEGER) daily_source_id,
      SUM(CASE WHEN coverage_period_kind='daily' AND coverage_rank=1 THEN 1 ELSE 0 END) daily_coverage_days,
      MIN(CASE WHEN coverage_period_kind='daily' AND coverage_rank=1 THEN period_start END) daily_period_start,
      MAX(CASE WHEN coverage_period_kind='daily' AND coverage_rank=1 THEN period_end END) daily_period_end
    FROM market_monthly_ranked GROUP BY ${identity}
  ), market_monthly_selected_ids AS MATERIALIZED (
    SELECT choice.category, choice.scope, choice.ranking_dimension, choice.sku_code, choice.coverage_month,
      choice.full_source_id selected_id, choice.full_source_id representative_id,
      choice.full_coverage_days coverage_days, 0 source_priority,
      selected.period_start coverage_period_start, selected.period_end coverage_period_end
    FROM market_monthly_choices choice
    JOIN market_monthly_ranked selected ON selected.id=choice.full_source_id
    WHERE choice.full_source_id IS NOT NULL AND choice.full_coverage_days>=choice.daily_coverage_days
    UNION ALL
    SELECT selected.category, selected.scope, selected.ranking_dimension, selected.sku_code, selected.coverage_month,
      selected.id, choice.daily_source_id, choice.daily_coverage_days, 1,
      choice.daily_period_start, choice.daily_period_end
    FROM market_monthly_ranked selected JOIN market_monthly_choices choice ON ${optionJoin}
    WHERE selected.coverage_period_kind='daily' AND selected.coverage_rank=1
      AND (choice.full_source_id IS NULL OR choice.full_coverage_days<choice.daily_coverage_days)
  ), market_monthly_metrics AS MATERIALIZED (
    SELECT ${selectedIdentity}, selected.representative_id, selected.coverage_days, selected.source_priority,
      selected.coverage_period_start, selected.coverage_period_end,
      SUM(fact.${gmv}) monthly_gmv_cents, SUM(fact.${quantity}) monthly_quantity,
      SUM(fact.${pageViews}) monthly_page_views, SUM(fact.${visitors}) monthly_visitors,
      ${monthlyConversionSql} monthly_conversion_bps
    FROM market_monthly_selected_ids selected JOIN ${options.source} fact ON fact.id=selected.selected_id
    GROUP BY ${selectedIdentity}, selected.representative_id, selected.coverage_days, selected.source_priority,
      selected.coverage_period_start, selected.coverage_period_end
  ), market_monthly_rows AS MATERIALIZED (
    SELECT source.*, metrics.coverage_month, metrics.monthly_gmv_cents, metrics.monthly_quantity, metrics.monthly_page_views,
      metrics.monthly_visitors, metrics.monthly_conversion_bps, metrics.coverage_days, metrics.source_priority,
      metrics.coverage_period_start, metrics.coverage_period_end
    FROM market_monthly_metrics metrics JOIN ${options.source} source ON source.id=metrics.representative_id
  )`;
}

export function marketEffectiveFactsCtes(factWhere = "") {
  const group = "category, period_start, period_end, scope, price_band_filter, ranking_dimension";
  const order = "COALESCE(rank, 2147483647), id";
  const reverseOrder = "COALESCE(rank, 2147483647) DESC, id DESC";
  return `market_fact_source AS MATERIALIZED (
    SELECT * FROM market_ranking_entries m ${factWhere}
  ), market_basis_ids AS MATERIALIZED (
    SELECT id, ROW_NUMBER() OVER (
      PARTITION BY period_start, period_end, category, scope, ranking_dimension, sku_code
      ORDER BY CASE COALESCE(price_band_filter,'') WHEN '全部' THEN 0 WHEN '' THEN 1 ELSE 2 END,
        COALESCE(price_band_filter,''), id DESC
    ) price_band_preference
    FROM market_fact_source
  ), market_basis_rows AS MATERIALIZED (
    SELECT source.* FROM market_fact_source source
    JOIN market_basis_ids chosen ON chosen.id=source.id AND chosen.price_band_preference=1
  ), real_gmv_anchor_rows AS MATERIALIZED (
    SELECT m.id, n.transaction_amount_cents real_gmv_cents
    FROM market_basis_rows m JOIN market_netshop_active_projection n
      ON m.ranking_dimension<>'SPU' AND n.sku_id=m.sku_code
      AND n.source='jd_sku_daily' AND n.dataset='sku_daily'
      AND n.business_date BETWEEN m.period_start AND m.period_end
    UNION ALL
    SELECT m.id, n.transaction_amount_cents real_gmv_cents
    FROM market_basis_rows m JOIN market_netshop_active_projection n
      ON m.ranking_dimension='SPU' AND n.spu_id=m.sku_code
      AND n.source='jd_sku_daily' AND n.dataset='spu_daily'
      AND n.business_date BETWEEN m.period_start AND m.period_end
  ), real_gmv_anchors AS MATERIALIZED (
    SELECT id, CAST(SUM(COALESCE(real_gmv_cents,0)) AS INTEGER) real_gmv_cents
    FROM real_gmv_anchor_rows GROUP BY id HAVING real_gmv_cents>0
  ), anchor_groups AS MATERIALIZED (
    SELECT DISTINCT ${group}
    FROM market_basis_rows source JOIN real_gmv_anchors anchors ON anchors.id=source.id
  ), range_texts AS MATERIALIZED (
    SELECT m.*, COALESCE(a.real_gmv_cents,0) real_gmv_cents,
      REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
        COALESCE(NULLIF(m.gmv_raw,''),json_extract(m.raw_json,'$."成交金额"'),json_extract(m.raw_json,'$."交易金额"'),json_extract(m.raw_json,'$."GMV"'),json_extract(m.raw_json,'$."销售额"'),''),
        ',',''),'，',''),'￥',''),'¥',''),'元',''),'～','~'),'至','~') gmv_range_text
    FROM market_basis_rows m JOIN anchor_groups USING (${group}) LEFT JOIN real_gmv_anchors a ON a.id=m.id
  ), range_parts AS MATERIALIZED (
    SELECT source.*,
      CASE WHEN instr(gmv_range_text,'~')>0 THEN substr(gmv_range_text,1,instr(gmv_range_text,'~')-1) ELSE gmv_range_text END gmv_low_text,
      CASE WHEN instr(gmv_range_text,'~')>0 THEN substr(gmv_range_text,instr(gmv_range_text,'~')+1) ELSE gmv_range_text END gmv_high_text
    FROM range_texts source
  ), parsed_ranges AS MATERIALIZED (
    SELECT source.*,
      CASE WHEN gmv_low_text<>'' THEN CAST(ROUND(CAST(REPLACE(REPLACE(REPLACE(TRIM(gmv_low_text),'亿',''),'万',''),'千','') AS REAL)
        * CASE WHEN instr(gmv_low_text,'亿')>0 THEN 100000000 WHEN instr(gmv_low_text,'万')>0 THEN 10000 WHEN instr(gmv_low_text,'千')>0 THEN 1000 ELSE 1 END * 100) AS INTEGER) END parsed_gmv_low_cents,
      CASE WHEN gmv_high_text<>'' THEN CAST(ROUND(CAST(REPLACE(REPLACE(REPLACE(TRIM(gmv_high_text),'亿',''),'万',''),'千','') AS REAL)
        * CASE WHEN instr(gmv_high_text,'亿')>0 THEN 100000000 WHEN instr(gmv_high_text,'万')>0 THEN 10000 WHEN instr(gmv_high_text,'千')>0 THEN 1000 ELSE 1 END * 100) AS INTEGER) END parsed_gmv_high_cents
    FROM range_parts source
  ), rank_source AS MATERIALIZED (
    SELECT m.*,
      CASE WHEN m.real_gmv_cents>0 THEN m.real_gmv_cents ELSE COALESCE(m.gmv_low_cents,m.parsed_gmv_low_cents,0) END rank_low,
      CASE WHEN m.real_gmv_cents>0 THEN m.real_gmv_cents ELSE COALESCE(m.gmv_high_cents,m.parsed_gmv_high_cents,9223372036854775807) END rank_high,
      CASE WHEN m.real_gmv_cents>0
        AND ((COALESCE(m.gmv_low_cents,m.parsed_gmv_low_cents) IS NOT NULL AND m.real_gmv_cents<COALESCE(m.gmv_low_cents,m.parsed_gmv_low_cents)) OR (COALESCE(m.gmv_high_cents,m.parsed_gmv_high_cents) IS NOT NULL AND m.real_gmv_cents>COALESCE(m.gmv_high_cents,m.parsed_gmv_high_cents)))
        THEN 1 ELSE 0 END gmv_out_of_band
    FROM parsed_ranges m
  ), narrowed AS MATERIALIZED (
    SELECT source.*,
      MIN(rank_high) OVER (PARTITION BY ${group} ORDER BY ${order} ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) narrowed_high,
      MAX(rank_low) OVER (PARTITION BY ${group} ORDER BY ${order} ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING) narrowed_low
    FROM rank_source source
  ), anchor_segments AS MATERIALIZED (
    SELECT source.*,
      SUM(CASE WHEN real_gmv_cents>0 THEN 1 ELSE 0 END) OVER (PARTITION BY ${group} ORDER BY ${order} ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) left_segment,
      SUM(CASE WHEN real_gmv_cents>0 THEN 1 ELSE 0 END) OVER (PARTITION BY ${group} ORDER BY ${reverseOrder} ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) right_segment
    FROM narrowed source
  ), anchor_values AS MATERIALIZED (
    SELECT source.*,
      MAX(CASE WHEN real_gmv_cents>0 THEN rank END) OVER (PARTITION BY ${group}, left_segment) left_anchor_rank,
      MAX(CASE WHEN real_gmv_cents>0 THEN real_gmv_cents END) OVER (PARTITION BY ${group}, left_segment) left_anchor_value,
      MAX(CASE WHEN real_gmv_cents>0 THEN rank END) OVER (PARTITION BY ${group}, right_segment) right_anchor_rank,
      MAX(CASE WHEN real_gmv_cents>0 THEN real_gmv_cents END) OVER (PARTITION BY ${group}, right_segment) right_anchor_value
    FROM anchor_segments source
  ), raw_candidates AS MATERIALIZED (
    SELECT source.*,
      CASE WHEN real_gmv_cents>0 THEN real_gmv_cents
        WHEN left_anchor_value>0 AND right_anchor_value>0 AND right_anchor_rank<>left_anchor_rank AND rank IS NOT NULL
          THEN CAST(ROUND(EXP(LN(left_anchor_value) + ((rank-left_anchor_rank)*1.0/(right_anchor_rank-left_anchor_rank))*(LN(right_anchor_value)-LN(left_anchor_value)))) AS INTEGER)
        ELSE gmv_cents END raw_effective_gmv_cents
    FROM anchor_values source
  ), candidates AS MATERIALIZED (
    SELECT source.*, MAX(MIN(raw_effective_gmv_cents, MAX(narrowed_low,narrowed_high)), MIN(narrowed_low,narrowed_high)) candidate_gmv_cents
    FROM raw_candidates source
  ), adjusted_rows AS MATERIALIZED (
    SELECT source.*,
      CASE WHEN real_gmv_cents>0 THEN real_gmv_cents
        ELSE MIN(candidate_gmv_cents) OVER (PARTITION BY ${group} ORDER BY ${order} ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
      END effective_gmv_cents
    FROM candidates source
  ), effective_values AS MATERIALIZED (
    SELECT id, effective_gmv_cents, real_gmv_cents, gmv_out_of_band FROM adjusted_rows
  ), effective_seeds AS MATERIALIZED (
    SELECT source.*,
      COALESCE(adjusted.effective_gmv_cents,source.gmv_cents) effective_gmv_cents,
      COALESCE(adjusted.real_gmv_cents,0) real_gmv_cents,
      COALESCE(adjusted.gmv_out_of_band,0) gmv_out_of_band
    FROM market_basis_rows source LEFT JOIN effective_values adjusted ON adjusted.id=source.id
  ), refined_quantities AS MATERIALIZED (
    SELECT source.*,
      CASE
        WHEN COALESCE(NULLIF(price_cents,0), CASE WHEN quantity>0 THEN effective_gmv_cents*1.0/quantity END) IS NULL THEN MAX(1,quantity)
        WHEN gmv_out_of_band=1 THEN MAX(1,CAST(ROUND(effective_gmv_cents*1.0/COALESCE(NULLIF(price_cents,0),effective_gmv_cents*1.0/NULLIF(quantity,0))) AS INTEGER))
        ELSE MAX(1,MIN(
          MAX(CAST(ROUND(effective_gmv_cents*1.0/COALESCE(NULLIF(price_cents,0),effective_gmv_cents*1.0/NULLIF(quantity,0))) AS INTEGER),COALESCE(quantity_low,1)),
          COALESCE(quantity_high,9223372036854775807)
        ))
      END effective_quantity
    FROM effective_seeds source
  ), market_effective_rows AS MATERIALIZED (
    SELECT source.*,
      CASE WHEN effective_quantity>0 THEN CAST(ROUND(effective_gmv_cents*1.0/effective_quantity) AS INTEGER) END effective_average_transaction_price_cents,
      CASE WHEN visitors<=0 THEN NULL
        WHEN gmv_out_of_band=1 THEN MIN(10000,MAX(0,CAST(ROUND(effective_quantity*10000.0/visitors) AS INTEGER)))
        ELSE MIN(10000,MAX(
          COALESCE(conversion_low_bps,0),
          MIN(CAST(ROUND(effective_quantity*10000.0/visitors) AS INTEGER),COALESCE(conversion_high_bps,10000))
        ))
      END effective_conversion_bps
    FROM refined_quantities source
  )`;
}

export function marketCachedEffectiveFactsCtes(factWhere = "") {
  return `market_effective_rows AS MATERIALIZED (
    SELECT m.*,
      cached.effective_gmv_cents,
      cached.real_gmv_cents,
      cached.gmv_out_of_band,
      cached.effective_quantity,
      cached.effective_average_transaction_price_cents,
      cached.effective_conversion_bps
    FROM market_ranking_entries m
    JOIN market_effective_metrics_cache cached ON cached.market_entry_id=m.id
    ${factWhere}
  ), market_basis_rows AS MATERIALIZED (
    SELECT * FROM market_effective_rows
  )`;
}

export function buildMarketOverviewEnrichedSql(options: MarketOverviewSqlOptions = {}) {
  const materialized = options.materialized ? "MATERIALIZED " : "";
  const effectiveFacts = options.useEffectiveMetricsCache
    ? marketCachedEffectiveFactsCtes(options.factWhere)
    : marketEffectiveFactsCtes(options.factWhere);
  return `WITH ${effectiveFacts}, enriched AS ${materialized}(
    SELECT m.*,
      mic.status AS image_cache_status_raw,
      mic.content_sha256 AS image_content_sha256,
      ps.confirmed_market_price_cents,
      ps.source_price_cents,
      ps.ai_image_price_cents,
      ps.ai_price_type,
      ps.ai_confidence_bps,
      ps.confirmation_status,
      ps.average_transaction_price_cents,
      ps.confirmed_market_price_cents AS official_market_price_cents,
      COALESCE(ps.source_price_cents, ps.average_transaction_price_cents, ps.ai_image_price_cents) AS candidate_price_cents,
      CASE
        WHEN ps.confirmed_market_price_cents IS NOT NULL THEN '人工确认'
        ELSE '未确认价格'
      END AS market_price_source,
      CASE
        WHEN ps.source_price_cents IS NOT NULL THEN '源表价格'
        WHEN ps.average_transaction_price_cents IS NOT NULL THEN '系统计算'
        WHEN ps.ai_image_price_cents IS NOT NULL THEN 'AI待确认'
        ELSE '暂无价格'
      END AS candidate_price_source,
      ${overviewPriceBandSql()} AS price_band,
      CASE WHEN EXISTS (
        SELECT 1 FROM market_netshop_active_projection n
        WHERE n.sku_id = m.sku_code OR n.product_code = m.sku_code OR n.spu_id = m.sku_code
      ) THEN 1 ELSE 0 END AS is_own
    FROM market_effective_rows m
    LEFT JOIN market_image_cache mic ON mic.source_url = m.image_url
    LEFT JOIN market_price_snapshots ps ON ps.category = m.category
      AND ps.scope = m.scope
      AND ps.sku_code = m.sku_code
      AND ps.ranking_dimension = m.ranking_dimension
      AND ps.month = substr(m.period_end, 1, 7)
    ${options.where ?? ""}
  ), filtered AS ${materialized}(SELECT * FROM enriched ${options.priceBandWhere ?? ""})`;
}

/**
 * Builds the ranking query in two stages so expensive ownership, image and
 * Price enrichment only runs for the bounded row window that can reach the
 * UI. The full report keeps the historical 200-row sample while the ranking
 * workspace can request smaller server-side pages.
 */
export function buildMarketRankingCtes(options: Pick<MarketOverviewSqlOptions, "factWhere" | "where" | "priceBandWhere" | "rankingLimit" | "rankingOffset"> = {}) {
  const clauses = [options.factWhere, options.where]
    .map((part) => part?.replace(/^\s*WHERE\s+/i, "").trim())
    .filter(Boolean);
  const selectionWhere = clauses.length ? `WHERE ${clauses.join(" AND ")}` : "";
  const priceBandFilter = options.priceBandWhere?.trim() ?? "";
  const rankingLimit = Math.max(1, Math.min(200, Math.trunc(options.rankingLimit ?? 200)));
  const rankingOffset = Math.max(0, Math.trunc(options.rankingOffset ?? 0));
  const rankingWindow = `LIMIT ${rankingLimit}${rankingOffset ? ` OFFSET ${rankingOffset}` : ""}`;
  const selectedIds = priceBandFilter
    ? `ranking_candidates AS MATERIALIZED (
      SELECT m.id, m.rank, cached.effective_gmv_cents,
        ${overviewPriceBandSql()} AS price_band
      FROM market_ranking_entries m
      JOIN market_effective_metrics_cache cached ON cached.market_entry_id=m.id
      LEFT JOIN market_price_snapshots ps ON ps.category=m.category
        AND ps.scope=m.scope AND ps.sku_code=m.sku_code
        AND ps.ranking_dimension=m.ranking_dimension AND ps.month=substr(m.period_end,1,7)
      ${selectionWhere}
    ), top_ranked_ids AS MATERIALIZED (
      SELECT id FROM ranking_candidates ${priceBandFilter}
      ORDER BY CASE WHEN rank IS NULL THEN 1 ELSE 0 END, rank, effective_gmv_cents DESC
      ${rankingWindow}
    )`
    : `top_ranked_ids AS MATERIALIZED (
      SELECT m.id
      FROM market_ranking_entries m
      JOIN market_effective_metrics_cache cached ON cached.market_entry_id=m.id
      ${selectionWhere}
      ORDER BY CASE WHEN m.rank IS NULL THEN 1 ELSE 0 END, m.rank, cached.effective_gmv_cents DESC
      ${rankingWindow}
    )`;
  return `WITH ${selectedIds}, top_ranked_sources AS MATERIALIZED (
    SELECT m.*,
      COALESCE((
        SELECT historical.image_url
        FROM market_ranking_entries historical INDEXED BY market_entries_representative_idx
        JOIN market_image_cache historical_cache
          ON historical_cache.source_url=historical.image_url
          AND historical_cache.status='ready'
          AND historical_cache.content_sha256<>''
        WHERE historical.category=m.category
          AND historical.scope=m.scope
          AND historical.ranking_dimension=m.ranking_dimension
          AND historical.sku_code=m.sku_code
          AND historical.image_url<>''
        ORDER BY historical.period_end DESC, historical.period_start DESC, historical.id DESC
        LIMIT 1
      ), NULLIF(m.image_url,''), '') AS resolved_image_url,
      cached.effective_gmv_cents,
      cached.real_gmv_cents,
      cached.gmv_out_of_band,
      cached.effective_quantity,
      cached.effective_average_transaction_price_cents,
      cached.effective_conversion_bps
    FROM top_ranked_ids selected
    JOIN market_ranking_entries m ON m.id=selected.id
    JOIN market_effective_metrics_cache cached ON cached.market_entry_id=m.id
  ), top_ranked AS MATERIALIZED (
    SELECT m.*,
      mic.status AS image_cache_status_raw,
      mic.content_sha256 AS image_content_sha256,
      ps.confirmed_market_price_cents,
      ps.source_price_cents,
      ps.ai_image_price_cents,
      ps.ai_price_type,
      ps.ai_confidence_bps,
      ps.confirmation_status,
      ps.average_transaction_price_cents,
      ps.confirmed_market_price_cents AS official_market_price_cents,
      COALESCE(ps.source_price_cents, ps.average_transaction_price_cents, ps.ai_image_price_cents) AS candidate_price_cents,
      CASE WHEN ps.confirmed_market_price_cents IS NOT NULL THEN '人工确认' ELSE '未确认价格' END AS market_price_source,
      CASE
        WHEN ps.source_price_cents IS NOT NULL THEN '源表价格'
        WHEN ps.average_transaction_price_cents IS NOT NULL THEN '系统计算'
        WHEN ps.ai_image_price_cents IS NOT NULL THEN 'AI待确认'
        ELSE '暂无价格'
      END AS candidate_price_source,
      ${overviewPriceBandSql()} AS price_band,
      CASE WHEN EXISTS (
        SELECT 1 FROM market_netshop_active_projection n
        WHERE n.sku_id=m.sku_code OR n.product_code=m.sku_code OR n.spu_id=m.sku_code
      ) THEN 1 ELSE 0 END AS is_own
    FROM top_ranked_sources m
    LEFT JOIN market_image_cache mic ON mic.source_url=m.resolved_image_url
    LEFT JOIN market_price_snapshots ps ON ps.category=m.category
      AND ps.scope=m.scope AND ps.sku_code=m.sku_code
      AND ps.ranking_dimension=m.ranking_dimension AND ps.month=substr(m.period_end,1,7)
  )`;
}

function marketAnalyticsAggregateCtes() {
  return `industry_months AS MATERIALIZED (
  SELECT DISTINCT substr(period_end,1,7) period FROM analytics_filtered
  ), industry_month_bounds AS MATERIALIZED (
  SELECT MIN(period) first_month, MAX(period) latest_month FROM industry_months
  ), industry_recent_months AS MATERIALIZED (
  SELECT period FROM (SELECT period FROM industry_months ORDER BY period DESC LIMIT 24) ORDER BY period
  ), industry_product_presence AS MATERIALIZED (
  SELECT DISTINCT category, scope, ranking_dimension, sku_code, substr(period_end,1,7) period
  FROM analytics_filtered
  ), industry_month_adjacency AS MATERIALIZED (
  SELECT current.period,
    previous.period previous_period, following.period following_period
  FROM industry_months current
  LEFT JOIN industry_months previous
    ON previous.period=strftime('%Y-%m',date(current.period || '-01','-1 month'))
  LEFT JOIN industry_months following
    ON following.period=strftime('%Y-%m',date(current.period || '-01','+1 month'))
  ), industry_lifecycle AS MATERIALIZED (
  SELECT presence.period,
    CASE WHEN adjacency.previous_period IS NULL THEN NULL
      ELSE SUM(CASE WHEN previous.sku_code IS NULL THEN 1 ELSE 0 END) END entry_count,
    CASE WHEN adjacency.following_period IS NULL THEN NULL
      ELSE SUM(CASE WHEN following.sku_code IS NULL THEN 1 ELSE 0 END) END exit_count
  FROM industry_product_presence presence
  JOIN industry_month_adjacency adjacency ON adjacency.period=presence.period
  LEFT JOIN industry_product_presence previous
    ON previous.category=presence.category AND previous.scope=presence.scope
      AND previous.ranking_dimension=presence.ranking_dimension AND previous.sku_code=presence.sku_code
      AND previous.period=adjacency.previous_period
  LEFT JOIN industry_product_presence following
    ON following.category=presence.category AND following.scope=presence.scope
      AND following.ranking_dimension=presence.ranking_dimension AND following.sku_code=presence.sku_code
      AND following.period=adjacency.following_period
  WHERE presence.period IN (SELECT period FROM industry_recent_months)
  GROUP BY presence.period
  ), industry_product_ranked AS MATERIALIZED (
  SELECT source.*, ROW_NUMBER() OVER (
    PARTITION BY category,scope,ranking_dimension,sku_code
    ORDER BY period_end DESC, period_start DESC, rank, sku_code
  ) industry_recency
  FROM analytics_filtered source
  ), industry_product_totals AS MATERIALIZED (
  SELECT category,scope,ranking_dimension,sku_code,
    MAX(CASE WHEN industry_recency=1 THEN product_name END) product_name,
    MAX(CASE WHEN industry_recency=1 THEN COALESCE(NULLIF(brand,''),'未识别品牌') END) brand,
    MAX(CASE WHEN industry_recency=1 THEN subcategory END) subcategory,
    MAX(CASE WHEN industry_recency=1 THEN operation_mode END) operation_mode,
    SUM(gmv_cents) gmv_cents,SUM(quantity) quantity,SUM(visitors) visitors,
    CASE WHEN SUM(visitors)>0 THEN MIN(10000,MAX(0,CAST(ROUND(SUM(quantity)*10000.0/SUM(visitors)) AS INTEGER))) END conversion_bps,
    COUNT(DISTINCT substr(period_end,1,7)) active_months,MIN(rank) best_rank
  FROM industry_product_ranked GROUP BY category,scope,ranking_dimension,sku_code
  ), industry_product_thresholds AS MATERIALIZED (
  SELECT COALESCE(AVG(visitors),0) visitor_threshold,COALESCE(AVG(conversion_bps),0) conversion_threshold_bps
  FROM industry_product_totals
  ), industry_product_quadrants AS MATERIALIZED (
  SELECT product.*,thresholds.visitor_threshold,thresholds.conversion_threshold_bps,
    CASE
      WHEN product.visitors>=thresholds.visitor_threshold AND COALESCE(product.conversion_bps,0)>=thresholds.conversion_threshold_bps THEN 'high_traffic_high_conversion'
      WHEN product.visitors>=thresholds.visitor_threshold THEN 'high_traffic_low_conversion'
      WHEN COALESCE(product.conversion_bps,0)>=thresholds.conversion_threshold_bps THEN 'low_traffic_high_conversion'
      ELSE 'low_traffic_low_conversion'
    END traffic_quadrant
  FROM industry_product_totals product CROSS JOIN industry_product_thresholds thresholds
  ), industry_brand_hero AS MATERIALIZED (
  SELECT brand,MAX(gmv_cents) hero_gmv_cents FROM industry_product_totals GROUP BY brand
  ), analytics_core AS MATERIALIZED (
  SELECT 'summary' section, '' row_key, MIN(period_start) text_1, MAX(period_end) text_2,
    COUNT(DISTINCT sku_code) number_1, COUNT(DISTINCT category) number_2,
    COUNT(DISTINCT COALESCE(NULLIF(brand,''), '未识别品牌')) number_3,
    COALESCE(SUM(gmv_cents),0) number_4, COALESCE(SUM(quantity),0) number_5,
    COALESCE(SUM(page_views),0) number_6, COALESCE(SUM(visitors),0) number_7,
    COUNT(DISTINCT CASE WHEN is_own=1 THEN sku_code END) number_8,
    COALESCE(SUM(CASE WHEN operation_mode='自营' THEN gmv_cents ELSE 0 END),0) number_9,
    COUNT(DISTINCT CASE WHEN COALESCE(confirmation_status,'') IN ('missing','ai_pending','review_pending')
      OR market_price_source='AI待确认' THEN sku_code END) number_10
  FROM analytics_filtered
  UNION ALL
  SELECT 'trend', substr(period_end,1,7), NULL, NULL,
    COALESCE(SUM(gmv_cents),0), COALESCE(SUM(quantity),0), COALESCE(SUM(visitors),0),
    COUNT(DISTINCT sku_code), COUNT(DISTINCT brand),
    COALESCE(SUM(CASE WHEN operation_mode='POP' THEN gmv_cents ELSE 0 END),0),
    COALESCE(SUM(CASE WHEN operation_mode='自营' THEN gmv_cents ELSE 0 END),0),
    COALESCE(SUM(CASE WHEN official_market_price_cents IS NULL THEN 0 ELSE official_market_price_cents*1.0*gmv_cents END),0),
    COALESCE(SUM(CASE WHEN official_market_price_cents IS NULL THEN 0 ELSE gmv_cents END),0), NULL
  FROM analytics_filtered GROUP BY substr(period_end,1,7)
  UNION ALL
  SELECT 'price_band', price_band, GROUP_CONCAT(DISTINCT brand), NULL,
    COUNT(*), COALESCE(SUM(gmv_cents),0), COALESCE(SUM(quantity),0), COUNT(DISTINCT sku_code),
    COALESCE(SUM(CASE WHEN operation_mode='POP' THEN gmv_cents ELSE 0 END),0),
    COALESCE(SUM(CASE WHEN operation_mode='自营' THEN gmv_cents ELSE 0 END),0),
    NULL, NULL, NULL, NULL
  FROM analytics_filtered GROUP BY price_band
  UNION ALL
  SELECT 'price_band_trend', substr(period_end,1,7), MAX(price_band), NULL,
    COALESCE(SUM(gmv_cents),0), COALESCE(SUM(quantity),0), NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
  FROM analytics_filtered GROUP BY substr(period_end,1,7), price_band
  UNION ALL
  SELECT 'lifecycle', period, NULL, NULL,
    entry_count,exit_count,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL
  FROM industry_lifecycle
  ), analytics_dimensions AS MATERIALIZED (
  SELECT 'brand', COALESCE(NULLIF(source.brand,''), '未识别品牌'), GROUP_CONCAT(DISTINCT source.price_band), GROUP_CONCAT(DISTINCT source.subcategory),
    COALESCE(SUM(source.gmv_cents),0), COALESCE(SUM(source.quantity),0), COUNT(DISTINCT source.sku_code), MIN(source.rank),
    MAX(hero.hero_gmv_cents), NULL, NULL, NULL, NULL, NULL
  FROM analytics_filtered source LEFT JOIN industry_brand_hero hero
    ON hero.brand=COALESCE(NULLIF(source.brand,''),'未识别品牌')
  GROUP BY COALESCE(NULLIF(source.brand,''), '未识别品牌')
  UNION ALL
  SELECT 'subcategory', subcategory, GROUP_CONCAT(DISTINCT brand), GROUP_CONCAT(DISTINCT price_band),
    COUNT(DISTINCT sku_code), COALESCE(SUM(gmv_cents),0), COALESCE(SUM(quantity),0),
    COALESCE(SUM(CASE WHEN operation_mode='自营' THEN gmv_cents ELSE 0 END),0),
    COUNT(DISTINCT CASE WHEN official_market_price_cents IS NULL THEN sku_code END),
    NULL, NULL, NULL, NULL, NULL
  FROM analytics_filtered GROUP BY subcategory
  UNION ALL
  SELECT 'price_value', CAST(official_market_price_cents AS TEXT), NULL, NULL,
    MAX(official_market_price_cents), COUNT(*), COALESCE(SUM(gmv_cents),0),
    NULL, NULL, NULL, NULL, NULL, NULL, NULL
  FROM analytics_filtered WHERE official_market_price_cents IS NOT NULL GROUP BY official_market_price_cents
  UNION ALL
  SELECT 'identity', '', NULL, NULL,
    COUNT(DISTINCT category),COUNT(DISTINCT scope),COUNT(DISTINCT ranking_dimension),COUNT(DISTINCT operation_mode),
    COUNT(DISTINCT CASE WHEN COALESCE(NULLIF(brand,''),'未识别品牌')='未识别品牌' THEN json_array(category,scope,ranking_dimension,sku_code) END),
    COUNT(DISTINCT CASE WHEN COALESCE(NULLIF(subcategory,''),'未分类')='未分类' THEN json_array(category,scope,ranking_dimension,sku_code) END),
    COUNT(DISTINCT CASE WHEN official_market_price_cents IS NULL THEN json_array(category,scope,ranking_dimension,sku_code) END),NULL,NULL,NULL
  FROM analytics_filtered
  ), analytics_industry AS MATERIALIZED (
  SELECT 'operation_mode', operation_mode, NULL, NULL,
    COALESCE(SUM(gmv_cents),0),COALESCE(SUM(quantity),0),COUNT(DISTINCT json_array(category,scope,ranking_dimension,sku_code)),COALESCE(SUM(visitors),0),
    CASE WHEN SUM(visitors)>0 THEN MIN(10000,MAX(0,CAST(ROUND(SUM(quantity)*10000.0/SUM(visitors)) AS INTEGER))) END,
    COUNT(DISTINCT COALESCE(NULLIF(brand,''),'未识别品牌')),NULL,NULL,NULL,NULL
  FROM analytics_filtered GROUP BY operation_mode
  UNION ALL
  SELECT 'subcategory_month', substr(period_end,1,7), subcategory, NULL,
    COALESCE(SUM(gmv_cents),0),COALESCE(SUM(quantity),0),COUNT(DISTINCT json_array(category,scope,ranking_dimension,sku_code)),COALESCE(SUM(visitors),0),
    CASE WHEN SUM(visitors)>0 THEN MIN(10000,MAX(0,CAST(ROUND(SUM(quantity)*10000.0/SUM(visitors)) AS INTEGER))) END,
    COALESCE(SUM(CASE WHEN operation_mode='自营' THEN gmv_cents ELSE 0 END),0),
    COUNT(DISTINCT COALESCE(NULLIF(brand,''),'未识别品牌')),
    COUNT(DISTINCT CASE WHEN official_market_price_cents IS NULL THEN json_array(category,scope,ranking_dimension,sku_code) END),NULL,NULL
  FROM analytics_filtered
  WHERE substr(period_end,1,7) IN (SELECT period FROM industry_recent_months)
  GROUP BY substr(period_end,1,7),subcategory
  UNION ALL
  SELECT 'brand_month', substr(period_end,1,7), COALESCE(NULLIF(brand,''),'未识别品牌'), NULL,
    COALESCE(SUM(gmv_cents),0),COALESCE(SUM(quantity),0),COUNT(DISTINCT json_array(category,scope,ranking_dimension,sku_code)),MIN(rank),
    COALESCE(SUM(visitors),0),
    CASE WHEN SUM(visitors)>0 THEN MIN(10000,MAX(0,CAST(ROUND(SUM(quantity)*10000.0/SUM(visitors)) AS INTEGER))) END,
    NULL,NULL,NULL,NULL
  FROM analytics_filtered
  WHERE substr(period_end,1,7) IN (SELECT period FROM industry_recent_months)
  GROUP BY substr(period_end,1,7),COALESCE(NULLIF(brand,''),'未识别品牌')
  UNION ALL
  SELECT 'opportunity_cell', subcategory, price_band, NULL,
    COALESCE(SUM(gmv_cents),0),COALESCE(SUM(quantity),0),COUNT(DISTINCT json_array(category,scope,ranking_dimension,sku_code)),COALESCE(SUM(visitors),0),
    CASE WHEN SUM(visitors)>0 THEN MIN(10000,MAX(0,CAST(ROUND(SUM(quantity)*10000.0/SUM(visitors)) AS INTEGER))) END,
    COALESCE(SUM(CASE WHEN operation_mode='自营' THEN gmv_cents ELSE 0 END),0),
    COUNT(DISTINCT COALESCE(NULLIF(brand,''),'未识别品牌')),
    COALESCE(SUM(CASE WHEN substr(period_end,1,7)=(SELECT latest_month FROM industry_month_bounds) THEN gmv_cents ELSE 0 END),0),
    COALESCE(SUM(CASE WHEN substr(period_end,1,7)=strftime('%Y-%m',date((SELECT latest_month FROM industry_month_bounds) || '-01','-1 month')) THEN gmv_cents ELSE 0 END),0),
    COUNT(DISTINCT CASE WHEN official_market_price_cents IS NULL THEN json_array(category,scope,ranking_dimension,sku_code) END)
  FROM analytics_filtered GROUP BY subcategory,price_band
  UNION ALL
  SELECT 'traffic_quadrant', traffic_quadrant, NULL, NULL,
    COUNT(*),COALESCE(SUM(gmv_cents),0),COALESCE(SUM(quantity),0),COALESCE(SUM(visitors),0),
    CASE WHEN SUM(visitors)>0 THEN MIN(10000,MAX(0,CAST(ROUND(SUM(quantity)*10000.0/SUM(visitors)) AS INTEGER))) END,
    MAX(visitor_threshold),MAX(conversion_threshold_bps),NULL,NULL,NULL
  FROM industry_product_quadrants GROUP BY traffic_quadrant
  )`;
}

const marketAnalyticsResultSql = `SELECT * FROM analytics_core
  UNION ALL
  SELECT * FROM analytics_dimensions
  UNION ALL
  SELECT * FROM analytics_industry
  UNION ALL
  SELECT 'ownership_product', sku_code, NULL, NULL,
    MAX(is_own), NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
  FROM analytics_filtered GROUP BY sku_code`;

export function buildMarketOverviewAnalyticsSql(options: Omit<MarketOverviewSqlOptions, "materialized"> = {}) {
  const priceBandWhere = options.priceBandWhere ?? "";
  const effectiveFacts = options.useEffectiveMetricsCache
    ? marketCachedEffectiveFactsCtes(options.factWhere)
    : marketEffectiveFactsCtes(options.factWhere);
  const commonCtes = `WITH ${effectiveFacts}, analytics_base AS MATERIALIZED (
    SELECT m.id, m.updated_at, m.period_start, m.period_end, m.category, m.scope, m.price_band_filter, m.ranking_dimension, m.operation_mode,
      m.subcategory, m.rank, m.sku_code, m.product_name, m.brand, m.price_cents, m.effective_gmv_cents gmv_cents, m.effective_quantity quantity, m.page_views, m.visitors,
      m.effective_conversion_bps conversion_bps
    FROM market_effective_rows m
  ), analytics_selection_base AS MATERIALIZED (
    SELECT m.* FROM market_basis_rows m
    ${options.where ?? ""}
  ),
  ${marketMonthlyCoverageCtes({
    source: "analytics_base",
    selectionSource: "analytics_selection_base",
    gmvColumn: "gmv_cents",
    quantityColumn: "quantity",
    pageViewsColumn: "page_views",
    visitorsColumn: "visitors",
    conversionColumn: "conversion_bps",
    includeConversion: false,
  })}, monthly_enriched AS MATERIALIZED (
    SELECT m.id, m.updated_at, m.coverage_period_start period_start, m.coverage_period_end period_end,
      m.category, m.scope, m.ranking_dimension, m.operation_mode,
      m.subcategory, m.rank, m.sku_code, m.product_name, m.brand, m.monthly_gmv_cents gmv_cents, m.monthly_quantity quantity,
      m.monthly_page_views page_views, m.monthly_visitors visitors, m.monthly_conversion_bps conversion_bps,
      ps.confirmed_market_price_cents AS official_market_price_cents,
      ps.confirmation_status,
      CASE WHEN ps.confirmed_market_price_cents IS NOT NULL THEN '人工确认' ELSE '未确认价格' END AS market_price_source,
      ${overviewPriceBandSql(Boolean(options.confirmedOnlyPriceBands))} AS price_band,
      CASE WHEN EXISTS (
        SELECT 1 FROM market_netshop_active_projection n
        WHERE n.sku_id=m.sku_code OR n.product_code=m.sku_code OR n.spu_id=m.sku_code
      ) THEN 1 ELSE 0 END AS is_own
    FROM market_monthly_rows m
    LEFT JOIN market_price_snapshots ps ON ps.category=m.category
      AND ps.scope=m.scope AND ps.sku_code=m.sku_code
      AND ps.ranking_dimension=m.ranking_dimension AND ps.month=substr(m.period_end,1,7)
  ), analytics_filtered AS MATERIALIZED (
    SELECT * FROM monthly_enriched ${priceBandWhere}
  ), ${marketAnalyticsAggregateCtes()}`;
  return `${commonCtes}\n  ${marketAnalyticsResultSql}`;
}

export function buildMarketMonthlySummaryRefreshSql() {
  const dirtyFactWhere = `WHERE EXISTS (
    SELECT 1 FROM market_monthly_summary_dirty_keys dirty
    WHERE dirty.dirty_revision<=?1
      AND dirty.category=m.category AND dirty.scope=m.scope
      AND dirty.ranking_dimension=m.ranking_dimension AND dirty.sku_code=m.sku_code
      AND dirty.month=substr(m.period_end,1,7)
  )`;
  return `WITH ${marketCachedEffectiveFactsCtes(dirtyFactWhere)}, analytics_base AS MATERIALIZED (
    SELECT m.id, m.updated_at, m.period_start, m.period_end, m.category, m.scope, m.price_band_filter, m.ranking_dimension, m.operation_mode,
      m.subcategory, m.rank, m.sku_code, m.product_name, m.brand, m.price_cents,
      m.effective_gmv_cents gmv_cents, m.effective_quantity quantity, m.page_views, m.visitors,
      m.effective_conversion_bps conversion_bps
    FROM market_effective_rows m
  ), analytics_selection_base AS MATERIALIZED (
    SELECT * FROM market_basis_rows
  ), ${marketMonthlyCoverageCtes({
    source: "analytics_base",
    selectionSource: "analytics_selection_base",
    gmvColumn: "gmv_cents",
    quantityColumn: "quantity",
    pageViewsColumn: "page_views",
    visitorsColumn: "visitors",
    conversionColumn: "conversion_bps",
    includeConversion: false,
  })}, monthly_cache_rows AS MATERIALIZED (
    SELECT m.id representative_entry_id, m.coverage_month month,
      m.coverage_period_start, m.coverage_period_end,
      m.category, m.scope, m.ranking_dimension, m.operation_mode, m.subcategory, m.rank,
      m.sku_code, m.product_name, m.brand,
      m.monthly_gmv_cents gmv_cents, m.monthly_quantity quantity,
      m.monthly_page_views page_views, m.monthly_visitors visitors,
      m.monthly_conversion_bps conversion_bps,
      ps.confirmed_market_price_cents AS official_market_price_cents,
      ps.confirmation_status,
      CASE WHEN ps.confirmed_market_price_cents IS NOT NULL THEN '人工确认' ELSE '未确认价格' END AS market_price_source,
      ${overviewPriceBandSql(false)} AS display_price_band,
      ${overviewPriceBandSql(true)} AS confirmed_price_band,
      CASE WHEN EXISTS (
        SELECT 1 FROM market_netshop_active_projection n
        WHERE n.sku_id=m.sku_code OR n.product_code=m.sku_code OR n.spu_id=m.sku_code
      ) THEN 1 ELSE 0 END AS is_own
    FROM market_monthly_rows m
    LEFT JOIN market_price_snapshots ps ON ps.category=m.category
      AND ps.scope=m.scope AND ps.sku_code=m.sku_code
      AND ps.ranking_dimension=m.ranking_dimension AND ps.month=m.coverage_month
  )
  INSERT INTO market_monthly_summary_cache (
    category, scope, ranking_dimension, sku_code, month, representative_entry_id,
    coverage_period_start, coverage_period_end, operation_mode, subcategory, rank,
    product_name, brand, gmv_cents, quantity, page_views, visitors, conversion_bps,
    official_market_price_cents, confirmation_status, market_price_source,
    display_price_band, confirmed_price_band, is_own, refreshed_revision, updated_at
  )
  SELECT category, scope, ranking_dimension, sku_code, month, representative_entry_id,
    coverage_period_start, coverage_period_end, operation_mode, subcategory, rank,
    product_name, brand, gmv_cents, quantity, page_views, visitors, conversion_bps,
    official_market_price_cents, confirmation_status, market_price_source,
    display_price_band, confirmed_price_band, is_own, ?1, CURRENT_TIMESTAMP
  FROM monthly_cache_rows WHERE 1
  ON CONFLICT(category,scope,ranking_dimension,sku_code,month) DO UPDATE SET
    representative_entry_id=excluded.representative_entry_id,
    coverage_period_start=excluded.coverage_period_start,
    coverage_period_end=excluded.coverage_period_end,
    operation_mode=excluded.operation_mode,
    subcategory=excluded.subcategory,
    rank=excluded.rank,
    product_name=excluded.product_name,
    brand=excluded.brand,
    gmv_cents=excluded.gmv_cents,
    quantity=excluded.quantity,
    page_views=excluded.page_views,
    visitors=excluded.visitors,
    conversion_bps=excluded.conversion_bps,
    official_market_price_cents=excluded.official_market_price_cents,
    confirmation_status=excluded.confirmation_status,
    market_price_source=excluded.market_price_source,
    display_price_band=excluded.display_price_band,
    confirmed_price_band=excluded.confirmed_price_band,
    is_own=excluded.is_own,
    refreshed_revision=excluded.refreshed_revision,
    updated_at=CURRENT_TIMESTAMP`;
}

export function buildMarketCachedOverviewAnalyticsSql(options: { where?: string; confirmedOnlyPriceBands?: boolean } = {}) {
  const priceBandColumn = options.confirmedOnlyPriceBands ? "confirmed_price_band" : "display_price_band";
  return `WITH cache_guard AS MATERIALIZED (
    SELECT built_revision FROM market_monthly_summary_cache_state
    WHERE id=1 AND status='ready' AND source_revision=built_revision
  ), analytics_filtered AS MATERIALIZED (
    SELECT m.coverage_period_start period_start, m.coverage_period_end period_end,
      m.category, m.scope, m.ranking_dimension, m.operation_mode, m.subcategory, m.rank,
      m.sku_code, m.product_name, m.brand, m.gmv_cents, m.quantity, m.page_views, m.visitors,
      m.conversion_bps, m.official_market_price_cents, m.confirmation_status, m.market_price_source,
      m.${priceBandColumn} price_band, m.is_own
    FROM market_monthly_summary_cache m JOIN cache_guard ON 1=1
    ${options.where ?? ""}
    ORDER BY m.category,m.scope,m.ranking_dimension,m.sku_code,m.month
  ), ${marketAnalyticsAggregateCtes()}
  ${marketAnalyticsResultSql}`;
}

export function buildMarketItemTrendSql() {
  return `WITH ${marketEffectiveFactsCtes(
    "WHERE m.sku_code=? AND m.category=? AND m.scope=? AND m.ranking_dimension=?",
  )}, trend_source AS MATERIALIZED (
    SELECT m.*,
      ps.confirmed_market_price_cents market_price_cents,
      COALESCE(ps.source_price_cents, ps.average_transaction_price_cents, ps.ai_image_price_cents) candidate_price_cents,
      ps.source_price_cents, ps.ai_image_price_cents, ps.ai_price_type, ps.ai_confidence_bps,
      ps.confirmed_market_price_cents,
      CASE WHEN ps.confirmed_market_price_cents IS NOT NULL THEN '人工确认' ELSE '未确认价格' END price_status,
      CASE
        WHEN ps.source_price_cents IS NOT NULL THEN '源表价格'
        WHEN ps.average_transaction_price_cents IS NOT NULL THEN '系统计算'
        WHEN ps.ai_image_price_cents IS NOT NULL THEN 'AI待确认'
        ELSE '暂无价格'
      END candidate_price_status,
      COALESCE(ps.confirmation_status, 'missing') confirmation_status
    FROM market_effective_rows m
    LEFT JOIN market_price_snapshots ps ON ps.category=m.category
      AND ps.scope=m.scope AND ps.sku_code=m.sku_code
      AND ps.ranking_dimension=m.ranking_dimension AND ps.month=substr(m.period_end,1,7)
  ), trend_selection_source AS MATERIALIZED (
    SELECT * FROM market_basis_rows
  ), ${marketMonthlyCoverageCtes({ source: "trend_source", selectionSource: "trend_selection_source" })}, recent_months AS MATERIALIZED (
    SELECT *, COUNT(*) OVER () total_months
    FROM market_monthly_rows ORDER BY coverage_month DESC LIMIT 120
  )
  SELECT m.coverage_period_start period_start, m.coverage_period_end period_end, m.coverage_month month, m.category, m.scope,
    m.ranking_dimension, m.operation_mode, m.subcategory, m.rank, m.sku_code, m.product_name, m.brand,
    m.monthly_gmv_cents gmv_cents, m.monthly_quantity quantity, m.monthly_visitors visitors,
    m.monthly_conversion_bps conversion_bps, m.market_price_cents, m.candidate_price_cents,
    m.source_price_cents, m.ai_image_price_cents, m.ai_price_type, m.ai_confidence_bps,
    m.confirmed_market_price_cents,
    CASE WHEN m.monthly_quantity>0 THEN CAST(ROUND(m.monthly_gmv_cents*1.0/m.monthly_quantity) AS INTEGER) END average_transaction_price_cents,
    m.price_status, m.candidate_price_status, m.confirmation_status, m.total_months
  FROM recent_months m ORDER BY m.coverage_month ASC`;
}

type MarketAdminAnalysisSqlOptions = {
  factWhere: string;
  priceBandWhere?: string;
  exactIdentity?: boolean;
};

function adminAnalysisPriceBandSql() {
  return officialPriceBandSql("ps.confirmed_market_price_cents", {
    confirmationStatusSql: "ps.confirmation_status",
    aiPriceTypeSql: "ps.ai_price_type",
    categorySql: "m.category",
    periodEndSql: "m.period_end",
    fallbackPriceSql: "NULLIF(m.price_cents, 0)",
  });
}

export function buildMarketAdminComparisonSql(options: MarketAdminAnalysisSqlOptions) {
  const identityColumns = options.exactIdentity
    ? "m.sku_code, m.category, m.scope, m.ranking_dimension"
    : "m.sku_code";
  const groupColumns = options.exactIdentity
    ? "sku_code, category, scope, ranking_dimension"
    : "sku_code";
  return `WITH ${marketEffectiveFactsCtes(options.factWhere)}, comparison_source AS MATERIALIZED (
    SELECT * FROM market_effective_rows
  ), comparison_selection_source AS MATERIALIZED (
    SELECT * FROM market_basis_rows
  ), ${marketMonthlyCoverageCtes({ source: "comparison_source", selectionSource: "comparison_selection_source", includeConversion: false })}, comparison_enriched AS MATERIALIZED (
    SELECT m.*, ps.confirmed_market_price_cents, ${adminAnalysisPriceBandSql()} price_band
    FROM market_monthly_rows m
    LEFT JOIN market_price_snapshots ps ON ps.category=m.category AND ps.scope=m.scope AND ps.sku_code=m.sku_code
      AND ps.ranking_dimension=m.ranking_dimension AND ps.month=m.coverage_month
  ), comparison_rows AS MATERIALIZED (
    SELECT m.sku_code, m.product_name, m.brand, m.category, m.scope, m.ranking_dimension,
      m.coverage_period_end period_end, m.id, m.monthly_gmv_cents gmv_cents,
      m.monthly_quantity quantity, m.monthly_visitors visitors, m.rank, m.confirmed_market_price_cents,
      ROW_NUMBER() OVER (PARTITION BY ${identityColumns} ORDER BY m.coverage_month DESC, m.id DESC) representative_rank
    FROM comparison_enriched m ${options.priceBandWhere ?? ""}
  ) SELECT sku_code,
      MAX(CASE WHEN representative_rank=1 THEN product_name ELSE '' END) product_name,
      MAX(CASE WHEN representative_rank=1 THEN brand ELSE '' END) brand,
      MAX(CASE WHEN representative_rank=1 THEN category ELSE '' END) category,
      MAX(CASE WHEN representative_rank=1 THEN scope ELSE '' END) scope,
      MAX(CASE WHEN representative_rank=1 THEN ranking_dimension ELSE '' END) ranking_dimension,
      SUM(gmv_cents) gmv_cents, SUM(quantity) quantity, SUM(visitors) visitors,
      CASE WHEN SUM(visitors)>0 THEN CAST(ROUND(SUM(quantity)*10000.0/SUM(visitors)) AS INTEGER) ELSE NULL END conversion_bps,
      MIN(rank) best_rank, MAX(confirmed_market_price_cents) market_price_cents,
      CASE WHEN SUM(quantity)>0 THEN CAST(ROUND(SUM(gmv_cents)*1.0/SUM(quantity)) AS INTEGER) ELSE NULL END average_transaction_price_cents
    FROM comparison_rows
    GROUP BY ${groupColumns}
    ORDER BY gmv_cents DESC`;
}

export function buildMarketAdminItemTrendLiteSql(options: MarketAdminAnalysisSqlOptions) {
  return `WITH ${marketEffectiveFactsCtes(options.factWhere)}, trend_source AS MATERIALIZED (
    SELECT * FROM market_effective_rows
  ), trend_selection_source AS MATERIALIZED (
    SELECT * FROM market_basis_rows
  ), ${marketMonthlyCoverageCtes({ source: "trend_source", selectionSource: "trend_selection_source", includeConversion: false })}, trend_enriched AS MATERIALIZED (
    SELECT m.*, ps.confirmed_market_price_cents market_price_cents,
      COALESCE(ps.confirmation_status, 'missing') confirmation_status,
      ${adminAnalysisPriceBandSql()} price_band
    FROM market_monthly_rows m
    LEFT JOIN market_price_snapshots ps ON ps.category=m.category AND ps.scope=m.scope AND ps.sku_code=m.sku_code
      AND ps.ranking_dimension=m.ranking_dimension AND ps.month=m.coverage_month
  ), comparison_months AS MATERIALIZED (
    SELECT coverage_month month, MIN(coverage_period_start) period_start, MAX(coverage_period_end) period_end,
      MIN(rank) rank,
      CASE WHEN COUNT(DISTINCT operation_mode)=1 THEN MAX(operation_mode) ELSE '混合' END operation_mode,
      SUM(monthly_gmv_cents) gmv_cents, SUM(monthly_quantity) quantity,
      SUM(monthly_visitors) visitors,
      CASE WHEN SUM(monthly_visitors)>0
        THEN MIN(10000,MAX(0,CAST(ROUND(SUM(monthly_quantity)*10000.0/SUM(monthly_visitors)) AS INTEGER)))
        ELSE NULL END conversion_bps,
      MAX(market_price_cents) market_price_cents,
      CASE WHEN SUM(monthly_quantity)>0 THEN CAST(ROUND(SUM(monthly_gmv_cents)*1.0/SUM(monthly_quantity)) AS INTEGER) END average_transaction_price_cents,
      CASE WHEN SUM(CASE WHEN confirmation_status='confirmed' THEN 1 ELSE 0 END)=COUNT(*) THEN 'confirmed'
        WHEN SUM(CASE WHEN confirmation_status='confirmed' THEN 1 ELSE 0 END)=0 THEN 'missing'
        ELSE 'mixed' END confirmation_status
    FROM trend_enriched m ${options.priceBandWhere ?? ""} GROUP BY coverage_month
  ), recent_months AS MATERIALIZED (
    SELECT *, COUNT(*) OVER () total_months
    FROM comparison_months ORDER BY month DESC LIMIT 120
  )
  SELECT * FROM recent_months ORDER BY month ASC`;
}

export const marketOverviewFilterOptionsSql = `SELECT
  (SELECT json_group_array(json_object('value', value, 'count', count)) FROM (
    SELECT category value, COUNT(*) count FROM market_ranking_entries WHERE category<>'' GROUP BY category ORDER BY count DESC, value LIMIT 100
  )) categories_json,
  (SELECT json_group_array(json_object('value', value, 'count', count)) FROM (
    SELECT scope value, COUNT(*) count FROM market_ranking_entries WHERE scope<>'' GROUP BY scope ORDER BY count DESC, value LIMIT 30
  )) scopes_json,
  (SELECT json_group_array(json_object('value', value, 'count', count)) FROM (
    SELECT brand value, COUNT(*) count FROM market_ranking_entries WHERE brand<>'' GROUP BY brand ORDER BY count DESC, value LIMIT 100
  )) brands_json,
  (SELECT json_group_array(json_object('value', value, 'count', count)) FROM (
    SELECT ranking_dimension value, COUNT(*) count FROM market_ranking_entries GROUP BY ranking_dimension ORDER BY value
  )) dimensions_json,
  (SELECT json_group_array(json_object('value', value, 'count', count)) FROM (
    SELECT operation_mode value, COUNT(*) count FROM market_ranking_entries GROUP BY operation_mode ORDER BY value
  )) modes_json,
  (SELECT json_group_array(json_object('value', value, 'count', count)) FROM (
    SELECT t.subcategory value, COUNT(DISTINCT m.sku_code) count
    FROM market_subcategory_taxonomy t
    LEFT JOIN market_ranking_entries m ON m.category=t.category AND m.subcategory=t.subcategory
    WHERE t.status='active' GROUP BY t.subcategory ORDER BY count DESC, value LIMIT 100
  )) subcategories_json`;
