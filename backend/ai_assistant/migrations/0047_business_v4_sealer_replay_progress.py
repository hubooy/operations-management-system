"""Claim-bound, append-only promotion replay receipts; never a seal authority."""
from django.db import migrations, models


TABLE = "public.ai_business_v4_sealer_replay_progress"
SEALER = "teruisi_ai_seal_writer"
WRITE = "public.ai_v4_sealer_record_replay_progress(text,text,text,integer,text,bigint,text,text,text)"
READ = "public.ai_v4_sealer_replay_progress(text,text,text,integer,text,bigint,text,text)"
CANONICAL = "public.ai_v4_replay_canonical(jsonb)"

# Python's contracts.canonical uses sorted keys, compact separators and UTF-8.
# This function is deliberately private to the SECURITY DEFINER writer.
CANONICAL_SQL = """CREATE FUNCTION public.ai_v4_replay_canonical(value jsonb)
RETURNS text LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
DECLARE result text;
BEGIN
  CASE jsonb_typeof(value)
    WHEN 'object' THEN
      SELECT '{'||coalesce(string_agg(to_jsonb(key)::text||':'||
        public.ai_v4_replay_canonical(item),',' ORDER BY key COLLATE "C"),'')||'}'
        INTO result FROM jsonb_each(value) AS pairs(key,item);
      RETURN result;
    WHEN 'array' THEN
      SELECT '['||coalesce(string_agg(public.ai_v4_replay_canonical(item),','
        ORDER BY ordinal),'')||']' INTO result
        FROM jsonb_array_elements(value) WITH ORDINALITY AS parts(item,ordinal);
      RETURN result;
    ELSE RETURN value::text;
  END CASE;
END $$"""

GUARD = """CREATE FUNCTION public.ai_v4_sealer_replay_progress_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'ai_v4_replay_progress_immutable'; END IF;
  IF session_user<>'teruisi_ai_seal_writer' OR current_user IS DISTINCT FROM
    pg_catalog.pg_get_userbyid((SELECT relowner FROM pg_catalog.pg_class
      WHERE oid=TG_RELID))
  THEN RAISE EXCEPTION 'ai_v4_replay_progress_direct_write_denied'; END IF;
  RETURN NEW;
END $$"""

RECORD = """CREATE FUNCTION public.ai_v4_sealer_record_replay_progress(
  selected_run text,selected_attempt text,selected_source text,
  selected_index integer,selected_actor text,selected_actor_version bigint,
  selected_nonce text,selected_claim text,selected_candidate text)
RETURNS TABLE(candidate_digest text,recorded_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE ticket_id uuid; ticket public.ai_business_v4_seal_tickets%ROWTYPE;
  prior_ticket public.ai_business_v4_seal_tickets%ROWTYPE;
  prior_claim public.ai_business_v4_seal_claims%ROWTYPE;
  attempt public.ai_business_v4_validation_attempts%ROWTYPE;
  source public.ai_business_v4_sources%ROWTYPE;
  segment public.ai_business_v4_validation_segments%ROWTYPE;
  prior public.ai_business_v4_sealer_replay_progress%ROWTYPE;
  existing public.ai_business_v4_sealer_replay_progress%ROWTYPE;
  candidate jsonb; finite jsonb; progress jsonb; expected_previous text;
  actual_root text; source_count bigint; digest_text text;
BEGIN
  IF session_user<>'teruisi_ai_seal_writer' OR selected_candidate IS NULL
     OR octet_length(selected_candidate)>48000
     OR selected_index IS NULL OR selected_index NOT BETWEEN 1 AND 1024
  THEN RAISE EXCEPTION 'ai_v4_replay_progress_input_invalid'; END IF;
  ticket_id:=public.ai_v4_sealer_assert_claim(selected_run,selected_attempt,
    selected_actor,selected_actor_version,selected_nonce,selected_claim);
  SELECT * INTO ticket FROM public.ai_business_v4_seal_tickets
    WHERE id=ticket_id;
  SELECT * INTO attempt FROM public.ai_business_v4_validation_attempts
    WHERE id=selected_attempt;
  SELECT * INTO source FROM public.ai_business_v4_sources
    WHERE id=selected_source AND run_id=selected_run FOR UPDATE;
  SELECT * INTO segment FROM public.ai_business_v4_validation_segments
    WHERE attempt_id=selected_attempt AND source_id=selected_source
      AND segment_index=selected_index;
  IF ticket.id IS NULL OR attempt.id IS NULL OR source.id IS NULL
     OR segment.id IS NULL OR attempt.run_id<>selected_run
     OR attempt.key_id !~ '^[0-9a-f]{16}$'
     OR ticket.attempt_id<>attempt.id OR ticket.run_id<>selected_run
     OR ticket.actor_email<>selected_actor
     OR ticket.actor_version<>selected_actor_version
     OR source.domain<>'netshop' OR NOT source.finished
     OR source.query_json::jsonb->>'platform' IS DISTINCT FROM '京东'
     OR source.query_json::jsonb->>'dataset' IS DISTINCT FROM 'promotion'
     OR segment.run_id<>selected_run OR segment.source_version<>source.version
     OR segment.source_ref<>source.source_ref
     OR segment.source_revision<>source.source_revision
     OR segment.proof_digest !~ '^[0-9a-f]{64}$'
     OR segment.progress_digest IS DISTINCT FROM
       encode(sha256(convert_to(segment.progress_json,'UTF8')),'hex')
  THEN RAISE EXCEPTION 'ai_v4_replay_progress_binding_invalid'; END IF;
  SELECT actual.root_digest,actual.source_count INTO actual_root,source_count
    FROM public.ai_v4_seal_ticket_source_root(selected_run) actual;
  IF actual_root IS DISTINCT FROM ticket.source_root
     OR source_count IS DISTINCT FROM ticket.source_count
  THEN RAISE EXCEPTION 'ai_v4_replay_progress_source_drift'; END IF;
  -- jsonb removes duplicate keys; equality with the canonical source text
  -- rejects those duplicates, alternate spellings and noncanonical numbers.
  BEGIN
    candidate:=selected_candidate::jsonb;
    IF jsonb_typeof(candidate)<>'object'
       OR selected_candidate IS DISTINCT FROM public.ai_v4_replay_canonical(candidate)
    THEN RAISE EXCEPTION 'ai_v4_replay_progress_noncanonical'; END IF;
  EXCEPTION WHEN invalid_text_representation OR invalid_parameter_value OR
      numeric_value_out_of_range THEN
    RAISE EXCEPTION 'ai_v4_replay_progress_json_invalid';
  END;
  IF NOT candidate ?& ARRAY['schemaVersion','runId','attemptId','sourceId',
      'sourceRoot','sourceKey','sourceRef','sourceRevision','sourceVersion',
      'keyId','segmentIndex','endSequence','segmentProofDigest','progress',
      'candidateOnly','authorityVerified','financeReplayed',
      'sourceMetadataVerified','upstreamSignatureVerified','sealCommitted',
      'previousCandidateDigest','candidateDigest']
     OR (SELECT count(*) FROM jsonb_object_keys(candidate))<>22
     OR candidate->>'schemaVersion' IS DISTINCT FROM
       'business-v4-sealer-promotion-segment-candidate-v2'
     OR candidate->'candidateOnly'<>'true'::jsonb
     OR candidate->'authorityVerified'<>'false'::jsonb
     OR candidate->'financeReplayed'<>'false'::jsonb
     OR candidate->'sourceMetadataVerified'<>'false'::jsonb
     OR candidate->'upstreamSignatureVerified'<>'false'::jsonb
     OR candidate->'sealCommitted'<>'false'::jsonb
     OR candidate->>'runId' IS DISTINCT FROM selected_run
     OR candidate->>'attemptId' IS DISTINCT FROM selected_attempt
     OR candidate->>'sourceId' IS DISTINCT FROM selected_source
     OR candidate->>'sourceRoot' IS DISTINCT FROM ticket.source_root
     OR candidate->>'sourceKey' IS DISTINCT FROM source.source_key
     OR candidate->>'sourceRef' IS DISTINCT FROM source.source_ref
     OR candidate->>'sourceRevision' IS DISTINCT FROM source.source_revision
     OR candidate->'sourceVersion' IS DISTINCT FROM to_jsonb(source.version)
     OR candidate->>'keyId' IS DISTINCT FROM attempt.key_id
     OR candidate->'segmentIndex' IS DISTINCT FROM to_jsonb(selected_index)
     OR candidate->'endSequence' IS DISTINCT FROM to_jsonb(segment.end_sequence)
     OR candidate->>'segmentProofDigest' IS DISTINCT FROM segment.proof_digest
     OR coalesce(candidate->>'candidateDigest','') !~ '^[0-9a-f]{64}$'
     OR coalesce(candidate->>'previousCandidateDigest','') !~ '^[0-9a-f]{64}$'
  THEN RAISE EXCEPTION 'ai_v4_replay_progress_candidate_invalid'; END IF;
  digest_text:=encode(sha256(convert_to(
    public.ai_v4_replay_canonical(candidate-'candidateDigest'),'UTF8')),'hex');
  IF digest_text IS DISTINCT FROM candidate->>'candidateDigest'
  THEN RAISE EXCEPTION 'ai_v4_replay_progress_digest_invalid'; END IF;
  progress:=segment.progress_json::jsonb;
  finite:=candidate->'progress';
  IF jsonb_typeof(finite)<>'object' OR NOT finite ?& ARRAY[
       'pageCount','rowCount','storedBytes','lastChunkDigest',
       'receiptChainDigest','verifier','observedDates']
     OR (SELECT count(*) FROM jsonb_object_keys(finite))<>7
     OR progress->>'schemaVersion' IS DISTINCT FROM
       'business-v4-validation-progress-candidate-v1'
     OR progress->>'sourceKey' IS DISTINCT FROM source.source_key
     OR progress->>'domain' IS DISTINCT FROM 'netshop'
     OR progress->>'sourceRef' IS DISTINCT FROM source.source_ref
     OR progress->>'sourceRevision' IS DISTINCT FROM source.source_revision
     OR finite->'pageCount' IS DISTINCT FROM progress->'pageCount'
     OR finite->'rowCount' IS DISTINCT FROM progress->'rowCount'
     OR finite->'storedBytes' IS DISTINCT FROM progress->'storedBytes'
     OR finite->'lastChunkDigest' IS DISTINCT FROM progress->'lastChunkDigest'
     OR finite->'receiptChainDigest' IS DISTINCT FROM progress->'receiptChainDigest'
     OR finite->'verifier' IS DISTINCT FROM progress->'domainState'->'verifier'
     OR finite->'observedDates' IS DISTINCT FROM
       progress->'domainState'->'observedDates'
     OR finite->'pageCount' IS DISTINCT FROM to_jsonb(segment.end_sequence)
     OR segment.start_sequence<>(selected_index-1)*16+1
     OR segment.end_sequence<>least(selected_index*16,source.page_count)
  THEN RAISE EXCEPTION 'ai_v4_replay_progress_mismatch'; END IF;
  IF selected_index=1 THEN
    expected_previous:=repeat('0',64);
    IF segment.previous_segment_digest<>expected_previous
    THEN RAISE EXCEPTION 'ai_v4_replay_progress_segment_chain_invalid'; END IF;
  ELSE
    SELECT * INTO prior FROM public.ai_business_v4_sealer_replay_progress
      WHERE attempt_id=selected_attempt AND source_id=selected_source
        AND segment_index=selected_index-1;
    SELECT * INTO prior_ticket FROM public.ai_business_v4_seal_tickets
      WHERE id=prior.ticket_id;
    SELECT * INTO prior_claim FROM public.ai_business_v4_seal_claims
      WHERE ticket_id=prior.ticket_id;
    IF prior.id IS NULL OR prior.run_id<>selected_run
       OR prior.source_root<>ticket.source_root
       OR prior.key_id<>attempt.key_id
       OR prior.actor_email<>selected_actor
       OR prior.actor_version<>selected_actor_version
       OR prior_ticket.id IS NULL OR prior_claim.ticket_id IS NULL
       OR prior_ticket.run_id<>selected_run
       OR prior_ticket.attempt_id<>selected_attempt
       OR prior_ticket.source_root<>ticket.source_root
       OR prior_ticket.actor_email<>selected_actor
       OR prior_ticket.actor_version<>selected_actor_version
       OR prior_ticket.plan_digest<>ticket.plan_digest
       OR prior_ticket.directory_digest<>ticket.directory_digest
       OR prior_claim.claimed_at<prior_ticket.issued_at
       OR prior_claim.claimed_at>=prior_ticket.expires_at
       OR prior.recorded_at<prior_claim.claimed_at
       OR prior.recorded_at>prior_claim.lease_until
       OR prior.segment_proof_digest<>segment.previous_segment_digest
    THEN RAISE EXCEPTION 'ai_v4_replay_progress_prior_missing'; END IF;
    expected_previous:=prior.candidate_digest;
  END IF;
  IF candidate->>'previousCandidateDigest' IS DISTINCT FROM expected_previous
  THEN RAISE EXCEPTION 'ai_v4_replay_progress_candidate_chain_invalid'; END IF;
  SELECT * INTO existing FROM public.ai_business_v4_sealer_replay_progress
    WHERE attempt_id=selected_attempt AND source_id=selected_source
      AND segment_index=selected_index;
  IF existing.id IS NOT NULL THEN
    IF existing.candidate_json<>selected_candidate
       OR existing.candidate_digest<>digest_text
    THEN RAISE EXCEPTION 'ai_v4_replay_progress_conflict'; END IF;
    candidate_digest:=existing.candidate_digest;
    recorded_at:=existing.recorded_at;
  ELSE
    INSERT INTO public.ai_business_v4_sealer_replay_progress
      (id,ticket_id,run_id,attempt_id,source_id,source_root,segment_index,
       segment_proof_digest,key_id,actor_email,actor_version,
       previous_candidate_digest,candidate_digest,candidate_json,recorded_at)
      VALUES (gen_random_uuid(),ticket_id,selected_run,selected_attempt,
       selected_source,ticket.source_root,selected_index,segment.proof_digest,
       attempt.key_id,selected_actor,selected_actor_version,
       expected_previous,digest_text,selected_candidate,clock_timestamp())
      RETURNING ai_business_v4_sealer_replay_progress.candidate_digest,
        ai_business_v4_sealer_replay_progress.recorded_at
        INTO candidate_digest,recorded_at;
  END IF;
  PERFORM public.ai_v4_sealer_assert_claim(selected_run,selected_attempt,
    selected_actor,selected_actor_version,selected_nonce,selected_claim);
  RETURN NEXT;
END $$"""

READ_SQL = """CREATE FUNCTION public.ai_v4_sealer_replay_progress(
  selected_run text,selected_attempt text,selected_source text,
  selected_index integer,selected_actor text,selected_actor_version bigint,
  selected_nonce text,selected_claim text)
RETURNS TABLE(candidate_json text,candidate_digest text,recorded_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
  PERFORM public.ai_v4_sealer_assert_claim(selected_run,selected_attempt,
    selected_actor,selected_actor_version,selected_nonce,selected_claim);
  RETURN QUERY SELECT p.candidate_json,p.candidate_digest,p.recorded_at
    FROM public.ai_business_v4_sealer_replay_progress p
    WHERE p.run_id=selected_run AND p.attempt_id=selected_attempt
      AND p.source_id=selected_source AND p.segment_index=selected_index
      AND p.actor_email=selected_actor
      AND p.actor_version=selected_actor_version;
  PERFORM public.ai_v4_sealer_assert_claim(selected_run,selected_attempt,
    selected_actor,selected_actor_version,selected_nonce,selected_claim);
END $$"""


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
            "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
            "WHERE rolname='teruisi_ai_seal_writer'")
        if cursor.fetchone() != (False,) * 7:
            raise RuntimeError("0047 requires unchanged NOLOGIN seal writer")
        cursor.execute("SELECT has_function_privilege('teruisi_ai_seal_writer',"
            "'public.ai_v4_commit_seal(text,text,bigint,text,text,text,text)',"
            "'EXECUTE')")
        if cursor.fetchone() != (False,):
            raise RuntimeError("0047 direct seal commit must remain revoked")
        cursor.execute("""CREATE TABLE public.ai_business_v4_sealer_replay_progress (
          id uuid PRIMARY KEY,
          ticket_id uuid NOT NULL REFERENCES public.ai_business_v4_seal_tickets(id)
            ON DELETE RESTRICT,
          run_id varchar(160) NOT NULL REFERENCES public.ai_business_v4_runs(id)
            ON DELETE RESTRICT,
          attempt_id varchar(160) NOT NULL
            REFERENCES public.ai_business_v4_validation_attempts(id)
            ON DELETE RESTRICT,
          source_id varchar(160) NOT NULL REFERENCES public.ai_business_v4_sources(id)
            ON DELETE RESTRICT,
          source_root varchar(64) NOT NULL,
          segment_index integer NOT NULL CHECK (segment_index BETWEEN 1 AND 1024),
          segment_proof_digest varchar(64) NOT NULL,
          key_id varchar(16) NOT NULL,
          actor_email varchar(320) NOT NULL,
          actor_version bigint NOT NULL CHECK (actor_version>=1),
          previous_candidate_digest varchar(64) NOT NULL,
          candidate_digest varchar(64) NOT NULL,
          candidate_json text NOT NULL CHECK (octet_length(candidate_json)<=48000),
          recorded_at timestamptz NOT NULL,
          UNIQUE (attempt_id,source_id,segment_index)
        )""")
        cursor.execute("REVOKE ALL ON " + TABLE + " FROM PUBLIC")
        for role in ("teruisi_ai_reader", "teruisi_ai_writer", SEALER):
            cursor.execute("SELECT to_regrole(%s)", [role])
            if cursor.fetchone()[0] is not None:
                cursor.execute("REVOKE ALL ON " + TABLE + " FROM " + role)
        for definition in (CANONICAL_SQL, GUARD, RECORD, READ_SQL):
            cursor.execute(definition)
        for signature in (CANONICAL, "public.ai_v4_sealer_replay_progress_guard()",
                          WRITE, READ):
            cursor.execute("REVOKE ALL ON FUNCTION " + signature + " FROM PUBLIC")
        for signature in (WRITE, READ):
            cursor.execute("GRANT EXECUTE ON FUNCTION " + signature + " TO " + SEALER)
        cursor.execute("CREATE TRIGGER ai_v4_replay_progress_guard BEFORE INSERT OR "
            "UPDATE OR DELETE ON " + TABLE + " FOR EACH ROW EXECUTE FUNCTION "
            "public.ai_v4_sealer_replay_progress_guard()")
        cursor.execute("CREATE TRIGGER ai_v4_replay_progress_no_truncate "
            "BEFORE TRUNCATE ON " + TABLE + " FOR EACH STATEMENT EXECUTE FUNCTION "
            "public.ai_v4_seal_ticket_no_truncate()")


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT EXISTS(SELECT 1 FROM " + TABLE + ")")
        if cursor.fetchone()[0]:
            raise RuntimeError("0047 cannot discard recorded replay progress")
        cursor.execute("DROP TRIGGER ai_v4_replay_progress_no_truncate ON " + TABLE)
        cursor.execute("DROP TRIGGER ai_v4_replay_progress_guard ON " + TABLE)
        for signature in (READ, WRITE,
                          "public.ai_v4_sealer_replay_progress_guard()", CANONICAL):
            cursor.execute("DROP FUNCTION " + signature)
        cursor.execute("DROP TABLE " + TABLE)


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0046_business_promotion_trial_file_guard")]
    operations = [
        migrations.SeparateDatabaseAndState(database_operations=[], state_operations=[
            migrations.CreateModel(name="AiBusinessV4SealerReplayProgress", fields=[
                ("id", models.UUIDField(primary_key=True, serialize=False)),
                ("ticket_id", models.UUIDField()),
                ("run_id", models.CharField(max_length=160)),
                ("attempt_id", models.CharField(max_length=160)),
                ("source_id", models.CharField(max_length=160)),
                ("source_root", models.CharField(max_length=64)),
                ("segment_index", models.PositiveIntegerField()),
                ("segment_proof_digest", models.CharField(max_length=64)),
                ("key_id", models.CharField(max_length=16)),
                ("actor_email", models.CharField(max_length=320)),
                ("actor_version", models.PositiveBigIntegerField()),
                ("previous_candidate_digest", models.CharField(max_length=64)),
                ("candidate_digest", models.CharField(max_length=64)),
                ("candidate_json", models.TextField()),
                ("recorded_at", models.DateTimeField()),
            ], options={"db_table": "ai_business_v4_sealer_replay_progress"}),
        ]),
        migrations.RunPython(install, uninstall),
    ]
