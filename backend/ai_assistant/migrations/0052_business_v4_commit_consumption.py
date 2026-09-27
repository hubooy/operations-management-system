"""Closed, claim-bound v4 seal and consumption in one database statement.

The database checks a versioned commit request and all staged replay receipts.
It does not possess the HMAC key: only a separately protected signer can
authenticate the supplied body MAC before calling this still-NOLOGIN role.
"""
from importlib import import_module

from django.db import migrations


SEALER = "teruisi_ai_seal_writer"
SIGNATURE = (
    "public.ai_v4_sealer_commit_with_consumption("
    "text,text,text,bigint,text,text,text,text,text,text,text)"
)
OLD_COMMIT = "public.ai_v4_commit_seal(text,text,bigint,text,text,text,text)"
ASSERT_CLAIM = (
    "public.ai_v4_sealer_assert_claim(text,text,text,bigint,text,text)"
)
PROGRESS = "public.ai_business_v4_sealer_replay_progress"
CONSUMPTIONS = "public.ai_business_v4_seal_consumptions"

SQL = """CREATE FUNCTION public.ai_v4_sealer_commit_with_consumption(
  selected_run text,selected_attempt text,selected_actor text,
  selected_actor_version bigint,selected_nonce text,selected_claim text,
  canonical_body text,selected_body_digest text,selected_body_mac text,
  selected_key_id text,selected_request_digest text)
RETURNS TABLE(run_id text,evidence_version bigint,sealed_digest text,
  consumed_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE selected_ticket_id uuid;
  ticket public.ai_business_v4_seal_tickets%ROWTYPE;
  claim public.ai_business_v4_seal_claims%ROWTYPE;
  attempt public.ai_business_v4_validation_attempts%ROWTYPE;
  parent public.ai_business_v4_runs%ROWTYPE;
  source public.ai_business_v4_sources%ROWTYPE;
  segment public.ai_business_v4_validation_segments%ROWTYPE;
  receipt public.ai_business_v4_sealer_replay_progress%ROWTYPE;
  prior_ticket public.ai_business_v4_seal_tickets%ROWTYPE;
  prior_claim public.ai_business_v4_seal_claims%ROWTYPE;
  candidate jsonb; request_body jsonb;
  expected_request text; actual_root text; source_count bigint;
  expected_segments integer; receipt_count bigint; selected_index integer;
  prior_digest text; prior_segment_digest text;
  committed record; result_time timestamptz;
BEGIN
  IF session_user<>'teruisi_ai_seal_writer'
     OR selected_request_digest !~ '^[0-9a-f]{64}$'
     OR selected_body_digest !~ '^[0-9a-f]{64}$'
     OR selected_body_mac !~ '^[0-9a-f]{64}$'
     OR selected_key_id !~ '^[0-9a-f]{16}$'
     OR canonical_body IS NULL OR octet_length(canonical_body)>131072
  THEN RAISE EXCEPTION 'ai_v4_commit_consumption_input_invalid'; END IF;
  -- This function must remain owned by the same protected database owner as
  -- the 0038 commit and 0043 consumption table. The sealer gets EXECUTE only.
  IF current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT p.proowner FROM pg_catalog.pg_proc p
        WHERE p.oid='public.ai_v4_commit_seal(text,text,bigint,text,text,text,text)'::regprocedure))
     OR current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT c.relowner FROM pg_catalog.pg_class c
        WHERE c.oid='public.ai_business_v4_seal_consumptions'::regclass))
     OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r
       WHERE r.rolname='teruisi_ai_seal_writer' AND NOT r.rolcanlogin
         AND NOT r.rolinherit AND NOT r.rolsuper AND NOT r.rolcreatedb
         AND NOT r.rolcreaterole AND NOT r.rolreplication AND NOT r.rolbypassrls)
     OR EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m
       WHERE m.roleid='teruisi_ai_seal_writer'::regrole
          OR m.member='teruisi_ai_seal_writer'::regrole)
     OR has_function_privilege('teruisi_ai_seal_writer',
       'public.ai_v4_commit_seal(text,text,bigint,text,text,text,text)',
       'EXECUTE')
     OR has_table_privilege('teruisi_ai_seal_writer',
       'public.ai_business_v4_seal_consumptions',
       'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')
  THEN RAISE EXCEPTION 'ai_v4_commit_consumption_privileges_invalid'; END IF;

  selected_ticket_id:=public.ai_v4_sealer_assert_claim(selected_run,selected_attempt,
    selected_actor,selected_actor_version,selected_nonce,selected_claim);
  SELECT * INTO ticket FROM public.ai_business_v4_seal_tickets t
    WHERE t.id=selected_ticket_id;
  SELECT * INTO claim FROM public.ai_business_v4_seal_claims c
    WHERE c.ticket_id=selected_ticket_id;
  SELECT * INTO parent FROM public.ai_business_v4_runs r
    WHERE r.id=selected_run FOR UPDATE;
  SELECT * INTO attempt FROM public.ai_business_v4_validation_attempts a
    WHERE a.id=selected_attempt;
  IF ticket.id IS NULL OR claim.ticket_id IS NULL
     OR parent.id IS NULL OR attempt.id IS NULL
     OR ticket.run_id<>selected_run OR ticket.attempt_id<>selected_attempt
     OR parent.status<>'collecting' OR parent.collection_status<>'manual'
     OR parent.version<>ticket.parent_version
     OR parent.owner_email<>selected_actor
     OR parent.plan_digest<>ticket.plan_digest
     OR attempt.run_id<>selected_run OR attempt.run_version<>parent.version
     OR attempt.actor_email<>selected_actor
     OR attempt.actor_version<>selected_actor_version
     OR attempt.plan_digest<>ticket.plan_digest
     OR attempt.directory_digest<>ticket.directory_digest
     OR attempt.key_id<>selected_key_id
     OR ticket.actor_email<>selected_actor
     OR ticket.actor_version<>selected_actor_version
     OR ticket.request_digest<>selected_request_digest
     OR claim.claimed_at<ticket.issued_at
     OR claim.claimed_at>=ticket.expires_at
     OR clock_timestamp()>=claim.lease_until
  THEN RAISE EXCEPTION 'ai_v4_commit_consumption_claim_invalid'; END IF;

  IF canonical_body IS DISTINCT FROM
       public.ai_v4_replay_canonical(canonical_body::jsonb)
     OR selected_body_digest IS DISTINCT FROM
       encode(sha256(convert_to(canonical_body,'UTF8')),'hex')
  THEN RAISE EXCEPTION 'ai_v4_commit_consumption_body_invalid'; END IF;
  request_body:=jsonb_build_object(
    'operation','commit-seal-v1','runId',selected_run,
    'attemptId',selected_attempt,'actorEmail',selected_actor,
    'actorVersion',selected_actor_version,
    'parentVersion',ticket.parent_version,
    'planDigest',ticket.plan_digest,
    'directoryDigest',ticket.directory_digest,
    'bodyDigest',selected_body_digest,
    'bodyMac',selected_body_mac,'keyId',selected_key_id);
  expected_request:=encode(sha256(convert_to(
    public.ai_v4_replay_canonical(request_body),'UTF8')),'hex');
  IF selected_request_digest IS DISTINCT FROM expected_request
     OR canonical_body::jsonb->>'runId' IS DISTINCT FROM selected_run
     OR canonical_body::jsonb->>'attemptId' IS DISTINCT FROM selected_attempt
     OR canonical_body::jsonb->>'keyId' IS DISTINCT FROM selected_key_id
     OR canonical_body::jsonb->'actorVersion' IS DISTINCT FROM
       to_jsonb(selected_actor_version)
     OR canonical_body::jsonb->'evidenceVersion' IS DISTINCT FROM
       to_jsonb(ticket.parent_version+1)
     OR canonical_body::jsonb->>'planDigest' IS DISTINCT FROM ticket.plan_digest
     OR canonical_body::jsonb->>'directoryDigest' IS DISTINCT FROM
       ticket.directory_digest
  THEN RAISE EXCEPTION 'ai_v4_commit_consumption_request_invalid'; END IF;
  SELECT actual.root_digest,actual.source_count INTO actual_root,source_count
    FROM public.ai_v4_seal_ticket_source_root(selected_run) actual;
  IF actual_root IS DISTINCT FROM ticket.source_root
     OR source_count IS DISTINCT FROM ticket.source_count
  THEN RAISE EXCEPTION 'ai_v4_commit_consumption_source_drift'; END IF;

  FOR source IN SELECT s.* FROM public.ai_business_v4_sources s
      WHERE s.run_id=selected_run ORDER BY s.ordinal FOR UPDATE OF s LOOP
    IF NOT source.finished OR source.page_count NOT BETWEEN 1 AND 16384
       OR source.version<>source.page_count+1
       OR source.domain NOT IN ('finance','netshop')
    THEN RAISE EXCEPTION 'ai_v4_commit_consumption_source_invalid'; END IF;
    expected_segments:=(source.page_count+15)/16;
    SELECT count(*) INTO receipt_count
      FROM public.ai_business_v4_sealer_replay_progress p
      WHERE p.attempt_id=selected_attempt AND p.source_id=source.id;
    IF receipt_count<>expected_segments
    THEN RAISE EXCEPTION 'ai_v4_commit_consumption_receipts_incomplete'; END IF;
    prior_digest:=repeat('0',64);
    prior_segment_digest:=repeat('0',64);
    FOR selected_index IN 1..expected_segments LOOP
      SELECT * INTO segment FROM public.ai_business_v4_validation_segments s
        WHERE s.attempt_id=selected_attempt AND s.source_id=source.id
          AND s.segment_index=selected_index;
      SELECT * INTO receipt FROM public.ai_business_v4_sealer_replay_progress p
        WHERE p.attempt_id=selected_attempt AND p.source_id=source.id
          AND p.segment_index=selected_index;
      SELECT * INTO prior_ticket FROM public.ai_business_v4_seal_tickets t
        WHERE t.id=receipt.ticket_id;
      SELECT * INTO prior_claim FROM public.ai_business_v4_seal_claims c
        WHERE c.ticket_id=receipt.ticket_id;
      IF segment.id IS NULL OR receipt.id IS NULL OR prior_ticket.id IS NULL
         OR prior_claim.ticket_id IS NULL
         OR segment.run_id<>selected_run OR segment.source_id<>source.id
         OR segment.source_version<>source.version
         OR segment.source_ref<>source.source_ref
         OR segment.source_revision<>source.source_revision
         OR segment.start_sequence<>(selected_index-1)*16+1
         OR segment.end_sequence<>least(selected_index*16,source.page_count)
         OR segment.previous_segment_digest IS DISTINCT FROM prior_segment_digest
         OR receipt.run_id<>selected_run OR receipt.attempt_id<>selected_attempt
         OR receipt.source_id<>source.id
         OR receipt.source_root<>ticket.source_root
         OR receipt.segment_proof_digest<>segment.proof_digest
         OR receipt.key_id<>selected_key_id
         OR receipt.actor_email<>selected_actor
         OR receipt.actor_version<>selected_actor_version
         OR receipt.previous_candidate_digest IS DISTINCT FROM prior_digest
         OR prior_ticket.run_id<>selected_run
         OR prior_ticket.attempt_id<>selected_attempt
         OR prior_ticket.actor_email<>selected_actor
         OR prior_ticket.actor_version<>selected_actor_version
         OR prior_ticket.parent_version<>ticket.parent_version
         OR prior_ticket.plan_digest<>ticket.plan_digest
         OR prior_ticket.directory_digest<>ticket.directory_digest
         OR prior_ticket.source_root<>ticket.source_root
         OR prior_ticket.source_count<>ticket.source_count
         OR prior_claim.claimed_at<prior_ticket.issued_at
         OR prior_claim.claimed_at>=prior_ticket.expires_at
         OR receipt.recorded_at<prior_claim.claimed_at
         OR receipt.recorded_at>prior_claim.lease_until
      THEN RAISE EXCEPTION 'ai_v4_commit_consumption_receipt_binding_invalid'; END IF;
      candidate:=receipt.candidate_json::jsonb;
      IF receipt.candidate_json IS DISTINCT FROM public.ai_v4_replay_canonical(candidate)
         OR receipt.candidate_digest IS DISTINCT FROM
           encode(sha256(convert_to(public.ai_v4_replay_canonical(
             candidate-'candidateDigest'),'UTF8')),'hex')
         OR candidate->>'candidateDigest' IS DISTINCT FROM receipt.candidate_digest
         OR candidate->>'previousCandidateDigest' IS DISTINCT FROM prior_digest
         OR candidate->>'sourceRoot' IS DISTINCT FROM ticket.source_root
         OR candidate->>'runId' IS DISTINCT FROM selected_run
         OR candidate->>'attemptId' IS DISTINCT FROM selected_attempt
         OR candidate->>'sourceId' IS DISTINCT FROM source.id
         OR candidate->>'sourceKey' IS DISTINCT FROM source.source_key
         OR candidate->>'sourceRef' IS DISTINCT FROM source.source_ref
         OR candidate->>'sourceRevision' IS DISTINCT FROM source.source_revision
         OR candidate->'sourceVersion' IS DISTINCT FROM to_jsonb(source.version)
         OR candidate->>'keyId' IS DISTINCT FROM selected_key_id
         OR candidate->'segmentIndex' IS DISTINCT FROM to_jsonb(selected_index)
         OR candidate->'endSequence' IS DISTINCT FROM to_jsonb(segment.end_sequence)
         OR candidate->>'segmentProofDigest' IS DISTINCT FROM segment.proof_digest
         OR candidate->'candidateOnly' IS DISTINCT FROM 'true'::jsonb
         OR candidate->'authorityVerified' IS DISTINCT FROM 'false'::jsonb
         OR candidate->'sealCommitted' IS DISTINCT FROM 'false'::jsonb
         OR candidate->'progress'->'pageCount' IS DISTINCT FROM
           to_jsonb(segment.end_sequence)
         OR candidate->'progress'->'rowCount' IS DISTINCT FROM
           segment.progress_json::jsonb->'rowCount'
         OR candidate->'progress'->'receiptChainDigest' IS DISTINCT FROM
           segment.progress_json::jsonb->'receiptChainDigest'
         OR candidate->>'schemaVersion' IS DISTINCT FROM
           (CASE source.domain WHEN 'finance' THEN
             'business-v4-sealer-finance-segment-candidate-v2' ELSE
             'business-v4-sealer-promotion-segment-candidate-v2' END)
      THEN RAISE EXCEPTION 'ai_v4_commit_consumption_candidate_invalid'; END IF;
      prior_digest:=receipt.candidate_digest;
      prior_segment_digest:=segment.proof_digest;
    END LOOP;
  END LOOP;

  -- 0038 repeats authority/revision and parent-body checks under locks.
  -- 0043's immediate guard and deferred seal trigger complete the fence.
  SELECT c.run_id,c.evidence_version,c.sealed_digest INTO committed
    FROM public.ai_v4_commit_seal(selected_run,selected_attempt,
      ticket.parent_version,canonical_body,selected_body_digest,
      selected_body_mac,selected_key_id) c;
  IF committed.run_id IS DISTINCT FROM selected_run
     OR committed.evidence_version IS DISTINCT FROM ticket.parent_version+1
     OR committed.sealed_digest IS DISTINCT FROM selected_body_digest
  THEN RAISE EXCEPTION 'ai_v4_commit_consumption_seal_result_invalid'; END IF;
  result_time:=clock_timestamp();
  IF result_time>=claim.lease_until
  THEN RAISE EXCEPTION 'ai_v4_commit_consumption_lease_expired'; END IF;
  INSERT INTO public.ai_business_v4_seal_consumptions
    (ticket_id,run_id,attempt_id,request_digest,evidence_version,
     body_digest,consumed_at)
    VALUES (ticket.id,selected_run,selected_attempt,selected_request_digest,
      committed.evidence_version,selected_body_digest,result_time);
  run_id:=committed.run_id; evidence_version:=committed.evidence_version;
  sealed_digest:=committed.sealed_digest; consumed_at:=result_time;
  RETURN NEXT;
END $$"""


def _preflight(cursor):
    cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
        "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
        "WHERE rolname=%s", [SEALER])
    if cursor.fetchone() != (False,) * 7:
        raise RuntimeError("0052 requires unchanged NOLOGIN sealer")
    cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members "
        "WHERE roleid=%s::regrole OR member=%s::regrole)", [SEALER, SEALER])
    if cursor.fetchone() != (False,):
        raise RuntimeError("0052 requires independent sealer role")
    cursor.execute("SELECT p.prosrc,p.prosecdef,l.lanname,p.proconfig,p.proowner "
        "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_language l ON l.oid=p.prolang "
        "WHERE p.oid=to_regprocedure(%s)", [OLD_COMMIT])
    row = cursor.fetchone()
    old = import_module("ai_assistant.migrations.0038_business_v4_seal_writer_gate")
    if (row is None or row[0] != old.COMMIT_SEAL.split("$$")[1]
            or row[1] is not True or row[2] != "plpgsql"
            or {x.replace(" ", "") for x in (row[3] or [])}
                != {"search_path=pg_catalog,public"}):
        raise RuntimeError("0052 requires frozen 0038 commit")
    owner = row[4]
    frozen = (
        (ASSERT_CLAIM, "0042_business_v4_claimed_read", "ASSERT_CLAIM"),
        ("public.ai_v4_replay_canonical(jsonb)",
         "0047_business_v4_sealer_replay_progress", "CANONICAL_SQL"),
        ("public.ai_v4_seal_ticket_source_root(text)",
         "0041_business_v4_seal_ticket", "SOURCE_ROOT"),
        ("public.ai_v4_seal_consumption_guard()",
         "0043_business_v4_seal_consumption_candidate", "CONSUMPTION_GUARD"),
        ("public.ai_v4_seal_requires_consumption()",
         "0043_business_v4_seal_consumption_candidate", "REQUIRE_CONSUMPTION"),
        ("public.ai_v4_sealer_replay_progress_guard()",
         "0047_business_v4_sealer_replay_progress", "GUARD"),
        ("public.ai_v4_sealer_record_replay_progress("
         "text,text,text,integer,text,bigint,text,text,text)",
         "0051_business_v4_prior_claim_column", "RECORD"),
    )
    for signature, module, name in frozen:
        cursor.execute("SELECT proowner,prosrc FROM pg_catalog.pg_proc "
            "WHERE oid=to_regprocedure(%s)", [signature])
        found = cursor.fetchone()
        definition = getattr(import_module("ai_assistant.migrations." + module),
            name)
        if found is None or found[0] != owner or found[1] != definition.split("$$")[1]:
            raise RuntimeError("0052 requires frozen same-owner helpers")
    for table in (PROGRESS, CONSUMPTIONS,
            "public.ai_business_v4_seals",
            "public.ai_business_v4_runs"):
        cursor.execute("SELECT relowner FROM pg_catalog.pg_class "
            "WHERE oid=to_regclass(%s)", [table])
        found = cursor.fetchone()
        if found is None or found[0] != owner:
            raise RuntimeError("0052 requires same-owner sealed tables")
    for trigger, table, procedure, deferred in (
            ("ai_v4_seal_consumption_required",
             "public.ai_business_v4_seals",
             "public.ai_v4_seal_requires_consumption()", True),
            ("ai_v4_consumption_state", CONSUMPTIONS,
             "public.ai_v4_seal_consumption_guard()", False),
            ("ai_v4_replay_progress_guard", PROGRESS,
             "public.ai_v4_sealer_replay_progress_guard()", False)):
        cursor.execute("SELECT t.tgenabled,t.tgfoid=to_regprocedure(%s),"
            "t.tgdeferrable,t.tginitdeferred FROM pg_catalog.pg_trigger t "
            "WHERE t.tgrelid=to_regclass(%s) AND t.tgname=%s",
            [procedure, table, trigger])
        if cursor.fetchone() != ("O", True, deferred, deferred):
            raise RuntimeError("0052 requires active consumption/replay triggers")
    for signature in (OLD_COMMIT, SIGNATURE):
        cursor.execute("SELECT to_regprocedure(%s)", [signature])
        exists = cursor.fetchone()[0] is not None
        if signature == SIGNATURE and exists:
            raise RuntimeError("0052 wrapper already exists")
    cursor.execute("SELECT has_function_privilege(%s,%s,'EXECUTE'),"
        "has_table_privilege(%s,%s,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')",
        [SEALER, OLD_COMMIT, SEALER, CONSUMPTIONS])
    if cursor.fetchone() != (False, False):
        raise RuntimeError("0052 requires closed direct seal and ledger ACL")
    cursor.execute("SELECT has_table_privilege(%s,%s,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE'),"
        "has_function_privilege(%s,%s,'EXECUTE')",
        [SEALER, PROGRESS, SEALER,
         "public.ai_v4_sealer_record_replay_progress("
         "text,text,text,integer,text,bigint,text,text,text)"])
    if cursor.fetchone() != (False, True):
        raise RuntimeError("0052 requires claim-only replay writer")


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        _preflight(cursor)
        cursor.execute(SQL)
        cursor.execute("REVOKE ALL ON FUNCTION " + SIGNATURE + " FROM PUBLIC")
        for role in ("teruisi_ai_reader", "teruisi_ai_writer"):
            cursor.execute("SELECT to_regrole(%s)", [role])
            if cursor.fetchone()[0] is not None:
                cursor.execute("REVOKE ALL ON FUNCTION " + SIGNATURE + " FROM " + role)
        cursor.execute("GRANT EXECUTE ON FUNCTION " + SIGNATURE + " TO " + SEALER)


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT EXISTS(SELECT 1 FROM " + CONSUMPTIONS + ")")
        if cursor.fetchone()[0]:
            raise RuntimeError("0052 cannot remove an issued seal consumption")
        cursor.execute("DROP FUNCTION " + SIGNATURE)


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0051_business_v4_prior_claim_column")]
    operations = [migrations.RunPython(install, uninstall)]
