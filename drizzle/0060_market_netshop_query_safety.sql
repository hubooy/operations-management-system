DROP TRIGGER IF EXISTS `market_monthly_summary_netshop_update`;--> statement-breakpoint
CREATE TRIGGER `market_monthly_summary_netshop_update`
AFTER UPDATE OF `sku_id`,`spu_id`,`product_code`,`metrics_json`,`source`,`dataset`,`business_date` ON `netshop_rows`
WHEN OLD.`sku_id` IS NOT NEW.`sku_id`
  OR OLD.`spu_id` IS NOT NEW.`spu_id`
  OR OLD.`product_code` IS NOT NEW.`product_code`
  OR OLD.`metrics_json` IS NOT NEW.`metrics_json`
  OR OLD.`source` IS NOT NEW.`source`
  OR OLD.`dataset` IS NOT NEW.`dataset`
  OR OLD.`business_date` IS NOT NEW.`business_date` BEGIN
  UPDATE `market_monthly_summary_cache_state`
  SET `source_revision`=`source_revision`+1,`status`='stale'
  WHERE `id`=1;
  INSERT INTO `market_monthly_summary_dirty_products` (`product_code`,`dirty_revision`)
  SELECT value,(SELECT `source_revision` FROM `market_monthly_summary_cache_state` WHERE `id`=1)
  FROM json_each(json_array(
    OLD.`sku_id`,OLD.`spu_id`,OLD.`product_code`,
    NEW.`sku_id`,NEW.`spu_id`,NEW.`product_code`
  ))
  WHERE value<>''
  ON CONFLICT(`product_code`) DO UPDATE
  SET `dirty_revision`=MAX(`dirty_revision`,excluded.`dirty_revision`);
END;--> statement-breakpoint

DROP TRIGGER IF EXISTS `market_monthly_summary_sales_update`;--> statement-breakpoint
CREATE TRIGGER `market_monthly_summary_sales_update`
AFTER UPDATE OF `product_code`,`allocated_amount_cents`,`sales_time`,`ship_time` ON `sales_order_lines`
WHEN OLD.`product_code` IS NOT NEW.`product_code`
  OR OLD.`allocated_amount_cents` IS NOT NEW.`allocated_amount_cents`
  OR OLD.`sales_time` IS NOT NEW.`sales_time`
  OR OLD.`ship_time` IS NOT NEW.`ship_time` BEGIN
  UPDATE `market_monthly_summary_cache_state`
  SET `source_revision`=`source_revision`+1,`status`='stale'
  WHERE `id`=1;
  INSERT INTO `market_monthly_summary_dirty_products` (`product_code`,`dirty_revision`)
  SELECT value,(SELECT `source_revision` FROM `market_monthly_summary_cache_state` WHERE `id`=1)
  FROM json_each(json_array(OLD.`product_code`,NEW.`product_code`))
  WHERE value<>''
  ON CONFLICT(`product_code`) DO UPDATE
  SET `dirty_revision`=MAX(`dirty_revision`,excluded.`dirty_revision`);
END;--> statement-breakpoint

UPDATE `market_monthly_summary_cache_state`
SET `source_revision`=`source_revision`+1,`status`='stale'
WHERE `id`=1;--> statement-breakpoint
INSERT INTO `market_monthly_summary_dirty_scopes` (`category`,`dirty_revision`)
SELECT '*',`source_revision`
FROM `market_monthly_summary_cache_state`
WHERE `id`=1
ON CONFLICT(`category`) DO UPDATE
SET `dirty_revision`=MAX(`dirty_revision`,excluded.`dirty_revision`);--> statement-breakpoint
DELETE FROM `market_overview_response_cache`;--> statement-breakpoint

CREATE INDEX IF NOT EXISTS `netshop_rows_source_dataset_scope_date_idx`
ON `netshop_rows` (`source`,`dataset`,`platform`,`shop_name`,`business_date`);--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `netshop_rows_daily_natural_identity_idx`
ON `netshop_rows` (
  `dataset`,`platform`,`shop_name`,`business_date`,
  (CASE WHEN `dataset`='sku_daily' THEN `sku_id` ELSE `spu_id` END),
  `last_import_batch_id`,`id`
)
WHERE `source`='jd_sku_daily'
  AND `dataset` IN ('sku_daily','spu_daily')
  AND `business_date` IS NOT NULL
  AND `business_date`<>''
  AND ((`dataset`='sku_daily' AND `sku_id`<>'') OR (`dataset`='spu_daily' AND `spu_id`<>''));--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `netshop_import_batches_latest_product_idx`
ON `netshop_import_batches` (`source`,`status`,`platform`,`shop_name`,`completed_at`,`created_at`,`id`);
