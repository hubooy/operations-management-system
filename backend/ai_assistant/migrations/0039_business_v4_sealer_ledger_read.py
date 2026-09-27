"""Narrow, default-closed ledger reads for a future independent v4 sealer."""
from django.db import migrations


ROLE = "teruisi_ai_seal_writer"
LEDGER = (
    "ai_business_v4_chunks", "ai_business_v4_tool_receipts",
    "ai_business_v4_validation_attempts", "ai_business_v4_validation_segments",
    "ai_business_v4_seals",
)
AUTHORITY_COLUMNS = ("id", "status", "authority_epoch", "cutover_id")
REVISION_COLUMNS = ("domain", "revision", "source_digest")


READ_GATE = """CREATE FUNCTION public.ai_v4_sealer_ledger_read_gate(
  selected_run text,selected_attempt text,selected_actor text,
  selected_actor_version bigint)
RETURNS TABLE(run_id text,attempt_id text,parent_version bigint,
  directory_digest text,source_count bigint)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE parent public.ai_business_v4_runs%ROWTYPE;
  attempt public.ai_business_v4_validation_attempts%ROWTYPE;
  table_name text;
BEGIN
  IF session_user<>'teruisi_ai_seal_writer'
     OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles role
       WHERE role.rolname='teruisi_ai_seal_writer'
         AND NOT role.rolinherit AND NOT role.rolsuper
         AND NOT role.rolcreatedb AND NOT role.rolcreaterole
         AND NOT role.rolreplication AND NOT role.rolbypassrls)
     OR EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members
       WHERE roleid='teruisi_ai_seal_writer'::regrole
          OR member='teruisi_ai_seal_writer'::regrole)
  THEN RAISE EXCEPTION 'ai_v4_sealer_ledger_role_denied'; END IF;
  IF NOT has_schema_privilege('teruisi_ai_seal_writer','public','USAGE')
     OR NOT has_function_privilege('teruisi_ai_seal_writer',
       'public.ai_v4_lock_source_revisions_for_admission()','EXECUTE')
     OR NOT has_function_privilege('teruisi_ai_seal_writer',
       'public.ai_v4_sealer_receipt_audit_segment(text,text,text,integer,text,bigint)',
       'EXECUTE')
  THEN RAISE EXCEPTION 'ai_v4_sealer_ledger_base_acl_missing'; END IF;
  FOREACH table_name IN ARRAY ARRAY[
    'ai_business_v4_runs','ai_business_v4_sources',
    'ai_business_v4_chunks','ai_business_v4_tool_receipts',
    'ai_business_v4_validation_attempts',
    'ai_business_v4_validation_segments','ai_business_v4_seals'] LOOP
    IF NOT has_table_privilege('teruisi_ai_seal_writer',
         'public.'||table_name,'SELECT')
       OR EXISTS (SELECT 1 FROM unnest(ARRAY[
         'INSERT','UPDATE','DELETE','TRUNCATE']) permission
         WHERE has_table_privilege('teruisi_ai_seal_writer',
           'public.'||table_name,permission))
       OR has_any_column_privilege('teruisi_ai_seal_writer',
         'public.'||table_name,'INSERT')
       OR has_any_column_privilege('teruisi_ai_seal_writer',
         'public.'||table_name,'UPDATE')
    THEN RAISE EXCEPTION 'ai_v4_sealer_ledger_acl_drift'; END IF;
  END LOOP;
  FOREACH table_name IN ARRAY ARRAY[
    'finance_lines','finance_months','finance_import_batches',
    'finance_data_revisions','finance_source_revision_markers',
    'netshop_rows','netshop_import_batches',
    'netshop_data_revisions','netshop_source_revision_markers'] LOOP
    IF EXISTS (SELECT 1 FROM unnest(ARRAY[
         'SELECT','INSERT','UPDATE','DELETE','TRUNCATE']) permission
         WHERE has_table_privilege('teruisi_ai_seal_writer',
           'public.'||table_name,permission))
       OR has_any_column_privilege('teruisi_ai_seal_writer',
         'public.'||table_name,'SELECT')
       OR has_any_column_privilege('teruisi_ai_seal_writer',
         'public.'||table_name,'INSERT')
       OR has_any_column_privilege('teruisi_ai_seal_writer',
         'public.'||table_name,'UPDATE')
    THEN RAISE EXCEPTION 'ai_v4_sealer_business_acl_drift'; END IF;
  END LOOP;
  IF EXISTS (SELECT 1 FROM unnest(ARRAY[
       'SELECT','INSERT','UPDATE','DELETE','TRUNCATE']) permission
       WHERE has_table_privilege('teruisi_ai_seal_writer',
         'public.ai_tool_audit_logs',permission))
     OR has_any_column_privilege('teruisi_ai_seal_writer',
       'public.ai_tool_audit_logs','SELECT')
     OR has_any_column_privilege('teruisi_ai_seal_writer',
       'public.access_control_users','SELECT')
     OR EXISTS (SELECT 1 FROM unnest(ARRAY[
       'SELECT','INSERT','UPDATE','DELETE','TRUNCATE']) permission
       WHERE has_table_privilege('teruisi_ai_seal_writer',
         'public.access_control_users',permission))
     OR EXISTS (SELECT 1 FROM unnest(ARRAY[
       'INSERT','UPDATE','DELETE','TRUNCATE']) permission
       WHERE has_table_privilege('teruisi_ai_seal_writer',
         'public.ai_write_authority',permission)
           OR has_table_privilege('teruisi_ai_seal_writer',
         'public.ai_data_revisions',permission))
  THEN RAISE EXCEPTION 'ai_v4_sealer_control_acl_drift'; END IF;
  FOREACH table_name IN ARRAY ARRAY[
    'ai_tool_audit_logs','access_control_users',
    'ai_write_authority','ai_data_revisions'] LOOP
    IF has_any_column_privilege('teruisi_ai_seal_writer',
         'public.'||table_name,'INSERT')
       OR has_any_column_privilege('teruisi_ai_seal_writer',
         'public.'||table_name,'UPDATE')
    THEN RAISE EXCEPTION 'ai_v4_sealer_control_column_write_drift'; END IF;
  END LOOP;
  IF EXISTS (SELECT 1 FROM pg_catalog.pg_attribute attribute
       WHERE attribute.attrelid='public.ai_write_authority'::regclass
         AND attribute.attnum>0 AND NOT attribute.attisdropped
         AND has_column_privilege('teruisi_ai_seal_writer',
           'public.ai_write_authority',attribute.attname,'SELECT')
           IS DISTINCT FROM (attribute.attname=ANY(ARRAY[
             'id','status','authority_epoch','cutover_id'])))
     OR EXISTS (SELECT 1 FROM pg_catalog.pg_attribute attribute
       WHERE attribute.attrelid='public.ai_data_revisions'::regclass
         AND attribute.attnum>0 AND NOT attribute.attisdropped
         AND has_column_privilege('teruisi_ai_seal_writer',
           'public.ai_data_revisions',attribute.attname,'SELECT')
           IS DISTINCT FROM (attribute.attname=ANY(ARRAY[
             'domain','revision','source_digest'])))
  THEN RAISE EXCEPTION 'ai_v4_sealer_control_column_acl_drift'; END IF;
  SELECT * INTO parent FROM public.ai_business_v4_runs
    WHERE id=selected_run;
  SELECT * INTO attempt FROM public.ai_business_v4_validation_attempts
    WHERE id=selected_attempt;
  IF parent.id IS NULL OR attempt.id IS NULL
     OR parent.id<>attempt.run_id OR parent.owner_email<>selected_actor
     OR parent.scope_json<>'null' OR parent.collection_status<>'manual'
     OR parent.status NOT IN ('collecting','sealed')
     OR attempt.actor_email<>selected_actor
     OR attempt.actor_version<>selected_actor_version
     OR attempt.plan_digest<>parent.plan_digest
     OR attempt.run_version IS DISTINCT FROM
       (CASE WHEN parent.status='sealed' THEN parent.version-1
             ELSE parent.version END)
     OR NOT EXISTS (SELECT 1 FROM public.access_control_users account
       WHERE account.email=selected_actor AND account.role='admin'
         AND account.status='active' AND account.scope IS NULL
         AND account.version=selected_actor_version)
     OR (SELECT latest.id FROM public.ai_business_v4_validation_attempts latest
       WHERE latest.run_id=parent.id
       ORDER BY latest.created_at DESC,latest.id DESC LIMIT 1)
       IS DISTINCT FROM attempt.id
  THEN RAISE EXCEPTION 'ai_v4_sealer_ledger_actor_or_run_invalid'; END IF;
  run_id:=parent.id; attempt_id:=attempt.id;
  parent_version:=parent.version;
  directory_digest:=attempt.directory_digest;
  SELECT count(*) INTO source_count FROM public.ai_business_v4_sources source
    WHERE source.run_id=parent.id AND source.finished;
  IF source_count NOT BETWEEN 2 AND 4 OR source_count<>
      (SELECT count(*) FROM public.ai_business_v4_sources source
       WHERE source.run_id=parent.id)
  THEN RAISE EXCEPTION 'ai_v4_sealer_ledger_sources_unfinished'; END IF;
  RETURN NEXT;
END $$"""


AUDIT_SEGMENT = """CREATE FUNCTION public.ai_v4_sealer_receipt_audit_segment(
  selected_run text,selected_attempt text,selected_source text,
  selected_segment integer,selected_actor text,selected_actor_version bigint)
RETURNS TABLE(sequence bigint,audit_id text,request_id text,invocation_id text,
  actor_email text,actor_role text,tool_name text,arguments_digest text,
  response_digest text,created_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE segment public.ai_business_v4_validation_segments%ROWTYPE;
  item record; expected bigint; seen bigint:=0;
BEGIN
  PERFORM 1 FROM public.ai_v4_sealer_ledger_read_gate(selected_run,
    selected_attempt,selected_actor,selected_actor_version);
  SELECT * INTO segment FROM public.ai_business_v4_validation_segments
    WHERE attempt_id=selected_attempt AND run_id=selected_run
      AND source_id=selected_source AND segment_index=selected_segment;
  IF segment.id IS NULL OR segment.end_sequence<segment.start_sequence
     OR segment.end_sequence-segment.start_sequence>=16
  THEN RAISE EXCEPTION 'ai_v4_sealer_ledger_segment_invalid'; END IF;
  expected:=segment.end_sequence-segment.start_sequence+1;
  FOR item IN SELECT receipt.sequence AS page_sequence,
       audit.id AS page_audit_id,audit.request_id AS page_request_id,
       audit.invocation_id AS page_invocation_id,
       audit.actor_email AS page_actor_email,
       audit.actor_role AS page_actor_role,
       audit.tool_name AS page_tool_name,
       audit.arguments_json AS page_arguments_json,
       audit.response_digest AS page_response_digest,
       audit.created_at AS page_created_at
    FROM public.ai_business_v4_tool_receipts receipt
    JOIN public.ai_tool_audit_logs audit ON audit.id=receipt.audit_id
    WHERE receipt.run_id=selected_run AND receipt.source_id=selected_source
      AND receipt.sequence BETWEEN segment.start_sequence AND segment.end_sequence
      AND receipt.actor_email=selected_actor
      AND receipt.request_id=audit.request_id
      AND receipt.invocation_id=audit.invocation_id
      AND receipt.tool_name=audit.tool_name
      AND receipt.response_digest=audit.response_digest
      AND audit.actor_email=selected_actor AND audit.actor_role='admin'
      AND audit.surface='business_collection' AND audit.status='succeeded'
      AND audit.error_code IS NULL
    ORDER BY receipt.sequence LOOP
    seen:=seen+1;
    IF item.page_sequence<>segment.start_sequence+seen-1
       OR octet_length(item.page_arguments_json)>256
       OR jsonb_typeof(item.page_arguments_json::jsonb) IS DISTINCT FROM 'object'
       OR item.page_arguments_json IS DISTINCT FROM
         ('{"argumentsDigest":"'||
          (item.page_arguments_json::jsonb->>'argumentsDigest')||'"}')
       OR (item.page_arguments_json::jsonb->>'argumentsDigest') !~ '^[0-9a-f]{64}$'
    THEN RAISE EXCEPTION 'ai_v4_sealer_ledger_audit_invalid'; END IF;
    sequence:=item.page_sequence; audit_id:=item.page_audit_id;
    request_id:=item.page_request_id;
    invocation_id:=item.page_invocation_id;
    actor_email:=item.page_actor_email; actor_role:=item.page_actor_role;
    tool_name:=item.page_tool_name;
    arguments_digest:=item.page_arguments_json::jsonb->>'argumentsDigest';
    response_digest:=item.page_response_digest;
    created_at:=item.page_created_at;
    RETURN NEXT;
  END LOOP;
  IF seen<>expected THEN RAISE EXCEPTION 'ai_v4_sealer_ledger_audit_missing'; END IF;
END $$"""


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
            "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
            "WHERE rolname='teruisi_ai_seal_writer'")
        if cursor.fetchone() != (False, False, False, False, False, False, False):
            raise RuntimeError("0039需要0038预建的精确NOLOGIN seal_writer")
        for table in LEDGER:
            cursor.execute("SELECT EXISTS(SELECT 1 FROM "
                "unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE']) "
                "permission WHERE has_table_privilege(%s,%s,permission))",
                [ROLE, "public." + table])
            if cursor.fetchone()[0]:
                raise RuntimeError("0039前驱seal_writer账本ACL已漂移")
            cursor.execute("GRANT SELECT ON public." + table + " TO " + ROLE)
        for table, columns in (("ai_write_authority", AUTHORITY_COLUMNS),
                ("ai_data_revisions", REVISION_COLUMNS)):
            cursor.execute("SELECT EXISTS(SELECT 1 FROM "
                "unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE']) "
                "permission WHERE has_table_privilege(%s,%s,permission))",
                [ROLE, "public." + table])
            if cursor.fetchone()[0]:
                raise RuntimeError("0039前驱seal_writer控制ACL已漂移")
            cursor.execute("GRANT SELECT (" + ",".join(columns) + ") ON public."
                + table + " TO " + ROLE)
        cursor.execute("SELECT has_any_column_privilege(%s,'public.ai_tool_audit_logs','SELECT')",
            [ROLE])
        if cursor.fetchone()[0]:
            raise RuntimeError("0039不得给seal_writer直接读取跨用途审计参数")
        cursor.execute("SELECT has_any_column_privilege(%s,"
            "'public.access_control_users','SELECT')", [ROLE])
        if cursor.fetchone()[0]:
            raise RuntimeError("0039不得给seal_writer直接遍历账号")
        cursor.execute(READ_GATE)
        cursor.execute(AUDIT_SEGMENT)
        for signature in (
                "public.ai_v4_sealer_ledger_read_gate(text,text,text,bigint)",
                "public.ai_v4_sealer_receipt_audit_segment(text,text,text,integer,text,bigint)"):
            cursor.execute("REVOKE ALL ON FUNCTION " + signature + " FROM PUBLIC")
            cursor.execute("GRANT EXECUTE ON FUNCTION " + signature + " TO " + ROLE)


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("DROP FUNCTION public.ai_v4_sealer_receipt_audit_segment("
            "text,text,text,integer,text,bigint)")
        cursor.execute("DROP FUNCTION public.ai_v4_sealer_ledger_read_gate("
            "text,text,text,bigint)")
        for table in LEDGER:
            cursor.execute("REVOKE SELECT ON public." + table + " FROM " + ROLE)
        for table, columns in (("ai_write_authority", AUTHORITY_COLUMNS),
                ("ai_data_revisions", REVISION_COLUMNS)):
            cursor.execute("REVOKE SELECT (" + ",".join(columns) + ") ON public."
                + table + " FROM " + ROLE)


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0038_business_v4_seal_writer_gate")]
    operations = [migrations.RunPython(install, uninstall)]
