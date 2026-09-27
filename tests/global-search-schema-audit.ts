/**
 * Offline retirement audit only: every durable table in db/schema.ts must remain classified here. The unit
 * test intentionally fails when a future migration adds an unreviewed table.
 */
export const GLOBAL_SEARCH_SCHEMA_TABLE_AUDIT = {
  searchable: [
    "workflow_tasks", "workflow_operation_records",
    "inventory_import_batches", "inventory_stock_lines", "inventory_age_metrics",
    "product_shipping_rate_import_batches",
    "erp_reference_import_batches", "erp_product_master", "erp_inventory_age_lines", "erp_combo_items",
    "replenishment_plan_items",
    "finance_import_batches", "finance_lines", "finance_targets_scoped",
    "market_import_batches", "market_ranking_entries", "market_sku_annotations",
    "customer_service_conversations", "customer_service_import_batches",
  ],
  coveredByProjection: [
    "finance_months", "finance_targets",
    "market_price_snapshots",
    "product_shipping_rates",
  ],
  excludedSensitiveOrInternal: [
    "ai_models", "ai_channels", "ai_channel_callback_events",
    "ai_chat_request_receipts", "ai_chat_provider_dispatches",
    "ai_conversations", "ai_conversation_scopes", "ai_conversation_messages", "ai_conversation_deletion_audits", "system_settings", "workflow_task_bootstrap",
    "ai_space_model_profiles", "ai_space_templates", "ai_space_jobs", "ai_space_job_items", "ai_space_assets",
    "ai_space_asset_favorites", "ai_space_asset_cleanup_queue", "ai_space_admin_audits",
    "ai_space_dispatch_receipts", "ai_space_dispatch_results", "ai_space_schema_upgrades",
    "ai_memory_entries", "ai_memory_audit_logs", "ai_memory_commit_guards",
    "ai_analysis_runs", "ai_agent_jobs", "ai_agent_checkpoints", "ai_agent_events",
    "ai_agent_provider_dispatches", "ai_agent_provider_results", "ai_agent_tool_dispatches", "ai_agent_tool_results",
    "ai_workflow_runs", "ai_workflow_node_runs", "ai_workflow_events",
    "workflow_operation_activities", "workflow_task_states", "workflow_task_template_states",
    "workflow_attachment_cleanup_queue", "workflow_task_activity_logs", "workflow_task_attachments", "workflow_task_comments",
    "workflow_task_entity_links", "workflow_task_reminders", "workflow_task_templates",
    "customer_service_conversation_versions", "customer_service_deletion_audits", "finance_write_authority", "finance_target_versions", "finance_target_deletion_audits",
    "finance_target_scoped_versions", "finance_target_scoped_deletion_audits", "finance_target_legacy_migrations",
    "ai_tool_audit_logs",
    "inventory_import_uploads", "inventory_import_upload_chunks", "inventory_import_upload_results",
    "market_annotation_prompt_versions", "market_annotation_prompt_audits", "market_annotation_jobs", "market_annotation_items",
    "market_annotation_commit_receipts", "market_annotation_validation_samples",
    "market_annotation_validation_runs", "market_annotation_validation_results", "market_annotation_local_agents",
    "market_image_cache", "market_sku_gmv_totals", "market_price_band_versions", "market_price_band_items",
    "market_master_mapping_rules", "market_subcategory_taxonomy", "market_download_configs", "market_download_tasks", "market_master_audit_logs",
  ],
} as const;
