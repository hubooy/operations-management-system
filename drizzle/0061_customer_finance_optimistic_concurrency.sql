CREATE TABLE IF NOT EXISTS `customer_service_conversation_versions` (
	`conversation_id` integer PRIMARY KEY NOT NULL REFERENCES `customer_service_conversations` (`id`) ON DELETE CASCADE,
	`version` integer DEFAULT 1 NOT NULL,
	`updated_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL
);--> statement-breakpoint
INSERT OR IGNORE INTO `customer_service_conversation_versions` (`conversation_id`, `version`, `updated_at`)
SELECT `id`, 1, CURRENT_TIMESTAMP FROM `customer_service_conversations`;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `customer_service_conversation_version_insert`
AFTER INSERT ON `customer_service_conversations`
BEGIN
	INSERT OR IGNORE INTO `customer_service_conversation_versions` (`conversation_id`, `version`, `updated_at`)
	VALUES (NEW.`id`, 1, CURRENT_TIMESTAMP);
END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `customer_service_conversation_version_update`
BEFORE UPDATE ON `customer_service_conversations`
WHEN EXISTS (SELECT 1 FROM `customer_service_conversation_versions` WHERE `conversation_id` = OLD.`id`)
BEGIN
	UPDATE `customer_service_conversation_versions`
	SET `version` = `version` + 1, `updated_at` = CURRENT_TIMESTAMP
	WHERE `conversation_id` = OLD.`id`;
END;--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `customer_service_deletion_audits` (
	`audit_id` text PRIMARY KEY NOT NULL,
	`conversation_id` integer NOT NULL,
	`conversation_key` text NOT NULL,
	`actor` text NOT NULL,
	`old_version` integer NOT NULL CHECK (`old_version` > 0),
	`expected_version` integer NOT NULL CHECK (`expected_version` > 0),
	`reason` text NOT NULL,
	`deleted_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL
);--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `finance_target_versions` (
	`target_id` text PRIMARY KEY NOT NULL REFERENCES `finance_targets` (`id`) ON DELETE CASCADE,
	`version` integer DEFAULT 1 NOT NULL,
	`updated_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL
);--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `finance_target_deletion_audits` (
	`audit_id` text PRIMARY KEY NOT NULL,
	`target_id` text NOT NULL,
	`period_type` text NOT NULL,
	`period_key` text NOT NULL,
	`shop_name` text NOT NULL,
	`category` text NOT NULL,
	`actor` text NOT NULL,
	`old_version` integer NOT NULL CHECK (`old_version` > 0),
	`expected_version` integer NOT NULL CHECK (`expected_version` > 0),
	`reason` text NOT NULL,
	`deleted_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL
);--> statement-breakpoint
INSERT OR IGNORE INTO `finance_target_versions` (`target_id`, `version`, `updated_at`)
SELECT `id`, 1, CURRENT_TIMESTAMP FROM `finance_targets`;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `finance_target_version_insert`
AFTER INSERT ON `finance_targets`
BEGIN
	INSERT OR IGNORE INTO `finance_target_versions` (`target_id`, `version`, `updated_at`)
	VALUES (NEW.`id`, 1, CURRENT_TIMESTAMP);
END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `finance_target_version_update`
BEFORE UPDATE ON `finance_targets`
WHEN EXISTS (SELECT 1 FROM `finance_target_versions` WHERE `target_id` = OLD.`id`)
BEGIN
	UPDATE `finance_target_versions`
	SET `version` = `version` + 1, `updated_at` = CURRENT_TIMESTAMP
	WHERE `target_id` = OLD.`id`;
END;--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `finance_targets_scoped` (
	`id` text PRIMARY KEY NOT NULL,
	`period_type` text NOT NULL,
	`period_key` text NOT NULL,
	`platform` text DEFAULT '' NOT NULL,
	`shop_name` text DEFAULT '' NOT NULL,
	`category` text DEFAULT '' NOT NULL,
	`manager` text DEFAULT '' NOT NULL,
	`sales_target_cents` integer DEFAULT 0 NOT NULL,
	`profit_target_cents` integer DEFAULT 0 NOT NULL,
	`small_margin_bps` integer DEFAULT 0 NOT NULL,
	`inventory_cleanup_target_cents` integer DEFAULT 0 NOT NULL,
	`promotion_fee_ratio_bps` integer DEFAULT 0 NOT NULL,
	`stagnant_inventory_target_cents` integer DEFAULT 0 NOT NULL,
	`created_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	`updated_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL,
	UNIQUE (`period_type`, `period_key`, `platform`, `shop_name`, `category`)
);--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `finance_target_legacy_migrations` (
	`target_id` text PRIMARY KEY NOT NULL,
	`migrated_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL
);--> statement-breakpoint
INSERT OR IGNORE INTO `finance_targets_scoped` (
	`id`, `period_type`, `period_key`, `platform`, `shop_name`, `category`, `manager`,
	`sales_target_cents`, `profit_target_cents`, `small_margin_bps`,
	`inventory_cleanup_target_cents`, `promotion_fee_ratio_bps`,
	`stagnant_inventory_target_cents`, `created_at`, `updated_at`
)
SELECT `id`, `period_type`, `period_key`, '', `shop_name`, `category`, `manager`,
	`sales_target_cents`, `profit_target_cents`, `small_margin_bps`,
	`inventory_cleanup_target_cents`, `promotion_fee_ratio_bps`,
	`stagnant_inventory_target_cents`, `created_at`, `updated_at`
FROM `finance_targets` AS legacy
WHERE NOT EXISTS (
	SELECT 1 FROM `finance_target_legacy_migrations` AS migration WHERE migration.`target_id` = legacy.`id`
);--> statement-breakpoint
INSERT OR IGNORE INTO `finance_target_legacy_migrations` (`target_id`, `migrated_at`)
SELECT `id`, CURRENT_TIMESTAMP FROM `finance_targets`;--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `finance_target_scoped_versions` (
	`target_id` text PRIMARY KEY NOT NULL REFERENCES `finance_targets_scoped` (`id`) ON DELETE CASCADE,
	`version` integer DEFAULT 1 NOT NULL,
	`updated_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL
);--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `finance_target_scoped_deletion_audits` (
	`audit_id` text PRIMARY KEY NOT NULL,
	`target_id` text NOT NULL,
	`period_type` text NOT NULL,
	`period_key` text NOT NULL,
	`platform` text NOT NULL,
	`shop_name` text NOT NULL,
	`category` text NOT NULL,
	`actor` text NOT NULL,
	`old_version` integer NOT NULL CHECK (`old_version` > 0),
	`expected_version` integer NOT NULL CHECK (`expected_version` > 0),
	`reason` text NOT NULL,
	`deleted_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL
);--> statement-breakpoint
INSERT OR IGNORE INTO `finance_target_scoped_versions` (`target_id`, `version`, `updated_at`)
SELECT scoped.`id`, COALESCE(legacy_version.`version`, 1), CURRENT_TIMESTAMP
FROM `finance_targets_scoped` AS scoped
LEFT JOIN `finance_target_versions` AS legacy_version ON legacy_version.`target_id` = scoped.`id`;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `finance_target_scoped_version_insert`
AFTER INSERT ON `finance_targets_scoped`
BEGIN
	INSERT OR IGNORE INTO `finance_target_scoped_versions` (`target_id`, `version`, `updated_at`)
	VALUES (NEW.`id`, 1, CURRENT_TIMESTAMP);
END;--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `finance_target_scoped_version_update`
BEFORE UPDATE ON `finance_targets_scoped`
WHEN EXISTS (SELECT 1 FROM `finance_target_scoped_versions` WHERE `target_id` = OLD.`id`)
BEGIN
	UPDATE `finance_target_scoped_versions`
	SET `version` = `version` + 1, `updated_at` = CURRENT_TIMESTAMP
	WHERE `target_id` = OLD.`id`;
END;--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `finance_targets_scoped_period_idx`
ON `finance_targets_scoped` (`period_type`, `period_key`);--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `finance_targets_scoped_shop_idx`
ON `finance_targets_scoped` (`platform`, `shop_name`, `period_type`, `period_key`);
