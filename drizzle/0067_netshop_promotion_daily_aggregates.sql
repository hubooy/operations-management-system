CREATE TABLE IF NOT EXISTS `netshop_promotion_product_daily` (
  `platform` text NOT NULL,
  `shop_name` text NOT NULL,
  `business_date` text NOT NULL,
  `product_id` text NOT NULL,
  `source` text NOT NULL,
  `product_name` text DEFAULT '' NOT NULL,
  `product_line` text DEFAULT '' NOT NULL,
  `spend_cents` integer DEFAULT 0 NOT NULL,
  `net_transaction_amount_cents` integer DEFAULT 0 NOT NULL,
  `gross_transaction_amount_cents` integer DEFAULT 0 NOT NULL,
  `impressions` integer DEFAULT 0 NOT NULL,
  `clicks` integer DEFAULT 0 NOT NULL,
  `net_orders` integer DEFAULT 0 NOT NULL,
  `favorites` integer DEFAULT 0 NOT NULL,
  `cart_quantity` integer DEFAULT 0 NOT NULL,
  `source_row_count` integer DEFAULT 0 NOT NULL,
  `source_batch_id` text DEFAULT '' NOT NULL,
  `source_batch_count` integer DEFAULT 0 NOT NULL,
  `rebuilt_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
  PRIMARY KEY (`platform`,`shop_name`,`business_date`,`product_id`)
);--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `netshop_promotion_shop_daily` (
  `platform` text NOT NULL,
  `shop_name` text NOT NULL,
  `business_date` text NOT NULL,
  `source` text NOT NULL,
  `product_count` integer DEFAULT 0 NOT NULL,
  `spend_cents` integer DEFAULT 0 NOT NULL,
  `net_transaction_amount_cents` integer DEFAULT 0 NOT NULL,
  `gross_transaction_amount_cents` integer DEFAULT 0 NOT NULL,
  `impressions` integer DEFAULT 0 NOT NULL,
  `clicks` integer DEFAULT 0 NOT NULL,
  `net_orders` integer DEFAULT 0 NOT NULL,
  `favorites` integer DEFAULT 0 NOT NULL,
  `cart_quantity` integer DEFAULT 0 NOT NULL,
  `source_row_count` integer DEFAULT 0 NOT NULL,
  `source_batch_id` text DEFAULT '' NOT NULL,
  `source_batch_count` integer DEFAULT 0 NOT NULL,
  `rebuilt_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
  PRIMARY KEY (`platform`,`shop_name`,`business_date`)
);--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `netshop_promotion_aggregate_state` (
  `platform` text NOT NULL,
  `shop_name` text NOT NULL,
  `business_date` text NOT NULL,
  `source` text NOT NULL,
  `ready` integer DEFAULT 0 NOT NULL CHECK (`ready` IN (0, 1)),
  `raw_row_count` integer DEFAULT 0 NOT NULL,
  `product_row_count` integer DEFAULT 0 NOT NULL,
  `source_batch_id` text DEFAULT '' NOT NULL,
  `source_batch_count` integer DEFAULT 0 NOT NULL,
  `rebuilt_at` text,
  `invalidated_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
  PRIMARY KEY (`platform`,`shop_name`,`business_date`)
);--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `netshop_promotion_product_daily_platform_date_shop_idx`
ON `netshop_promotion_product_daily` (`platform`,`business_date`,`shop_name`);--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `netshop_promotion_product_daily_scope_date_product_idx`
ON `netshop_promotion_product_daily` (`platform`,`shop_name`,`business_date`,`product_id`);--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `netshop_promotion_product_daily_scope_product_date_idx`
ON `netshop_promotion_product_daily` (`platform`,`shop_name`,`product_id`,`business_date`);--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `netshop_promotion_shop_daily_platform_date_shop_idx`
ON `netshop_promotion_shop_daily` (`platform`,`business_date`,`shop_name`);--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `netshop_promotion_aggregate_state_ready_scope_date_idx`
ON `netshop_promotion_aggregate_state` (`ready`,`platform`,`shop_name`,`business_date`);--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `netshop_promotion_aggregate_raw_insert`
AFTER INSERT ON `netshop_rows`
WHEN NEW.`business_date` IS NOT NULL AND NEW.`business_date` <> '' AND (
  (NEW.`source` = 'jd_promotion' AND NEW.`dataset` = 'ad' AND NEW.`sku_id` <> '') OR
  (NEW.`source` = 'tmall_promotion' AND NEW.`dataset` = 'promotion_daily' AND NEW.`spu_id` <> '')
) BEGIN
  INSERT INTO `netshop_promotion_aggregate_state` (
    `platform`,`shop_name`,`business_date`,`source`,`ready`,`invalidated_at`
  ) VALUES (
    NEW.`platform`,NEW.`shop_name`,NEW.`business_date`,NEW.`source`,0,CURRENT_TIMESTAMP
  )
  ON CONFLICT(`platform`,`shop_name`,`business_date`) DO UPDATE SET
    `source`=excluded.`source`,`ready`=0,`invalidated_at`=CURRENT_TIMESTAMP;
END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `netshop_promotion_aggregate_raw_delete`
AFTER DELETE ON `netshop_rows`
WHEN OLD.`business_date` IS NOT NULL AND OLD.`business_date` <> '' AND (
  (OLD.`source` = 'jd_promotion' AND OLD.`dataset` = 'ad' AND OLD.`sku_id` <> '') OR
  (OLD.`source` = 'tmall_promotion' AND OLD.`dataset` = 'promotion_daily' AND OLD.`spu_id` <> '')
) BEGIN
  INSERT INTO `netshop_promotion_aggregate_state` (
    `platform`,`shop_name`,`business_date`,`source`,`ready`,`invalidated_at`
  ) VALUES (
    OLD.`platform`,OLD.`shop_name`,OLD.`business_date`,OLD.`source`,0,CURRENT_TIMESTAMP
  )
  ON CONFLICT(`platform`,`shop_name`,`business_date`) DO UPDATE SET
    `source`=excluded.`source`,`ready`=0,`invalidated_at`=CURRENT_TIMESTAMP;
END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `netshop_promotion_aggregate_raw_update_old`
AFTER UPDATE OF `source`,`dataset`,`platform`,`shop_name`,`business_date`,`sku_id`,`spu_id`,`product_name`,`metrics_json`,`raw_json`,`last_import_batch_id` ON `netshop_rows`
WHEN OLD.`business_date` IS NOT NULL AND OLD.`business_date` <> '' AND (
  (OLD.`source` = 'jd_promotion' AND OLD.`dataset` = 'ad' AND OLD.`sku_id` <> '') OR
  (OLD.`source` = 'tmall_promotion' AND OLD.`dataset` = 'promotion_daily' AND OLD.`spu_id` <> '')
) BEGIN
  INSERT INTO `netshop_promotion_aggregate_state` (
    `platform`,`shop_name`,`business_date`,`source`,`ready`,`invalidated_at`
  ) VALUES (
    OLD.`platform`,OLD.`shop_name`,OLD.`business_date`,OLD.`source`,0,CURRENT_TIMESTAMP
  )
  ON CONFLICT(`platform`,`shop_name`,`business_date`) DO UPDATE SET
    `source`=excluded.`source`,`ready`=0,`invalidated_at`=CURRENT_TIMESTAMP;
END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `netshop_promotion_aggregate_raw_update_new`
AFTER UPDATE OF `source`,`dataset`,`platform`,`shop_name`,`business_date`,`sku_id`,`spu_id`,`product_name`,`metrics_json`,`raw_json`,`last_import_batch_id` ON `netshop_rows`
WHEN NEW.`business_date` IS NOT NULL AND NEW.`business_date` <> '' AND (
  (NEW.`source` = 'jd_promotion' AND NEW.`dataset` = 'ad' AND NEW.`sku_id` <> '') OR
  (NEW.`source` = 'tmall_promotion' AND NEW.`dataset` = 'promotion_daily' AND NEW.`spu_id` <> '')
) BEGIN
  INSERT INTO `netshop_promotion_aggregate_state` (
    `platform`,`shop_name`,`business_date`,`source`,`ready`,`invalidated_at`
  ) VALUES (
    NEW.`platform`,NEW.`shop_name`,NEW.`business_date`,NEW.`source`,0,CURRENT_TIMESTAMP
  )
  ON CONFLICT(`platform`,`shop_name`,`business_date`) DO UPDATE SET
    `source`=excluded.`source`,`ready`=0,`invalidated_at`=CURRENT_TIMESTAMP;
END;
