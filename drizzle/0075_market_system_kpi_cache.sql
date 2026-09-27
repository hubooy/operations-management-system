CREATE TABLE IF NOT EXISTS `market_system_kpi_cache_state` (
	`id` integer PRIMARY KEY NOT NULL CHECK (`id` = 1),
	`source_revision` integer DEFAULT 1 NOT NULL,
	`updated_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL
);
--> statement-breakpoint
INSERT OR IGNORE INTO `market_system_kpi_cache_state` (`id`, `source_revision`) VALUES (1, 1);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `market_system_kpi_cache_control` (
	`id` integer PRIMARY KEY NOT NULL CHECK (`id` = 1),
	`suppress_all_revision` integer DEFAULT 0 NOT NULL CHECK (`suppress_all_revision` IN (0, 1)),
	`suppress_identity_revision` integer DEFAULT 0 NOT NULL CHECK (`suppress_identity_revision` IN (0, 1)),
	`owner_token` text DEFAULT '' NOT NULL,
	`updated_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL
);
--> statement-breakpoint
INSERT OR IGNORE INTO `market_system_kpi_cache_control`
	(`id`, `suppress_all_revision`, `suppress_identity_revision`, `owner_token`) VALUES (1, 0, 0, '');
--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_system_kpi_cache_ranking_insert`;
--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_system_kpi_cache_ranking_update`;
--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_system_kpi_cache_ranking_delete`;
--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_system_kpi_cache_price_insert`;
--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_system_kpi_cache_price_update`;
--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_system_kpi_cache_price_delete`;
--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_system_kpi_cache_prompt_insert`;
--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_system_kpi_cache_prompt_update`;
--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_system_kpi_cache_prompt_delete`;
--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_system_kpi_cache_image_insert`;
--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_system_kpi_cache_image_update`;
--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_system_kpi_cache_image_delete`;
--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_system_kpi_cache_annotation_insert`;
--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_system_kpi_cache_annotation_update`;
--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_system_kpi_cache_annotation_delete`;
--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_system_kpi_cache_taxonomy_insert`;
--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_system_kpi_cache_taxonomy_update`;
--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_system_kpi_cache_taxonomy_delete`;
--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_system_kpi_cache_identity_insert`;
--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_system_kpi_cache_identity_update`;
--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_system_kpi_cache_identity_delete`;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_system_kpi_cache_ranking_insert`
AFTER INSERT ON `market_ranking_entries`
WHEN NOT EXISTS (
	SELECT 1 FROM `market_system_kpi_cache_control`
	WHERE `id`=1 AND (`suppress_all_revision`=1)
)
BEGIN
	UPDATE `market_system_kpi_cache_state`
	SET `source_revision`=`source_revision`+1, `updated_at`=CURRENT_TIMESTAMP
	WHERE `id`=1;
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_system_kpi_cache_ranking_update`
AFTER UPDATE ON `market_ranking_entries`
WHEN NOT EXISTS (
	SELECT 1 FROM `market_system_kpi_cache_control`
	WHERE `id`=1 AND (`suppress_all_revision`=1)
)
BEGIN
	UPDATE `market_system_kpi_cache_state`
	SET `source_revision`=`source_revision`+1, `updated_at`=CURRENT_TIMESTAMP
	WHERE `id`=1;
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_system_kpi_cache_ranking_delete`
AFTER DELETE ON `market_ranking_entries`
WHEN NOT EXISTS (
	SELECT 1 FROM `market_system_kpi_cache_control`
	WHERE `id`=1 AND (`suppress_all_revision`=1)
)
BEGIN
	UPDATE `market_system_kpi_cache_state`
	SET `source_revision`=`source_revision`+1, `updated_at`=CURRENT_TIMESTAMP
	WHERE `id`=1;
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_system_kpi_cache_price_insert`
AFTER INSERT ON `market_price_snapshots`
WHEN NOT EXISTS (
	SELECT 1 FROM `market_system_kpi_cache_control`
	WHERE `id`=1 AND (`suppress_all_revision`=1)
)
BEGIN
	UPDATE `market_system_kpi_cache_state`
	SET `source_revision`=`source_revision`+1, `updated_at`=CURRENT_TIMESTAMP
	WHERE `id`=1;
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_system_kpi_cache_price_update`
AFTER UPDATE ON `market_price_snapshots`
WHEN NOT EXISTS (
	SELECT 1 FROM `market_system_kpi_cache_control`
	WHERE `id`=1 AND (`suppress_all_revision`=1)
)
BEGIN
	UPDATE `market_system_kpi_cache_state`
	SET `source_revision`=`source_revision`+1, `updated_at`=CURRENT_TIMESTAMP
	WHERE `id`=1;
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_system_kpi_cache_price_delete`
AFTER DELETE ON `market_price_snapshots`
WHEN NOT EXISTS (
	SELECT 1 FROM `market_system_kpi_cache_control`
	WHERE `id`=1 AND (`suppress_all_revision`=1)
)
BEGIN
	UPDATE `market_system_kpi_cache_state`
	SET `source_revision`=`source_revision`+1, `updated_at`=CURRENT_TIMESTAMP
	WHERE `id`=1;
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_system_kpi_cache_prompt_insert`
AFTER INSERT ON `market_annotation_prompt_versions`
WHEN NOT EXISTS (
	SELECT 1 FROM `market_system_kpi_cache_control`
	WHERE `id`=1 AND (`suppress_all_revision`=1)
)
BEGIN
	UPDATE `market_system_kpi_cache_state`
	SET `source_revision`=`source_revision`+1, `updated_at`=CURRENT_TIMESTAMP
	WHERE `id`=1;
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_system_kpi_cache_prompt_update`
AFTER UPDATE ON `market_annotation_prompt_versions`
WHEN NOT EXISTS (
	SELECT 1 FROM `market_system_kpi_cache_control`
	WHERE `id`=1 AND (`suppress_all_revision`=1)
)
BEGIN
	UPDATE `market_system_kpi_cache_state`
	SET `source_revision`=`source_revision`+1, `updated_at`=CURRENT_TIMESTAMP
	WHERE `id`=1;
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_system_kpi_cache_prompt_delete`
AFTER DELETE ON `market_annotation_prompt_versions`
WHEN NOT EXISTS (
	SELECT 1 FROM `market_system_kpi_cache_control`
	WHERE `id`=1 AND (`suppress_all_revision`=1)
)
BEGIN
	UPDATE `market_system_kpi_cache_state`
	SET `source_revision`=`source_revision`+1, `updated_at`=CURRENT_TIMESTAMP
	WHERE `id`=1;
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_system_kpi_cache_image_insert`
AFTER INSERT ON `market_image_cache`
WHEN NOT EXISTS (
	SELECT 1 FROM `market_system_kpi_cache_control`
	WHERE `id`=1 AND (`suppress_all_revision`=1)
)
BEGIN
	UPDATE `market_system_kpi_cache_state`
	SET `source_revision`=`source_revision`+1, `updated_at`=CURRENT_TIMESTAMP
	WHERE `id`=1;
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_system_kpi_cache_image_update`
AFTER UPDATE ON `market_image_cache`
WHEN NOT EXISTS (
	SELECT 1 FROM `market_system_kpi_cache_control`
	WHERE `id`=1 AND (`suppress_all_revision`=1)
)
BEGIN
	UPDATE `market_system_kpi_cache_state`
	SET `source_revision`=`source_revision`+1, `updated_at`=CURRENT_TIMESTAMP
	WHERE `id`=1;
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_system_kpi_cache_image_delete`
AFTER DELETE ON `market_image_cache`
WHEN NOT EXISTS (
	SELECT 1 FROM `market_system_kpi_cache_control`
	WHERE `id`=1 AND (`suppress_all_revision`=1)
)
BEGIN
	UPDATE `market_system_kpi_cache_state`
	SET `source_revision`=`source_revision`+1, `updated_at`=CURRENT_TIMESTAMP
	WHERE `id`=1;
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_system_kpi_cache_annotation_insert`
AFTER INSERT ON `market_annotation_items`
WHEN NOT EXISTS (
	SELECT 1 FROM `market_system_kpi_cache_control`
	WHERE `id`=1 AND (`suppress_all_revision`=1)
)
BEGIN
	UPDATE `market_system_kpi_cache_state`
	SET `source_revision`=`source_revision`+1, `updated_at`=CURRENT_TIMESTAMP
	WHERE `id`=1;
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_system_kpi_cache_annotation_update`
AFTER UPDATE ON `market_annotation_items`
WHEN NOT EXISTS (
	SELECT 1 FROM `market_system_kpi_cache_control`
	WHERE `id`=1 AND (`suppress_all_revision`=1)
)
BEGIN
	UPDATE `market_system_kpi_cache_state`
	SET `source_revision`=`source_revision`+1, `updated_at`=CURRENT_TIMESTAMP
	WHERE `id`=1;
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_system_kpi_cache_annotation_delete`
AFTER DELETE ON `market_annotation_items`
WHEN NOT EXISTS (
	SELECT 1 FROM `market_system_kpi_cache_control`
	WHERE `id`=1 AND (`suppress_all_revision`=1)
)
BEGIN
	UPDATE `market_system_kpi_cache_state`
	SET `source_revision`=`source_revision`+1, `updated_at`=CURRENT_TIMESTAMP
	WHERE `id`=1;
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_system_kpi_cache_taxonomy_insert`
AFTER INSERT ON `market_subcategory_taxonomy`
WHEN NOT EXISTS (
	SELECT 1 FROM `market_system_kpi_cache_control`
	WHERE `id`=1 AND (`suppress_all_revision`=1)
)
BEGIN
	UPDATE `market_system_kpi_cache_state`
	SET `source_revision`=`source_revision`+1, `updated_at`=CURRENT_TIMESTAMP
	WHERE `id`=1;
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_system_kpi_cache_taxonomy_update`
AFTER UPDATE ON `market_subcategory_taxonomy`
WHEN NOT EXISTS (
	SELECT 1 FROM `market_system_kpi_cache_control`
	WHERE `id`=1 AND (`suppress_all_revision`=1)
)
BEGIN
	UPDATE `market_system_kpi_cache_state`
	SET `source_revision`=`source_revision`+1, `updated_at`=CURRENT_TIMESTAMP
	WHERE `id`=1;
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_system_kpi_cache_taxonomy_delete`
AFTER DELETE ON `market_subcategory_taxonomy`
WHEN NOT EXISTS (
	SELECT 1 FROM `market_system_kpi_cache_control`
	WHERE `id`=1 AND (`suppress_all_revision`=1)
)
BEGIN
	UPDATE `market_system_kpi_cache_state`
	SET `source_revision`=`source_revision`+1, `updated_at`=CURRENT_TIMESTAMP
	WHERE `id`=1;
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_system_kpi_cache_identity_insert`
AFTER INSERT ON `market_master_identities`
WHEN NOT EXISTS (
	SELECT 1 FROM `market_system_kpi_cache_control`
	WHERE `id`=1 AND (`suppress_all_revision`=1 OR `suppress_identity_revision`=1)
)
BEGIN
	UPDATE `market_system_kpi_cache_state`
	SET `source_revision`=`source_revision`+1, `updated_at`=CURRENT_TIMESTAMP
	WHERE `id`=1;
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_system_kpi_cache_identity_update`
AFTER UPDATE ON `market_master_identities`
WHEN NOT EXISTS (
	SELECT 1 FROM `market_system_kpi_cache_control`
	WHERE `id`=1 AND (`suppress_all_revision`=1 OR `suppress_identity_revision`=1)
)
BEGIN
	UPDATE `market_system_kpi_cache_state`
	SET `source_revision`=`source_revision`+1, `updated_at`=CURRENT_TIMESTAMP
	WHERE `id`=1;
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_system_kpi_cache_identity_delete`
AFTER DELETE ON `market_master_identities`
WHEN NOT EXISTS (
	SELECT 1 FROM `market_system_kpi_cache_control`
	WHERE `id`=1 AND (`suppress_all_revision`=1 OR `suppress_identity_revision`=1)
)
BEGIN
	UPDATE `market_system_kpi_cache_state`
	SET `source_revision`=`source_revision`+1, `updated_at`=CURRENT_TIMESTAMP
	WHERE `id`=1;
END;
