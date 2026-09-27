"""One claim-bound source identity and completion read for the independent sealer.

The function discloses one selected source, never a raw page or a different
source's checkpoint. It does not activate the NOLOGIN sealer or seal writes.
"""
from django.db import migrations


ROLE = "teruisi_ai_seal_writer"
SIGNATURE = (
    "public.ai_v4_sealer_ticket_source(text,text,text,text,bigint,text,text)"
)

SOURCE = """CREATE FUNCTION public.ai_v4_sealer_ticket_source(
  selected_run text,selected_attempt text,selected_source text,
  selected_actor text,selected_actor_version bigint,
  selected_nonce text,selected_claim text)
RETURNS TABLE(source_id text,source_key text,ordinal integer,domain text,
  temporal_role text,query_json text,query_digest text,
  source_identity_digest text,source_revision_hint text,
  source_version bigint,source_ref text,source_revision text,
  page_count bigint,row_count bigint,stored_bytes bigint,
  source_root text,key_id text,checkpoint_digest text,
  last_chunk_digest text,metadata_json text,finance_state_digest text,
  run_bound_capability_verified boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE ticket_id uuid; ticket public.ai_business_v4_seal_tickets%ROWTYPE;
  attempt public.ai_business_v4_validation_attempts%ROWTYPE;
  source public.ai_business_v4_sources%ROWTYPE; checkpoint jsonb;
BEGIN
  ticket_id:=public.ai_v4_sealer_assert_claim(selected_run,selected_attempt,
    selected_actor,selected_actor_version,selected_nonce,selected_claim);
  SELECT * INTO ticket FROM public.ai_business_v4_seal_tickets t
    WHERE t.id=ticket_id;
  SELECT * INTO attempt FROM public.ai_business_v4_validation_attempts a
    WHERE a.id=selected_attempt AND a.run_id=selected_run;
  SELECT * INTO source FROM public.ai_business_v4_sources s
    WHERE s.id=selected_source AND s.run_id=selected_run;
  IF ticket.id IS NULL OR attempt.id IS NULL OR source.id IS NULL
     OR NOT source.finished OR source.page_count NOT BETWEEN 1 AND 16384
     OR source.version<>source.page_count+1
     OR source.row_count>1638400 OR source.stored_bytes>2147483648
     OR source.ordinal NOT BETWEEN 1 AND ticket.source_count
     OR source.domain NOT IN ('finance','netshop')
     OR (source.domain='finance' AND source.temporal_role<>'monthly_context')
     OR (source.domain='netshop' AND source.temporal_role<>'daily_fact')
     OR octet_length(source.query_json)>4096
     OR source.query_json IS DISTINCT FROM
       public.ai_v4_replay_canonical(source.query_json::jsonb)
     OR source.query_digest IS DISTINCT FROM
       encode(sha256(convert_to(source.query_json,'UTF8')),'hex')
     OR octet_length(source.checkpoint_json)>32768
     OR source.checkpoint_json IS DISTINCT FROM
       public.ai_v4_replay_canonical(source.checkpoint_json::jsonb)
  THEN RAISE EXCEPTION 'ai_v4_sealer_ticket_source_invalid'; END IF;
  checkpoint:=source.checkpoint_json::jsonb;
  IF checkpoint->>'schemaVersion' IS DISTINCT FROM 'business-v4-checkpoint-v1'
     OR checkpoint->'finished' IS DISTINCT FROM 'true'::jsonb
     OR checkpoint->>'sourceRef' IS DISTINCT FROM source.source_ref
     OR checkpoint->>'sourceRevision' IS DISTINCT FROM source.source_revision
     OR checkpoint->'pageCount' IS DISTINCT FROM to_jsonb(source.page_count)
     OR checkpoint->'rowCount' IS DISTINCT FROM to_jsonb(source.row_count)
     OR checkpoint->'storedBytes' IS DISTINCT FROM to_jsonb(source.stored_bytes)
     OR coalesce(checkpoint->>'lastChunkDigest','') !~ '^[0-9a-f]{64}$'
     OR (source.domain='netshop' AND
       jsonb_typeof(checkpoint->'metadata') IS DISTINCT FROM 'object')
     OR (source.domain='finance' AND
       jsonb_typeof(checkpoint->'financeState') IS DISTINCT FROM 'object')
  THEN RAISE EXCEPTION 'ai_v4_sealer_ticket_source_checkpoint_invalid'; END IF;
  source_id:=source.id; source_key:=source.source_key;
  ordinal:=source.ordinal; domain:=source.domain;
  temporal_role:=source.temporal_role; query_json:=source.query_json;
  query_digest:=source.query_digest;
  source_identity_digest:=source.source_identity_digest;
  source_revision_hint:=source.source_revision_hint;
  source_version:=source.version; source_ref:=source.source_ref;
  source_revision:=source.source_revision; page_count:=source.page_count;
  row_count:=source.row_count; stored_bytes:=source.stored_bytes;
  source_root:=ticket.source_root; key_id:=attempt.key_id;
  checkpoint_digest:=encode(sha256(convert_to(
    source.checkpoint_json,'UTF8')),'hex');
  last_chunk_digest:=checkpoint->>'lastChunkDigest';
  metadata_json:=CASE WHEN source.domain='netshop'
    THEN public.ai_v4_replay_canonical(checkpoint->'metadata') ELSE NULL END;
  finance_state_digest:=CASE WHEN source.domain='finance'
    THEN encode(sha256(convert_to(public.ai_v4_replay_canonical(
      checkpoint->'financeState'),'UTF8')),'hex') ELSE NULL END;
  run_bound_capability_verified:=true;
  PERFORM public.ai_v4_sealer_assert_claim(selected_run,selected_attempt,
    selected_actor,selected_actor_version,selected_nonce,selected_claim);
  RETURN NEXT;
END $$"""


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
            "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
            "WHERE rolname=%s", [ROLE])
        if cursor.fetchone() != (False,) * 7:
            raise RuntimeError("0049 requires unchanged NOLOGIN seal writer")
        for old in (
                "public.ai_v4_sealer_assert_claim(text,text,text,bigint,text,text)",
                "public.ai_v4_sealer_read_context(text,text,text,bigint)",
                "public.ai_v4_commit_seal(text,text,bigint,text,text,text,text)"):
            cursor.execute("SELECT has_function_privilege(%s,%s,'EXECUTE')",
                [ROLE, old])
            if cursor.fetchone() != (False,):
                raise RuntimeError("0049 predecessor direct EXECUTE reopened")
        cursor.execute(SOURCE)
        cursor.execute("REVOKE ALL ON FUNCTION " + SIGNATURE + " FROM PUBLIC")
        cursor.execute("GRANT EXECUTE ON FUNCTION " + SIGNATURE + " TO " + ROLE)


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("DROP FUNCTION " + SIGNATURE)


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0048_business_v4_finance_replay_progress")]
    operations = [migrations.RunPython(install, uninstall)]
