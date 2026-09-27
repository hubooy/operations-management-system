-- Operator-only migration, deliberately absent from the normal Drizzle
-- journal while the post-sales-retirement audit baseline remains closed.
-- Market analysis keeps a bounded, read-only D1 projection of the PostgreSQL
-- netshop authority.  The revision switch is atomic: readers continue to see
-- the previous complete revision while a successor is staged.
CREATE TABLE IF NOT EXISTS `market_netshop_projection_control` (
  `id` integer PRIMARY KEY NOT NULL CHECK (`id` = 1),
  `active_revision` text NOT NULL DEFAULT '',
  `active_total` integer NOT NULL DEFAULT 0,
  `syncing_revision` text NOT NULL DEFAULT '',
  `owner_token` text NOT NULL DEFAULT '',
  `lease_expires_at` text,
  `updated_at` text NOT NULL DEFAULT CURRENT_TIMESTAMP
);--> statement-breakpoint
INSERT OR IGNORE INTO `market_netshop_projection_control`
  (`id`,`active_revision`,`active_total`,`syncing_revision`,`owner_token`)
VALUES (1,'',0,'','');--> statement-breakpoint

CREATE TABLE IF NOT EXISTS `market_netshop_projection` (
  `projection_revision` text NOT NULL,
  `projection_key` text NOT NULL,
  `kind` text NOT NULL CHECK (`kind` IN ('metric','identity','brand')),
  `source` text NOT NULL DEFAULT '',
  `dataset` text NOT NULL DEFAULT '',
  `platform` text NOT NULL DEFAULT '',
  `shop_name` text NOT NULL DEFAULT '',
  `business_date` text NOT NULL DEFAULT '',
  `sku_id` text NOT NULL DEFAULT '',
  `spu_id` text NOT NULL DEFAULT '',
  `product_code` text NOT NULL DEFAULT '',
  `transaction_amount_cents` integer NOT NULL DEFAULT 0,
  `brand` text NOT NULL DEFAULT '',
  `created_at` text NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`projection_revision`,`projection_key`)
);--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `market_netshop_projection_metric_idx`
  ON `market_netshop_projection`
    (`projection_revision`,`kind`,`source`,`dataset`,`business_date`,`sku_id`,`spu_id`);--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `market_netshop_projection_identity_idx`
  ON `market_netshop_projection`
    (`projection_revision`,`kind`,`sku_id`,`spu_id`,`product_code`);--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `market_netshop_projection_brand_idx`
  ON `market_netshop_projection` (`projection_revision`,`kind`,`brand`);--> statement-breakpoint
CREATE VIEW IF NOT EXISTS `market_netshop_active_projection` AS
  SELECT projection.* FROM `market_netshop_projection` projection
  JOIN `market_netshop_projection_control` control
    ON control.`id`=1
   AND control.`active_revision`=projection.`projection_revision`;--> statement-breakpoint

-- The old triggers observed D1-owned netshop_rows directly.  After migration,
-- only an atomic projection activation invalidates derived market caches.
DROP TRIGGER IF EXISTS `market_effective_cache_netshop_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_effective_cache_netshop_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_effective_cache_netshop_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_effective_cache_netshop_projection`;--> statement-breakpoint
CREATE TRIGGER `market_effective_cache_netshop_projection`
AFTER UPDATE OF `active_revision` ON `market_netshop_projection_control`
WHEN OLD.`active_revision` IS NOT NEW.`active_revision`
BEGIN
  DELETE FROM `market_effective_metrics_cache_state` WHERE `id`=1;
END;--> statement-breakpoint

DROP TRIGGER IF EXISTS `market_monthly_summary_netshop_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_monthly_summary_netshop_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_monthly_summary_netshop_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_monthly_summary_netshop_projection`;--> statement-breakpoint
CREATE TRIGGER `market_monthly_summary_netshop_projection`
AFTER UPDATE OF `active_revision` ON `market_netshop_projection_control`
WHEN OLD.`active_revision` IS NOT NEW.`active_revision`
BEGIN
  UPDATE `market_monthly_summary_cache_state`
    SET `source_revision`=`source_revision`+1,`status`='stale' WHERE `id`=1;
  INSERT INTO `market_monthly_summary_dirty_scopes` (`category`,`dirty_revision`)
    SELECT '*',`source_revision` FROM `market_monthly_summary_cache_state` WHERE `id`=1
    ON CONFLICT(`category`) DO UPDATE
      SET `dirty_revision`=MAX(`dirty_revision`,excluded.`dirty_revision`);
END;--> statement-breakpoint

-- Force one rebuild when the dependency source changes from legacy facts to
-- the PostgreSQL projection, even before the first non-empty activation.
DELETE FROM `market_effective_metrics_cache_state` WHERE `id`=1;--> statement-breakpoint
UPDATE `market_monthly_summary_cache_state`
  SET `source_revision`=`source_revision`+1,`status`='stale' WHERE `id`=1;--> statement-breakpoint
INSERT INTO `market_monthly_summary_dirty_scopes` (`category`,`dirty_revision`)
  SELECT '*',`source_revision` FROM `market_monthly_summary_cache_state` WHERE `id`=1
  ON CONFLICT(`category`) DO UPDATE
    SET `dirty_revision`=MAX(`dirty_revision`,excluded.`dirty_revision`);
