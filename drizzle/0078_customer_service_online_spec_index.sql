CREATE INDEX IF NOT EXISTS netshop_rows_jd_master_online_spec_idx
ON netshop_rows (
  CAST(json_extract(raw_json, '$."商家SKU"') AS TEXT),
  sku_id
)
WHERE source = 'jd_product_master'
  AND dataset = 'product_master'
  AND json_valid(raw_json);
