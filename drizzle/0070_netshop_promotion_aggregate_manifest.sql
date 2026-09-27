CREATE TABLE IF NOT EXISTS `netshop_promotion_aggregate_manifest` (
  `platform` text PRIMARY KEY NOT NULL,
  `ready` integer DEFAULT 0 NOT NULL CHECK (`ready` IN (0, 1)),
  `historical_data_cutoff` text,
  `source_shop_count` integer DEFAULT 0 NOT NULL,
  `raw_row_count` integer DEFAULT 0 NOT NULL,
  `product_row_count` integer DEFAULT 0 NOT NULL,
  `shop_day_count` integer DEFAULT 0 NOT NULL,
  `state_day_count` integer DEFAULT 0 NOT NULL,
  `completed_at` text,
  `invalidated_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
  `data_version` integer DEFAULT 0 NOT NULL
);
