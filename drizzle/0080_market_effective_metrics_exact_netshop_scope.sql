DROP TRIGGER IF EXISTS `market_effective_cache_netshop_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_effective_cache_netshop_update`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_effective_cache_netshop_delete`;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_effective_cache_netshop_insert`
AFTER INSERT ON `netshop_rows`
WHEN NEW.`source`='jd_sku_daily' AND NEW.`dataset` IN ('sku_daily','spu_daily')
BEGIN DELETE FROM `market_effective_metrics_cache_state` WHERE `id`=1; END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_effective_cache_netshop_update`
AFTER UPDATE ON `netshop_rows`
WHEN (OLD.`source`='jd_sku_daily' AND OLD.`dataset` IN ('sku_daily','spu_daily'))
  OR (NEW.`source`='jd_sku_daily' AND NEW.`dataset` IN ('sku_daily','spu_daily'))
BEGIN DELETE FROM `market_effective_metrics_cache_state` WHERE `id`=1; END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_effective_cache_netshop_delete`
AFTER DELETE ON `netshop_rows`
WHEN OLD.`source`='jd_sku_daily' AND OLD.`dataset` IN ('sku_daily','spu_daily')
BEGIN DELETE FROM `market_effective_metrics_cache_state` WHERE `id`=1; END;--> statement-breakpoint
DELETE FROM `market_effective_metrics_cache_state` WHERE `id`=1;
