CREATE TABLE IF NOT EXISTS `market_master_database_filters_cache_state` (
  `id` integer PRIMARY KEY NOT NULL CHECK (`id` = 1),
  `source_revision` integer NOT NULL DEFAULT 1,
  `updated_at` text NOT NULL DEFAULT CURRENT_TIMESTAMP
);--> statement-breakpoint
INSERT OR IGNORE INTO `market_master_database_filters_cache_state` (`id`, `source_revision`) VALUES (1, 1);--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_master_filters_v1_ranking_insert`
AFTER INSERT ON `market_ranking_entries` BEGIN
  UPDATE `market_master_database_filters_cache_state`
  SET `source_revision`=`source_revision`+1,`updated_at`=CURRENT_TIMESTAMP WHERE `id`=1;
END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_master_filters_v1_ranking_update`
AFTER UPDATE OF `category`,`subcategory`,`sku_code` ON `market_ranking_entries`
WHEN OLD.`category` IS NOT NEW.`category` OR OLD.`subcategory` IS NOT NEW.`subcategory` OR OLD.`sku_code` IS NOT NEW.`sku_code`
BEGIN
  UPDATE `market_master_database_filters_cache_state`
  SET `source_revision`=`source_revision`+1,`updated_at`=CURRENT_TIMESTAMP WHERE `id`=1;
END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_master_filters_v1_ranking_delete`
AFTER DELETE ON `market_ranking_entries` BEGIN
  UPDATE `market_master_database_filters_cache_state`
  SET `source_revision`=`source_revision`+1,`updated_at`=CURRENT_TIMESTAMP WHERE `id`=1;
END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_master_filters_v1_taxonomy_insert`
AFTER INSERT ON `market_subcategory_taxonomy` BEGIN
  UPDATE `market_master_database_filters_cache_state`
  SET `source_revision`=`source_revision`+1,`updated_at`=CURRENT_TIMESTAMP WHERE `id`=1;
END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_master_filters_v1_taxonomy_update`
AFTER UPDATE OF `category`,`subcategory`,`status` ON `market_subcategory_taxonomy`
WHEN OLD.`category` IS NOT NEW.`category` OR OLD.`subcategory` IS NOT NEW.`subcategory` OR OLD.`status` IS NOT NEW.`status`
BEGIN
  UPDATE `market_master_database_filters_cache_state`
  SET `source_revision`=`source_revision`+1,`updated_at`=CURRENT_TIMESTAMP WHERE `id`=1;
END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_master_filters_v1_taxonomy_delete`
AFTER DELETE ON `market_subcategory_taxonomy` BEGIN
  UPDATE `market_master_database_filters_cache_state`
  SET `source_revision`=`source_revision`+1,`updated_at`=CURRENT_TIMESTAMP WHERE `id`=1;
END;
