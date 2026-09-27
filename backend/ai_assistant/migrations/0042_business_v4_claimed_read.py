"""Claim-bound v4 page reads only; final seal consumption remains closed."""
from importlib import import_module

from django.db import migrations


previous = import_module("ai_assistant.migrations.0040_business_v4_sealer_narrow_stream")
old_context = previous.CONTEXT
acl_needle = """IF NOT has_schema_privilege('teruisi_ai_seal_writer','public','USAGE')
     OR NOT has_function_privilege('teruisi_ai_seal_writer',
       'public.ai_v4_lock_source_revisions_for_admission()','EXECUTE')
  THEN RAISE EXCEPTION 'ai_v4_sealer_read_base_acl_missing'; END IF;"""
if old_context.count(acl_needle) != 1:
    raise RuntimeError("0042 predecessor context ACL changed")
# 0041 revoked the sealer's direct probe EXECUTE. The retained 0040 context is
# callable only by this migration's SECURITY DEFINER wrappers, whose owner can
# invoke the probe without granting it to the session role.
internal_context = old_context.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1).replace(
    acl_needle,
    """IF NOT has_schema_privilege('teruisi_ai_seal_writer','public','USAGE')
  THEN RAISE EXCEPTION 'ai_v4_sealer_read_base_acl_missing'; END IF;""", 1)

ASSERT_CLAIM = """CREATE FUNCTION public.ai_v4_sealer_assert_claim(
  selected_run text,selected_attempt text,selected_actor text,
  selected_actor_version bigint,selected_nonce text,selected_claim text)
RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE ticket public.ai_business_v4_seal_tickets%ROWTYPE;
  claim public.ai_business_v4_seal_claims%ROWTYPE;
  parent public.ai_business_v4_runs%ROWTYPE;
  attempt public.ai_business_v4_validation_attempts%ROWTYPE;
  root text; sources bigint; guard_text text; guard_cutoff timestamptz;
BEGIN
  IF session_user<>'teruisi_ai_seal_writer'
     OR selected_nonce !~ '^[0-9a-f]{64}$'
     OR selected_claim !~ '^[0-9a-f]{64}$'
  THEN RAISE EXCEPTION 'ai_v4_claimed_read_role_or_token_invalid'; END IF;
  -- 0038's probe runs as this function owner while still checking the true
  -- seal-writer session and its current AI authority. It also holds both
  -- source revision row locks until this read statement/transaction ends.
  SELECT probe.guard_version,probe.guard_installed_at
    INTO guard_text,guard_cutoff
    FROM public.ai_v4_lock_source_revisions_for_admission() probe;
  IF guard_text IS DISTINCT FROM 'business-v4-source-write-fence-read-v1'
     OR guard_cutoff IS NULL
  THEN RAISE EXCEPTION 'ai_v4_claimed_read_source_fence_missing'; END IF;
  SELECT * INTO ticket FROM public.ai_business_v4_seal_tickets t
    WHERE t.nonce_hash=encode(sha256(convert_to(
      'v4-seal-ticket-v1:'||selected_nonce,'UTF8')),'hex');
  SELECT * INTO claim FROM public.ai_business_v4_seal_claims c
    WHERE c.ticket_id=ticket.id AND c.claim_hash=encode(sha256(convert_to(
      'v4-seal-claim-v1:'||selected_claim,'UTF8')),'hex');
  SELECT * INTO parent FROM public.ai_business_v4_runs p
    WHERE p.id=selected_run;
  SELECT * INTO attempt FROM public.ai_business_v4_validation_attempts a
    WHERE a.id=selected_attempt;
  IF ticket.id IS NULL OR claim.ticket_id IS NULL
     OR parent.id IS NULL OR attempt.id IS NULL
     OR ticket.run_id<>selected_run OR ticket.attempt_id<>selected_attempt
     OR ticket.actor_email<>selected_actor
     OR ticket.actor_version<>selected_actor_version
     OR parent.status<>'collecting' OR parent.collection_status<>'manual'
     OR parent.scope_json<>'null' OR parent.owner_email<>selected_actor
     OR parent.version<>ticket.parent_version
     OR parent.plan_digest<>ticket.plan_digest
     OR attempt.run_id<>parent.id OR attempt.run_version<>parent.version
     OR attempt.actor_email<>selected_actor
     OR attempt.actor_version<>selected_actor_version
     OR attempt.plan_digest<>parent.plan_digest
     OR attempt.directory_digest<>ticket.directory_digest
     OR attempt.created_at<=guard_cutoff
     OR claim.claimed_at<ticket.issued_at
     OR claim.claimed_at>=ticket.expires_at
     OR clock_timestamp()>=claim.lease_until
     OR (SELECT latest.id FROM public.ai_business_v4_validation_attempts latest
       WHERE latest.run_id=parent.id ORDER BY latest.created_at DESC,latest.id DESC
       LIMIT 1) IS DISTINCT FROM attempt.id
     OR NOT EXISTS (SELECT 1 FROM public.access_control_users account
       WHERE account.email=selected_actor AND account.role='admin'
         AND account.status='active' AND account.scope IS NULL
         AND account.version=selected_actor_version)
     OR EXISTS (SELECT 1 FROM public.ai_business_v4_seals seal
       WHERE seal.run_id=parent.id)
  THEN RAISE EXCEPTION 'ai_v4_claimed_read_binding_invalid'; END IF;
  SELECT actual.root_digest,actual.source_count INTO root,sources
    FROM public.ai_v4_seal_ticket_source_root(parent.id) actual;
  IF root IS DISTINCT FROM ticket.source_root
     OR sources IS DISTINCT FROM ticket.source_count
  THEN RAISE EXCEPTION 'ai_v4_claimed_read_source_drift'; END IF;
  RETURN ticket.id;
END $$"""

CONTEXT = """CREATE FUNCTION public.ai_v4_sealer_ticket_context(
  selected_run text,selected_attempt text,selected_actor text,
  selected_actor_version bigint,selected_nonce text,selected_claim text)
RETURNS TABLE(run_id text,attempt_id text,parent_status text,
  parent_version bigint,plan_json text,plan_digest text,
  directory_digest text,key_id text,source_count bigint,
  seal_body_json text,seal_body_digest text,seal_body_mac text,
  run_bound_capability_verified boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
  PERFORM public.ai_v4_sealer_assert_claim(selected_run,selected_attempt,
    selected_actor,selected_actor_version,selected_nonce,selected_claim);
  RETURN QUERY SELECT old.run_id,old.attempt_id,old.parent_status,
    old.parent_version,old.plan_json,old.plan_digest,old.directory_digest,
    old.key_id,old.source_count,old.seal_body_json,old.seal_body_digest,
    old.seal_body_mac,true
    FROM public.ai_v4_sealer_read_context(selected_run,selected_attempt,
      selected_actor,selected_actor_version) old;
  PERFORM public.ai_v4_sealer_assert_claim(selected_run,selected_attempt,
    selected_actor,selected_actor_version,selected_nonce,selected_claim);
END $$"""

SEGMENT = """CREATE FUNCTION public.ai_v4_sealer_ticket_segment(
  selected_run text,selected_attempt text,selected_source text,
  selected_index integer,selected_actor text,selected_actor_version bigint,
  selected_nonce text,selected_claim text)
RETURNS TABLE(segment_id text,source_version bigint,source_ref text,
  source_revision text,start_sequence integer,end_sequence integer,
  previous_segment_digest text,progress_json text,progress_digest text,
  proof_digest text,proof_mac text,run_bound_capability_verified boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
  PERFORM public.ai_v4_sealer_assert_claim(selected_run,selected_attempt,
    selected_actor,selected_actor_version,selected_nonce,selected_claim);
  RETURN QUERY SELECT old.segment_id,old.source_version,old.source_ref,
    old.source_revision,old.start_sequence,old.end_sequence,
    old.previous_segment_digest,old.progress_json,old.progress_digest,
    old.proof_digest,old.proof_mac,true
    FROM public.ai_v4_sealer_read_segment(selected_run,selected_attempt,
      selected_source,selected_index,selected_actor,selected_actor_version) old;
  PERFORM public.ai_v4_sealer_assert_claim(selected_run,selected_attempt,
    selected_actor,selected_actor_version,selected_nonce,selected_claim);
END $$"""

PAGE = """CREATE FUNCTION public.ai_v4_sealer_ticket_page(
  selected_run text,selected_attempt text,selected_source text,
  selected_sequence bigint,selected_actor text,selected_actor_version bigint,
  selected_nonce text,selected_claim text)
RETURNS TABLE(source_key text,domain text,source_version bigint,
  source_ref text,source_revision text,segment_id text,
  chunk_id text,page_sequence bigint,row_count integer,
  payload_json text,payload_digest text,payload_bytes integer,
  audit_id text,request_id text,invocation_id text,tool_name text,
  arguments_digest text,response_digest text,audit_created_at timestamptz,
  run_bound_capability_verified boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
  PERFORM public.ai_v4_sealer_assert_claim(selected_run,selected_attempt,
    selected_actor,selected_actor_version,selected_nonce,selected_claim);
  RETURN QUERY SELECT old.source_key,old.domain,old.source_version,
    old.source_ref,old.source_revision,old.segment_id,old.chunk_id,
    old.page_sequence,old.row_count,old.payload_json,old.payload_digest,
    old.payload_bytes,old.audit_id,old.request_id,old.invocation_id,
    old.tool_name,old.arguments_digest,old.response_digest,
    old.audit_created_at,true
    FROM public.ai_v4_sealer_read_page(selected_run,selected_attempt,
      selected_source,selected_sequence,selected_actor,selected_actor_version) old;
  PERFORM public.ai_v4_sealer_assert_claim(selected_run,selected_attempt,
    selected_actor,selected_actor_version,selected_nonce,selected_claim);
END $$"""

FUNCTIONS = (
    "public.ai_v4_sealer_assert_claim(text,text,text,bigint,text,text)",
    "public.ai_v4_sealer_ticket_context(text,text,text,bigint,text,text)",
    "public.ai_v4_sealer_ticket_segment(text,text,text,integer,text,bigint,text,text)",
    "public.ai_v4_sealer_ticket_page(text,text,text,bigint,text,bigint,text,text)",
)
OLD = (
    "public.ai_v4_sealer_read_context(text,text,text,bigint)",
    "public.ai_v4_sealer_read_segment(text,text,text,integer,text,bigint)",
    "public.ai_v4_sealer_read_page(text,text,text,bigint,text,bigint)",
    "public.ai_v4_commit_seal(text,text,bigint,text,text,text,text)",
    "public.ai_v4_lock_source_revisions_for_admission()",
)


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
            "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
            "WHERE rolname='teruisi_ai_seal_writer'")
        if cursor.fetchone() != (False,) * 7:
            raise RuntimeError("0042 requires unchanged NOLOGIN seal writer")
        for signature in OLD:
            cursor.execute("SELECT has_function_privilege('teruisi_ai_seal_writer',"
                "%s,'EXECUTE')", [signature])
            if cursor.fetchone() != (False,):
                raise RuntimeError("0042 predecessor no-ticket EXECUTE reopened")
        cursor.execute(internal_context)
        for definition in (ASSERT_CLAIM, CONTEXT, SEGMENT, PAGE):
            cursor.execute(definition)
        for signature in FUNCTIONS:
            cursor.execute("REVOKE ALL ON FUNCTION " + signature + " FROM PUBLIC")
        for signature in FUNCTIONS[1:]:
            cursor.execute("GRANT EXECUTE ON FUNCTION " + signature +
                " TO teruisi_ai_seal_writer")


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT EXISTS(SELECT 1 FROM public.ai_business_v4_seal_claims)")
        if cursor.fetchone()[0]:
            raise RuntimeError("0042 cannot remove a claimed read capability")
        for signature in reversed(FUNCTIONS):
            cursor.execute("DROP FUNCTION " + signature)
        cursor.execute(old_context.replace("CREATE FUNCTION",
            "CREATE OR REPLACE FUNCTION", 1))


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0041_business_v4_seal_ticket")]
    operations = [migrations.RunPython(install, uninstall)]
