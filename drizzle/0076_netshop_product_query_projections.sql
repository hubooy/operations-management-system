DROP INDEX IF EXISTS `netshop_rows_product_batch_page_idx`;

CREATE INDEX IF NOT EXISTS `netshop_rows_product_batch_page_idx`
ON `netshop_rows` (`last_import_batch_id`, `shop_name`, `product_name`, `sku_id`, `platform`, `id`)
WHERE `source` IN ('jd_product_master', 'tmall_product_master')
  AND `dataset` = 'product_master';
