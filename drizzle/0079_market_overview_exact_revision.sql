DROP TRIGGER IF EXISTS `market_system_kpi_cache_batch_insert`;
--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_system_kpi_cache_batch_update`;
--> statement-breakpoint
DROP TRIGGER IF EXISTS `market_system_kpi_cache_batch_delete`;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_system_kpi_cache_batch_insert`
AFTER INSERT ON `market_import_batches`
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
CREATE TRIGGER IF NOT EXISTS `market_system_kpi_cache_batch_update`
AFTER UPDATE ON `market_import_batches`
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
CREATE TRIGGER IF NOT EXISTS `market_system_kpi_cache_batch_delete`
AFTER DELETE ON `market_import_batches`
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
UPDATE `market_system_kpi_cache_state`
SET `source_revision`=`source_revision`+1, `updated_at`=CURRENT_TIMESTAMP
WHERE `id`=1;
