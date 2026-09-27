"""Test-only 0075 report-bound read of one persisted sealed-v4 promotion page.

The legacy v4 seal explicitly denies report generation. This function returns
source material only; it does not change that seal, issue authority, or create
an Agent/file receipt. Its database and session checks are intentionally too
strict for a formal installation.
"""

from . import business_v4_report_link_sql as legacy

READER = "teruisi_ai_reader"
WRITER = "teruisi_ai_writer"
RO_BINDINGS = "public.ai_v4_report_source_bindings_ro(text)"
RO_READ = "public.ai_v4_read_report_source_link_ro(text,text,bigint)"
PAGE = "public.ai_v4_read_report_promotion_page(text,text,text,bigint,text,bigint)"

# The legacy SQL cannot run inside the reader role's read-only transaction:
# its two revision probes use FOR SHARE. Derive a new, versioned read-only
# copy from that frozen contract; never edit the 0071 functions or turn off
# transaction_read_only. The double page pass and final current-state check
# remain necessary because an unlocked revision read is not linearizable.
assert legacy.BINDINGS_SQL.count(" FOR SHARE") == 2
assert legacy.READ_SQL.count("public.ai_v4_report_source_bindings(v4.id)") == 1
RO_BINDINGS_SQL = (legacy.BINDINGS_SQL.replace(
    "CREATE FUNCTION public.ai_v4_report_source_bindings(",
    "CREATE FUNCTION public.ai_v4_report_source_bindings_ro(", 1)
    .replace(" FOR SHARE", ""))
RO_READ_SQL = (legacy.READ_SQL.replace(
    "CREATE FUNCTION public.ai_v4_read_report_source_link(",
    "CREATE FUNCTION public.ai_v4_read_report_source_link_ro(", 1)
    .replace("public.ai_v4_report_source_bindings(v4.id)",
        "public.ai_v4_report_source_bindings_ro(v4.id)"))

PAGE_SQL = """CREATE FUNCTION public.ai_v4_read_report_promotion_page(
  selected_report text,selected_run text,selected_source text,
  selected_sequence bigint,actor_email text,expected_actor_version bigint)
RETURNS TABLE(page_sequence bigint,payload_json text,payload_digest text,
  source_ref text,source_revision text,row_count integer,
  receipt_response_digest text,audit_response_digest text,
  request_arguments_digest text,tool_name text,audit_id text,
  invocation_id text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE linked jsonb; bindings jsonb; bound_source jsonb;
  source public.ai_business_v4_sources%ROWTYPE;
  chunk public.ai_business_v4_chunks%ROWTYPE;
  receipt public.ai_business_v4_tool_receipts%ROWTYPE;
  audit public.ai_tool_audit_logs%ROWTYPE;
  actual_digest text; expected_tool text;
BEGIN
  IF session_user IS DISTINCT FROM 'teruisi_ai_reader'
     OR current_database() IS DISTINCT FROM 'test_teruisi_ai_rehearsal'
     OR pg_catalog.inet_server_addr() IS DISTINCT FROM '127.0.0.1'::inet
     OR pg_catalog.inet_server_port() NOT BETWEEN 55440 AND 55999
     OR pg_catalog.current_setting('transaction_read_only') IS DISTINCT FROM 'on'
  THEN RAISE EXCEPTION 'ai_v4_report_page_test_reader_only'; END IF;
  IF selected_sequence NOT BETWEEN 1 AND 16384
  THEN RAISE EXCEPTION 'ai_v4_report_page_sequence_invalid'; END IF;
  linked:=public.ai_v4_read_report_source_link_ro(selected_report,
    actor_email,expected_actor_version);
  bindings:=linked->'sourceBindings';
  IF linked->>'v4RunId' IS DISTINCT FROM selected_run
     OR linked->'creationTimeLinkPersisted' IS DISTINCT FROM 'true'::jsonb
     OR linked->'authorityVerified' IS DISTINCT FROM 'false'::jsonb
     OR linked->'reportGenerationSupported' IS DISTINCT FROM 'false'::jsonb
     OR jsonb_array_length(bindings->'sources') IS DISTINCT FROM 4
  THEN RAISE EXCEPTION 'ai_v4_report_page_link_invalid'; END IF;
  SELECT * INTO source FROM public.ai_business_v4_sources s
    WHERE s.id=selected_source AND s.run_id=selected_run;
  IF source.id IS NULL OR source.domain IS DISTINCT FROM 'netshop'
     OR source.temporal_role IS DISTINCT FROM 'daily_fact'
     OR source.ordinal NOT BETWEEN 1 AND 4
     OR NOT source.finished OR source.version IS DISTINCT FROM source.page_count+1
     OR selected_sequence>source.page_count
  THEN RAISE EXCEPTION 'ai_v4_report_page_source_invalid'; END IF;
  bound_source:=bindings->'sources'->(source.ordinal-1);
  IF bound_source->>'sourceKey' IS DISTINCT FROM source.source_key
     OR bound_source->>'ordinal' IS DISTINCT FROM source.ordinal::text
     OR bound_source->>'domain' IS DISTINCT FROM 'netshop'
     OR bound_source->>'window' IS NULL
     OR bound_source->>'window' NOT IN ('current','previous','yearAgo')
     OR bound_source->>'queryDigest' IS DISTINCT FROM source.query_digest
     OR bound_source->>'sourceIdentityDigest' IS DISTINCT FROM source.source_identity_digest
     OR bound_source->>'sourceRef' IS DISTINCT FROM source.source_ref
     OR bound_source->>'sourceRevision' IS DISTINCT FROM source.source_revision
     OR bound_source->>'sourceVersion' IS DISTINCT FROM source.version::text
     OR bound_source->>'pageCount' IS DISTINCT FROM source.page_count::text
     OR bound_source->>'rowCount' IS DISTINCT FROM source.row_count::text
     OR bound_source->>'storedBytes' IS DISTINCT FROM source.stored_bytes::text
     OR source.query_json::jsonb->>'window' IS DISTINCT FROM bound_source->>'window'
  THEN RAISE EXCEPTION 'ai_v4_report_page_binding_invalid'; END IF;
  SELECT * INTO chunk FROM public.ai_business_v4_chunks c
    WHERE c.run_id=selected_run AND c.source_id=selected_source
      AND c.sequence=selected_sequence;
  SELECT * INTO receipt FROM public.ai_business_v4_tool_receipts r
    WHERE r.run_id=selected_run AND r.source_id=selected_source
      AND r.sequence=selected_sequence;
  IF receipt.audit_id IS NOT NULL THEN
    SELECT * INTO audit FROM public.ai_tool_audit_logs a
      WHERE a.id=receipt.audit_id;
  END IF;
  IF chunk.id IS NULL OR receipt.chunk_id IS NULL OR audit.id IS NULL
     OR receipt.chunk_id IS DISTINCT FROM chunk.id
     OR chunk.source_ref IS DISTINCT FROM source.source_ref
     OR chunk.source_revision IS DISTINCT FROM source.source_revision
     OR chunk.row_count NOT BETWEEN 0 AND 100
     OR receipt.actor_email IS DISTINCT FROM actor_email
     OR receipt.surface IS DISTINCT FROM 'business_collection'
     OR receipt.request_id IS DISTINCT FROM audit.request_id
     OR receipt.invocation_id IS DISTINCT FROM audit.invocation_id
     OR receipt.tool_name IS DISTINCT FROM audit.tool_name
     OR audit.actor_email IS DISTINCT FROM actor_email
     OR audit.actor_role IS DISTINCT FROM 'admin'
     OR audit.surface IS DISTINCT FROM 'business_collection'
     OR audit.status IS DISTINCT FROM 'succeeded'
     OR audit.error_code IS NOT NULL
     OR audit.created_at>chunk.created_at
     OR chunk.created_at>receipt.created_at
  THEN RAISE EXCEPTION 'ai_v4_report_page_audit_invalid'; END IF;
  expected_tool:=(CASE WHEN selected_sequence=1 THEN
    'get_business_source_page' ELSE
    'get_business_netshop_continuation_page' END);
  actual_digest:=encode(sha256(convert_to(chunk.payload_json,'UTF8')),'hex');
  IF pg_catalog.octet_length(chunk.payload_json) NOT BETWEEN 1 AND 131072
     OR receipt.tool_name IS DISTINCT FROM expected_tool
     OR chunk.payload_digest IS DISTINCT FROM actual_digest
     OR receipt.payload_bytes IS DISTINCT FROM pg_catalog.octet_length(chunk.payload_json)
     OR receipt.response_digest IS DISTINCT FROM actual_digest
     OR audit.response_digest IS DISTINCT FROM actual_digest
     OR pg_catalog.octet_length(audit.arguments_json)>256
     OR audit.arguments_json IS DISTINCT FROM
       ('{"argumentsDigest":"'||
         (audit.arguments_json::jsonb->>'argumentsDigest')||'"}')
     OR (audit.arguments_json::jsonb->>'argumentsDigest') !~ '^[0-9a-f]{64}$'
  THEN RAISE EXCEPTION 'ai_v4_report_page_digest_invalid'; END IF;
  -- A revision or report/link change during this call cannot return a page.
  IF pg_catalog.current_setting('transaction_read_only') IS DISTINCT FROM 'on'
     OR public.ai_v4_read_report_source_link_ro(selected_report,
      actor_email,expected_actor_version) IS DISTINCT FROM linked
  THEN RAISE EXCEPTION 'ai_v4_report_page_late_link_drift'; END IF;
  page_sequence:=chunk.sequence; payload_json:=chunk.payload_json;
  payload_digest:=actual_digest; source_ref:=source.source_ref;
  source_revision:=source.source_revision; row_count:=chunk.row_count;
  receipt_response_digest:=receipt.response_digest;
  audit_response_digest:=audit.response_digest;
  request_arguments_digest:=audit.arguments_json::jsonb->>'argumentsDigest';
  tool_name:=receipt.tool_name; audit_id:=audit.id;
  invocation_id:=receipt.invocation_id;
  RETURN NEXT;
END $$"""
