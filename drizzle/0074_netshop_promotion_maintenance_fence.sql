CREATE TABLE IF NOT EXISTS `netshop_promotion_scope_revisions` (
  `platform` text NOT NULL,
  `shop_name` text NOT NULL,
  `data_version` integer DEFAULT 0 NOT NULL CHECK (`data_version` >= 0),
  `updated_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
  PRIMARY KEY (`platform`,`shop_name`)
);--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `netshop_product_daily_scope_revisions` (
  `platform` text NOT NULL,
  `shop_name` text NOT NULL,
  `data_version` integer DEFAULT 0 NOT NULL CHECK (`data_version` >= 0),
  `updated_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
  PRIMARY KEY (`platform`,`shop_name`)
);--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `netshop_promotion_aggregate_control` (
  `platform` text PRIMARY KEY NOT NULL,
  `bootstrap_batch_id` text DEFAULT '' NOT NULL,
  `bootstrap_raw_row_count` integer DEFAULT 0 NOT NULL CHECK (`bootstrap_raw_row_count` >= 0),
  `bootstrap_product_row_count` integer DEFAULT 0 NOT NULL CHECK (`bootstrap_product_row_count` >= 0),
  `bootstrap_shop_day_count` integer DEFAULT 0 NOT NULL CHECK (`bootstrap_shop_day_count` >= 0),
  `bootstrap_data_cutoff` text,
  `maintenance_token` text DEFAULT '' NOT NULL,
  `maintenance_version` integer DEFAULT 0 NOT NULL CHECK (`maintenance_version` >= 0),
  `maintenance_previous_ready` integer DEFAULT 0 NOT NULL CHECK (`maintenance_previous_ready` IN (0, 1)),
  `maintenance_started_at` text,
  `updated_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL
);--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `netshop_promotion_raw_platform_batch_idx`
ON `netshop_rows` (`platform`,`last_import_batch_id`)
WHERE `business_date` IS NOT NULL AND `business_date` <> '' AND (
  (`source` = 'jd_promotion' AND `dataset` = 'ad' AND `sku_id` <> '') OR
  (`source` = 'tmall_promotion' AND `dataset` = 'promotion_daily' AND `spu_id` <> '')
);--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `netshop_promotion_product_daily_platform_batch_idx`
ON `netshop_promotion_product_daily` (`platform`,`source_batch_id`);--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `netshop_promotion_shop_daily_platform_batch_idx`
ON `netshop_promotion_shop_daily` (`platform`,`source_batch_id`);--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `netshop_promotion_aggregate_state_platform_batch_ready_idx`
ON `netshop_promotion_aggregate_state` (`platform`,`source_batch_id`,`ready`);--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_promotion_aggregate_raw_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_promotion_aggregate_raw_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_promotion_aggregate_raw_update_old`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_promotion_aggregate_raw_update_new`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_promotion_aggregate_bootstrap_after_insert`;--> statement-breakpoint
CREATE TRIGGER `netshop_promotion_aggregate_raw_insert`
AFTER INSERT ON `netshop_rows`
WHEN NEW.`business_date` IS NOT NULL AND NEW.`business_date` <> '' AND (
  (NEW.`source` = 'jd_promotion' AND NEW.`dataset` = 'ad' AND NEW.`sku_id` <> '') OR
  (NEW.`source` = 'tmall_promotion' AND NEW.`dataset` = 'promotion_daily' AND NEW.`spu_id` <> '')
) BEGIN
  UPDATE `netshop_promotion_aggregate_manifest`
  SET `data_version` = `data_version` + 1, `invalidated_at` = CURRENT_TIMESTAMP
  WHERE `platform` = NEW.`platform` AND (
    NOT EXISTS (
      SELECT 1 FROM `netshop_promotion_aggregate_state`
      WHERE `platform` = NEW.`platform` AND `shop_name` = NEW.`shop_name`
        AND `business_date` = NEW.`business_date`
    ) OR EXISTS (
      SELECT 1 FROM `netshop_promotion_aggregate_state`
      WHERE `platform` = NEW.`platform` AND `shop_name` = NEW.`shop_name`
        AND `business_date` = NEW.`business_date` AND `ready` = 1
    )
  );
  INSERT INTO `netshop_promotion_scope_revisions` (`platform`,`shop_name`,`data_version`,`updated_at`)
  SELECT NEW.`platform`,NEW.`shop_name`,1,CURRENT_TIMESTAMP
  WHERE (
    NOT EXISTS (
      SELECT 1 FROM `netshop_promotion_aggregate_state`
      WHERE `platform` = NEW.`platform` AND `shop_name` = NEW.`shop_name`
        AND `business_date` = NEW.`business_date`
    ) OR EXISTS (
      SELECT 1 FROM `netshop_promotion_aggregate_state`
      WHERE `platform` = NEW.`platform` AND `shop_name` = NEW.`shop_name`
        AND `business_date` = NEW.`business_date` AND `ready` = 1
    )
  )
  ON CONFLICT(`platform`,`shop_name`) DO UPDATE SET
    `data_version` = `netshop_promotion_scope_revisions`.`data_version` + 1,
    `updated_at` = CURRENT_TIMESTAMP;
  INSERT INTO `netshop_promotion_aggregate_state` (
    `platform`,`shop_name`,`business_date`,`source`,`ready`,`invalidated_at`
  ) VALUES (
    NEW.`platform`,NEW.`shop_name`,NEW.`business_date`,NEW.`source`,0,CURRENT_TIMESTAMP
  )
  ON CONFLICT(`platform`,`shop_name`,`business_date`) DO UPDATE SET
    `source`=excluded.`source`,`ready`=0,`invalidated_at`=CURRENT_TIMESTAMP;
END;--> statement-breakpoint
CREATE TRIGGER `netshop_promotion_aggregate_raw_delete`
AFTER DELETE ON `netshop_rows`
WHEN OLD.`business_date` IS NOT NULL AND OLD.`business_date` <> '' AND (
  (OLD.`source` = 'jd_promotion' AND OLD.`dataset` = 'ad' AND OLD.`sku_id` <> '') OR
  (OLD.`source` = 'tmall_promotion' AND OLD.`dataset` = 'promotion_daily' AND OLD.`spu_id` <> '')
) BEGIN
  UPDATE `netshop_promotion_aggregate_manifest`
  SET `data_version` = `data_version` + 1, `invalidated_at` = CURRENT_TIMESTAMP
  WHERE `platform` = OLD.`platform` AND (
    NOT EXISTS (
      SELECT 1 FROM `netshop_promotion_aggregate_state`
      WHERE `platform` = OLD.`platform` AND `shop_name` = OLD.`shop_name`
        AND `business_date` = OLD.`business_date`
    ) OR EXISTS (
      SELECT 1 FROM `netshop_promotion_aggregate_state`
      WHERE `platform` = OLD.`platform` AND `shop_name` = OLD.`shop_name`
        AND `business_date` = OLD.`business_date` AND `ready` = 1
    )
  );
  INSERT INTO `netshop_promotion_scope_revisions` (`platform`,`shop_name`,`data_version`,`updated_at`)
  SELECT OLD.`platform`,OLD.`shop_name`,1,CURRENT_TIMESTAMP
  WHERE (
    NOT EXISTS (
      SELECT 1 FROM `netshop_promotion_aggregate_state`
      WHERE `platform` = OLD.`platform` AND `shop_name` = OLD.`shop_name`
        AND `business_date` = OLD.`business_date`
    ) OR EXISTS (
      SELECT 1 FROM `netshop_promotion_aggregate_state`
      WHERE `platform` = OLD.`platform` AND `shop_name` = OLD.`shop_name`
        AND `business_date` = OLD.`business_date` AND `ready` = 1
    )
  )
  ON CONFLICT(`platform`,`shop_name`) DO UPDATE SET
    `data_version` = `netshop_promotion_scope_revisions`.`data_version` + 1,
    `updated_at` = CURRENT_TIMESTAMP;
  INSERT INTO `netshop_promotion_aggregate_state` (
    `platform`,`shop_name`,`business_date`,`source`,`ready`,`invalidated_at`
  ) VALUES (
    OLD.`platform`,OLD.`shop_name`,OLD.`business_date`,OLD.`source`,0,CURRENT_TIMESTAMP
  )
  ON CONFLICT(`platform`,`shop_name`,`business_date`) DO UPDATE SET
    `source`=excluded.`source`,`ready`=0,`invalidated_at`=CURRENT_TIMESTAMP;
END;--> statement-breakpoint
CREATE TRIGGER `netshop_promotion_aggregate_raw_update_old`
AFTER UPDATE OF `source`,`dataset`,`platform`,`shop_name`,`business_date`,`sku_id`,`spu_id`,`product_name`,`metrics_json`,`raw_json`,`last_import_batch_id` ON `netshop_rows`
WHEN OLD.`business_date` IS NOT NULL AND OLD.`business_date` <> '' AND (
  (OLD.`source` = 'jd_promotion' AND OLD.`dataset` = 'ad' AND OLD.`sku_id` <> '') OR
  (OLD.`source` = 'tmall_promotion' AND OLD.`dataset` = 'promotion_daily' AND OLD.`spu_id` <> '')
) BEGIN
  UPDATE `netshop_promotion_aggregate_manifest`
  SET `data_version` = `data_version` + 1, `invalidated_at` = CURRENT_TIMESTAMP
  WHERE `platform` = OLD.`platform` AND (
    NOT EXISTS (
      SELECT 1 FROM `netshop_promotion_aggregate_state`
      WHERE `platform` = OLD.`platform` AND `shop_name` = OLD.`shop_name`
        AND `business_date` = OLD.`business_date`
    ) OR EXISTS (
      SELECT 1 FROM `netshop_promotion_aggregate_state`
      WHERE `platform` = OLD.`platform` AND `shop_name` = OLD.`shop_name`
        AND `business_date` = OLD.`business_date` AND `ready` = 1
    )
  );
  INSERT INTO `netshop_promotion_scope_revisions` (`platform`,`shop_name`,`data_version`,`updated_at`)
  SELECT OLD.`platform`,OLD.`shop_name`,1,CURRENT_TIMESTAMP
  WHERE (
    NOT EXISTS (
      SELECT 1 FROM `netshop_promotion_aggregate_state`
      WHERE `platform` = OLD.`platform` AND `shop_name` = OLD.`shop_name`
        AND `business_date` = OLD.`business_date`
    ) OR EXISTS (
      SELECT 1 FROM `netshop_promotion_aggregate_state`
      WHERE `platform` = OLD.`platform` AND `shop_name` = OLD.`shop_name`
        AND `business_date` = OLD.`business_date` AND `ready` = 1
    )
  )
  ON CONFLICT(`platform`,`shop_name`) DO UPDATE SET
    `data_version` = `netshop_promotion_scope_revisions`.`data_version` + 1,
    `updated_at` = CURRENT_TIMESTAMP;
  INSERT INTO `netshop_promotion_aggregate_state` (
    `platform`,`shop_name`,`business_date`,`source`,`ready`,`invalidated_at`
  ) VALUES (
    OLD.`platform`,OLD.`shop_name`,OLD.`business_date`,OLD.`source`,0,CURRENT_TIMESTAMP
  )
  ON CONFLICT(`platform`,`shop_name`,`business_date`) DO UPDATE SET
    `source`=excluded.`source`,`ready`=0,`invalidated_at`=CURRENT_TIMESTAMP;
END;--> statement-breakpoint
CREATE TRIGGER `netshop_promotion_aggregate_raw_update_new`
AFTER UPDATE OF `source`,`dataset`,`platform`,`shop_name`,`business_date`,`sku_id`,`spu_id`,`product_name`,`metrics_json`,`raw_json`,`last_import_batch_id` ON `netshop_rows`
WHEN NEW.`business_date` IS NOT NULL AND NEW.`business_date` <> '' AND (
  (NEW.`source` = 'jd_promotion' AND NEW.`dataset` = 'ad' AND NEW.`sku_id` <> '') OR
  (NEW.`source` = 'tmall_promotion' AND NEW.`dataset` = 'promotion_daily' AND NEW.`spu_id` <> '')
) BEGIN
  UPDATE `netshop_promotion_aggregate_manifest`
  SET `data_version` = `data_version` + 1, `invalidated_at` = CURRENT_TIMESTAMP
  WHERE `platform` = NEW.`platform` AND (
    NOT EXISTS (
      SELECT 1 FROM `netshop_promotion_aggregate_state`
      WHERE `platform` = NEW.`platform` AND `shop_name` = NEW.`shop_name`
        AND `business_date` = NEW.`business_date`
    ) OR EXISTS (
      SELECT 1 FROM `netshop_promotion_aggregate_state`
      WHERE `platform` = NEW.`platform` AND `shop_name` = NEW.`shop_name`
        AND `business_date` = NEW.`business_date` AND `ready` = 1
    )
  );
  INSERT INTO `netshop_promotion_scope_revisions` (`platform`,`shop_name`,`data_version`,`updated_at`)
  SELECT NEW.`platform`,NEW.`shop_name`,1,CURRENT_TIMESTAMP
  WHERE (
    NOT EXISTS (
      SELECT 1 FROM `netshop_promotion_aggregate_state`
      WHERE `platform` = NEW.`platform` AND `shop_name` = NEW.`shop_name`
        AND `business_date` = NEW.`business_date`
    ) OR EXISTS (
      SELECT 1 FROM `netshop_promotion_aggregate_state`
      WHERE `platform` = NEW.`platform` AND `shop_name` = NEW.`shop_name`
        AND `business_date` = NEW.`business_date` AND `ready` = 1
    )
  )
  ON CONFLICT(`platform`,`shop_name`) DO UPDATE SET
    `data_version` = `netshop_promotion_scope_revisions`.`data_version` + 1,
    `updated_at` = CURRENT_TIMESTAMP;
  INSERT INTO `netshop_promotion_aggregate_state` (
    `platform`,`shop_name`,`business_date`,`source`,`ready`,`invalidated_at`
  ) VALUES (
    NEW.`platform`,NEW.`shop_name`,NEW.`business_date`,NEW.`source`,0,CURRENT_TIMESTAMP
  )
  ON CONFLICT(`platform`,`shop_name`,`business_date`) DO UPDATE SET
    `source`=excluded.`source`,`ready`=0,`invalidated_at`=CURRENT_TIMESTAMP;
END;--> statement-breakpoint
CREATE TRIGGER `netshop_promotion_aggregate_bootstrap_after_insert`
AFTER INSERT ON `netshop_promotion_aggregate_control`
WHEN NEW.`bootstrap_batch_id` <> '' AND NEW.`maintenance_token` = ''
BEGIN
  UPDATE `netshop_promotion_aggregate_manifest`
  SET `ready` = 1,
    `historical_data_cutoff` = NEW.`bootstrap_data_cutoff`,
    `source_shop_count` = 1,
    `raw_row_count` = NEW.`bootstrap_raw_row_count`,
    `product_row_count` = NEW.`bootstrap_product_row_count`,
    `shop_day_count` = NEW.`bootstrap_shop_day_count`,
    `state_day_count` = NEW.`bootstrap_shop_day_count`,
    `completed_at` = CURRENT_TIMESTAMP,
    `invalidated_at` = CURRENT_TIMESTAMP
  WHERE `platform` = NEW.`platform` AND `ready` = 0
    AND EXISTS (
      SELECT 1 FROM `netshop_import_batches`
      WHERE `id` = NEW.`bootstrap_batch_id` AND `status` = 'processing'
    )
    AND NOT EXISTS (
      SELECT 1 FROM `netshop_rows` r
      WHERE r.`platform` = NEW.`platform`
        AND r.`business_date` IS NOT NULL AND r.`business_date` <> ''
        AND ((r.`source` = 'jd_promotion' AND r.`dataset` = 'ad' AND r.`sku_id` <> '')
          OR (r.`source` = 'tmall_promotion' AND r.`dataset` = 'promotion_daily' AND r.`spu_id` <> ''))
        AND r.`last_import_batch_id` <> NEW.`bootstrap_batch_id`
      LIMIT 1
    )
    AND (
      SELECT COUNT(*) FROM `netshop_rows` r
      WHERE r.`platform` = NEW.`platform`
        AND r.`business_date` IS NOT NULL AND r.`business_date` <> ''
        AND ((r.`source` = 'jd_promotion' AND r.`dataset` = 'ad' AND r.`sku_id` <> '')
          OR (r.`source` = 'tmall_promotion' AND r.`dataset` = 'promotion_daily' AND r.`spu_id` <> ''))
        AND r.`last_import_batch_id` = NEW.`bootstrap_batch_id`
    ) = NEW.`bootstrap_raw_row_count`
    AND NOT EXISTS (
      SELECT 1 FROM `netshop_promotion_product_daily` p
      WHERE p.`platform` = NEW.`platform` AND p.`source_batch_id` <> NEW.`bootstrap_batch_id`
      LIMIT 1
    )
    AND (
      SELECT COUNT(*) FROM `netshop_promotion_product_daily` p
      WHERE p.`platform` = NEW.`platform` AND p.`source_batch_id` = NEW.`bootstrap_batch_id`
    ) = NEW.`bootstrap_product_row_count`
    AND NOT EXISTS (
      SELECT 1 FROM `netshop_promotion_shop_daily` s
      WHERE s.`platform` = NEW.`platform` AND s.`source_batch_id` <> NEW.`bootstrap_batch_id`
      LIMIT 1
    )
    AND (
      SELECT COUNT(*) FROM `netshop_promotion_shop_daily` s
      WHERE s.`platform` = NEW.`platform` AND s.`source_batch_id` = NEW.`bootstrap_batch_id`
    ) = NEW.`bootstrap_shop_day_count`
    AND NOT EXISTS (
      SELECT 1 FROM `netshop_promotion_aggregate_state` state
      WHERE state.`platform` = NEW.`platform`
        AND (state.`source_batch_id` <> NEW.`bootstrap_batch_id` OR state.`ready` <> 1)
      LIMIT 1
    )
    AND (
      SELECT COUNT(*) FROM `netshop_promotion_aggregate_state` state
      WHERE state.`platform` = NEW.`platform`
        AND state.`source_batch_id` = NEW.`bootstrap_batch_id` AND state.`ready` = 1
    ) = NEW.`bootstrap_shop_day_count`
    AND COALESCE((
      SELECT SUM(state.`raw_row_count`) FROM `netshop_promotion_aggregate_state` state
      WHERE state.`platform` = NEW.`platform`
        AND state.`source_batch_id` = NEW.`bootstrap_batch_id` AND state.`ready` = 1
    ), 0) = NEW.`bootstrap_raw_row_count`
    AND COALESCE((
      SELECT SUM(state.`product_row_count`) FROM `netshop_promotion_aggregate_state` state
      WHERE state.`platform` = NEW.`platform`
        AND state.`source_batch_id` = NEW.`bootstrap_batch_id` AND state.`ready` = 1
    ), 0) = NEW.`bootstrap_product_row_count`;
END;
