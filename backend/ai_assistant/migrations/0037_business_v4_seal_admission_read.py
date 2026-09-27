"""Narrow, read-only v4 admission lock/fence probe; sealing stays forbidden."""
from django.db import migrations


LOCK_PROBE = """CREATE FUNCTION public.ai_v4_lock_source_revisions_for_admission()
RETURNS TABLE(finance_revision bigint,finance_digest text,
              netshop_revision bigint,netshop_digest text,guard_version text,
              guard_installed_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE guards integer;
BEGIN
  IF session_user<>'teruisi_ai_writer' AND session_user<>current_user
  THEN RAISE EXCEPTION 'ai_v4_admission_role_denied'; END IF;
  IF session_user='teruisi_ai_writer' AND NOT EXISTS (
      SELECT 1 FROM public.ai_write_authority
      WHERE id=1 AND status='postgres'
        AND authority_epoch::text=current_setting('teruisi.ai_epoch',true)
        AND cutover_id=current_setting('teruisi.ai_cutover',true))
  THEN RAISE EXCEPTION 'ai_v4_admission_authority_mismatch'; END IF;
  IF NOT EXISTS (SELECT 1 FROM public.django_migrations WHERE app='ai_assistant'
      AND name='0037_business_v4_seal_admission_read')
     OR NOT EXISTS (SELECT 1 FROM public.django_migrations WHERE app='finance'
      AND name='0003_finance_source_revision_guard')
     OR NOT EXISTS (SELECT 1 FROM public.django_migrations WHERE app='finance'
      AND name='0004_finance_revision_monotonic')
     OR NOT EXISTS (SELECT 1 FROM public.django_migrations WHERE app='netshop'
      AND name='0003_netshop_source_revision_guard')
  THEN RAISE EXCEPTION 'ai_v4_admission_source_migrations_missing'; END IF;
  IF to_regclass('public.finance_source_revision_markers') IS NULL
     OR to_regclass('public.netshop_source_revision_markers') IS NULL
     OR to_regrole('teruisi_finance_writer') IS NULL
     OR to_regrole('teruisi_netshop_writer') IS NULL
     OR to_regrole('teruisi_ai_writer') IS NULL
  THEN RAISE EXCEPTION 'ai_v4_admission_source_guard_missing'; END IF;
  WITH expected(table_name,trigger_name,function_name,kind,deferred,initial) AS (
    VALUES
      ('finance_lines','finance_line_revision_required',
        'finance_source_mark_revision_required',30,false,false),
      ('finance_months','finance_month_revision_required',
        'finance_source_mark_revision_required',30,false,false),
      ('finance_import_batches','finance_batch_revision_required',
        'finance_source_mark_revision_required',30,false,false),
      ('finance_source_revision_markers','finance_source_revision_required',
        'finance_source_revision_required_at_commit',5,true,true),
      ('finance_data_revisions','finance_revision_monotonic',
        'finance_revision_monotonic_guard',31,false,false),
      ('netshop_rows','netshop_row_revision_required',
        'netshop_source_mark_revision_required',30,false,false),
      ('netshop_import_batches','netshop_batch_revision_required',
        'netshop_source_mark_revision_required',30,false,false),
      ('netshop_source_revision_markers','netshop_source_revision_required',
        'netshop_source_revision_required_at_commit',5,true,true),
      ('netshop_data_revisions','netshop_source_revision_monotonic',
        'netshop_source_revision_monotonic',31,false,false))
  SELECT count(*) INTO guards FROM expected e
    JOIN pg_catalog.pg_class c ON c.relname=e.table_name
    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace AND n.nspname='public'
    JOIN pg_catalog.pg_trigger t ON t.tgrelid=c.oid AND t.tgname=e.trigger_name
    JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid AND p.proname=e.function_name
    JOIN pg_catalog.pg_namespace pn ON pn.oid=p.pronamespace AND pn.nspname='public'
    WHERE NOT t.tgisinternal AND t.tgenabled='O' AND t.tgtype=e.kind
      AND t.tgdeferrable=e.deferred AND t.tginitdeferred=e.initial;
  IF guards<>9
  THEN RAISE EXCEPTION 'ai_v4_admission_source_triggers_invalid'; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_proc p
      WHERE p.oid='public.finance_source_mark_revision_required()'::regprocedure
        AND p.prosecdef AND replace(p.proconfig::text,' ','')
          LIKE '%search_path=pg_catalog,public%')
     OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_proc p
      WHERE p.oid='public.finance_source_revision_required_at_commit()'::regprocedure
        AND p.prosecdef AND replace(p.proconfig::text,' ','')
          LIKE '%search_path=pg_catalog,public%')
     OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_proc p
      WHERE p.oid='public.netshop_source_mark_revision_required()'::regprocedure
        AND p.prosecdef AND replace(p.proconfig::text,' ','')
          LIKE '%search_path=pg_catalog,public%')
     OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_proc p
      WHERE p.oid='public.netshop_source_revision_required_at_commit()'::regprocedure
        AND p.prosecdef AND replace(p.proconfig::text,' ','')
          LIKE '%search_path=pg_catalog,public%')
  THEN RAISE EXCEPTION 'ai_v4_admission_marker_functions_invalid'; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_proc p
      WHERE p.oid='public.finance_revision_monotonic_guard()'::regprocedure
        AND NOT p.prosecdef AND replace(p.proconfig::text,' ','')
          LIKE '%search_path=pg_catalog,public%')
     OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_proc p
      WHERE p.oid='public.netshop_source_revision_monotonic()'::regprocedure
        AND NOT p.prosecdef AND replace(p.proconfig::text,' ','')
          LIKE '%search_path=pg_catalog,public%')
  THEN RAISE EXCEPTION 'ai_v4_admission_monotonic_functions_invalid'; END IF;
  IF has_table_privilege('teruisi_finance_writer',
        'public.finance_source_revision_markers','INSERT')
     OR has_table_privilege('teruisi_finance_writer',
        'public.finance_source_revision_markers','UPDATE')
     OR has_table_privilege('teruisi_finance_writer',
        'public.finance_source_revision_markers','DELETE')
     OR has_table_privilege('teruisi_finance_writer',
        'public.finance_source_revision_markers','TRUNCATE')
     OR has_any_column_privilege('teruisi_finance_writer',
        'public.finance_source_revision_markers','INSERT')
     OR has_any_column_privilege('teruisi_finance_writer',
        'public.finance_source_revision_markers','UPDATE')
     OR has_table_privilege('teruisi_netshop_writer',
        'public.netshop_source_revision_markers','INSERT')
     OR has_table_privilege('teruisi_netshop_writer',
        'public.netshop_source_revision_markers','UPDATE')
     OR has_table_privilege('teruisi_netshop_writer',
        'public.netshop_source_revision_markers','DELETE')
     OR has_table_privilege('teruisi_netshop_writer',
        'public.netshop_source_revision_markers','TRUNCATE')
     OR has_any_column_privilege('teruisi_netshop_writer',
        'public.netshop_source_revision_markers','INSERT')
     OR has_any_column_privilege('teruisi_netshop_writer',
        'public.netshop_source_revision_markers','UPDATE')
     OR has_function_privilege('teruisi_finance_writer',
        'public.finance_source_mark_revision_required()','EXECUTE')
     OR has_function_privilege('teruisi_netshop_writer',
        'public.netshop_source_mark_revision_required()','EXECUTE')
     OR has_function_privilege('teruisi_finance_writer',
        'public.finance_source_revision_required_at_commit()','EXECUTE')
     OR has_function_privilege('teruisi_netshop_writer',
        'public.netshop_source_revision_required_at_commit()','EXECUTE')
     OR has_table_privilege('teruisi_ai_writer','public.finance_data_revisions','UPDATE')
     OR has_table_privilege('teruisi_ai_writer','public.netshop_data_revisions','UPDATE')
  THEN RAISE EXCEPTION 'ai_v4_admission_source_privileges_invalid'; END IF;
  IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles r
      WHERE r.rolname IN ('teruisi_finance_writer','teruisi_netshop_writer',
        'teruisi_ai_writer')
        AND (r.rolsuper OR r.rolcreaterole OR r.rolcreatedb
             OR r.rolbypassrls OR r.rolinherit))
     OR EXISTS (SELECT 1 FROM pg_catalog.pg_class c
       JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname='public' AND c.relname IN (
         'finance_lines','finance_months','finance_import_batches',
         'finance_data_revisions','finance_source_revision_markers',
         'netshop_rows','netshop_import_batches','netshop_data_revisions',
         'netshop_source_revision_markers','ai_business_v4_runs',
         'ai_business_v4_sources','ai_business_v4_chunks',
         'ai_business_v4_tool_receipts','ai_business_v4_validation_attempts',
         'ai_business_v4_validation_segments')
         AND pg_catalog.pg_get_userbyid(c.relowner) IN (
           'teruisi_finance_writer','teruisi_netshop_writer','teruisi_ai_writer'))
     OR EXISTS (SELECT 1 FROM pg_catalog.pg_proc p
       JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
       WHERE n.nspname='public' AND p.proname IN (
         'finance_source_mark_revision_required',
         'finance_source_revision_required_at_commit',
         'finance_revision_monotonic_guard',
         'netshop_source_mark_revision_required',
         'netshop_source_revision_required_at_commit',
         'netshop_source_revision_monotonic',
         'ai_runtime_write_fence','ai_immutable_record_guard')
         AND pg_catalog.pg_get_userbyid(p.proowner) IN (
           'teruisi_finance_writer','teruisi_netshop_writer','teruisi_ai_writer'))
  THEN RAISE EXCEPTION 'ai_v4_admission_writer_owner_invalid'; END IF;
  WITH expected(table_name,trigger_name,function_name,kind,deferred,initial) AS (
    VALUES
      ('ai_business_v4_runs','ai_write_fence','ai_runtime_write_fence',31,false,false),
      ('ai_business_v4_runs','ai_v4_state','ai_business_v4_run_guard',31,false,false),
      ('ai_business_v4_sources','ai_write_fence','ai_runtime_write_fence',31,false,false),
      ('ai_business_v4_sources','ai_v4_state','ai_business_v4_source_guard',31,false,false),
      ('ai_business_v4_chunks','ai_write_fence','ai_runtime_write_fence',31,false,false),
      ('ai_business_v4_chunks','ai_immutable_v4','ai_immutable_record_guard',27,false,false),
      ('ai_business_v4_chunks','ai_v4_state','ai_business_v4_chunk_guard',31,false,false),
      ('ai_business_v4_chunks','ai_v4_chunk_complete','ai_business_v4_chunk_complete_guard',5,true,true),
      ('ai_business_v4_tool_receipts','ai_write_fence','ai_runtime_write_fence',31,false,false),
      ('ai_business_v4_tool_receipts','ai_immutable_v4','ai_immutable_record_guard',27,false,false),
      ('ai_business_v4_tool_receipts','ai_v4_state','ai_business_v4_receipt_guard',31,false,false),
      ('ai_business_v4_validation_attempts','ai_write_fence','ai_runtime_write_fence',31,false,false),
      ('ai_business_v4_validation_attempts','ai_immutable_v4','ai_immutable_record_guard',27,false,false),
      ('ai_business_v4_validation_attempts','ai_v4_state','ai_business_v4_validation_attempt_guard',31,false,false),
      ('ai_business_v4_validation_segments','ai_write_fence','ai_runtime_write_fence',31,false,false),
      ('ai_business_v4_validation_segments','ai_immutable_v4','ai_immutable_record_guard',27,false,false),
      ('ai_business_v4_validation_segments','ai_v4_state','ai_business_v4_validation_segment_guard',31,false,false))
  SELECT count(*) INTO guards FROM expected e
    JOIN pg_catalog.pg_class c ON c.relname=e.table_name
    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace AND n.nspname='public'
    JOIN pg_catalog.pg_trigger t ON t.tgrelid=c.oid AND t.tgname=e.trigger_name
    JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid AND p.proname=e.function_name
    JOIN pg_catalog.pg_namespace pn ON pn.oid=p.pronamespace AND pn.nspname='public'
    WHERE NOT t.tgisinternal AND t.tgenabled='O' AND t.tgtype=e.kind
      AND t.tgdeferrable=e.deferred AND t.tginitdeferred=e.initial;
  IF guards<>17
  THEN RAISE EXCEPTION 'ai_v4_admission_ai_ledger_triggers_invalid'; END IF;
  IF has_table_privilege('teruisi_ai_writer','public.ai_business_v4_chunks','UPDATE')
     OR has_table_privilege('teruisi_ai_writer','public.ai_business_v4_chunks','DELETE')
     OR has_table_privilege('teruisi_ai_writer','public.ai_business_v4_tool_receipts','UPDATE')
     OR has_table_privilege('teruisi_ai_writer','public.ai_business_v4_tool_receipts','DELETE')
     OR has_table_privilege('teruisi_ai_writer','public.ai_business_v4_validation_attempts','UPDATE')
     OR has_table_privilege('teruisi_ai_writer','public.ai_business_v4_validation_attempts','DELETE')
     OR has_table_privilege('teruisi_ai_writer','public.ai_business_v4_validation_segments','UPDATE')
     OR has_table_privilege('teruisi_ai_writer','public.ai_business_v4_validation_segments','DELETE')
     OR has_table_privilege('teruisi_ai_reader','public.ai_business_v4_chunks','SELECT')
     OR has_table_privilege('teruisi_ai_reader','public.ai_business_v4_tool_receipts','SELECT')
     OR has_table_privilege('teruisi_ai_reader','public.ai_business_v4_validation_segments','SELECT')
  THEN RAISE EXCEPTION 'ai_v4_admission_ai_ledger_privileges_invalid'; END IF;
  -- Both writer triggers take these row locks before changing facts. Retain the
  -- locks until the caller's admission transaction finishes; never release
  -- between finance and netshop or accept an unversioned mixed snapshot.
  SELECT revision,source_digest INTO finance_revision,finance_digest
    FROM public.finance_data_revisions WHERE domain='finance' FOR UPDATE;
  SELECT revision,source_digest INTO netshop_revision,netshop_digest
    FROM public.netshop_data_revisions WHERE domain='netshop' FOR UPDATE;
  IF finance_revision IS NULL OR finance_revision<0 OR finance_revision>9007199254740991
     OR finance_digest !~ '^[0-9a-f]{64}$'
     OR netshop_revision IS NULL OR netshop_revision<0 OR netshop_revision>9007199254740991
     OR netshop_digest !~ '^[0-9a-f]{64}$'
     OR EXISTS (SELECT 1 FROM public.finance_source_revision_markers)
     OR EXISTS (SELECT 1 FROM public.netshop_source_revision_markers)
  THEN RAISE EXCEPTION 'ai_v4_admission_source_revision_invalid'; END IF;
  guard_version:='business-v4-source-write-fence-read-v1';
  SELECT max(applied) INTO guard_installed_at FROM public.django_migrations
    WHERE (app,name) IN (
      ('ai_assistant','0037_business_v4_seal_admission_read'),
      ('finance','0004_finance_revision_monotonic'),
      ('netshop','0003_netshop_source_revision_guard'));
  IF guard_installed_at IS NULL
  THEN RAISE EXCEPTION 'ai_v4_admission_guard_install_time_missing'; END IF;
  RETURN NEXT;
END $$"""


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql": return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT EXISTS(SELECT 1 FROM public.ai_business_v4_chunks) OR "
            "EXISTS(SELECT 1 FROM public.ai_business_v4_tool_receipts) OR "
            "EXISTS(SELECT 1 FROM public.ai_business_v4_validation_attempts) OR "
            "EXISTS(SELECT 1 FROM public.ai_business_v4_validation_segments)")
        if cursor.fetchone()[0]:
            raise RuntimeError("存在旧v4事实或分段，不能安装0037并追认旧来源")
        cursor.execute(LOCK_PROBE)
        cursor.execute("REVOKE ALL ON FUNCTION public.ai_v4_lock_source_revisions_for_admission() FROM PUBLIC")
        cursor.execute("DO $$ BEGIN IF to_regrole('teruisi_ai_writer') IS NOT NULL "
            "THEN GRANT EXECUTE ON FUNCTION "
            "public.ai_v4_lock_source_revisions_for_admission() "
            "TO teruisi_ai_writer; END IF; END $$")


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql": return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT EXISTS(SELECT 1 FROM public.ai_business_v4_chunks) OR "
            "EXISTS(SELECT 1 FROM public.ai_business_v4_tool_receipts) OR "
            "EXISTS(SELECT 1 FROM public.ai_business_v4_validation_attempts) OR "
            "EXISTS(SELECT 1 FROM public.ai_business_v4_validation_segments)")
        if cursor.fetchone()[0]:
            raise RuntimeError("存在v4事实或运行分段证明，不能逆迁移并失去只读写源门禁")
        cursor.execute("DROP FUNCTION public.ai_v4_lock_source_revisions_for_admission()")


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0036_business_v4_validation_segments"),
        ("finance", "0004_finance_revision_monotonic"),
        ("netshop", "0003_netshop_source_revision_guard")]
    operations = [migrations.RunPython(install, uninstall)]
