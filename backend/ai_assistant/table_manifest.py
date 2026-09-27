"""Closed AI backup inventory; checked against the ORM in readiness and tests."""

# Frozen pre-0032 inventory. Historical migration evidence must stay at these
# exact 65 tables even when later migrations add new AI storage.
AI_TABLES_PRE_TOOL_RECEIPTS = (
    "ai_business_screening_runs",
    "ai_business_screening_pages",
    "ai_business_budget_plans",
    "ai_business_file_runs",
    "ai_business_file_chunks",
    "ai_business_volume_chunks",
    "ai_business_evidence_runs",
    "ai_business_evidence_chunks",
    "ai_business_evidence_sources",
    "ai_library_revisions",
    "ai_execution_guidance",
    "ai_report_runs",
    "ai_report_deliveries",

    "ai_prompt_settings_revisions",
    "ai_dingtalk_settings",
    "ai_dingtalk_sessions",
    "ai_dingtalk_receipts",
    "ai_dingtalk_schedules",
    "ai_dingtalk_schedule_runs",
    "ai_conversation_workspaces",
    "ai_agent_checkpoints",
    "ai_agent_events",
    "ai_agent_jobs",
    "ai_agent_provider_dispatches",
    "ai_agent_provider_results",
    "ai_agent_tool_dispatches",
    "ai_agent_tool_results",
    "ai_analysis_runs",
    "ai_artifact_deliveries",
    "ai_artifacts",
    "ai_channel_callback_events",
    "ai_channels",
    "ai_chat_provider_dispatches",
    "ai_chat_request_receipts",
    "ai_conversation_deletion_audits",
    "ai_conversation_messages",
    "ai_conversation_scopes",
    "ai_conversations",
    "ai_data_revisions",
    "ai_knowledge_entries",
    "ai_memory_audit_logs",
    "ai_memory_commit_guards",
    "ai_memory_entries",
    "ai_migration_runs",
    "ai_models",
    "ai_mutation_audits",
    "ai_space_admin_audits",
    "ai_space_asset_cleanup_queue",
    "ai_space_asset_favorites",
    "ai_space_asset_payloads",
    "ai_space_assets",
    "ai_space_dispatch_receipts",
    "ai_space_dispatch_results",
    "ai_space_job_items",
    "ai_space_jobs",
    "ai_space_model_profiles",
    "ai_space_schema_upgrades",
    "ai_space_templates",
    "ai_system_settings",
    "ai_tool_audit_logs",
    "ai_workflow_events",
    "ai_workflow_node_runs",
    "ai_workflow_runs",
    "ai_write_authority",
    "ai_write_request_receipts",
)

# Frozen 0032–0033 inventory. Later tables must not rewrite prior backup proofs.
AI_TABLES_PRE_V3_REPORT_INTENTS = (*AI_TABLES_PRE_TOOL_RECEIPTS, "ai_business_source_tool_receipts")

# Frozen 0034 inventory for historical v3 intent/backup rehearsals.
AI_TABLES_PRE_V4_LEDGER = (*AI_TABLES_PRE_V3_REPORT_INTENTS, "ai_business_v3_report_intents")

# Frozen 0035 inventory. Historical backup proofs must retain 71 AI tables.
AI_TABLES_PRE_V4_VALIDATION = (*AI_TABLES_PRE_V4_LEDGER,
    "ai_business_v4_runs", "ai_business_v4_sources",
    "ai_business_v4_chunks", "ai_business_v4_tool_receipts")

# Frozen 0037 inventory. Historical admission/backup proofs remain 73 tables.
AI_TABLES_PRE_V4_SEALS = (*AI_TABLES_PRE_V4_VALIDATION,
    "ai_business_v4_validation_attempts", "ai_business_v4_validation_segments")

# Frozen 0038–0040 inventory. A later ticket migration must not rewrite it.
AI_TABLES_PRE_V4_TICKETS = (*AI_TABLES_PRE_V4_SEALS, "ai_business_v4_seals")

# Frozen 0041–0042 inventory; claimed reads add no table.
AI_TABLES_PRE_V4_CONSUMPTIONS = (*AI_TABLES_PRE_V4_TICKETS,
    "ai_business_v4_seal_tickets", "ai_business_v4_seal_claims")

# The consumption row is SQL-owned and has no ordinary reader/writer DML.
AI_TABLES_PRE_MARKET_V2_MATERIALS = (*AI_TABLES_PRE_V4_CONSUMPTIONS,
    "ai_business_v4_seal_consumptions")

AI_TABLES_PRE_V4_REPLAY_PROGRESS = (*AI_TABLES_PRE_MARKET_V2_MATERIALS,
    "ai_business_market_v2_materials")

AI_TABLES_PRE_V4_PERIOD_CANDIDATES = (*AI_TABLES_PRE_V4_REPLAY_PROGRESS,
    "ai_business_v4_sealer_replay_progress")

# 0055 adds a candidate-only period sidecar; the 79-table predecessor remains
# frozen for pre-0055 backup and upgrade rehearsals.
AI_TABLES_PRE_BUDGET_V10_ATTESTATIONS = (*AI_TABLES_PRE_V4_PERIOD_CANDIDATES,
    "ai_business_v4_period_plan_candidates")

# 0057 is a second SQL-owned sidecar; freeze the 80-table predecessor.
AI_TABLES_PRE_MARKET_V2_CONTEXT_PROOFS = (*AI_TABLES_PRE_BUDGET_V10_ATTESTATIONS,
    "ai_business_promotion_budget_v10_attestations")

# 0061 binds the parked/admitted/execution chain to a SQL-derived context.
AI_TABLES_PRE_MARKET_V2_READ_RECEIPTS = (*AI_TABLES_PRE_MARKET_V2_CONTEXT_PROOFS,
    "ai_business_market_v2_context_proofs")

# 0062 only stages a closed, SQL-owned genuine same-job read receipt.
AI_TABLES_PRE_MARKET_V2_EXECUTION_PLANS = (*AI_TABLES_PRE_MARKET_V2_READ_RECEIPTS,
    "ai_business_market_v2_read_receipts")

# 0063 records a SQL-owned, paused execution proposal; no new workflow.
AI_TABLES_PRE_MARKET_V2_COST_LEDGER_CANDIDATES = (*AI_TABLES_PRE_MARKET_V2_EXECUTION_PLANS,
    "ai_business_market_v2_execution_plans")

# 0065 freezes a pending CNY cost requirement; reserved funds remain zero.
AI_TABLES_PRE_BUDGET_V11_ATTESTATIONS = (
    *AI_TABLES_PRE_MARKET_V2_COST_LEDGER_CANDIDATES,
    "ai_business_market_v2_cost_ledger_candidates")

# 0067 adds one SQL-owned proof without changing frozen historical inventories.
AI_TABLES_PRE_MARKET_V2_PAID_REHEARSAL = (*AI_TABLES_PRE_BUDGET_V11_ATTESTATIONS,
    "ai_business_promotion_budget_v11_attestations")

# 0069 adds only isolated, SQL-owned paid-round rehearsal rows.
AI_TABLES = (*AI_TABLES_PRE_MARKET_V2_PAID_REHEARSAL,
    "ai_business_market_v2_paid_authorities",
    "ai_business_market_v2_round_reservations",
    "ai_business_market_v2_round_events")

# 0070/0071 append-only protected SQL objects are not ORM MODELS. Keep the
# formal AI_TABLES inventory exactly equal to database_contract.MODELS and
# expose a separate full physical inventory only for upgrade/restore proofs.
AI_SQL_ONLY_0070 = (
    "protected_business_budget_v11_proof_tickets",
    "protected_business_budget_v11_proof_ticket_claims")
AI_SQL_ONLY_0071 = (
    "protected_business_v4_report_link_intents", "protected_business_v4_report_source_links")
AI_FULL_TABLES_PRE_V4_REPORT_LINKS = (*AI_TABLES, *AI_SQL_ONLY_0070)
AI_FULL_TABLES_AFTER_V4_REPORT_LINKS = (
    *AI_FULL_TABLES_PRE_V4_REPORT_LINKS, *AI_SQL_ONLY_0071)

# 0072 is a separate, default-empty SQL-owned authority proposal ledger.
AI_SQL_ONLY_0072 = (
    "protected_business_market_v2_rate_proposals",
    "protected_business_market_v2_cap_proposals",
    "protected_business_market_v2_authority_revocations")
AI_FULL_TABLES_AFTER_MARKET_AUTHORITY_0072 = (
    *AI_FULL_TABLES_AFTER_V4_REPORT_LINKS, *AI_SQL_ONLY_0072)
