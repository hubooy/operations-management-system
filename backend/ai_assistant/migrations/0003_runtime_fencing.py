from django.db import migrations

TABLES = """ai_agent_checkpoints ai_agent_events ai_agent_jobs ai_agent_provider_dispatches ai_agent_provider_results ai_agent_tool_dispatches ai_agent_tool_results ai_analysis_runs ai_artifact_deliveries ai_artifacts ai_channel_callback_events ai_channels ai_chat_provider_dispatches ai_chat_request_receipts ai_conversation_deletion_audits ai_conversation_messages ai_conversation_scopes ai_conversations ai_knowledge_entries ai_memory_audit_logs ai_memory_commit_guards ai_memory_entries ai_models ai_space_admin_audits ai_space_asset_cleanup_queue ai_space_asset_favorites ai_space_assets ai_space_dispatch_receipts ai_space_dispatch_results ai_space_job_items ai_space_jobs ai_space_model_profiles ai_space_schema_upgrades ai_space_templates ai_system_settings ai_tool_audit_logs ai_workflow_events ai_workflow_node_runs ai_workflow_runs ai_data_revisions ai_write_request_receipts ai_mutation_audits""".split()
APPEND_ONLY = """ai_chat_provider_dispatches ai_agent_checkpoints ai_agent_events ai_agent_provider_results ai_agent_tool_results ai_analysis_runs ai_artifact_deliveries ai_channel_callback_events ai_conversation_deletion_audits ai_memory_audit_logs ai_space_admin_audits ai_space_dispatch_receipts ai_space_dispatch_results ai_tool_audit_logs ai_workflow_events ai_mutation_audits""".split()
IDENTITIES = {
    "ai_agent_jobs": "id owner_email client_request_id request_digest scope_json task input_json model_id model_version allowed_tools_json tool_policy_digest workflow_run_id workflow_node_key",
    "ai_workflow_runs": "id owner_email client_request_id request_digest scope_json name graph_json graph_digest input_json dry_run model_id model_version allowed_tools_json tool_policy_digest",
    "ai_space_jobs": "id owner_email client_request_id request_digest scope_json template_id template_version model_profile_id model_profile_version model_name final_prompt prompt_digest size requested_count",
    "ai_agent_provider_dispatches": "id job_id owner_email model_id model_version dispatch_ordinal",
    "ai_agent_tool_dispatches": "id job_id provider_dispatch_id provider_call_id tool_name arguments_json arguments_digest tool_call_ordinal invocation_id lease_epoch reserved_at tool_called_at",
    "ai_chat_request_receipts": "id owner_email client_request_id request_digest",
    "ai_write_request_receipts": "request_id actor_email principal_digest method path query_sha256 body_sha256",
    "ai_memory_entries": "id owner_email kind scope_mode scope_json scope_digest",
}


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("""CREATE FUNCTION ai_runtime_write_fence() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
        BEGIN
          IF current_user='teruisi_ai_writer' AND NOT EXISTS (
            SELECT 1 FROM public.ai_write_authority WHERE id=1 AND status='postgres'
              AND authority_epoch::text=current_setting('teruisi.ai_epoch',true)
              AND cutover_id=current_setting('teruisi.ai_cutover',true)
          ) THEN RAISE EXCEPTION 'ai_write_authority_mismatch'; END IF;
          IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
        END $$""")
        cursor.execute("""CREATE FUNCTION ai_immutable_record_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
        DECLARE field text;
        BEGIN
          IF TG_NARGS=0 THEN RAISE EXCEPTION 'ai_immutable_evidence'; END IF;
          FOREACH field IN ARRAY TG_ARGV LOOP
            IF (to_jsonb(OLD)->field) IS DISTINCT FROM (to_jsonb(NEW)->field) THEN RAISE EXCEPTION 'ai_immutable_identity'; END IF;
          END LOOP;
          RETURN NEW;
        END $$""")
        cursor.execute("""CREATE FUNCTION ai_memory_audit_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
        BEGIN
          IF EXISTS (SELECT 1 FROM public.ai_memory_entries entry WHERE entry.id=NEW.id AND NOT EXISTS (
            SELECT 1 FROM public.ai_memory_audit_logs audit WHERE audit.operation_id=entry.last_operation_id
              AND audit.memory_id=entry.id AND audit.owner_email=entry.owner_email
              AND audit.result_version=entry.version AND audit.after_digest=entry.content_digest
          )) THEN RAISE EXCEPTION 'ai_memory_audit_missing'; END IF;
          RETURN NULL;
        END $$""")
        for table in TABLES:
            cursor.execute(
                f'CREATE TRIGGER ai_write_fence BEFORE INSERT OR UPDATE OR DELETE ON "{table}" FOR EACH ROW EXECUTE FUNCTION ai_runtime_write_fence()'
            )
        for table in APPEND_ONLY:
            cursor.execute(
                f'CREATE TRIGGER ai_immutable_evidence BEFORE UPDATE OR DELETE ON "{table}" FOR EACH ROW EXECUTE FUNCTION ai_immutable_record_guard()'
            )
        for table, fields in IDENTITIES.items():
            arguments = ",".join("'" + field + "'" for field in fields.split())
            cursor.execute(
                f'CREATE TRIGGER ai_immutable_identity BEFORE UPDATE ON "{table}" FOR EACH ROW EXECUTE FUNCTION ai_immutable_record_guard({arguments})'
            )
        cursor.execute(
            "CREATE CONSTRAINT TRIGGER ai_memory_requires_audit AFTER INSERT OR UPDATE ON ai_memory_entries DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ai_memory_audit_guard()"
        )


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0002_seed_authority")]
    operations = [migrations.RunPython(install)]
