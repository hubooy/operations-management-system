# 系统数据集覆盖清单

当前源码：219 个记录数据集，加上 24 个分析数据集，共 243 个。记录清单覆盖 12 个 Django 业务 app 的全部 221 个模型；重复模型与退役模型在文末解释。此清单不代表生产已发布。

所有记录数据集仅允许无范围限制的管理员；私有 AI 内容另按本人过滤。每个字段的类型、单位、排除原因、所有者关系和唯一键详见 `backend/system_datasets/manifest.json` 或对应详情 API。未列入清单的新表/新字段不会自动暴露。查询方式见 [API 文档](SYSTEM_DATASETS_API.md)。

| 领域 | 记录数据集数 | 开放字段数 | 排除字段数 |
| --- | ---: | ---: | ---: |
| access_control | 7 | 63 | 3 |
| ai_assistant | 45 | 498 | 39 |
| bi | 1 | 9 | 1 |
| customer_service | 12 | 154 | 14 |
| erp_reference | 11 | 133 | 11 |
| finance | 12 | 136 | 8 |
| inventory | 18 | 229 | 16 |
| market | 40 | 542 | 29 |
| netshop | 20 | 257 | 11 |
| products | 13 | 134 | 13 |
| sales | 16 | 218 | 23 |
| workflow | 24 | 271 | 14 |

## access_control

| 数据集 ID | Django 模型 | 开放列 | 排除列 | 私有内容 |
| --- | --- | ---: | ---: | --- |
| `rows_access_control_roles` | `AccessRole` | 7 | 0 | 管理员 |
| `rows_access_control_users` | `AppUser` | 9 | 0 | 管理员 |
| `rows_access_control_permission_audits` | `PermissionAuditEvent` | 15 | 0 | 管理员 |
| `rows_access_control_data_revisions` | `AccessControlDataRevision` | 4 | 0 | 管理员 |
| `rows_access_control_write_authority` | `AccessControlWriteAuthority` | 7 | 0 | 管理员 |
| `rows_access_control_write_request_receipts` | `AccessControlWriteRequestReceipt` | 10 | 1 | 管理员 |
| `rows_access_control_migration_runs` | `AccessControlMigrationRun` | 11 | 2 | 管理员 |

## ai_assistant

| 数据集 ID | Django 模型 | 开放列 | 排除列 | 私有内容 |
| --- | --- | ---: | ---: | --- |
| `rows_ai_data_revisions` | `AiDataRevision` | 4 | 0 | 管理员 |
| `rows_ai_write_authority` | `AiWriteAuthority` | 6 | 0 | 管理员 |
| `rows_ai_write_request_receipts` | `AiWriteReceipt` | 11 | 1 | 仅本人 |
| `rows_ai_mutation_audits` | `AiMutationAudit` | 9 | 0 | 仅本人 |
| `rows_ai_migration_runs` | `AiMigrationRun` | 11 | 2 | 管理员 |
| `rows_ai_agent_checkpoints` | `AiAgentCheckpoints` | 7 | 0 | 仅本人 |
| `rows_ai_agent_events` | `AiAgentEvents` | 10 | 0 | 仅本人 |
| `rows_ai_agent_jobs` | `AiAgentJobs` | 33 | 4 | 仅本人 |
| `rows_ai_agent_provider_dispatches` | `AiAgentProviderDispatches` | 15 | 1 | 仅本人 |
| `rows_ai_agent_provider_results` | `AiAgentProviderResults` | 6 | 0 | 仅本人 |
| `rows_ai_agent_tool_dispatches` | `AiAgentToolDispatches` | 15 | 1 | 仅本人 |
| `rows_ai_agent_tool_results` | `AiAgentToolResults` | 4 | 0 | 仅本人 |
| `rows_ai_analysis_runs` | `AiAnalysisRuns` | 15 | 0 | 仅本人 |
| `rows_ai_artifact_deliveries` | `AiArtifactDeliveries` | 11 | 0 | 仅本人 |
| `rows_ai_artifacts` | `AiArtifacts` | 15 | 0 | 仅本人 |
| `rows_ai_channel_callback_events` | `AiChannelCallbackEvents` | 5 | 0 | 管理员 |
| `rows_ai_channels` | `AiChannels` | 9 | 7 | 管理员 |
| `rows_ai_chat_provider_dispatches` | `AiChatProviderDispatches` | 7 | 0 | 仅本人 |
| `rows_ai_chat_request_receipts` | `AiChatRequestReceipts` | 16 | 0 | 仅本人 |
| `rows_ai_conversation_deletion_audits` | `AiConversationDeletionAudits` | 9 | 0 | 仅本人 |
| `rows_ai_conversation_messages` | `AiConversationMessages` | 7 | 0 | 仅本人 |
| `rows_ai_conversation_scopes` | `AiConversationScopes` | 3 | 0 | 仅本人 |
| `rows_ai_conversations` | `AiConversations` | 6 | 0 | 仅本人 |
| `rows_ai_knowledge_entries` | `AiKnowledgeEntries` | 12 | 0 | 管理员 |
| `rows_ai_memory_audit_logs` | `AiMemoryAuditLogs` | 14 | 1 | 仅本人 |
| `rows_ai_memory_commit_guards` | `AiMemoryCommitGuards` | 2 | 0 | 管理员 |
| `rows_ai_memory_entries` | `AiMemoryEntries` | 19 | 0 | 仅本人 |
| `rows_ai_models` | `AiModels` | 17 | 4 | 管理员 |
| `rows_ai_space_admin_audits` | `AiSpaceAdminAudits` | 9 | 0 | 仅本人 |
| `rows_ai_space_asset_cleanup_queue` | `AiSpaceAssetCleanupQueue` | 3 | 2 | 管理员 |
| `rows_ai_space_asset_favorites` | `AiSpaceAssetFavorites` | 3 | 0 | 仅本人 |
| `rows_ai_space_asset_payloads` | `AiSpaceAssetPayload` | 1 | 1 | 仅本人 |
| `rows_ai_space_assets` | `AiSpaceAssets` | 12 | 1 | 仅本人 |
| `rows_ai_space_dispatch_receipts` | `AiSpaceDispatchReceipts` | 12 | 0 | 仅本人 |
| `rows_ai_space_dispatch_results` | `AiSpaceDispatchResults` | 8 | 0 | 仅本人 |
| `rows_ai_space_job_items` | `AiSpaceJobItems` | 16 | 3 | 仅本人 |
| `rows_ai_space_jobs` | `AiSpaceJobs` | 31 | 1 | 仅本人 |
| `rows_ai_space_model_profiles` | `AiSpaceModelProfiles` | 11 | 3 | 管理员 |
| `rows_ai_space_schema_upgrades` | `AiSpaceSchemaUpgrades` | 2 | 0 | 管理员 |
| `rows_ai_space_templates` | `AiSpaceTemplates` | 12 | 0 | 管理员 |
| `rows_ai_system_settings` | `AiSystemSettings` | 3 | 1 | 管理员 |
| `rows_ai_tool_audit_logs` | `AiToolAuditLogs` | 15 | 0 | 仅本人 |
| `rows_ai_workflow_events` | `AiWorkflowEvents` | 11 | 0 | 仅本人 |
| `rows_ai_workflow_node_runs` | `AiWorkflowNodeRuns` | 19 | 2 | 仅本人 |
| `rows_ai_workflow_runs` | `AiWorkflowRuns` | 32 | 4 | 仅本人 |

## bi

| 数据集 ID | Django 模型 | 开放列 | 排除列 | 私有内容 |
| --- | --- | ---: | ---: | --- |
| `rows_bi_migration_runs` | `BiMigrationRun` | 9 | 1 | 管理员 |

## customer_service

| 数据集 ID | Django 模型 | 开放列 | 排除列 | 私有内容 |
| --- | --- | ---: | ---: | --- |
| `rows_customer_service_import_batches` | `CustomerServiceImportBatch` | 20 | 1 | 管理员 |
| `rows_customer_service_conversations` | `CustomerServiceConversation` | 36 | 4 | 管理员 |
| `rows_customer_service_deletion_audits` | `CustomerServiceDeletionAudit` | 9 | 0 | 管理员 |
| `rows_customer_service_import_scope_heads` | `CustomerServiceImportScopeHead` | 6 | 2 | 管理员 |
| `rows_customer_service_import_fingerprints` | `CustomerServiceImportFingerprint` | 12 | 1 | 管理员 |
| `rows_customer_service_import_attempts` | `CustomerServiceImportAttempt` | 18 | 0 | 管理员 |
| `rows_customer_service_data_revisions` | `CustomerServiceDataRevision` | 4 | 0 | 管理员 |
| `rows_customer_service_write_authority` | `CustomerServiceWriteAuthority` | 7 | 0 | 管理员 |
| `rows_customer_service_write_request_receipts` | `CustomerServiceWriteRequestReceipt` | 11 | 2 | 管理员 |
| `rows_customer_service_migration_runs` | `CustomerServiceMigrationRun` | 10 | 1 | 管理员 |
| `rows_customer_service_raw_upload_sessions` | `CustomerServiceRawUploadSession` | 15 | 2 | 管理员 |
| `rows_customer_service_raw_upload_chunks` | `CustomerServiceRawUploadChunk` | 6 | 1 | 管理员 |

## erp_reference

| 数据集 ID | Django 模型 | 开放列 | 排除列 | 私有内容 |
| --- | --- | ---: | ---: | --- |
| `rows_erp_product_master` | `ErpProductMaster` | 13 | 0 | 管理员 |
| `rows_erp_combo_items` | `ErpComboItem` | 11 | 0 | 管理员 |
| `rows_erp_reference_import_batches_pg` | `ErpReferenceImportBatch` | 22 | 1 | 管理员 |
| `rows_erp_reference_import_scope_heads` | `ErpReferenceImportScopeHead` | 8 | 2 | 管理员 |
| `rows_erp_reference_import_fingerprints` | `ErpReferenceImportFingerprint` | 12 | 1 | 管理员 |
| `rows_erp_reference_import_attempts` | `ErpReferenceImportAttempt` | 18 | 0 | 管理员 |
| `rows_erp_reference_write_authority` | `ErpReferenceWriteAuthority` | 7 | 0 | 管理员 |
| `rows_erp_reference_write_request_receipts` | `ErpReferenceWriteRequestReceipt` | 11 | 2 | 管理员 |
| `rows_erp_reference_migration_runs` | `ErpReferenceMigrationRun` | 10 | 2 | 管理员 |
| `rows_erp_reference_raw_upload_sessions` | `ErpReferenceRawUploadSession` | 15 | 2 | 管理员 |
| `rows_erp_reference_raw_upload_chunks` | `ErpReferenceRawUploadChunk` | 6 | 1 | 管理员 |

## finance

| 数据集 ID | Django 模型 | 开放列 | 排除列 | 私有内容 |
| --- | --- | ---: | ---: | --- |
| `rows_finance_import_batches` | `FinanceImportBatch` | 22 | 2 | 管理员 |
| `rows_finance_months` | `FinanceMonth` | 10 | 0 | 管理员 |
| `rows_finance_lines` | `FinanceLine` | 18 | 0 | 管理员 |
| `rows_finance_targets_scoped` | `FinanceTarget` | 17 | 0 | 管理员 |
| `rows_finance_target_deletion_audits` | `FinanceTargetDeletionAudit` | 12 | 0 | 管理员 |
| `rows_finance_import_scope_heads` | `FinanceImportScopeHead` | 8 | 2 | 管理员 |
| `rows_finance_import_attempts` | `FinanceImportAttempt` | 11 | 0 | 管理员 |
| `rows_finance_import_fingerprints` | `FinanceImportFingerprint` | 7 | 1 | 管理员 |
| `rows_finance_data_revisions` | `FinanceDataRevision` | 4 | 0 | 管理员 |
| `rows_finance_write_authority` | `FinanceWriteAuthority` | 7 | 0 | 管理员 |
| `rows_finance_write_request_receipts` | `FinanceWriteRequestReceipt` | 10 | 1 | 管理员 |
| `rows_finance_migration_runs` | `FinanceMigrationRun` | 10 | 2 | 管理员 |

## inventory

| 数据集 ID | Django 模型 | 开放列 | 排除列 | 私有内容 |
| --- | --- | ---: | ---: | --- |
| `rows_inventory_guangdong_monitor_items` | `GuangdongMonitorItem` | 5 | 0 | 管理员 |
| `rows_inventory_guangdong_supplier_cycles` | `GuangdongSupplierCycle` | 5 | 0 | 管理员 |
| `rows_inventory_guangdong_monitor_audits` | `GuangdongMonitorAudit` | 8 | 1 | 管理员 |
| `rows_inventory_import_batches` | `InventoryImportBatch` | 23 | 1 | 管理员 |
| `rows_inventory_stock_lines` | `InventoryStockLine` | 25 | 0 | 管理员 |
| `rows_inventory_age_lines` | `InventoryAgeLine` | 18 | 0 | 管理员 |
| `rows_inventory_import_scope_heads` | `InventoryImportScopeHead` | 8 | 2 | 管理员 |
| `rows_inventory_import_attempts` | `InventoryImportAttempt` | 15 | 0 | 管理员 |
| `rows_inventory_import_fingerprints` | `InventoryImportFingerprint` | 11 | 1 | 管理员 |
| `rows_inventory_data_revisions` | `InventoryDataRevision` | 4 | 0 | 管理员 |
| `rows_inventory_write_authority` | `InventoryWriteAuthority` | 7 | 0 | 管理员 |
| `rows_inventory_write_request_receipts` | `InventoryWriteRequestReceipt` | 11 | 2 | 管理员 |
| `rows_inventory_raw_upload_sessions` | `InventoryRawUploadSession` | 17 | 2 | 管理员 |
| `rows_inventory_raw_upload_chunks` | `InventoryRawUploadChunk` | 6 | 2 | 管理员 |
| `rows_replenishment_plan_items` | `ReplenishmentPlanItem` | 34 | 1 | 管理员 |
| `rows_inventory_replenishment_group_deliveries` | `ReplenishmentGroupDelivery` | 12 | 2 | 管理员 |
| `rows_inventory_operating_settings` | `InventoryOperatingSettings` | 12 | 1 | 管理员 |
| `rows_inventory_migration_runs` | `InventoryMigrationRun` | 10 | 2 | 管理员 |

## market

| 数据集 ID | Django 模型 | 开放列 | 排除列 | 私有内容 |
| --- | --- | ---: | ---: | --- |
| `rows_market_import_batches` | `MarketImportBatch` | 20 | 1 | 管理员 |
| `rows_market_ranking_entries` | `MarketRankingEntry` | 52 | 0 | 管理员 |
| `rows_market_master_identities` | `MarketMasterIdentity` | 7 | 0 | 管理员 |
| `rows_market_sku_gmv_totals` | `MarketSkuGmvTotal` | 3 | 0 | 管理员 |
| `rows_market_price_snapshots` | `MarketPriceSnapshot` | 26 | 0 | 管理员 |
| `rows_market_data_revisions` | `MarketDataRevision` | 4 | 0 | 管理员 |
| `rows_market_import_scope_heads` | `MarketImportScopeHead` | 7 | 2 | 管理员 |
| `rows_market_import_attempts` | `MarketImportAttempt` | 11 | 0 | 管理员 |
| `rows_market_import_fingerprints` | `MarketImportFingerprint` | 11 | 1 | 管理员 |
| `rows_market_write_authority` | `MarketWriteAuthority` | 7 | 0 | 管理员 |
| `rows_market_write_request_receipts` | `MarketWriteRequestReceipt` | 10 | 1 | 管理员 |
| `rows_market_migration_runs` | `MarketMigrationRun` | 10 | 2 | 管理员 |
| `rows_market_image_cache` | `MarketImageCache` | 11 | 2 | 管理员 |
| `rows_market_image_cache_jobs` | `MarketImageCacheJob` | 23 | 2 | 管理员 |
| `rows_market_image_cache_job_items` | `MarketImageCacheJobItem` | 10 | 1 | 管理员 |
| `rows_market_image_cache_claims` | `MarketImageCacheClaim` | 7 | 2 | 管理员 |
| `rows_market_price_band_versions` | `MarketPriceBandVersion` | 11 | 0 | 管理员 |
| `rows_market_price_band_items` | `MarketPriceBandItem` | 6 | 0 | 管理员 |
| `rows_market_master_mapping_rules` | `MarketMasterMappingRule` | 11 | 0 | 管理员 |
| `rows_market_subcategory_taxonomy` | `MarketSubcategoryTaxonomy` | 9 | 0 | 管理员 |
| `rows_market_brand_suggestions` | `MarketBrandSuggestion` | 14 | 1 | 管理员 |
| `rows_market_brand_recognition_jobs` | `MarketBrandRecognitionJob` | 16 | 2 | 管理员 |
| `rows_market_brand_seeds` | `MarketBrandSeed` | 11 | 0 | 管理员 |
| `rows_market_download_configs` | `MarketDownloadConfig` | 10 | 0 | 管理员 |
| `rows_market_download_tasks` | `MarketDownloadTask` | 23 | 2 | 管理员 |
| `rows_market_master_audit_logs` | `MarketMasterAuditLog` | 9 | 0 | 管理员 |
| `rows_market_annotation_prompt_versions` | `MarketAnnotationPromptVersion` | 14 | 0 | 管理员 |
| `rows_market_annotation_jobs` | `MarketAnnotationJob` | 22 | 1 | 管理员 |
| `rows_market_annotation_items` | `MarketAnnotationItem` | 42 | 2 | 管理员 |
| `rows_market_sku_annotations` | `MarketSkuAnnotation` | 18 | 0 | 管理员 |
| `rows_market_annotation_commit_receipts` | `MarketAnnotationCommitReceipt` | 10 | 0 | 管理员 |
| `rows_market_annotation_validation_samples` | `MarketAnnotationValidationSample` | 14 | 0 | 管理员 |
| `rows_market_annotation_validation_runs` | `MarketAnnotationValidationRun` | 15 | 0 | 管理员 |
| `rows_market_annotation_validation_results` | `MarketAnnotationValidationResult` | 13 | 3 | 管理员 |
| `rows_market_annotation_prompt_audits` | `MarketAnnotationPromptAudit` | 7 | 0 | 管理员 |
| `rows_market_annotation_local_agents` | `MarketAnnotationLocalAgent` | 8 | 1 | 管理员 |
| `rows_market_annotation_concurrency_settings` | `MarketAnnotationConcurrencySetting` | 6 | 0 | 管理员 |
| `rows_market_annotation_cloud_runs` | `MarketAnnotationCloudRun` | 10 | 2 | 管理员 |
| `rows_market_netshop_projection` | `MarketNetshopProjection` | 15 | 0 | 管理员 |
| `rows_market_netshop_projection_control` | `MarketNetshopProjectionControl` | 9 | 1 | 管理员 |

## netshop

| 数据集 ID | Django 模型 | 开放列 | 排除列 | 私有内容 |
| --- | --- | ---: | ---: | --- |
| `rows_netshop_import_batches` | `NetshopImportBatch` | 27 | 1 | 管理员 |
| `rows_netshop_rows` | `NetshopRow` | 62 | 1 | 管理员 |
| `rows_netshop_promotion_product_daily` | `NetshopPromotionProductDaily` | 20 | 0 | 管理员 |
| `rows_netshop_promotion_shop_daily` | `NetshopPromotionShopDaily` | 18 | 0 | 管理员 |
| `rows_netshop_data_revisions` | `NetshopDataRevision` | 4 | 0 | 管理员 |
| `rows_netshop_product_daily_revisions` | `NetshopProductDailyRevision` | 3 | 0 | 管理员 |
| `rows_netshop_product_daily_scope_revisions` | `NetshopProductDailyScopeRevision` | 5 | 0 | 管理员 |
| `rows_netshop_promotion_scope_revisions` | `NetshopPromotionScopeRevision` | 5 | 0 | 管理员 |
| `rows_netshop_promotion_aggregate_state` | `NetshopPromotionAggregateState` | 12 | 0 | 管理员 |
| `rows_netshop_promotion_aggregate_manifest` | `NetshopPromotionAggregateManifest` | 11 | 0 | 管理员 |
| `rows_netshop_promotion_aggregate_control` | `NetshopPromotionAggregateControl` | 10 | 1 | 管理员 |
| `rows_netshop_import_scope_heads` | `NetshopImportScopeHead` | 7 | 2 | 管理员 |
| `rows_netshop_import_attempts` | `NetshopImportAttempt` | 11 | 0 | 管理员 |
| `rows_netshop_import_fingerprints` | `NetshopImportFingerprint` | 11 | 1 | 管理员 |
| `rows_netshop_write_authority` | `NetshopWriteAuthority` | 7 | 0 | 管理员 |
| `rows_netshop_write_request_receipts` | `NetshopWriteRequestReceipt` | 10 | 1 | 管理员 |
| `rows_netshop_asset_uploads` | `NetshopAssetUpload` | 16 | 0 | 管理员 |
| `rows_netshop_asset_upload_chunks` | `NetshopAssetUploadChunk` | 6 | 1 | 管理员 |
| `rows_netshop_asset_upload_results` | `NetshopAssetUploadResult` | 2 | 1 | 管理员 |
| `rows_netshop_migration_runs` | `NetshopMigrationRun` | 10 | 2 | 管理员 |

## products

| 数据集 ID | Django 模型 | 开放列 | 排除列 | 私有内容 |
| --- | --- | ---: | ---: | --- |
| `rows_product_shipping_rate_import_batches` | `ProductShippingRateImportBatch` | 22 | 1 | 管理员 |
| `rows_product_shipping_rates` | `ProductShippingRate` | 6 | 0 | 管理员 |
| `rows_product_import_scope_heads` | `ProductImportScopeHead` | 7 | 2 | 管理员 |
| `rows_product_import_attempts` | `ProductImportAttempt` | 13 | 0 | 管理员 |
| `rows_product_import_fingerprints` | `ProductImportFingerprint` | 11 | 1 | 管理员 |
| `rows_product_data_revisions` | `ProductDataRevision` | 4 | 0 | 管理员 |
| `rows_product_write_authority` | `ProductWriteAuthority` | 7 | 0 | 管理员 |
| `rows_product_write_request_receipts` | `ProductWriteRequestReceipt` | 11 | 2 | 管理员 |
| `rows_product_inventory_projection` | `ProductInventoryProjection` | 9 | 0 | 管理员 |
| `rows_product_inventory_projection_control` | `ProductInventoryProjectionControl` | 13 | 1 | 管理员 |
| `rows_product_raw_upload_sessions` | `ProductRawUploadSession` | 15 | 2 | 管理员 |
| `rows_product_raw_upload_chunks` | `ProductRawUploadChunk` | 6 | 2 | 管理员 |
| `rows_product_migration_runs` | `ProductMigrationRun` | 10 | 2 | 管理员 |

## sales

| 数据集 ID | Django 模型 | 开放列 | 排除列 | 私有内容 |
| --- | --- | ---: | ---: | --- |
| `rows_sales_import_batches` | `SalesImportBatch` | 21 | 1 | 管理员 |
| `rows_sales_order_lines` | `SalesOrderLine` | 47 | 0 | 管理员 |
| `rows_sales_data_revisions` | `SalesDataRevision` | 4 | 0 | 管理员 |
| `rows_sales_migration_runs` | `SalesMigrationRun` | 16 | 4 | 管理员 |
| `rows_sales_migration_locks` | `SalesMigrationLock` | 3 | 0 | 管理员 |
| `rows_sales_legacy_upload_audits` | `SalesLegacyUploadAudit` | 18 | 0 | 管理员 |
| `rows_sales_import_scope_heads` | `SalesImportScopeHead` | 6 | 2 | 管理员 |
| `rows_sales_write_authority` | `SalesWriteAuthority` | 6 | 0 | 管理员 |
| `rows_sales_write_request_receipts` | `SalesWriteRequestReceipt` | 10 | 2 | 管理员 |
| `rows_sales_import_attempts` | `SalesImportAttempt` | 18 | 1 | 管理员 |
| `rows_sales_import_fingerprints` | `SalesImportFingerprint` | 12 | 0 | 管理员 |
| `rows_sales_raw_upload_sessions` | `SalesRawUploadSession` | 17 | 2 | 管理员 |
| `rows_sales_raw_upload_chunks` | `SalesRawUploadChunk` | 6 | 2 | 管理员 |
| `rows_sales_staged_import_sessions` | `SalesStagedImportSession` | 19 | 6 | 管理员 |
| `rows_sales_staged_import_chunks` | `SalesStagedImportChunk` | 6 | 1 | 管理员 |
| `rows_sales_cutover_attestations` | `SalesCutoverAttestation` | 9 | 2 | 管理员 |

## workflow

| 数据集 ID | Django 模型 | 开放列 | 排除列 | 私有内容 |
| --- | --- | ---: | ---: | --- |
| `rows_workflow_data_revisions` | `WorkflowDataRevision` | 4 | 0 | 管理员 |
| `rows_workflow_write_authority` | `WorkflowWriteAuthority` | 7 | 0 | 管理员 |
| `rows_workflow_migration_runs` | `WorkflowMigrationRun` | 11 | 2 | 管理员 |
| `rows_workflow_write_request_receipts` | `WorkflowWriteRequestReceipt` | 11 | 2 | 管理员 |
| `rows_workflow_operations_write_authority` | `WorkflowOperationsWriteAuthority` | 7 | 0 | 管理员 |
| `rows_workflow_operations_migration_runs` | `WorkflowOperationsMigrationRun` | 10 | 2 | 管理员 |
| `rows_workflow_tasks` | `WorkflowTask` | 17 | 1 | 管理员 |
| `rows_workflow_task_comments` | `WorkflowTaskComment` | 5 | 0 | 管理员 |
| `rows_workflow_task_activity_logs` | `WorkflowTaskActivityLog` | 7 | 0 | 管理员 |
| `rows_workflow_task_reminders` | `WorkflowTaskReminder` | 8 | 0 | 管理员 |
| `rows_workflow_task_templates` | `WorkflowTaskTemplate` | 17 | 1 | 管理员 |
| `rows_workflow_task_entity_links` | `WorkflowTaskEntityLink` | 8 | 0 | 管理员 |
| `rows_workflow_task_attachments` | `WorkflowTaskAttachment` | 8 | 1 | 管理员 |
| `rows_workflow_attachment_cleanup_queue` | `WorkflowAttachmentCleanup` | 3 | 2 | 管理员 |
| `rows_workflow_operation_records` | `WorkflowOperationRecord` | 22 | 1 | 管理员 |
| `rows_workflow_operation_activities` | `WorkflowOperationActivity` | 9 | 0 | 管理员 |
| `rows_workflow_new_product_projects` | `NewProductProject` | 28 | 0 | 管理员 |
| `rows_workflow_new_product_targets` | `NewProductTarget` | 10 | 0 | 管理员 |
| `rows_workflow_new_product_stages` | `NewProductStage` | 15 | 0 | 管理员 |
| `rows_workflow_new_product_activities` | `NewProductActivity` | 12 | 0 | 管理员 |
| `rows_workflow_new_product_lines` | `NewProductLine` | 18 | 1 | 管理员 |
| `rows_workflow_new_product_line_codes` | `NewProductLineCode` | 10 | 0 | 管理员 |
| `rows_workflow_new_product_weekly_report_config` | `NewProductWeeklyReportConfig` | 9 | 0 | 管理员 |
| `rows_workflow_new_product_weekly_deliveries` | `NewProductWeeklyDelivery` | 15 | 1 | 管理员 |

## 未重复开放的模型

| 模型 | 处理 | 原因 |
| --- | --- | --- |
| `sales.ErpProductMaster` | 归并到 `erp_reference.ErpProductMaster` | ERP 权威模型的历史消费者映射，不重复暴露 |
| `erp_reference.ErpReferenceSyncCheckpoint` | 排除 | 已退役 ERP bridge 的非生产模型；仅历史迁移审计，不复活旧读取路径 |
