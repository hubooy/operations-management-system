export const netshopPromotionMetrics = {
  spendCents: `COALESCE(CAST(json_extract(r.metrics_json, '$.spendCents') AS REAL), ROUND(CAST(json_extract(r.metrics_json, '$."花费"') AS REAL) * 100), 0)`,
  netTransactionAmountCents: `COALESCE(CAST(json_extract(r.metrics_json, '$.netTransactionAmountCents') AS REAL), ROUND(CAST(json_extract(r.metrics_json, '$."总订单金额"') AS REAL) * 100), 0)`,
  grossTransactionAmountCents: `COALESCE(CAST(json_extract(r.metrics_json, '$.grossTransactionAmountCents') AS REAL), ROUND(CAST(json_extract(r.metrics_json, '$."总订单金额"') AS REAL) * 100), 0)`,
  impressions: `COALESCE(CAST(json_extract(r.metrics_json, '$.impressions') AS REAL), CAST(json_extract(r.metrics_json, '$."展现数"') AS REAL), 0)`,
  clicks: `COALESCE(CAST(json_extract(r.metrics_json, '$.clicks') AS REAL), CAST(json_extract(r.metrics_json, '$."点击数"') AS REAL), 0)`,
  netOrders: `COALESCE(CAST(json_extract(r.metrics_json, '$.netOrders') AS REAL), CAST(json_extract(r.metrics_json, '$."总订单行"') AS REAL), 0)`,
  favorites: `COALESCE(CAST(json_extract(r.metrics_json, '$.favorites') AS REAL), 0)`,
  cartQuantity: `COALESCE(CAST(json_extract(r.metrics_json, '$.cartQuantity') AS REAL), 0)`,
} as const;

export const netshopPromotionProductIdSql = "CASE WHEN r.source = 'jd_promotion' THEN r.sku_id ELSE r.spu_id END";

export const netshopPromotionSourceSql = `(
  (r.source = 'tmall_promotion' AND r.dataset = 'promotion_daily' AND r.spu_id <> '')
  OR (r.source = 'jd_promotion' AND r.dataset = 'ad' AND r.sku_id <> '')
)`;

export const netshopPromotionPaymentSourceSql = `(
  (r.source = 'tmall_product_daily' AND r.dataset = 'spu_daily' AND r.platform = '天猫')
  OR (r.source = 'jd_sku_daily' AND r.dataset = 'sku_daily' AND r.platform = '京东')
)`;
