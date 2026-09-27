CREATE INDEX IF NOT EXISTS `netshop_promotion_aggregate_state_stale_platform_date_idx`
ON `netshop_promotion_aggregate_state` (`ready`,`platform`,`business_date`);--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_promotion_aggregate_raw_insert`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_promotion_aggregate_raw_delete`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_promotion_aggregate_raw_update_old`;--> statement-breakpoint
DROP TRIGGER IF EXISTS `netshop_promotion_aggregate_raw_update_new`;--> statement-breakpoint
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
  INSERT INTO `netshop_promotion_aggregate_state` (
    `platform`,`shop_name`,`business_date`,`source`,`ready`,`invalidated_at`
  ) VALUES (
    NEW.`platform`,NEW.`shop_name`,NEW.`business_date`,NEW.`source`,0,CURRENT_TIMESTAMP
  )
  ON CONFLICT(`platform`,`shop_name`,`business_date`) DO UPDATE SET
    `source`=excluded.`source`,`ready`=0,`invalidated_at`=CURRENT_TIMESTAMP;
END;
