CREATE TABLE IF NOT EXISTS `market_import_identity_refresh_keys_v2` (
	`batch_id` text NOT NULL,
	`owner_token` text NOT NULL,
	`category` text NOT NULL,
	`scope` text NOT NULL,
	`ranking_dimension` text NOT NULL,
	`sku_code` text NOT NULL,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY (`batch_id`, `owner_token`, `category`, `scope`, `ranking_dimension`, `sku_code`)
);
