"""Closed runtime grants; only AI records can be written by the AI writer."""

from . import models as m
from .control_models import (
    AiDataRevision,
    AiWriteAuthority,
    AiWriteReceipt,
    AiMutationAudit,
    AiMigrationRun,
)

CONTROL_MODELS = (
    AiDataRevision,
    AiWriteAuthority,
    AiWriteReceipt,
    AiMutationAudit,
    AiMigrationRun,
)
MODELS = {
    "ai_business_screening_runs": m.AiBusinessScreeningRun,
    "ai_business_screening_pages": m.AiBusinessScreeningPage,
    "ai_business_budget_plans": m.AiBusinessBudgetPlan,
    "ai_business_file_runs": m.AiBusinessFileRun,
    "ai_business_file_chunks": m.AiBusinessFileChunk,
    "ai_business_volume_chunks": m.AiBusinessVolumeChunk,
    "ai_business_evidence_runs": m.AiBusinessEvidenceRun,
    "ai_business_evidence_chunks": m.AiBusinessEvidenceChunk,
    "ai_business_evidence_sources": m.AiBusinessEvidenceSource,
    "ai_business_source_tool_receipts": m.AiBusinessSourceToolReceipt,
    "ai_business_v3_report_intents": m.AiBusinessV3ReportIntent,
    "ai_business_v4_runs": m.AiBusinessV4Run,
    "ai_business_v4_sources": m.AiBusinessV4Source,
    "ai_business_v4_chunks": m.AiBusinessV4Chunk,
    "ai_business_v4_tool_receipts": m.AiBusinessV4ToolReceipt,
    "ai_business_v4_validation_attempts": m.AiBusinessV4ValidationAttempt,
    "ai_business_v4_validation_segments": m.AiBusinessV4ValidationSegment,
    "ai_business_v4_seals": m.AiBusinessV4Seal,
    "ai_business_v4_seal_tickets": m.AiBusinessV4SealTicket,
    "ai_business_v4_seal_claims": m.AiBusinessV4SealClaim,
    "ai_business_v4_seal_consumptions": m.AiBusinessV4SealConsumption,
    "ai_business_v4_sealer_replay_progress": m.AiBusinessV4SealerReplayProgress,
    "ai_business_v4_period_plan_candidates": m.AiBusinessV4PeriodPlanCandidate,
    "ai_business_market_v2_materials": m.AiBusinessMarketV2Material,
    "ai_business_market_v2_context_proofs": m.AiBusinessMarketV2ContextProof,
    "ai_business_market_v2_read_receipts": m.AiBusinessMarketV2ReadReceipt,
    "ai_business_market_v2_execution_plans": m.AiBusinessMarketV2ExecutionPlan,
    "ai_business_market_v2_cost_ledger_candidates": m.AiBusinessMarketV2CostLedgerCandidate,
    "ai_business_market_v2_paid_authorities": m.AiBusinessMarketV2PaidAuthority,
    "ai_business_market_v2_round_reservations": m.AiBusinessMarketV2RoundReservation,
    "ai_business_market_v2_round_events": m.AiBusinessMarketV2RoundEvent,
    "ai_business_promotion_budget_v10_attestations": m.AiBusinessPromotionBudgetV10Attestation,
    "ai_business_promotion_budget_v11_attestations": m.AiBusinessPromotionBudgetV11Attestation,
    "ai_library_revisions": m.AiLibraryRevision,
    "ai_execution_guidance": m.AiExecutionGuidance,
    "ai_report_runs": m.AiReportRun,
    "ai_report_deliveries": m.AiReportDelivery,

    "ai_prompt_settings_revisions": m.AiPromptSettingsRevision,
    "ai_dingtalk_settings": m.AiDingTalkSettings,
    "ai_dingtalk_sessions": m.AiDingTalkSession,
    "ai_dingtalk_receipts": m.AiDingTalkReceipt,
    "ai_dingtalk_schedules": m.AiDingTalkSchedule,
    "ai_dingtalk_schedule_runs": m.AiDingTalkScheduleRun,
    **m.HISTORICAL_MODELS,
    "ai_space_asset_payloads": m.AiSpaceAssetPayload,
    "ai_conversation_workspaces": m.AiConversationWorkspace,
    **{model._meta.db_table: model for model in CONTROL_MODELS},
}
READ_TABLES = {
    "ai_business_screening_runs",
    "ai_business_screening_pages",
    "ai_business_budget_plans",
    "ai_business_file_runs",
    "ai_business_file_chunks",
    "ai_business_volume_chunks",
    "ai_business_evidence_runs",
    "ai_business_evidence_chunks",
    "ai_business_evidence_sources",
    "ai_business_source_tool_receipts",
    "ai_business_v3_report_intents",
    "ai_library_revisions",
    "ai_execution_guidance",
    "ai_report_runs",
    "ai_report_deliveries",

    "ai_prompt_settings_revisions",
    "ai_dingtalk_settings",
    "ai_dingtalk_sessions",
    "ai_dingtalk_schedules",
    "ai_dingtalk_schedule_runs",
    "ai_conversation_workspaces",
    "ai_channels",
    "ai_conversations",
    "ai_conversation_messages",
    "ai_conversation_scopes",
    "ai_artifacts",
    "ai_memory_entries",
    "ai_knowledge_entries",
    "ai_analysis_runs",
    "ai_agent_jobs",
    "ai_agent_checkpoints",
    "ai_workflow_runs",
    "ai_workflow_node_runs",
    "ai_space_templates",
    "ai_space_model_profiles",
    "ai_space_jobs",
    "ai_space_job_items",
    "ai_space_assets",
    "ai_space_asset_payloads",
    "ai_space_asset_favorites",
    "ai_data_revisions",
    "ai_write_authority",
    "ai_migration_runs",
    "access_control_users",
}
# The AI reader's model transport view and the system dataset need only these
# non-secret columns.  Provider credentials remain on the writer-only runtime
# path; never restore a table-level ai_models SELECT to fix a reader query.
MODEL_READER_COLUMNS = frozenset({
    "id", "version", "name", "protocol", "model_type", "model_name",
    "base_url", "is_default_text_model", "status", "timeout_ms",
    "reasoning_mode", "temperature_milli", "max_tool_rounds",
    "max_total_tool_calls", "last_tested_at", "created_at", "updated_at",
    "generation_options_json", "max_tokens",
})
MODEL_READER_EXTRA_COLUMNS = ("generation_options_json", "max_tokens")
assert "ai_models" not in READ_TABLES
assert set(MODEL_READER_EXTRA_COLUMNS) <= MODEL_READER_COLUMNS
assert not {"api_key_encrypted", "api_key_suffix", "last_test_result"}.intersection(
    MODEL_READER_COLUMNS)
APPEND_ONLY = {
    "ai_business_screening_runs",
    "ai_business_screening_pages",
    "ai_business_budget_plans",
    "ai_business_file_chunks",
    "ai_business_volume_chunks",
    "ai_business_evidence_chunks",
    "ai_business_source_tool_receipts",
    "ai_business_v3_report_intents",
    "ai_business_v4_chunks",
    "ai_business_v4_tool_receipts",
    "ai_business_v4_validation_attempts",
    "ai_business_v4_validation_segments",
    "ai_library_revisions",
    "ai_execution_guidance",
    "ai_report_runs",

    "ai_prompt_settings_revisions",
    "ai_space_asset_payloads",
    "ai_chat_provider_dispatches",
    "ai_agent_checkpoints",
    "ai_agent_events",
    "ai_agent_provider_results",
    "ai_agent_tool_results",
    "ai_analysis_runs",
    "ai_artifact_deliveries",
    "ai_channel_callback_events",
    "ai_conversation_deletion_audits",
    "ai_memory_audit_logs",
    "ai_space_admin_audits",
    "ai_space_dispatch_receipts",
    "ai_space_dispatch_results",
    "ai_tool_audit_logs",
    "ai_workflow_events",
    "ai_mutation_audits",
}
WRITER_PRIVILEGES = {
    table: ("SELECT", "INSERT", "UPDATE", "DELETE") for table in MODELS
}
for table in APPEND_ONLY:
    WRITER_PRIVILEGES[table] = ("SELECT", "INSERT")
for table in {
    "ai_migration_runs",
    "ai_write_authority",
    "ai_space_schema_upgrades",
    "ai_system_settings",
    "ai_memory_commit_guards",
}:
    WRITER_PRIVILEGES[table] = ("SELECT",)
WRITER_PRIVILEGES["ai_data_revisions"] = ("SELECT", "UPDATE")
for table in {
    "ai_dingtalk_settings",
    "ai_dingtalk_sessions",
    "ai_dingtalk_receipts",
    "ai_dingtalk_schedules",
    "ai_dingtalk_schedule_runs",
    "ai_memory_entries",
    "ai_chat_request_receipts",
    "ai_write_request_receipts",
    "ai_agent_provider_dispatches",
    "ai_agent_tool_dispatches",
}:
    WRITER_PRIVILEGES[table] = ("SELECT", "INSERT", "UPDATE")
WRITER_PRIVILEGES["ai_report_deliveries"] = ("SELECT", "INSERT", "UPDATE")
WRITER_PRIVILEGES["ai_business_evidence_runs"] = ("SELECT", "INSERT", "UPDATE")
WRITER_PRIVILEGES["ai_business_evidence_sources"] = ("SELECT", "INSERT", "UPDATE")
WRITER_PRIVILEGES["ai_business_v4_runs"] = ("SELECT", "INSERT", "UPDATE")
WRITER_PRIVILEGES["ai_business_v4_sources"] = ("SELECT", "INSERT", "UPDATE")
WRITER_PRIVILEGES["ai_business_v4_seals"] = ("SELECT",)
WRITER_PRIVILEGES["access_control_users"] = ("SELECT",)
# 0041's SQL-owned rows remain in MODELS for schema inventory, but are absent
# from READ_TABLES and WRITER_PRIVILEGES. ProvisionRoles must never grant direct
# reader/writer access to nonce hashes or one-time claim state.
CLOSED_SEAL_TICKET_TABLES = (
    "ai_business_v4_seal_tickets", "ai_business_v4_seal_claims",
    "ai_business_v4_seal_consumptions")
CLOSED_SQL_OWNED_TABLES = (*CLOSED_SEAL_TICKET_TABLES,
    "ai_business_market_v2_materials",
    "ai_business_market_v2_context_proofs",
    "ai_business_market_v2_read_receipts",
    "ai_business_market_v2_execution_plans",
    "ai_business_market_v2_cost_ledger_candidates",
    "ai_business_market_v2_paid_authorities",
    "ai_business_market_v2_round_reservations",
    "ai_business_market_v2_round_events",
    "ai_business_v4_sealer_replay_progress",
    "ai_business_v4_period_plan_candidates",
    "ai_business_promotion_budget_v10_attestations",
    "ai_business_promotion_budget_v11_attestations")
for table in CLOSED_SQL_OWNED_TABLES:
    WRITER_PRIVILEGES.pop(table)
assert not set(CLOSED_SQL_OWNED_TABLES).intersection(
    set(READ_TABLES) | set(WRITER_PRIVILEGES))


def provision(connection, reader_password, writer_password):
    from psycopg import sql

    if (
        min(len(reader_password), len(writer_password)) < 32
        or reader_password == writer_password
    ):
        raise ValueError("Independent AI role passwords are required")
    with connection.transaction(), connection.cursor() as cursor:
        for role, password in [
            ("teruisi_ai_reader", reader_password),
            ("teruisi_ai_writer", writer_password),
        ]:
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,))
            if not cursor.fetchone():
                cursor.execute(
                    sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(role))
                )
            cursor.execute(
                sql.SQL(
                    "ALTER ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD {}"
                ).format(sql.Identifier(role), sql.Literal(password))
            )
            cursor.execute(
                "SELECT parent.rolname FROM pg_auth_members membership JOIN pg_roles parent ON parent.oid=membership.roleid JOIN pg_roles member ON member.oid=membership.member WHERE member.rolname=%s",
                (role,),
            )
            for (parent,) in cursor.fetchall():
                cursor.execute(
                    sql.SQL("REVOKE {} FROM {}").format(
                        sql.Identifier(parent), sql.Identifier(role)
                    )
                )
            cursor.execute(
                sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(
                    sql.Identifier(role)
                )
            )
            cursor.execute(
                sql.SQL("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {}").format(
                    sql.Identifier(role)
                )
            )
            cursor.execute(
                sql.SQL("REVOKE ALL ON SCHEMA public FROM {}").format(
                    sql.Identifier(role)
                )
            )
            cursor.execute(
                sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
                    sql.Identifier(role)
                )
            )
            cursor.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(connection.info.dbname), sql.Identifier(role)
                )
            )
            privileges = (
                {table: ("SELECT",) for table in READ_TABLES}
                if role == "teruisi_ai_reader"
                else WRITER_PRIVILEGES
            )
            # Historical isolated upgrade rehearsals provision exact pre-0032
            # schemas with today's code. Only the newly introduced receipt
            # table is conditional; every predecessor grant remains unchanged.
            cursor.execute("SELECT to_regclass('public.ai_business_source_tool_receipts')")
            if cursor.fetchone()[0] is None:
                privileges = {table: allowed for table, allowed in privileges.items()
                              if table != "ai_business_source_tool_receipts"}
            cursor.execute("SELECT to_regclass('public.ai_business_v3_report_intents')")
            if cursor.fetchone()[0] is None:
                privileges = {table: allowed for table, allowed in privileges.items()
                              if table != "ai_business_v3_report_intents"}
            cursor.execute("SELECT to_regclass('public.ai_business_v4_runs')")
            if cursor.fetchone()[0] is None:
                privileges = {table: allowed for table, allowed in privileges.items()
                              if table not in {"ai_business_v4_runs", "ai_business_v4_sources",
                                  "ai_business_v4_chunks", "ai_business_v4_tool_receipts"}}
            cursor.execute("SELECT to_regclass('public.ai_business_v4_validation_attempts')")
            if cursor.fetchone()[0] is None:
                privileges = {table: allowed for table, allowed in privileges.items()
                              if table not in {"ai_business_v4_validation_attempts",
                                  "ai_business_v4_validation_segments"}}
            cursor.execute("SELECT to_regclass('public.ai_business_v4_seals')")
            if cursor.fetchone()[0] is None:
                privileges = {table: allowed for table, allowed in privileges.items()
                              if table != "ai_business_v4_seals"}
            for table, allowed in privileges.items():
                cursor.execute(
                    sql.SQL("GRANT {} ON {} TO {}").format(
                        sql.SQL(",").join(sql.SQL(v) for v in allowed),
                        sql.Identifier(table),
                        sql.Identifier(role),
                    )
                )
            for table in CLOSED_SQL_OWNED_TABLES:
                cursor.execute("SELECT to_regclass(%s)", ("public." + table,))
                if cursor.fetchone()[0] is not None:
                    cursor.execute(sql.SQL("REVOKE ALL ON {} FROM {}").format(
                        sql.Identifier("public", table), sql.Identifier(role)))
            cursor.execute("SELECT to_regprocedure('public.ai_v4_issue_seal_ticket("
                "text,text,text,bigint,bigint,text,text)')")
            if cursor.fetchone()[0] is not None:
                signature = ("public.ai_v4_issue_seal_ticket("
                    "text,text,text,bigint,bigint,text,text)")
                cursor.execute("REVOKE ALL ON FUNCTION " + signature + " FROM PUBLIC")
                if role == "teruisi_ai_writer":
                    cursor.execute("GRANT EXECUTE ON FUNCTION " + signature +
                        " TO teruisi_ai_writer")
                else:
                    cursor.execute("REVOKE EXECUTE ON FUNCTION " + signature +
                        " FROM teruisi_ai_reader")
            cursor.execute("SELECT to_regprocedure('public.ai_v4_verify_seal_consumption("
                "text,text,bigint,text)')")
            if cursor.fetchone()[0] is not None:
                signature = ("public.ai_v4_verify_seal_consumption("
                    "text,text,bigint,text)")
                cursor.execute("REVOKE ALL ON FUNCTION " + signature + " FROM PUBLIC")
                if role == "teruisi_ai_writer":
                    cursor.execute("GRANT EXECUTE ON FUNCTION " + signature +
                        " TO teruisi_ai_writer")
                else:
                    cursor.execute("REVOKE EXECUTE ON FUNCTION " + signature +
                        " FROM teruisi_ai_reader")
            cursor.execute("SELECT to_regprocedure('public.ai_v4_record_period_plan_candidate("
                "text,text,text,bigint,text,text)')")
            if cursor.fetchone()[0] is not None:
                signature = ("public.ai_v4_record_period_plan_candidate("
                    "text,text,text,bigint,text,text)")
                cursor.execute("REVOKE ALL ON FUNCTION " + signature + " FROM PUBLIC")
                if role == "teruisi_ai_writer":
                    cursor.execute("GRANT EXECUTE ON FUNCTION " + signature +
                        " TO teruisi_ai_writer")
                else:
                    cursor.execute("REVOKE EXECUTE ON FUNCTION " + signature +
                        " FROM teruisi_ai_reader")
            cursor.execute("SELECT to_regprocedure('public.ai_v4_lock_source_revisions_for_admission()')")
            if cursor.fetchone()[0] is not None:
                cursor.execute("REVOKE ALL ON FUNCTION "
                    "public.ai_v4_lock_source_revisions_for_admission() FROM PUBLIC")
                cursor.execute("REVOKE ALL ON FUNCTION "
                    "public.ai_v4_lock_source_revisions_for_admission() FROM teruisi_ai_reader")
                if role == "teruisi_ai_writer":
                    cursor.execute("GRANT EXECUTE ON FUNCTION "
                        "public.ai_v4_lock_source_revisions_for_admission() TO teruisi_ai_writer")
            if role == "teruisi_ai_reader":
                cursor.execute("SELECT to_regprocedure('public."
                    "ai_market_v2_cost_candidate_receipt(text,text,bigint)')")
                if cursor.fetchone()[0] is not None:
                    cursor.execute("REVOKE ALL ON FUNCTION public."
                        "ai_market_v2_cost_candidate_receipt(text,text,bigint) "
                        "FROM PUBLIC")
                    cursor.execute("GRANT EXECUTE ON FUNCTION public."
                        "ai_market_v2_cost_candidate_receipt(text,text,bigint) "
                        "TO teruisi_ai_reader")
                cursor.execute("SELECT to_regprocedure('public."
                    "ai_market_v2_execution_plan_receipt(text,text,bigint)')")
                if cursor.fetchone()[0] is not None:
                    cursor.execute("REVOKE ALL ON FUNCTION public."
                        "ai_market_v2_execution_plan_receipt(text,text,bigint) "
                        "FROM PUBLIC")
                    cursor.execute("GRANT EXECUTE ON FUNCTION public."
                        "ai_market_v2_execution_plan_receipt(text,text,bigint) "
                        "TO teruisi_ai_reader")
                cursor.execute("SELECT to_regprocedure('public."
                    "ai_market_v2_read_receipt(text,text,bigint)')")
                if cursor.fetchone()[0] is not None:
                    cursor.execute("REVOKE ALL ON FUNCTION public."
                        "ai_market_v2_read_receipt(text,text,bigint) FROM PUBLIC")
                    cursor.execute("GRANT EXECUTE ON FUNCTION public."
                        "ai_market_v2_read_receipt(text,text,bigint) "
                        "TO teruisi_ai_reader")
                cursor.execute("SELECT to_regprocedure('public."
                    "ai_market_v2_context_receipt(text,text,bigint)')")
                if cursor.fetchone()[0] is not None:
                    cursor.execute("REVOKE ALL ON FUNCTION public."
                        "ai_market_v2_context_receipt(text,text,bigint) FROM PUBLIC")
                    cursor.execute("GRANT EXECUTE ON FUNCTION public."
                        "ai_market_v2_context_receipt(text,text,bigint) "
                        "TO teruisi_ai_reader")
                # Remove a legacy whole-table SELECT before the dataset helper
                # reconstructs its 17 column grants.  Reversing this order
                # can clear freshly granted columns on some PostgreSQL paths.
                cursor.execute("REVOKE ALL ON TABLE public.ai_models "
                    "FROM teruisi_ai_reader")
                from system_datasets.permissions import grant_columns
                grant_columns(cursor, "ai_assistant")
                # The dataset helper reclaims stale column grants first; add
                # only the two transport fields outside its public allowlist.
                cursor.execute(sql.SQL("GRANT SELECT ({}) ON TABLE "
                    "public.ai_models TO teruisi_ai_reader").format(
                    sql.SQL(",").join(sql.Identifier(name)
                        for name in MODEL_READER_EXTRA_COLUMNS)))
                cursor.execute("SELECT a.attname FROM pg_catalog.pg_attribute a "
                    "WHERE a.attrelid='public.ai_models'::regclass "
                    "AND a.attnum>0 AND NOT a.attisdropped "
                    "AND pg_catalog.has_column_privilege("
                    "'teruisi_ai_reader','public.ai_models',a.attname,'SELECT')")
                actual_model_columns = {name for (name,) in cursor.fetchall()}
                if actual_model_columns != MODEL_READER_COLUMNS:
                    raise ValueError("AI model reader column grants drifted: "
                        f"missing={sorted(MODEL_READER_COLUMNS - actual_model_columns)}, "
                        f"extra={sorted(actual_model_columns - MODEL_READER_COLUMNS)}")
            else:
                cursor.execute("SELECT to_regprocedure('public."
                    "ai_market_v2_cost_candidate_receipt(text,text,bigint)')")
                if cursor.fetchone()[0] is not None:
                    cursor.execute("REVOKE EXECUTE ON FUNCTION public."
                        "ai_market_v2_cost_candidate_receipt(text,text,bigint) "
                        "FROM teruisi_ai_writer")
                cursor.execute("SELECT to_regprocedure('public."
                    "ai_market_v2_execution_plan_receipt(text,text,bigint)')")
                if cursor.fetchone()[0] is not None:
                    cursor.execute("REVOKE EXECUTE ON FUNCTION public."
                        "ai_market_v2_execution_plan_receipt(text,text,bigint) "
                        "FROM teruisi_ai_writer")
                cursor.execute("SELECT to_regprocedure('public."
                    "ai_market_v2_read_receipt(text,text,bigint)')")
                if cursor.fetchone()[0] is not None:
                    cursor.execute("REVOKE EXECUTE ON FUNCTION public."
                        "ai_market_v2_read_receipt(text,text,bigint) "
                        "FROM teruisi_ai_writer")
                cursor.execute("SELECT to_regprocedure('public."
                    "ai_market_v2_context_receipt(text,text,bigint)')")
                if cursor.fetchone()[0] is not None:
                    cursor.execute("REVOKE EXECUTE ON FUNCTION public."
                        "ai_market_v2_context_receipt(text,text,bigint) "
                        "FROM teruisi_ai_writer")
            cursor.execute(
                sql.SQL("ALTER ROLE {} SET default_transaction_read_only={}").format(
                    sql.Identifier(role),
                    sql.SQL("on" if role == "teruisi_ai_reader" else "off"),
                )
            )
            cursor.execute(
                sql.SQL("ALTER ROLE {} SET statement_timeout='15000'").format(
                    sql.Identifier(role)
                )
            )
            cursor.execute(
                sql.SQL(
                    "ALTER ROLE {} SET idle_in_transaction_session_timeout='30000'"
                ).format(sql.Identifier(role))
            )
