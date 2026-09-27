import re
from django.conf import settings
from django.db import connection, transaction
from django.http import JsonResponse
from .database_contract import (MODELS, READ_TABLES, WRITER_PRIVILEGES,
    MODEL_READER_COLUMNS)
from .control_models import AiDataRevision, AiWriteAuthority, AiMigrationRun
from .table_manifest import AI_TABLES


def _verify_promotion_trial_file_guard(cursor, *, budget_stage_enabled=False,
                                        publish_gate_enabled=False,
                                        slim_stage_enabled=False):
    """Pin the renderer-9, staged-10, or unpublished renderer-11 guard."""
    import importlib

    if publish_gate_enabled and not budget_stage_enabled:
        raise ValueError("budget publish gate has no staged predecessor")
    if slim_stage_enabled and not publish_gate_enabled:
        raise ValueError("budget slim stage has no publish-gate predecessor")
    migration = importlib.import_module(
        "ai_assistant.business_promotion_budget_v11_stage_sql"
        if slim_stage_enabled else
        "ai_assistant.migrations.0058_business_promotion_budget_v10_publish_gate"
        if publish_gate_enabled else
        "ai_assistant.migrations.0054_business_promotion_budget_file_staging"
        if budget_stage_enabled else
        "ai_assistant.migrations.0046_business_promotion_trial_file_guard")
    cursor.execute("SELECT c.convalidated,pg_catalog.pg_get_constraintdef(c.oid) "
        "FROM pg_catalog.pg_constraint c WHERE c.conrelid="
        "'public.ai_business_file_runs'::regclass "
        "AND c.conname='ai_business_file_bound' AND c.contype='c'")
    row = cursor.fetchone()
    versions = (re.search(r"renderer_version\s*=\s*ANY\s*\(ARRAY\[([0-9,\s]+)\]\)",
                          row[1]) if row and row[0] else None)
    expected_versions = ((1, 2, 3, 4, 5, 6, 7, 9, 10, 11) if slim_stage_enabled
                         else (1, 2, 3, 4, 5, 6, 7, 9, 10) if budget_stage_enabled
                         else (1, 2, 3, 4, 5, 6, 7, 9))
    if (versions is None or row[1].count("renderer_version") != 1
            or re.search(r"\bOR\b", row[1], re.IGNORECASE)
            or tuple(int(part.strip()) for part in versions.group(1).split(","))
                != expected_versions):
        raise ValueError("AI promotion trial file version constraint drift")

    signatures = (
        "public.ai_business_file_chunk_guard()",
        "public.ai_business_volume_chunk_guard()",
        "public.ai_business_volume_manifest_check(text,integer,text,boolean,text)",
        "public.ai_business_files_guard()",
        "public.ai_business_volume_complete_guard()",
        "public.ai_business_promotion_trial_parent_requirements(text,text,text)",
        "public.ai_business_promotion_trial_ready_requirements(text)",
    ) + (("public.ai_business_promotion_budget_parent_requirements(text,text,text)",)
         if budget_stage_enabled else ())
    publish = (importlib.import_module(
        "ai_assistant.migrations.0058_business_promotion_budget_v10_publish_gate")
        if publish_gate_enabled else None)
    trial = (publish.stage.previous if publish_gate_enabled else
             migration.previous if budget_stage_enabled else migration)
    definitions = (*migration.NEW_SQL, trial.PARENT_REQUIREMENTS,
                   trial.READY_REQUIREMENTS) + (((publish.stage if publish_gate_enabled
                   else migration).BUDGET_PARENT_REQUIREMENTS,)
                   if budget_stage_enabled else ())
    for index, (signature, definition) in enumerate(zip(signatures, definitions)):
        cursor.execute("SELECT p.prosrc,p.prosecdef,p.proconfig,l.lanname,"
            "pg_catalog.pg_get_userbyid(p.proowner),p.oid "
            "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_language l "
            "ON l.oid=p.prolang WHERE p.oid=to_regprocedure(%s)", [signature])
        function = cursor.fetchone()
        if (function is None or function[0] != definition.split("$$")[1]
                or function[1] is not (publish_gate_enabled and index == 4)
                or function[3] != "plpgsql"
                or {item.replace(" ", "") for item in (function[2] or [])}
                != {"search_path=pg_catalog,public"}
                or function[4] in {"teruisi_ai_writer", "teruisi_ai_reader"}):
            raise ValueError("AI promotion trial file function drift")
        cursor.execute("SELECT CASE WHEN acl.grantee=p.proowner THEN 'OWNER' "
            "WHEN acl.grantee=0 THEN 'PUBLIC' ELSE grantee.rolname END,"
            "acl.privilege_type,acl.is_grantable FROM pg_catalog.pg_proc p "
            "CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(p.proacl,"
            "pg_catalog.acldefault('f',p.proowner))) acl "
            "LEFT JOIN pg_catalog.pg_roles grantee ON grantee.oid=acl.grantee "
            "WHERE p.oid=to_regprocedure(%s)", [signature])
        acl = set(cursor.fetchall())
        expected_acl = {("OWNER", "EXECUTE", False)}
        expected_acl.add(("PUBLIC", "EXECUTE", False) if index < 5 else
                         ("teruisi_ai_writer", "EXECUTE", False))
        if acl != expected_acl:
            raise ValueError("AI promotion trial file function ACL drift: " + signature)

    if slim_stage_enabled:
        migration.verify_catalog(cursor)

    cursor.execute("SELECT c.relname,t.tgname,t.tgtype,t.tgdeferrable,"
        "t.tginitdeferred,t.tgenabled,t.tgfoid "
        "FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND NOT t.tgisinternal "
        "AND t.tgname IN ('ai_business_file_chunk_state',"
        "'ai_business_volume_chunk_state','ai_business_volume_initial',"
        "'ai_business_file_state','ai_business_volume_complete')")
    expected_triggers = {
        ("ai_business_file_chunks", "ai_business_file_chunk_state"):
            (7, False, False, signatures[0]),
        ("ai_business_volume_chunks", "ai_business_volume_chunk_state"):
            (7, False, False, signatures[1]),
        ("ai_business_file_runs", "ai_business_volume_initial"):
            (7, False, False, signatures[3]),
        ("ai_business_file_runs", "ai_business_file_state"):
            (27, False, False, signatures[3]),
        ("ai_business_file_runs", "ai_business_volume_complete"):
            (21, True, True, signatures[4]),
        ("ai_business_volume_chunks", "ai_business_volume_complete"):
            (5, True, True, signatures[4]),
    }
    triggers = cursor.fetchall()
    if len(triggers) != len(expected_triggers):
        raise ValueError("AI promotion trial file trigger drift")
    for table, name, kind, deferred, initially_deferred, enabled, function_oid in triggers:
        expected = expected_triggers.get((table, name))
        if expected is None or (kind, deferred, initially_deferred) != expected[:3] or enabled != "O":
            raise ValueError("AI promotion trial file trigger drift")
        cursor.execute("SELECT to_regprocedure(%s)::oid", [expected[3]])
        if cursor.fetchone() != (function_oid,):
            raise ValueError("AI promotion trial file trigger OID drift")


def _verify_market_v2_material_attestation(cursor):
    import importlib

    migration = importlib.import_module(
        "ai_assistant.migrations.0045_business_market_v2_material_attestation")
    role = "teruisi_ai_market_attestor"
    table = "public.ai_business_market_v2_materials"
    cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
        "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
        "WHERE rolname=%s", [role])
    if cursor.fetchone() != (False,) * 7:
        raise ValueError("AI market v2 material attestor role is not closed")
    cursor.execute("SELECT 1 FROM pg_catalog.pg_auth_members membership "
        "JOIN pg_catalog.pg_roles member ON member.oid=membership.member "
        "JOIN pg_catalog.pg_roles parent ON parent.oid=membership.roleid "
        "WHERE member.rolname=%s OR parent.rolname=%s", [role, role])
    if cursor.fetchone() is not None:
        raise ValueError("AI market v2 material attestor membership drift")
    cursor.execute("SELECT c.relkind,pg_catalog.pg_get_userbyid(c.relowner) "
        "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n "
        "ON n.oid=c.relnamespace WHERE n.nspname='public' "
        "AND c.relname='ai_business_market_v2_materials'")
    state = cursor.fetchone()
    if (state is None or state[0] != "r"
            or state[1] in {role, "teruisi_ai_reader", "teruisi_ai_writer"}):
        raise ValueError("AI market v2 material table ownership drift")
    cursor.execute("SELECT a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod),"
        "a.attnotnull FROM pg_catalog.pg_attribute a "
        "WHERE a.attrelid='public.ai_business_market_v2_materials'::regclass "
        "AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum")
    expected_columns = [
        ("report_id", "character varying(160)", True),
        ("source_report_id", "character varying(160)", True),
        *[(name, "character varying(64)", True) for name in (
            "source_snapshot_digest", "source_workflow_input_digest",
            "selector_digest", "algorithms_digest", "manifest_digest",
            "manifest_json_sha256", "summary_digest")],
        ("table_spec_digests_json", "text", True),
        ("manifest_json", "text", True), ("summary_json", "text", True),
        ("created_at", "timestamp with time zone", True)]
    if cursor.fetchall() != expected_columns:
        raise ValueError("AI market v2 material table columns drift")
    cursor.execute("SELECT c.contype,pg_catalog.pg_get_constraintdef(c.oid) "
        "FROM pg_catalog.pg_constraint c WHERE c.conrelid="
        "'public.ai_business_market_v2_materials'::regclass")
    constraints = cursor.fetchall()
    if len(constraints) != 3 or set(constraints) != {
            ("p", "PRIMARY KEY (report_id)"),
            ("f", "FOREIGN KEY (report_id) REFERENCES ai_report_runs(id) ON DELETE RESTRICT"),
            ("f", "FOREIGN KEY (source_report_id) REFERENCES ai_report_runs(id) ON DELETE RESTRICT") }:
        raise ValueError("AI market v2 material table constraints drift")
    for checked_role in (role, "teruisi_ai_writer", "teruisi_ai_reader"):
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE",
                          "REFERENCES", "TRIGGER"):
            cursor.execute("SELECT pg_catalog.has_table_privilege(%s,%s,%s)",
                [checked_role, table, privilege])
            if cursor.fetchone() != (False,):
                raise ValueError("AI market v2 material table ACL drift")
        for privilege in ("SELECT", "INSERT", "UPDATE", "REFERENCES"):
            cursor.execute("SELECT pg_catalog.has_any_column_privilege(%s,%s,%s)",
                [checked_role, table, privilege])
            if cursor.fetchone() != (False,):
                raise ValueError("AI market v2 material column ACL drift")
    cursor.execute("SELECT c.relname,t.tgname,t.tgtype,t.tgdeferrable,"
        "t.tginitdeferred,t.tgenabled,pn.nspname,p.proname,"
        "pg_catalog.pg_get_function_identity_arguments(p.oid) "
        "FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c "
        "ON c.oid=t.tgrelid JOIN pg_catalog.pg_namespace n "
        "ON n.oid=c.relnamespace JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid "
        "JOIN pg_catalog.pg_namespace pn ON pn.oid=p.pronamespace "
        "WHERE n.nspname='public' AND c.relname='ai_business_market_v2_materials' "
        "AND NOT t.tgisinternal")
    triggers = cursor.fetchall()
    if len(triggers) != 2 or set(triggers) != {
            ("ai_business_market_v2_materials", "ai_market_v2_material_guard",
             31, False, False, "O", "public", "ai_market_v2_material_guard", ""),
            ("ai_business_market_v2_materials", "ai_market_v2_material_no_truncate",
             34, False, False, "O", "public", "ai_v4_seal_ticket_no_truncate", "") }:
        raise ValueError("AI market v2 material trigger drift")
    for signature, definition, definer, allowed_attestor in (
            ("public.ai_market_v2_material_guard()", migration.GUARD, False, False),
            ("public.ai_market_v2_attest_material(text,text,text,text,text,text,text,text)",
             migration.ATTEST, True, True),
            ("public.ai_v4_seal_ticket_no_truncate()", importlib.import_module(
                "ai_assistant.migrations.0041_business_v4_seal_ticket").NO_TRUNCATE,
             False, False)):
        cursor.execute("SELECT p.prosrc,p.prosecdef,p.proconfig,l.lanname,"
            "pg_catalog.pg_get_userbyid(p.proowner) FROM pg_catalog.pg_proc p "
            "JOIN pg_catalog.pg_language l ON l.oid=p.prolang "
            "WHERE p.oid=to_regprocedure(%s)", [signature])
        function = cursor.fetchone()
        if (function is None or function[0] != definition.split("$$")[1]
                or function[1] is not definer or function[3] != "plpgsql"
                or {item.replace(" ", "") for item in (function[2] or [])}
                != {"search_path=pg_catalog,public"}
                or function[4] in {role, "teruisi_ai_writer", "teruisi_ai_reader"}):
            raise ValueError("AI market v2 material function drift")
        cursor.execute("SELECT pg_catalog.has_function_privilege(%s,%s,'EXECUTE'),"
            "pg_catalog.has_function_privilege(%s,%s,'EXECUTE'),"
            "pg_catalog.has_function_privilege(%s,%s,'EXECUTE')",
            [role, signature, "teruisi_ai_writer", signature,
             "teruisi_ai_reader", signature])
        if cursor.fetchone() != (allowed_attestor, False, False):
            raise ValueError("AI market v2 material function ACL drift")


def _verify_model_reader_columns(cursor):
    """Reject a legacy table SELECT or even one extra model column grant."""
    cursor.execute("SELECT pg_catalog.has_table_privilege("
        "current_user,'public.ai_models','SELECT')")
    if cursor.fetchone() != (False,):
        raise ValueError("AI model reader retains whole-table SELECT")
    cursor.execute("SELECT a.attname FROM pg_catalog.pg_attribute a "
        "WHERE a.attrelid='public.ai_models'::regclass "
        "AND a.attnum>0 AND NOT a.attisdropped "
        "AND pg_catalog.has_column_privilege("
        "current_user,'public.ai_models',a.attname,'SELECT')")
    if {name for (name,) in cursor.fetchall()} != MODEL_READER_COLUMNS:
        raise ValueError("AI model reader column grants differ from closed contract")


def check():
    if set(AI_TABLES) != set(MODELS):
        raise ValueError("AI backup inventory drift")
    if connection.vendor != "postgresql":
        raise ValueError("AI runtime requires PostgreSQL")
    writer = settings.DJANGO_PROCESS_ROLE == "ai_writer"
    expected = "teruisi_ai_writer" if writer else "teruisi_ai_reader"
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "SELECT current_user,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user"
        )
        row = cursor.fetchone()
        if not row or row[0] != expected or any(row[1:]):
            raise ValueError("AI database role mismatch")
        cursor.execute("SHOW transaction_read_only")
        if (cursor.fetchone()[0] == "on") == writer:
            raise ValueError("AI read/write connection mismatch")
        cursor.execute(
            "SELECT has_schema_privilege(current_user,'public','CREATE'),has_database_privilege(current_user,current_database(),'CREATE')"
        )
        if any(cursor.fetchone()):
            raise ValueError("AI runtime retains DDL privileges")
        cursor.execute(
            "SELECT 1 FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=current_user)"
        )
        if cursor.fetchone():
            raise ValueError("AI runtime role membership is not empty")
        allowed = (
            WRITER_PRIVILEGES
            if writer
            else {table: ("SELECT",) for table in READ_TABLES}
        )
        if not writer:
            from system_datasets.permissions import validate_reader_columns
            validate_reader_columns(cursor, settings.DJANGO_PROCESS_ROLE)
            _verify_model_reader_columns(cursor)
        cursor.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
        )
        for (table,) in cursor.fetchall():
            for privilege in [
                "SELECT",
                "INSERT",
                "UPDATE",
                "DELETE",
                "TRUNCATE",
                "REFERENCES",
                "TRIGGER",
            ]:
                cursor.execute(
                    "SELECT has_table_privilege(current_user,%s,%s)",
                    ("public." + table, privilege),
                )
                if cursor.fetchone()[0] != (privilege in allowed.get(table, ())):
                    raise ValueError(
                        "AI table privileges differ from the closed contract"
                    )
        for table, model in MODELS.items():
            cursor.execute(
                "SELECT a.attname FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=%s AND a.attnum>0 AND NOT a.attisdropped",
                (table,),
            )
            if {r[0] for r in cursor.fetchall()} != {
                f.column for f in model._meta.local_concrete_fields
            }:
                raise ValueError("AI schema mismatch")
        cursor.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname='public' AND tablename LIKE 'ai_%'"
        )
        indexes = {row[0] for row in cursor.fetchall()}
        required = {
            index.name for model in MODELS.values() for index in model._meta.indexes
        } | {
            "ai_memory_active_key_uq",
            "ai_default_text_model_uq",
            "ai_space_default_scene_uq",
        }
        if not required <= indexes:
            raise ValueError("AI indexes incomplete")
        import importlib

        market_profile = importlib.import_module(
            "ai_assistant.migrations.0044_business_market_v2_profile")
        market_execution = importlib.import_module(
            "ai_assistant.migrations.0060_business_market_v2_execution_snapshot")
        market_synthetic = importlib.import_module(
            "ai_assistant.migrations.0064_business_market_v2_synthetic_vertical")
        expected_market_guards = {
            ("ai_report_runs", "ai_market_v2_report_guard"):
                (31, False, False, "ai_market_v2_parked_report_guard",
                 market_execution.NEW_PARKED_REPORT),
            ("ai_workflow_runs", "ai_market_v2_workflow_guard"):
                (31, False, False, "ai_market_v2_parked_workflow_guard",
                 market_synthetic.NEW_PARKED_WORKFLOW),
            ("ai_workflow_runs", "ai_market_v2_workflow_complete"):
                (5, True, True, "ai_market_v2_parked_orphan_guard",
                 market_profile.ORPHAN_GUARD),
            ("ai_agent_jobs", "ai_market_v2_job_guard"):
                (7, False, False, "ai_market_v2_parked_job_guard",
                 market_profile.JOB_GUARD),
        }
        cursor.execute("SELECT c.relname,t.tgname,t.tgtype,t.tgdeferrable,"
            "t.tginitdeferred,t.tgenabled,p.proname,pn.nspname,"
            "pg_catalog.pg_get_function_identity_arguments(p.oid),"
            "p.prosrc,p.proconfig,p.prosecdef,l.lanname "
            "FROM pg_catalog.pg_trigger t "
            "JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid "
            "JOIN pg_catalog.pg_namespace cn ON cn.oid=c.relnamespace "
            "JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid "
            "JOIN pg_catalog.pg_namespace pn ON pn.oid=p.pronamespace "
            "JOIN pg_catalog.pg_language l ON l.oid=p.prolang "
            "WHERE cn.nspname='public' AND NOT t.tgisinternal "
            "AND t.tgname IN ('ai_market_v2_report_guard',"
            "'ai_market_v2_workflow_guard','ai_market_v2_workflow_complete',"
            "'ai_market_v2_job_guard')")
        found = cursor.fetchall()
        if len(found) != len(expected_market_guards):
            raise ValueError("AI market v2 parked profile guards missing")
        for (table, name, kind, deferred, initially_deferred, enabled,
             function, namespace, arguments, body, config, security_definer,
             language) in found:
            expected_guard = expected_market_guards.get((table, name))
            if (expected_guard is None
                    or (kind, deferred, initially_deferred, function)
                    != expected_guard[:4]
                    or enabled != "O" or namespace != "public"
                    or arguments != "" or body != expected_guard[4].split("$$")[1]
                    or {item.replace(" ", "") for item in (config or [])}
                    != {"search_path=pg_catalog,public"}
                    or security_definer is not False or language != "plpgsql"):
                raise ValueError("AI market v2 parked profile guard drift")

        _verify_market_v2_material_attestation(cursor)
        _verify_promotion_trial_file_guard(cursor, budget_stage_enabled=True,
                                            publish_gate_enabled=True,
                                            slim_stage_enabled=True)
        from importlib import import_module
        import_module("ai_assistant.migrations.0059_business_promotion_budget_v10_reader_fence").verify_catalog(cursor)
        import_module("ai_assistant.migrations.0067_business_promotion_budget_v11_attestation").verify_catalog(cursor)
        import_module("ai_assistant.migrations.0068_business_promotion_budget_v11_verifier_receipt").verify_catalog(cursor)
        from .business_market_v2_context_catalog import verify as verify_market_context
        verify_market_context(cursor)
        from .business_market_v2_read_catalog import verify as verify_market_read
        verify_market_read(cursor)
        from .business_market_v2_execution_plan_catalog import verify as verify_market_plan
        verify_market_plan(cursor)
        from .business_market_v2_synthetic_catalog import verify as verify_market_synthetic
        verify_market_synthetic(cursor)
        from .business_market_v2_cost_catalog import verify as verify_market_cost
        verify_market_cost(cursor)
        import_module("ai_assistant.migrations.0069_business_market_v2_paid_round_rehearsal").verify_catalog(cursor)
        import_module("ai_assistant.migrations.0070_business_promotion_budget_v11_limited_identity").verify_catalog(cursor)
        import_module("ai_assistant.migrations.0071_business_v4_report_source_link").verify_catalog(cursor)
        import_module("ai_assistant.migrations.0072_business_market_v2_authority_proposals").verify_catalog(cursor)
        from .v4_replay_progress_catalog import verify as verify_v4_replay_progress
        verify_v4_replay_progress(cursor, finance_enabled=True,
                                  read_cast_enabled=True,
                                  prior_claim_qualified=True)
        from .v4_sealer_source_catalog import verify as verify_v4_sealer_source
        verify_v4_sealer_source(cursor)
        from .v4_commit_consumption_catalog import verify as verify_v4_commit
        verify_v4_commit(cursor)
        from .market_v2_admitted_catalog import verify as verify_market_v2_admitted
        verify_market_v2_admitted(cursor)
        from .v4_period_plan_catalog import verify as verify_v4_period_plan
        verify_v4_period_plan(cursor)

        fencing = importlib.import_module(
            "ai_assistant.migrations.0003_runtime_fencing"
        )
        required_triggers = (
            {("ai_report_runs", "ai_business_screening_report_binding"),
             ("ai_workflow_runs", "ai_business_screening_workflow_binding")}
            | {(table, trigger) for table in ("ai_business_screening_runs", "ai_business_screening_pages")
             for trigger in ("ai_write_fence", "ai_immutable_evidence", "ai_screen_complete")}
            | {("ai_business_screening_runs", "ai_screen_initial"), ("ai_business_screening_pages", "ai_screen_page_initial")}
            |
            {("ai_business_budget_plans", "ai_write_fence"),
             ("ai_business_budget_plans", "ai_immutable_evidence"),
             ("ai_business_budget_plans", "ai_business_budget_initial"),
             ("ai_business_budget_plans", "ai_business_budget_complete"),
             ("ai_report_runs", "ai_business_budget_report_binding"),
             ("ai_report_runs", "ai_business_budget_complete"),
             ("ai_report_runs", "ai_business_integrated_report_binding")}
            |
            {("ai_business_volume_chunks", "ai_write_fence"),
             ("ai_business_volume_chunks", "ai_immutable_evidence"),
             ("ai_business_volume_chunks", "ai_business_volume_chunk_state"),
             ("ai_business_volume_chunks", "ai_business_volume_complete"),
             ("ai_business_file_runs", "ai_business_volume_complete"),
             ("ai_business_file_runs", "ai_business_volume_initial")}
            |
            {(table, "ai_write_fence") for table in ("ai_business_file_runs", "ai_business_file_chunks")}
            | {("ai_business_file_runs", "ai_immutable_identity"), ("ai_business_file_runs", "ai_business_file_state"),
               ("ai_business_file_chunks", "ai_immutable_evidence"), ("ai_business_file_chunks", "ai_business_file_chunk_state")}
            |
            {(table, "ai_write_fence") for table in ("ai_business_evidence_runs", "ai_business_evidence_chunks")}
            | {("ai_business_evidence_runs", "ai_immutable_identity"), ("ai_business_evidence_runs", "ai_business_terminal"), ("ai_business_evidence_chunks", "ai_immutable_evidence")}
            | {("ai_business_evidence_sources", "ai_write_fence"),
               ("ai_business_evidence_sources", "ai_immutable_identity"),
               ("ai_business_evidence_sources", "ai_business_source_state"),
               ("ai_business_evidence_sources", "ai_business_directory_complete"),
               ("ai_business_evidence_runs", "ai_business_directory_complete"),
               ("ai_business_evidence_chunks", "ai_business_v2_chunk_source"),
               ("ai_business_evidence_chunks", "ai_business_directory_complete")}
            |
            {(table, "ai_write_fence") for table in ("ai_library_revisions", "ai_execution_guidance", "ai_report_runs", "ai_report_deliveries")}
            | {(table, "ai_immutable_evidence") for table in ("ai_library_revisions", "ai_execution_guidance", "ai_report_runs")}
            |
            {(table, "ai_write_fence") for table in fencing.TABLES}
            | {(table, "ai_immutable_evidence") for table in fencing.APPEND_ONLY}
            | {(table, "ai_immutable_identity") for table in fencing.IDENTITIES}
            | {("ai_memory_entries", "ai_memory_requires_audit")}
            | {
                ("ai_prompt_settings_revisions", "ai_write_fence"),
                ("ai_prompt_settings_revisions", "ai_immutable_evidence"),
                ("ai_dingtalk_settings", "ai_write_fence"),
                ("ai_dingtalk_settings", "ai_immutable_identity"),
                ("ai_dingtalk_sessions", "ai_write_fence"),
                ("ai_dingtalk_sessions", "ai_immutable_identity"),
                ("ai_dingtalk_sessions", "ai_ding_conversation_guard"),
                ("ai_dingtalk_receipts", "ai_write_fence"),
                ("ai_dingtalk_receipts", "ai_immutable_identity"),
                ("ai_dingtalk_schedules", "ai_write_fence"),
                ("ai_dingtalk_schedules", "ai_immutable_identity"),
                ("ai_dingtalk_schedule_runs", "ai_write_fence"),
                ("ai_dingtalk_schedule_runs", "ai_immutable_identity"),
                ("ai_conversation_workspaces", "ai_write_fence"),
                ("ai_conversation_workspaces", "ai_immutable_identity"),
                ("ai_space_asset_payloads", "ai_write_fence"),
                ("ai_space_asset_payloads", "ai_immutable_evidence"),
                ("ai_space_asset_payloads", "ai_asset_payload_complete"),
                ("ai_space_assets", "ai_asset_payload_complete"),
            }
            | {
                (table, "ai_terminal_control")
                for table in (
                    "ai_write_authority",
                    "ai_data_revisions",
                    "ai_migration_runs",
                )
            }
        )
        required_triggers |= {
            (table, trigger)
            for table in ("ai_business_v4_seal_tickets", "ai_business_v4_seal_claims")
            for trigger in ("ai_v4_ticket_immutable", "ai_v4_ticket_no_truncate")
        }
        required_triggers |= {
            ("ai_business_v4_seal_consumptions", name)
            for name in ("ai_v4_consumption_state", "ai_v4_ticket_immutable",
                         "ai_v4_ticket_no_truncate")
        } | {("ai_business_v4_seals", "ai_v4_seal_consumption_required")}
        cursor.execute(
            "SELECT c.relname,t.tgname,t.tgenabled FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal AND c.relname LIKE 'ai_%'"
        )
        triggers = {
            (table, trigger)
            for table, trigger, enabled in cursor.fetchall()
            if enabled == "O"
        }
        if not required_triggers <= triggers:
            raise ValueError("AI write fences or immutable audit guards missing")
        cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
            "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
            "WHERE rolname='teruisi_ai_seal_writer'")
        if cursor.fetchone() != (False, False, False, False, False, False, False):
            raise ValueError("AI v4 seal ticket role is not default closed")
        for signature, sealer, ai_writer in (
            ("public.ai_v4_issue_seal_ticket(text,text,text,bigint,bigint,text,text)",
             False, True),
            ("public.ai_v4_claim_seal_ticket(text,text,text,bigint,text)",
             True, False),
            ("public.ai_v4_sealer_read_context(text,text,text,bigint)",
             False, False),
            ("public.ai_v4_sealer_read_segment(text,text,text,integer,text,bigint)",
             False, False),
            ("public.ai_v4_sealer_read_page(text,text,text,bigint,text,bigint)",
             False, False),
            ("public.ai_v4_commit_seal(text,text,bigint,text,text,text,text)",
             False, False),
            ("public.ai_v4_lock_source_revisions_for_admission()",
             False, True),
        ):
            cursor.execute("SELECT to_regprocedure(%s)", [signature])
            if cursor.fetchone()[0] is None:
                raise ValueError("AI v4 seal ticket function missing")
            cursor.execute("SELECT has_function_privilege('teruisi_ai_seal_writer',%s,'EXECUTE'),"
                "has_function_privilege('teruisi_ai_writer',%s,'EXECUTE'),"
                "has_function_privilege('teruisi_ai_reader',%s,'EXECUTE')",
                [signature, signature, signature])
            if cursor.fetchone() != (sealer, ai_writer, False):
                raise ValueError("AI v4 seal ticket function ACL drift")
        for signature, sealer in (
            ("public.ai_v4_sealer_assert_claim(text,text,text,bigint,text,text)", False),
            ("public.ai_v4_sealer_ticket_context(text,text,text,bigint,text,text)", True),
            ("public.ai_v4_sealer_ticket_segment(text,text,text,integer,text,bigint,text,text)", True),
            ("public.ai_v4_sealer_ticket_page(text,text,text,bigint,text,bigint,text,text)", True),
        ):
            cursor.execute("SELECT to_regprocedure(%s)", [signature])
            if cursor.fetchone()[0] is None:
                raise ValueError("AI v4 claimed reader function missing")
            cursor.execute("SELECT has_function_privilege('teruisi_ai_seal_writer',%s,'EXECUTE'),"
                "has_function_privilege('teruisi_ai_writer',%s,'EXECUTE'),"
                "has_function_privilege('teruisi_ai_reader',%s,'EXECUTE')",
                [signature] * 3)
            if cursor.fetchone() != (sealer, False, False):
                raise ValueError("AI v4 claimed reader function ACL drift")
        cursor.execute("SELECT t.tgtype,t.tgdeferrable,t.tginitdeferred,"
            "t.tgenabled,t.tgfoid='public.ai_v4_seal_requires_consumption()'"
            "::regprocedure,p.prosecdef,p.proconfig,p.prosrc,"
            "p.proowner='teruisi_ai_seal_writer'::regrole "
            "FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_proc p "
            "ON p.oid=t.tgfoid WHERE t.tgrelid="
            "'public.ai_business_v4_seals'::regclass AND "
            "t.tgname='ai_v4_seal_consumption_required'")
        required_seal = cursor.fetchone()
        consumption_migration = importlib.import_module(
            "ai_assistant.migrations.0043_business_v4_seal_consumption_candidate")
        if (required_seal is None or required_seal[:6] !=
                (5, True, True, "O", True, True)
                or {value.replace(" ", "") for value in (required_seal[6] or [])}
                != {"search_path=pg_catalog,public"}
                or required_seal[7] != consumption_migration.REQUIRE_CONSUMPTION.split("$$")[1]
                or required_seal[8] is not False):
            raise ValueError("AI v4 seal consumption commit fence missing")
        signature = "public.ai_v4_sealer_consumption_result(text,text,text,text,text)"
        cursor.execute("SELECT to_regprocedure(%s)", [signature])
        if cursor.fetchone()[0] is None:
            raise ValueError("AI v4 seal result function missing")
        cursor.execute("SELECT has_function_privilege('teruisi_ai_seal_writer',%s,'EXECUTE'),"
            "has_function_privilege('teruisi_ai_writer',%s,'EXECUTE'),"
            "has_function_privilege('teruisi_ai_reader',%s,'EXECUTE')",
            [signature] * 3)
        if cursor.fetchone() != (True, False, False):
            raise ValueError("AI v4 seal result function ACL drift")
        signature = "public.ai_v4_verify_seal_consumption(text,text,bigint,text)"
        cursor.execute("SELECT p.prosrc,p.prosecdef,p.proconfig,"
            "pg_catalog.pg_get_userbyid(p.proowner) FROM pg_catalog.pg_proc p "
            "WHERE p.oid=to_regprocedure(%s)", [signature])
        verifier = cursor.fetchone()
        if (verifier is None
                or verifier[0] != consumption_migration.VERIFY_CONSUMPTION.split("$$")[1]
                or verifier[1] is not True
                or {item.replace(" ", "") for item in (verifier[2] or [])}
                != {"search_path=pg_catalog,public"}
                or verifier[3] in {"teruisi_ai_reader", "teruisi_ai_writer",
                                   "teruisi_ai_seal_writer"}):
            raise ValueError("AI v4 seal consumption verifier missing")
        cursor.execute("SELECT has_function_privilege('teruisi_ai_seal_writer',%s,'EXECUTE'),"
            "has_function_privilege('teruisi_ai_writer',%s,'EXECUTE'),"
            "has_function_privilege('teruisi_ai_reader',%s,'EXECUTE')",
            [signature] * 3)
        if cursor.fetchone() != (False, True, False):
            raise ValueError("AI v4 seal consumption verifier ACL drift")
        integrated = importlib.import_module("ai_assistant.migrations.0022_business_integrated_reports")
        screening = importlib.import_module("ai_assistant.migrations.0023_business_screening_storage")
        screening_runtime = importlib.import_module("ai_assistant.migrations.0024_business_screening_runtime")
        for signature, definition, volatility in (
            ("public.ai_screen_fields(json,text[])", screening.FIELDS, "i"),
            ("public.ai_screen_uint(json,bigint,bigint)", screening.UINT, "i"),
            ("public.ai_screen_initial_guard()", screening_runtime.SCREEN_INITIAL, "v"),
            ("public.ai_screen_page_guard()", screening.PAGE, "v"),
            ("public.ai_screen_complete_guard()", screening.COMPLETE, "v"),
            ("public.ai_business_mapping_plan_json(text)", integrated.PLAN_GUARD, "i"),
            ("public.ai_business_integrated_report_guard()", screening_runtime.INTEGRATED_REPORT_GUARD, "v"),
            ("public.ai_business_budget_report_guard()", screening_runtime.NEW_BUDGET_GUARD, "v"),
            ("public.ai_business_screening_report_guard()", screening_runtime.REPORT_GUARD, "v"),
            ("public.ai_business_screening_workflow_guard()", screening_runtime.WORKFLOW_GUARD, "v"),
        ):
            cursor.execute("""SELECT p.prosrc,p.provolatile,p.prosecdef,p.proconfig,l.lanname
                FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang WHERE p.oid=to_regprocedure(%s)""", [signature])
            function = cursor.fetchone()
            if (function is None or function[0] != definition.split("$$")[1]
                    or function[1:3] != (volatility, False) or function[4] != "plpgsql"
                    or {item.replace(" ", "") for item in (function[3] or [])} != {"search_path=pg_catalog,public"}):
                raise ValueError("AI integrated function contract missing or changed")
        cursor.execute("""SELECT tgtype,tgdeferrable,tginitdeferred,
            tgfoid='public.ai_business_integrated_report_guard()'::regprocedure
            FROM pg_trigger WHERE tgrelid='public.ai_report_runs'::regclass
            AND tgname='ai_business_integrated_report_binding' AND tgenabled='O'""")
        if cursor.fetchone() != (7, False, False, True):
            raise ValueError("AI integrated report trigger contract changed")
        for table, name, expected_type, deferred, function in (
            ("ai_report_runs", "ai_business_screening_report_binding", 7, False, "ai_business_screening_report_guard"),
            ("ai_workflow_runs", "ai_business_screening_workflow_binding", 5, True, "ai_business_screening_workflow_guard"),
            ("ai_business_screening_runs", "ai_screen_initial", 7, False, "ai_screen_initial_guard"),
            ("ai_business_screening_pages", "ai_screen_page_initial", 7, False, "ai_screen_page_guard"),
            ("ai_business_screening_runs", "ai_screen_complete", 5, True, "ai_screen_complete_guard"),
            ("ai_business_screening_pages", "ai_screen_complete", 5, True, "ai_screen_complete_guard"),
        ):
            cursor.execute("""SELECT tgtype,tgdeferrable,tginitdeferred,tgfoid=to_regprocedure(%s)
                FROM pg_trigger WHERE tgrelid=%s::regclass AND tgname=%s AND tgenabled='O'""",
                ["public."+function+"()", "public."+table, name])
            if cursor.fetchone() != (expected_type, deferred, deferred, True):
                raise ValueError("AI screening publication trigger contract changed")
        for table, expected in (
            ("ai_business_screening_runs", {"ai_screen_run_bound", "ai_screen_binding_uq"}),
            ("ai_business_screening_pages", {"ai_screen_page_bound", "ai_screen_page_sequence_uq", "ai_screen_page_offset_uq"}),
            ("ai_business_budget_plans", {"ai_business_budget_bound"}),
            ("ai_business_file_runs", {"ai_business_file_bound", "ai_business_file_binding_uq"}),
            ("ai_business_file_chunks", {"ai_business_file_chunk_bound", "ai_business_file_chunk_uq"}),
            ("ai_business_volume_chunks", {"ai_business_volume_chunk_bound", "ai_business_volume_chunk_uq"}),
            ("ai_business_evidence_runs", {"ai_business_run_bound", "ai_business_client_uq"}),
            ("ai_business_evidence_chunks", {"ai_business_chunk_bound", "ai_business_chunk_uq"}),
            ("ai_business_evidence_sources", {"ai_business_source_bound", "ai_business_source_key_uq", "ai_business_source_ord_uq", "ai_business_source_query_uq"}),
            ("ai_library_revisions", {"ai_library_revisions_bound"}),
            ("ai_execution_guidance", {"ai_execution_guidance_bound"}),
            ("ai_report_runs", {"ai_report_runs_bound", "ai_report_client_uq"}),
            ("ai_report_deliveries", {"ai_report_deliveries_bound"}),
            ("ai_prompt_settings_revisions", {"ai_prompt_revision_bound"}),
            ("ai_dingtalk_settings", {"ai_ding_settings_singleton", "ai_ding_settings_version", "ai_ding_settings_size"}),
            ("ai_dingtalk_sessions", {"ai_ding_session_type", "ai_ding_scope_size"}),
            ("ai_dingtalk_receipts", {"ai_ding_receipt_status", "ai_ding_ack_status", "ai_ding_content_size"}),
            ("ai_dingtalk_schedules", {"ai_ding_schedule_bound", "ai_ding_schedule_media_bound"}),
            ("ai_dingtalk_schedule_runs", {"ai_ding_run_status", "ai_ding_schedule_slot_uq"}),
        ):
            cursor.execute("SELECT conname FROM pg_constraint WHERE conrelid=%s::regclass AND convalidated", [table])
            if not expected <= {row[0] for row in cursor.fetchall()}:
                raise ValueError("AI DingTalk constraints missing")
        # Check FK/uniqueness by columns rather than Django-generated names.
        for table, column, target in (("ai_business_screening_runs", "report_id", "ai_report_runs"),
                                      ("ai_business_screening_runs", "evidence_id", "ai_business_evidence_runs"),
                                      ("ai_business_screening_pages", "run_id", "ai_business_screening_runs"),
                                      ("ai_report_runs", "budget_plan_id", "ai_business_budget_plans"),
                                      ("ai_business_budget_plans", "evidence_id", "ai_business_evidence_runs")):
            cursor.execute("""SELECT c.contype,c.confrelid=%s::regclass FROM pg_constraint c
                JOIN pg_attribute a ON a.attrelid=c.conrelid AND c.conkey=ARRAY[a.attnum]::smallint[]
                WHERE c.conrelid=%s::regclass AND a.attname=%s AND c.convalidated""", [target, table, column])
            constraints = cursor.fetchall()
            if ("f", True) not in constraints or table == "ai_report_runs" and not any(kind == "u" for kind, _ in constraints):
                raise ValueError("AI fixed budget reference constraints missing")
        cursor.execute("""SELECT c.relname,t.tgdeferrable,t.tginitdeferred FROM pg_trigger t
            JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relname IN ('ai_business_budget_plans','ai_report_runs')
            AND t.tgname='ai_business_budget_complete' AND t.tgenabled='O'""")
        if set(cursor.fetchall()) != {("ai_business_budget_plans", True, True), ("ai_report_runs", True, True)}:
            raise ValueError("AI fixed budget deferred completeness missing")
        cursor.execute("SELECT conname FROM pg_constraint WHERE conrelid='public.ai_conversation_workspaces'::regclass AND convalidated")
        if not {"ai_workspace_module", "ai_workspace_context_size"} <= {row[0] for row in cursor.fetchall()}:
            raise ValueError("AI conversation workspace constraints missing")
        cursor.execute("SELECT convalidated FROM pg_constraint WHERE conrelid='public.ai_space_asset_payloads'::regclass AND conname='ai_payload_size'")
        if cursor.fetchone() != (True,):
            raise ValueError("AI image payload size constraint missing")
        authority = AiWriteAuthority.objects.get(id=1)
        revision = AiDataRevision.objects.get(domain="ai-assistant")
        if (
            authority.status != "postgres"
            or str(authority.authority_epoch) != settings.AI_WRITE_AUTHORITY_EPOCH
            or authority.cutover_id != settings.AI_WRITE_CUTOVER_ID
            or revision.revision < 1
            or not re.fullmatch("[a-f0-9]{64}", revision.source_digest)
        ):
            raise ValueError("AI authority or revision invalid")
        adopted = AiMigrationRun.objects.get(
            id=authority.migration_verify_run_id, mode="apply", status="verified"
        )
        if (
            adopted.source_snapshot_digest != revision.source_digest
            or adopted.target_snapshot_digest != revision.source_digest
            or adopted.source_counts != adopted.target_counts
        ):
            raise ValueError("AI adoption evidence invalid")
    return {
        "status": "ready",
        "service": "teruisi-django",
        "database": "ready",
        "domain": "ai-assistant",
        "authority": "postgres",
        "processRole": settings.DJANGO_PROCESS_ROLE,
        "revision": str(revision.revision),
        "cutoverId": authority.cutover_id,
    }


def ready():
    try:
        return JsonResponse(check(), headers={"Cache-Control": "no-store"})
    except Exception:
        return JsonResponse(
            {"status": "not_ready", "code": "ai_service_unavailable"},
            status=503,
            headers={"Cache-Control": "no-store"},
        )
