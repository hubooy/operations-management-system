"""Default-closed, run-selected v4 sealer reads; no LOGIN or capability grant."""
from importlib import import_module

from django.db import migrations


previous = import_module("ai_assistant.migrations.0039_business_v4_sealer_ledger_read")
ROLE = "teruisi_ai_seal_writer"
PHYSICAL = previous.LEDGER

CONTEXT = """CREATE FUNCTION public.ai_v4_sealer_read_context(
  selected_run text,selected_attempt text,selected_actor text,
  selected_actor_version bigint)
RETURNS TABLE(run_id text,attempt_id text,parent_status text,
  parent_version bigint,plan_json text,plan_digest text,
  directory_digest text,key_id text,source_count bigint,
  seal_body_json text,seal_body_digest text,seal_body_mac text,
  run_bound_capability_verified boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE parent public.ai_business_v4_runs%ROWTYPE;
  attempt public.ai_business_v4_validation_attempts%ROWTYPE;
  seal public.ai_business_v4_seals%ROWTYPE; table_name text;
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
  THEN RAISE EXCEPTION 'ai_v4_sealer_read_role_denied'; END IF;
  IF NOT has_schema_privilege('teruisi_ai_seal_writer','public','USAGE')
     OR NOT has_function_privilege('teruisi_ai_seal_writer',
       'public.ai_v4_lock_source_revisions_for_admission()','EXECUTE')
  THEN RAISE EXCEPTION 'ai_v4_sealer_read_base_acl_missing'; END IF;
  FOREACH table_name IN ARRAY ARRAY[
    'ai_business_v4_runs','ai_business_v4_sources'] LOOP
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
    THEN RAISE EXCEPTION 'ai_v4_sealer_read_base_acl_drift'; END IF;
  END LOOP;
  FOREACH table_name IN ARRAY ARRAY[
    'ai_business_v4_chunks','ai_business_v4_tool_receipts',
    'ai_business_v4_validation_attempts',
    'ai_business_v4_validation_segments','ai_business_v4_seals',
    'ai_tool_audit_logs','access_control_users'] LOOP
    IF has_any_column_privilege('teruisi_ai_seal_writer',
         'public.'||table_name,'SELECT')
       OR has_any_column_privilege('teruisi_ai_seal_writer',
         'public.'||table_name,'INSERT')
       OR has_any_column_privilege('teruisi_ai_seal_writer',
         'public.'||table_name,'UPDATE')
       OR EXISTS (SELECT 1 FROM unnest(ARRAY[
         'SELECT','INSERT','UPDATE','DELETE','TRUNCATE']) permission
         WHERE has_table_privilege('teruisi_ai_seal_writer',
           'public.'||table_name,permission))
    THEN RAISE EXCEPTION 'ai_v4_sealer_read_physical_acl_drift'; END IF;
  END LOOP;
  FOREACH table_name IN ARRAY ARRAY[
    'finance_lines','finance_months','finance_import_batches',
    'finance_data_revisions','finance_source_revision_markers',
    'netshop_rows','netshop_import_batches',
    'netshop_data_revisions','netshop_source_revision_markers'] LOOP
    IF has_any_column_privilege('teruisi_ai_seal_writer',
         'public.'||table_name,'SELECT')
       OR has_any_column_privilege('teruisi_ai_seal_writer',
         'public.'||table_name,'INSERT')
       OR has_any_column_privilege('teruisi_ai_seal_writer',
         'public.'||table_name,'UPDATE')
       OR EXISTS (SELECT 1 FROM unnest(ARRAY[
         'SELECT','INSERT','UPDATE','DELETE','TRUNCATE']) permission
         WHERE has_table_privilege('teruisi_ai_seal_writer',
           'public.'||table_name,permission))
    THEN RAISE EXCEPTION 'ai_v4_sealer_read_business_acl_drift'; END IF;
  END LOOP;
  FOREACH table_name IN ARRAY ARRAY[
    'ai_write_authority','ai_data_revisions'] LOOP
    IF has_any_column_privilege('teruisi_ai_seal_writer',
         'public.'||table_name,'INSERT')
       OR has_any_column_privilege('teruisi_ai_seal_writer',
         'public.'||table_name,'UPDATE')
       OR EXISTS (SELECT 1 FROM unnest(ARRAY[
         'INSERT','UPDATE','DELETE','TRUNCATE']) permission
         WHERE has_table_privilege('teruisi_ai_seal_writer',
           'public.'||table_name,permission))
    THEN RAISE EXCEPTION 'ai_v4_sealer_read_control_acl_drift'; END IF;
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
  THEN RAISE EXCEPTION 'ai_v4_sealer_read_control_column_acl_drift'; END IF;
  SELECT * INTO parent FROM public.ai_business_v4_runs selected_parent
    WHERE selected_parent.id=selected_run;
  SELECT * INTO attempt FROM public.ai_business_v4_validation_attempts selected_validation
    WHERE selected_validation.id=selected_attempt;
  SELECT * INTO seal FROM public.ai_business_v4_seals selected_seal
    WHERE selected_seal.run_id=selected_run;
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
     OR (parent.status='sealed' AND
       (seal.run_id IS NULL OR seal.attempt_id<>attempt.id
         OR seal.evidence_version<>parent.version))
     OR (parent.status='collecting' AND seal.run_id IS NOT NULL)
     OR octet_length(parent.plan_json)>131072
  THEN RAISE EXCEPTION 'ai_v4_sealer_read_actor_or_run_invalid'; END IF;
  run_id:=parent.id; attempt_id:=attempt.id;
  parent_status:=parent.status; parent_version:=parent.version;
  plan_json:=parent.plan_json; plan_digest:=parent.plan_digest;
  directory_digest:=attempt.directory_digest; key_id:=attempt.key_id;
  SELECT count(*) INTO source_count FROM public.ai_business_v4_sources source
    WHERE source.run_id=parent.id AND source.finished;
  IF source_count NOT BETWEEN 2 AND 4 OR source_count<>
      (SELECT count(*) FROM public.ai_business_v4_sources source
       WHERE source.run_id=parent.id)
  THEN RAISE EXCEPTION 'ai_v4_sealer_read_sources_unfinished'; END IF;
  seal_body_json:=seal.body_json; seal_body_digest:=seal.body_digest;
  seal_body_mac:=seal.body_mac;
  run_bound_capability_verified:=false;
  IF NOT EXISTS (SELECT 1 FROM public.access_control_users account
       WHERE account.email=selected_actor AND account.role='admin'
         AND account.status='active' AND account.scope IS NULL
         AND account.version=selected_actor_version)
     OR (SELECT latest.id FROM public.ai_business_v4_validation_attempts latest
       WHERE latest.run_id=parent.id ORDER BY latest.created_at DESC,latest.id DESC
       LIMIT 1) IS DISTINCT FROM attempt.id
     OR (SELECT current_parent.version FROM public.ai_business_v4_runs current_parent
       WHERE current_parent.id=parent.id) IS DISTINCT FROM parent.version
  THEN RAISE EXCEPTION 'ai_v4_sealer_read_late_cas_invalid'; END IF;
  RETURN NEXT;
END $$"""


SEGMENT = """CREATE FUNCTION public.ai_v4_sealer_read_segment(
  selected_run text,selected_attempt text,selected_source text,
  selected_index integer,selected_actor text,selected_actor_version bigint)
RETURNS TABLE(segment_id text,source_version bigint,source_ref text,
  source_revision text,start_sequence integer,end_sequence integer,
  previous_segment_digest text,progress_json text,progress_digest text,
  proof_digest text,proof_mac text,run_bound_capability_verified boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE source public.ai_business_v4_sources%ROWTYPE;
  segment public.ai_business_v4_validation_segments%ROWTYPE;
BEGIN
  PERFORM 1 FROM public.ai_v4_sealer_read_context(selected_run,
    selected_attempt,selected_actor,selected_actor_version);
  SELECT * INTO source FROM public.ai_business_v4_sources chosen_source
    WHERE chosen_source.id=selected_source AND chosen_source.run_id=selected_run;
  SELECT * INTO STRICT segment FROM public.ai_business_v4_validation_segments chosen_segment
    WHERE chosen_segment.attempt_id=selected_attempt
      AND chosen_segment.run_id=selected_run
      AND chosen_segment.source_id=selected_source
      AND chosen_segment.segment_index=selected_index;
  IF source.id IS NULL OR NOT source.finished OR segment.id IS NULL
     OR selected_index NOT BETWEEN 1 AND 1024
     OR source.version<>source.page_count+1
     OR segment.start_sequence<>(selected_index-1)*16+1
     OR segment.end_sequence<>least(selected_index*16,source.page_count)
     OR segment.end_sequence<segment.start_sequence
     OR segment.source_version<>source.version
     OR segment.source_ref<>source.source_ref
     OR segment.source_revision<>source.source_revision
     OR octet_length(segment.progress_json)>32768
     OR segment.progress_digest IS DISTINCT FROM
       encode(sha256(convert_to(segment.progress_json,'UTF8')),'hex')
     OR segment.proof_digest !~ '^[0-9a-f]{64}$'
     OR segment.proof_mac !~ '^[0-9a-f]{64}$'
  THEN RAISE EXCEPTION 'ai_v4_sealer_read_segment_invalid'; END IF;
  segment_id:=segment.id; source_version:=source.version;
  source_ref:=source.source_ref; source_revision:=source.source_revision;
  start_sequence:=segment.start_sequence; end_sequence:=segment.end_sequence;
  previous_segment_digest:=segment.previous_segment_digest;
  progress_json:=segment.progress_json; progress_digest:=segment.progress_digest;
  proof_digest:=segment.proof_digest; proof_mac:=segment.proof_mac;
  run_bound_capability_verified:=false;
  PERFORM 1 FROM public.ai_v4_sealer_read_context(selected_run,
    selected_attempt,selected_actor,selected_actor_version);
  RETURN NEXT;
END $$"""


PAGE = """CREATE FUNCTION public.ai_v4_sealer_read_page(
  selected_run text,selected_attempt text,selected_source text,
  selected_sequence bigint,selected_actor text,selected_actor_version bigint)
RETURNS TABLE(source_key text,domain text,source_version bigint,
  source_ref text,source_revision text,segment_id text,
  chunk_id text,page_sequence bigint,row_count integer,
  payload_json text,payload_digest text,payload_bytes integer,
  audit_id text,request_id text,invocation_id text,tool_name text,
  arguments_digest text,response_digest text,audit_created_at timestamptz,
  run_bound_capability_verified boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE source public.ai_business_v4_sources%ROWTYPE;
  segment public.ai_business_v4_validation_segments%ROWTYPE;
  chunk public.ai_business_v4_chunks%ROWTYPE;
  receipt public.ai_business_v4_tool_receipts%ROWTYPE;
  audit public.ai_tool_audit_logs%ROWTYPE;
  actual_bytes integer; actual_digest text; expected_tool text;
  max_bytes integer;
BEGIN
  PERFORM 1 FROM public.ai_v4_sealer_read_context(selected_run,
    selected_attempt,selected_actor,selected_actor_version);
  IF selected_sequence NOT BETWEEN 1 AND 16384
  THEN RAISE EXCEPTION 'ai_v4_sealer_read_page_sequence_invalid'; END IF;
  SELECT * INTO source FROM public.ai_business_v4_sources chosen_source
    WHERE chosen_source.id=selected_source AND chosen_source.run_id=selected_run;
  SELECT * INTO STRICT segment FROM public.ai_business_v4_validation_segments chosen_segment
    WHERE chosen_segment.attempt_id=selected_attempt
      AND chosen_segment.run_id=selected_run
      AND chosen_segment.source_id=selected_source
      AND chosen_segment.segment_index=((selected_sequence-1)/16+1);
  SELECT * INTO STRICT chunk FROM public.ai_business_v4_chunks chosen_chunk
    WHERE chosen_chunk.run_id=selected_run
      AND chosen_chunk.source_id=selected_source
      AND chosen_chunk.sequence=selected_sequence;
  SELECT * INTO STRICT receipt FROM public.ai_business_v4_tool_receipts chosen_receipt
    WHERE chosen_receipt.run_id=selected_run
      AND chosen_receipt.source_id=selected_source
      AND chosen_receipt.sequence=selected_sequence;
  IF receipt.audit_id IS NOT NULL THEN
    SELECT * INTO audit FROM public.ai_tool_audit_logs chosen_audit
      WHERE chosen_audit.id=receipt.audit_id;
  END IF;
  IF source.id IS NULL OR NOT source.finished OR segment.id IS NULL
     OR chunk.id IS NULL OR receipt.chunk_id IS NULL OR audit.id IS NULL
     OR selected_sequence>source.page_count
     OR source.version<>source.page_count+1
     OR selected_sequence NOT BETWEEN segment.start_sequence AND segment.end_sequence
     OR segment.source_version<>source.version
     OR segment.source_ref<>source.source_ref
     OR segment.source_revision<>source.source_revision
     OR chunk.source_ref<>source.source_ref
     OR chunk.source_revision<>source.source_revision
     OR receipt.chunk_id<>chunk.id OR receipt.actor_email<>selected_actor
     OR receipt.request_id<>audit.request_id
     OR receipt.invocation_id<>audit.invocation_id
     OR receipt.tool_name<>audit.tool_name
     OR receipt.surface<>'business_collection'
     OR receipt.response_digest IS DISTINCT FROM audit.response_digest
     OR audit.actor_email<>selected_actor OR audit.actor_role<>'admin'
     OR audit.surface<>'business_collection'
     OR audit.status<>'succeeded' OR audit.error_code IS NOT NULL
     OR audit.created_at>chunk.created_at
     OR chunk.created_at>receipt.created_at
     OR chunk.row_count>100 OR chunk.row_count<0
  THEN RAISE EXCEPTION 'ai_v4_sealer_read_page_identity_invalid'; END IF;
  expected_tool:=(CASE WHEN source.domain='finance'
    THEN 'get_business_finance_source_page'
    WHEN source.domain='netshop' AND selected_sequence=1
    THEN 'get_business_source_page'
    WHEN source.domain='netshop'
    THEN 'get_business_netshop_continuation_page'
    ELSE NULL END);
  actual_bytes:=octet_length(chunk.payload_json);
  max_bytes:=(CASE WHEN source.domain='finance' THEN 38000 ELSE 131072 END);
  IF actual_bytes NOT BETWEEN 1 AND max_bytes
  THEN RAISE EXCEPTION 'ai_v4_sealer_read_page_capacity_invalid'; END IF;
  actual_digest:=encode(sha256(convert_to(chunk.payload_json,'UTF8')),'hex');
  IF expected_tool IS NULL OR receipt.tool_name<>expected_tool
     OR chunk.payload_digest IS DISTINCT FROM actual_digest
     OR receipt.payload_bytes IS DISTINCT FROM actual_bytes
     OR receipt.response_digest IS DISTINCT FROM actual_digest
     OR audit.response_digest IS DISTINCT FROM actual_digest
     OR octet_length(audit.arguments_json)>256
     OR audit.arguments_json IS DISTINCT FROM
       ('{"argumentsDigest":"'||
        (audit.arguments_json::jsonb->>'argumentsDigest')||'"}')
     OR (audit.arguments_json::jsonb->>'argumentsDigest') !~ '^[0-9a-f]{64}$'
  THEN RAISE EXCEPTION 'ai_v4_sealer_read_page_digest_invalid'; END IF;
  source_key:=source.source_key; domain:=source.domain;
  source_version:=source.version; source_ref:=source.source_ref;
  source_revision:=source.source_revision; segment_id:=segment.id;
  chunk_id:=chunk.id; page_sequence:=chunk.sequence; row_count:=chunk.row_count;
  payload_json:=chunk.payload_json; payload_digest:=actual_digest;
  payload_bytes:=actual_bytes; audit_id:=audit.id;
  request_id:=audit.request_id; invocation_id:=audit.invocation_id;
  tool_name:=audit.tool_name;
  arguments_digest:=audit.arguments_json::jsonb->>'argumentsDigest';
  response_digest:=audit.response_digest; audit_created_at:=audit.created_at;
  run_bound_capability_verified:=false;
  PERFORM 1 FROM public.ai_v4_sealer_read_context(selected_run,
    selected_attempt,selected_actor,selected_actor_version);
  RETURN NEXT;
END $$"""


SIGNATURES = (
    "public.ai_v4_sealer_read_context(text,text,text,bigint)",
    "public.ai_v4_sealer_read_segment(text,text,text,integer,text,bigint)",
    "public.ai_v4_sealer_read_page(text,text,text,bigint,text,bigint)",
)


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper FROM pg_catalog.pg_roles "
            "WHERE rolname='teruisi_ai_seal_writer'")
        if cursor.fetchone() != (False, False, False):
            raise RuntimeError("0040只接受0039默认NOLOGIN隔离封存身份")
        cursor.execute("DROP FUNCTION public.ai_v4_sealer_receipt_audit_segment("
            "text,text,text,integer,text,bigint)")
        cursor.execute("DROP FUNCTION public.ai_v4_sealer_ledger_read_gate("
            "text,text,text,bigint)")
        for table in PHYSICAL:
            cursor.execute("REVOKE SELECT ON public." + table + " FROM " + ROLE)
        for definition in (CONTEXT, SEGMENT, PAGE):
            cursor.execute(definition)
        for signature in SIGNATURES:
            cursor.execute("REVOKE ALL ON FUNCTION " + signature + " FROM PUBLIC")
            cursor.execute("GRANT EXECUTE ON FUNCTION " + signature + " TO " + ROLE)


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for signature in reversed(SIGNATURES):
            cursor.execute("DROP FUNCTION " + signature)
        for table in PHYSICAL:
            cursor.execute("GRANT SELECT ON public." + table + " TO " + ROLE)
        cursor.execute(previous.READ_GATE)
        cursor.execute(previous.AUDIT_SEGMENT)
        for signature in (
                "public.ai_v4_sealer_ledger_read_gate(text,text,text,bigint)",
                "public.ai_v4_sealer_receipt_audit_segment(text,text,text,integer,text,bigint)"):
            cursor.execute("REVOKE ALL ON FUNCTION " + signature + " FROM PUBLIC")
            cursor.execute("GRANT EXECUTE ON FUNCTION " + signature + " TO " + ROLE)


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0039_business_v4_sealer_ledger_read")]
    operations = [migrations.RunPython(install, uninstall)]
