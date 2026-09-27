"""Default-closed independent v4 seal-writer DB transition; no sealer CLI."""
from importlib import import_module

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


SEAL_ROLE = "teruisi_ai_seal_writer"
old_run = import_module("ai_assistant.migrations.0035_business_v4_ledger").RUN_GUARD
old_probe = import_module("ai_assistant.migrations.0037_business_v4_seal_admission_read").LOCK_PROBE
run_needle = "  IF OLD.status<>'collecting' OR NEW.status<>'collecting'"
probe_role_needle = "IF session_user<>'teruisi_ai_writer' AND session_user<>current_user"
probe_authority_needle = "IF session_user='teruisi_ai_writer' AND NOT EXISTS"
if (old_run.count(run_needle) != 1 or old_probe.count(probe_role_needle) != 1
        or old_probe.count(probe_authority_needle) != 1):
    raise RuntimeError("v4 seal migration predecessor guards changed")

SEAL_TRANSITION = """
  IF OLD.status='collecting' AND NEW.status='sealed' THEN
    IF session_user<>'teruisi_ai_seal_writer'
       OR OLD.collection_status<>'manual' OR NEW.collection_status<>'manual'
       OR NEW.version<>OLD.version+1
       OR to_jsonb(NEW)-ARRAY['status','version'] IS DISTINCT FROM
          to_jsonb(OLD)-ARRAY['status','version']
       OR NOT EXISTS (SELECT 1 FROM public.ai_business_v4_seals seal
           WHERE seal.run_id=NEW.id AND seal.evidence_version=NEW.version)
       OR EXISTS (SELECT 1 FROM public.ai_business_v4_sources source
           WHERE source.run_id=NEW.id AND NOT source.finished)
       OR NOT EXISTS (SELECT 1 FROM public.access_control_users account
           WHERE account.email=NEW.owner_email AND account.role='admin'
             AND account.status='active' AND account.scope IS NULL
             AND account.version=(SELECT attempt.actor_version
               FROM public.ai_business_v4_seals seal
               JOIN public.ai_business_v4_validation_attempts attempt
                 ON attempt.id=seal.attempt_id WHERE seal.run_id=NEW.id))
    THEN RAISE EXCEPTION 'ai_business_v4_seal_transition_denied'; END IF;
    RETURN NEW;
  END IF;
"""
RUN_GUARD = old_run.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1).replace(
    run_needle, SEAL_TRANSITION + run_needle, 1)
LOCK_PROBE = old_probe.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1).replace(
    probe_role_needle,
    "IF session_user NOT IN ('teruisi_ai_writer','teruisi_ai_seal_writer') AND session_user<>current_user",
    1).replace(probe_authority_needle,
    "IF session_user IN ('teruisi_ai_writer','teruisi_ai_seal_writer') AND NOT EXISTS", 1)
truncate_anchor = "  -- Both writer triggers take these row locks before changing facts."
if LOCK_PROBE.count(truncate_anchor) != 1:
    raise RuntimeError("v4 source lock probe truncate anchor changed")
LOCK_PROBE = LOCK_PROBE.replace(truncate_anchor, """
  IF has_table_privilege('teruisi_finance_writer','public.finance_lines','TRUNCATE')
     OR has_table_privilege('teruisi_finance_writer','public.finance_months','TRUNCATE')
     OR has_table_privilege('teruisi_finance_writer','public.finance_import_batches','TRUNCATE')
     OR has_table_privilege('teruisi_netshop_writer','public.netshop_rows','TRUNCATE')
     OR has_table_privilege('teruisi_netshop_writer','public.netshop_import_batches','TRUNCATE')
  THEN RAISE EXCEPTION 'ai_v4_admission_source_truncate_privilege'; END IF;
""" + truncate_anchor, 1)


SEAL_GUARD = """CREATE FUNCTION public.ai_business_v4_seal_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE parent public.ai_business_v4_runs%ROWTYPE;
  attempt public.ai_business_v4_validation_attempts%ROWTYPE;
  body jsonb; item jsonb; source public.ai_business_v4_sources%ROWTYPE;
  terminal public.ai_business_v4_validation_segments%ROWTYPE;
  expected_segments integer; segment_count bigint; source_count bigint;
  pages bigint; rows bigint; bytes bigint; finance_count integer:=0;
  promotion_count integer:=0; current_count integer:=0;
  common_shop text:=NULL; common_start text:=NULL; common_end text:=NULL;
  used_windows text[]:=ARRAY[]::text[];
BEGIN
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'ai_business_v4_seal_immutable'; END IF;
  IF session_user<>'teruisi_ai_seal_writer'
  THEN RAISE EXCEPTION 'ai_business_v4_seal_role_denied'; END IF;
  SELECT * INTO parent FROM public.ai_business_v4_runs
    WHERE id=NEW.run_id FOR UPDATE;
  SELECT * INTO attempt FROM public.ai_business_v4_validation_attempts
    WHERE id=NEW.attempt_id;
  IF NEW.body_json IS NULL OR octet_length(NEW.body_json)>131072
  THEN RAISE EXCEPTION 'ai_business_v4_seal_body_capacity_invalid'; END IF;
  body:=public.ai_screen_fields(NEW.body_json::json,ARRAY[
    'schemaVersion','runId','attemptId','evidenceVersion','planDigest',
    'directoryDigest','actorVersion','keyId','sourceCount','sources',
    'crossDomainSnapshotAtomic','financeDailyProrationAllowed',
    'inferSkuProfit','sumOverlappingErpB2bAdsAllowed',
    'upstreamSignatureVerified','reportGenerationSupported',
    'agentDispatchSupported','humanReviewRequired']);
  IF parent.id IS NULL OR attempt.id IS NULL OR attempt.run_id<>parent.id
     OR parent.status<>'collecting' OR parent.collection_status<>'manual'
     OR NEW.evidence_version<>parent.version+1
     OR attempt.run_version<>parent.version
     OR attempt.plan_digest<>parent.plan_digest
     OR attempt.actor_email<>parent.owner_email
     OR attempt.key_id<>NEW.key_id
     OR NEW.key_id !~ '^[0-9a-f]{16}$'
     OR NEW.body_mac !~ '^[0-9a-f]{64}$'
     OR NEW.body_digest IS DISTINCT FROM
       encode(sha256(convert_to(NEW.body_json,'UTF8')),'hex')
     OR body->>'schemaVersion' IS DISTINCT FROM 'business-v4-parent-seal-internal-v1'
     OR body->>'runId' IS DISTINCT FROM parent.id
     OR body->>'attemptId' IS DISTINCT FROM attempt.id
     OR (body->>'evidenceVersion')::bigint IS DISTINCT FROM NEW.evidence_version
     OR body->>'planDigest' IS DISTINCT FROM parent.plan_digest
     OR body->>'directoryDigest' IS DISTINCT FROM attempt.directory_digest
     OR (body->>'actorVersion')::bigint IS DISTINCT FROM attempt.actor_version
     OR body->>'keyId' IS DISTINCT FROM NEW.key_id
     OR body->'crossDomainSnapshotAtomic' IS DISTINCT FROM 'false'::jsonb
     OR body->'financeDailyProrationAllowed' IS DISTINCT FROM 'false'::jsonb
     OR body->'inferSkuProfit' IS DISTINCT FROM 'false'::jsonb
     OR body->'sumOverlappingErpB2bAdsAllowed' IS DISTINCT FROM 'false'::jsonb
     OR body->'upstreamSignatureVerified' IS DISTINCT FROM 'false'::jsonb
     OR body->'reportGenerationSupported' IS DISTINCT FROM 'false'::jsonb
     OR body->'agentDispatchSupported' IS DISTINCT FROM 'false'::jsonb
     OR body->'humanReviewRequired' IS DISTINCT FROM 'true'::jsonb
     OR jsonb_typeof(body->'sources') IS DISTINCT FROM 'array'
     OR NOT EXISTS (SELECT 1 FROM public.access_control_users account
       WHERE account.email=parent.owner_email AND account.role='admin'
         AND account.status='active' AND account.scope IS NULL
         AND account.version=attempt.actor_version)
     OR (SELECT id FROM public.ai_business_v4_validation_attempts
       WHERE run_id=parent.id ORDER BY created_at DESC,id DESC LIMIT 1)
       IS DISTINCT FROM attempt.id
  THEN RAISE EXCEPTION 'ai_business_v4_seal_identity_invalid'; END IF;
  SELECT count(*),coalesce(sum(page_count),0),coalesce(sum(row_count),0),
    coalesce(sum(stored_bytes),0) INTO source_count,pages,rows,bytes
    FROM public.ai_business_v4_sources WHERE run_id=parent.id;
  IF source_count NOT BETWEEN 2 AND 4
     OR source_count<>jsonb_array_length(body->'sources')
     OR (body->>'sourceCount')::integer IS DISTINCT FROM source_count
     OR source_count<>(parent.plan_json::jsonb->>'sourceCount')::integer
     OR pages<>parent.page_count OR rows<>parent.row_count
     OR bytes<>parent.stored_bytes
  THEN RAISE EXCEPTION 'ai_business_v4_seal_directory_invalid'; END IF;
  FOR source IN SELECT * FROM public.ai_business_v4_sources
      WHERE run_id=parent.id ORDER BY ordinal LOOP
    item:=public.ai_screen_fields(
      NEW.body_json::json->'sources'->(source.ordinal-1),
      CASE WHEN source.domain='finance' THEN ARRAY[
        'sourceKey','domain','queryDigest','sourceRef','sourceRevision',
        'sourceVersion','pageCount','rowCount','storedBytes','segmentCount',
        'terminalSegmentDigest','receiptChainDigest','revisionFreshness',
        'liveRevision','scope','analysisPeriod','missingMonths']
      ELSE ARRAY[
        'sourceKey','domain','queryDigest','sourceRef','sourceRevision',
        'sourceVersion','pageCount','rowCount','storedBytes','segmentCount',
        'terminalSegmentDigest','receiptChainDigest','revisionFreshness',
        'liveRevision','window','coverage'] END);
    expected_segments:=(source.page_count+15)/16;
    SELECT count(*) INTO segment_count FROM public.ai_business_v4_validation_segments
      WHERE attempt_id=attempt.id AND source_id=source.id;
    SELECT * INTO terminal FROM public.ai_business_v4_validation_segments
      WHERE attempt_id=attempt.id AND source_id=source.id
        AND segment_index=expected_segments;
    IF NOT source.finished OR source.page_count<1 OR segment_count<>expected_segments
       OR terminal.id IS NULL OR terminal.end_sequence<>source.page_count
       OR terminal.source_version<>source.version
       OR terminal.source_ref<>source.source_ref
       OR terminal.source_revision<>source.source_revision
       OR (terminal.progress_json::jsonb->>'pageCount')::bigint IS DISTINCT FROM source.page_count
       OR (terminal.progress_json::jsonb->>'rowCount')::bigint IS DISTINCT FROM source.row_count
       OR (terminal.progress_json::jsonb->>'storedBytes')::bigint IS DISTINCT FROM source.stored_bytes
       OR terminal.progress_json::jsonb->>'lastChunkDigest' IS DISTINCT FROM
          source.checkpoint_json::jsonb->>'lastChunkDigest'
       OR (SELECT count(*) FROM public.ai_business_v4_chunks chunk
           WHERE chunk.run_id=parent.id AND chunk.source_id=source.id)<>source.page_count
       OR (SELECT count(*) FROM public.ai_business_v4_tool_receipts receipt
           WHERE receipt.run_id=parent.id AND receipt.source_id=source.id)<>source.page_count
       OR item->>'sourceKey' IS DISTINCT FROM source.source_key
       OR item->>'domain' IS DISTINCT FROM source.domain
       OR item->>'queryDigest' IS DISTINCT FROM source.query_digest
       OR item->>'sourceRef' IS DISTINCT FROM source.source_ref
       OR item->>'sourceRevision' IS DISTINCT FROM source.source_revision
       OR (item->>'sourceVersion')::bigint IS DISTINCT FROM source.version
       OR (item->>'pageCount')::bigint IS DISTINCT FROM source.page_count
       OR (item->>'rowCount')::bigint IS DISTINCT FROM source.row_count
       OR (item->>'storedBytes')::bigint IS DISTINCT FROM source.stored_bytes
       OR (item->>'segmentCount')::integer IS DISTINCT FROM expected_segments
       OR item->>'terminalSegmentDigest' IS DISTINCT FROM terminal.proof_digest
       OR item->>'receiptChainDigest' IS DISTINCT FROM
          terminal.progress_json::jsonb->>'receiptChainDigest'
    THEN RAISE EXCEPTION 'ai_business_v4_seal_source_incomplete'; END IF;
    IF source.domain='finance' THEN
      finance_count:=finance_count+1;
      IF source.temporal_role<>'monthly_context'
         OR item->'scope' IS DISTINCT FROM source.query_json::jsonb->'scope'
         OR item->'analysisPeriod' IS DISTINCT FROM
            source.query_json::jsonb->'analysisPeriod'
         OR item->'missingMonths' IS DISTINCT FROM
            terminal.progress_json::jsonb->'domainState'->'publication'->'missingMonths'
      THEN RAISE EXCEPTION 'ai_business_v4_seal_finance_context_invalid'; END IF;
    ELSIF source.domain='netshop' THEN
      promotion_count:=promotion_count+1;
      IF source.temporal_role<>'daily_fact'
         OR source.query_json::jsonb->>'platform' IS DISTINCT FROM '京东'
         OR source.query_json::jsonb->>'dataset' IS DISTINCT FROM 'promotion'
         OR item->>'window' IS DISTINCT FROM source.query_json::jsonb->>'window'
         OR item->'coverage' IS DISTINCT FROM
            terminal.progress_json::jsonb->'domainState'->'metadata'->'coverage'
      THEN RAISE EXCEPTION 'ai_business_v4_seal_promotion_invalid'; END IF;
      IF source.query_json::jsonb->>'window'='current' THEN
        current_count:=current_count+1;
      END IF;
      IF source.query_json::jsonb->>'window'=ANY(used_windows)
      THEN RAISE EXCEPTION 'ai_business_v4_seal_duplicate_window'; END IF;
      used_windows:=array_append(used_windows,source.query_json::jsonb->>'window');
      IF common_shop IS NULL THEN
        common_shop:=source.query_json::jsonb->>'shop';
        common_start:=source.query_json::jsonb->>'startDate';
        common_end:=source.query_json::jsonb->>'endDate';
      ELSIF common_shop IS DISTINCT FROM source.query_json::jsonb->>'shop'
         OR common_start IS DISTINCT FROM source.query_json::jsonb->>'startDate'
         OR common_end IS DISTINCT FROM source.query_json::jsonb->>'endDate'
      THEN RAISE EXCEPTION 'ai_business_v4_seal_cross_shop_or_period'; END IF;
    ELSE
      RAISE EXCEPTION 'ai_business_v4_seal_unsupported_domain';
    END IF;
  END LOOP;
  IF finance_count<>1 OR promotion_count NOT BETWEEN 1 AND 3 OR current_count<>1
  THEN RAISE EXCEPTION 'ai_business_v4_seal_exact_source_set_invalid'; END IF;
  RETURN NEW;
END $$"""


COMMIT_SEAL = """CREATE FUNCTION public.ai_v4_commit_seal(
  selected_run_id text,selected_attempt_id text,expected_version bigint,
  canonical_body text,body_digest text,body_mac text,key_id text)
RETURNS TABLE(run_id text,evidence_version bigint,sealed_digest text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE parent public.ai_business_v4_runs%ROWTYPE;
  attempt public.ai_business_v4_validation_attempts%ROWTYPE;
  source public.ai_business_v4_sources%ROWTYPE;
  item jsonb; body jsonb;
  live_finance bigint; finance_hash text; live_netshop bigint; netshop_hash text;
  guard_text text; installed_at timestamptz;
  captured bigint; live bigint; current_hash text; updated_count integer;
BEGIN
  IF session_user<>'teruisi_ai_seal_writer'
  THEN RAISE EXCEPTION 'ai_business_v4_commit_role_denied'; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r
      WHERE r.rolname='teruisi_ai_seal_writer'
        AND NOT r.rolinherit AND NOT r.rolsuper AND NOT r.rolcreatedb
        AND NOT r.rolcreaterole AND NOT r.rolreplication AND NOT r.rolbypassrls)
     OR EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members
       WHERE roleid='teruisi_ai_seal_writer'::regrole
          OR member='teruisi_ai_seal_writer'::regrole)
     OR EXISTS (SELECT 1 FROM pg_catalog.pg_class c
       JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname='public' AND c.relname IN (
         'ai_business_v4_runs','ai_business_v4_sources',
         'ai_business_v4_chunks','ai_business_v4_tool_receipts',
         'ai_business_v4_validation_attempts','ai_business_v4_validation_segments',
         'ai_business_v4_seals','finance_lines','finance_data_revisions',
         'netshop_rows','netshop_data_revisions')
         AND c.relowner='teruisi_ai_seal_writer'::regrole)
     OR EXISTS (SELECT 1 FROM pg_catalog.pg_proc p
       WHERE p.oid IN (
         'public.ai_v4_commit_seal(text,text,bigint,text,text,text,text)'::regprocedure,
         'public.ai_v4_lock_source_revisions_for_admission()'::regprocedure)
         AND p.proowner='teruisi_ai_seal_writer'::regrole)
     OR has_table_privilege('teruisi_ai_seal_writer','public.ai_business_v4_runs','UPDATE')
     OR has_table_privilege('teruisi_ai_seal_writer','public.ai_business_v4_sources','UPDATE')
     OR has_table_privilege('teruisi_ai_seal_writer','public.ai_business_v4_chunks','INSERT')
     OR has_table_privilege('teruisi_ai_seal_writer','public.ai_business_v4_tool_receipts','INSERT')
     OR has_table_privilege('teruisi_ai_seal_writer','public.ai_business_v4_validation_attempts','INSERT')
     OR has_table_privilege('teruisi_ai_seal_writer','public.ai_business_v4_validation_segments','INSERT')
     OR has_table_privilege('teruisi_ai_seal_writer','public.ai_business_v4_seals','INSERT')
     OR has_table_privilege('teruisi_ai_seal_writer','public.ai_business_v4_seals','UPDATE')
     OR has_table_privilege('teruisi_ai_seal_writer','public.ai_business_v4_seals','DELETE')
     OR has_table_privilege('teruisi_ai_seal_writer','public.ai_business_v4_seals','TRUNCATE')
     OR has_table_privilege('teruisi_ai_writer','public.ai_business_v4_seals','TRUNCATE')
     OR has_table_privilege('teruisi_ai_seal_writer','public.finance_lines','UPDATE')
     OR has_table_privilege('teruisi_ai_seal_writer','public.finance_lines','INSERT')
     OR has_table_privilege('teruisi_ai_seal_writer','public.finance_lines','DELETE')
     OR has_table_privilege('teruisi_ai_seal_writer','public.finance_data_revisions','UPDATE')
     OR has_table_privilege('teruisi_ai_seal_writer','public.finance_data_revisions','INSERT')
     OR has_table_privilege('teruisi_ai_seal_writer','public.finance_data_revisions','DELETE')
     OR has_table_privilege('teruisi_ai_seal_writer','public.finance_source_revision_markers','INSERT')
     OR has_table_privilege('teruisi_ai_seal_writer','public.netshop_rows','UPDATE')
     OR has_table_privilege('teruisi_ai_seal_writer','public.netshop_rows','INSERT')
     OR has_table_privilege('teruisi_ai_seal_writer','public.netshop_rows','DELETE')
     OR has_table_privilege('teruisi_ai_seal_writer','public.netshop_data_revisions','UPDATE')
     OR has_table_privilege('teruisi_ai_seal_writer','public.netshop_data_revisions','INSERT')
     OR has_table_privilege('teruisi_ai_seal_writer','public.netshop_data_revisions','DELETE')
     OR has_table_privilege('teruisi_ai_seal_writer','public.netshop_source_revision_markers','INSERT')
     OR has_any_column_privilege('teruisi_ai_seal_writer','public.finance_lines','INSERT')
     OR has_any_column_privilege('teruisi_ai_seal_writer','public.finance_lines','UPDATE')
     OR has_any_column_privilege('teruisi_ai_seal_writer','public.finance_data_revisions','UPDATE')
     OR has_any_column_privilege('teruisi_ai_seal_writer','public.netshop_rows','INSERT')
     OR has_any_column_privilege('teruisi_ai_seal_writer','public.netshop_rows','UPDATE')
     OR has_any_column_privilege('teruisi_ai_seal_writer','public.netshop_data_revisions','UPDATE')
  THEN RAISE EXCEPTION 'ai_business_v4_commit_sealer_privileges_invalid'; END IF;
  IF canonical_body IS NULL OR octet_length(canonical_body)>131072
  THEN RAISE EXCEPTION 'ai_business_v4_commit_body_capacity_invalid'; END IF;
  body:=canonical_body::jsonb;
  SELECT probe.finance_revision,probe.finance_digest,
         probe.netshop_revision,probe.netshop_digest,
         probe.guard_version,probe.guard_installed_at
    INTO live_finance,finance_hash,live_netshop,netshop_hash,guard_text,installed_at
    FROM public.ai_v4_lock_source_revisions_for_admission() probe;
  IF guard_text<>'business-v4-source-write-fence-read-v1'
  THEN RAISE EXCEPTION 'ai_business_v4_commit_source_fence_missing'; END IF;
  SELECT * INTO parent FROM public.ai_business_v4_runs
    WHERE id=selected_run_id FOR UPDATE;
  SELECT * INTO attempt FROM public.ai_business_v4_validation_attempts
    WHERE id=selected_attempt_id;
  IF parent.id IS NULL OR attempt.id IS NULL OR attempt.run_id<>parent.id
     OR parent.status<>'collecting' OR parent.collection_status<>'manual'
     OR parent.version<>expected_version OR attempt.run_version<>parent.version
     OR attempt.created_at<=installed_at OR attempt.key_id<>key_id
     OR attempt.plan_digest<>parent.plan_digest
     OR body->>'runId' IS DISTINCT FROM parent.id
     OR body->>'attemptId' IS DISTINCT FROM attempt.id
     OR body->>'directoryDigest' IS DISTINCT FROM attempt.directory_digest
     OR body->>'keyId' IS DISTINCT FROM key_id
     OR NOT EXISTS (SELECT 1 FROM public.access_control_users actor
       WHERE actor.email=parent.owner_email AND actor.role='admin'
         AND actor.status='active' AND actor.scope IS NULL
         AND actor.version=attempt.actor_version)
  THEN RAISE EXCEPTION 'ai_business_v4_commit_cas_or_actor_invalid'; END IF;
  FOR source IN SELECT selected_source.* FROM public.ai_business_v4_sources selected_source
      WHERE selected_source.run_id=parent.id
      ORDER BY selected_source.ordinal FOR UPDATE OF selected_source LOOP
    item:=body->'sources'->(source.ordinal-1);
    captured:=split_part(source.source_revision,':',1)::bigint;
    IF source.domain='finance' THEN
      live:=live_finance; current_hash:=finance_hash;
    ELSIF source.domain='netshop' THEN
      live:=live_netshop; current_hash:=substr(netshop_hash,1,12);
    ELSE
      RAISE EXCEPTION 'ai_business_v4_commit_unsupported_source';
    END IF;
    IF live<captured OR (live=captured AND
         split_part(source.source_revision,':',2)<>current_hash)
       OR (item->>'liveRevision') IS DISTINCT FROM
         (live::text||':'||current_hash)
       OR (item->>'revisionFreshness') IS DISTINCT FROM
         (CASE WHEN live=captured THEN 'current_revision'
               ELSE 'historical_revision' END)
    THEN RAISE EXCEPTION 'ai_business_v4_commit_revision_drift'; END IF;
  END LOOP;
  INSERT INTO public.ai_business_v4_seals
    (run_id,attempt_id,evidence_version,body_json,body_digest,body_mac,key_id,created_at)
    VALUES (parent.id,attempt.id,parent.version+1,canonical_body,
      body_digest,body_mac,key_id,now());
  UPDATE public.ai_business_v4_runs SET status='sealed',version=version+1
    WHERE id=parent.id;
  UPDATE public.ai_data_revisions SET revision=revision+1,updated_at=now()
    WHERE domain='ai-assistant';
  GET DIAGNOSTICS updated_count=ROW_COUNT;
  IF updated_count<>1
  THEN RAISE EXCEPTION 'ai_business_v4_commit_global_revision_missing'; END IF;
  run_id:=parent.id; evidence_version:=parent.version+1; sealed_digest:=body_digest;
  RETURN NEXT;
END $$"""


TRUNCATE_GUARD = """CREATE FUNCTION public.ai_business_v4_seal_truncate_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
  -- TransactionTestCase/controlled maintenance must be able to flush this
  -- table as its actual owner. SET ROLE/GUC cannot impersonate session_user.
  IF EXISTS (SELECT 1 FROM pg_catalog.pg_class c
       WHERE c.oid=TG_RELID AND
         pg_catalog.pg_get_userbyid(c.relowner)=session_user)
     OR EXISTS (SELECT 1 FROM pg_catalog.pg_roles r
       WHERE r.rolname=session_user AND r.rolsuper)
  THEN RETURN NULL; END IF;
  RAISE EXCEPTION 'ai_business_v4_seal_truncate_denied';
END $$"""


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql": return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
            "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
            "WHERE rolname='teruisi_ai_seal_writer'")
        if cursor.fetchone() != (False,False,False,False,False,False,False):
            raise RuntimeError("0038需要受保护Provision预建精确NOLOGIN seal_writer")
        cursor.execute("SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members "
            "WHERE roleid='teruisi_ai_seal_writer'::regrole "
            "OR member='teruisi_ai_seal_writer'::regrole)")
        if cursor.fetchone()[0]:
            raise RuntimeError("0038 seal_writer不得继承或授出其他角色")
        cursor.execute("REVOKE ALL ON SCHEMA public FROM teruisi_ai_seal_writer")
        cursor.execute("GRANT USAGE ON SCHEMA public TO teruisi_ai_seal_writer")
        cursor.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM teruisi_ai_seal_writer")
        # 0035's deferred parent-counter trigger is SECURITY INVOKER at COMMIT
        # and reads these two tables under the dedicated session identity.
        # The sealer still receives no direct INSERT/UPDATE/DELETE privilege.
        cursor.execute("GRANT SELECT ON public.ai_business_v4_runs,"
            "public.ai_business_v4_sources TO teruisi_ai_seal_writer")
        cursor.execute(LOCK_PROBE)
        cursor.execute(RUN_GUARD)
        cursor.execute("DO $$ BEGIN "
            "IF to_regrole('teruisi_ai_reader') IS NOT NULL THEN "
            "REVOKE ALL ON FUNCTION "
            "public.ai_v4_lock_source_revisions_for_admission() FROM teruisi_ai_reader; "
            "END IF; "
            "IF to_regrole('teruisi_ai_writer') IS NOT NULL THEN "
            "GRANT EXECUTE ON FUNCTION public.ai_v4_lock_source_revisions_for_admission() "
            "TO teruisi_ai_writer; END IF; END $$")
        cursor.execute("GRANT EXECUTE ON FUNCTION "
            "public.ai_v4_lock_source_revisions_for_admission() "
            "TO teruisi_ai_seal_writer")
        cursor.execute(SEAL_GUARD)
        cursor.execute(COMMIT_SEAL)
        cursor.execute(TRUNCATE_GUARD)
        cursor.execute("REVOKE ALL ON FUNCTION public.ai_business_v4_seal_guard() FROM PUBLIC")
        cursor.execute("REVOKE ALL ON FUNCTION "
            "public.ai_v4_commit_seal(text,text,bigint,text,text,text,text) FROM PUBLIC")
        cursor.execute("DO $$ BEGIN "
            "IF to_regrole('teruisi_ai_writer') IS NOT NULL THEN "
            "REVOKE ALL ON FUNCTION public.ai_v4_commit_seal(text,text,bigint,text,text,text,text) "
            "FROM teruisi_ai_writer; END IF; "
            "IF to_regrole('teruisi_ai_reader') IS NOT NULL THEN "
            "REVOKE ALL ON FUNCTION public.ai_v4_commit_seal(text,text,bigint,text,text,text,text) "
            "FROM teruisi_ai_reader; END IF; END $$")
        cursor.execute("GRANT EXECUTE ON FUNCTION "
            "public.ai_v4_commit_seal(text,text,bigint,text,text,text,text) "
            "TO teruisi_ai_seal_writer")
        cursor.execute("REVOKE ALL ON public.ai_business_v4_seals FROM PUBLIC")
        cursor.execute("REVOKE ALL ON public.ai_business_v4_seals FROM "
            "teruisi_ai_seal_writer")
        cursor.execute("DO $$ BEGIN "
            "IF to_regrole('teruisi_ai_writer') IS NOT NULL THEN "
            "REVOKE ALL ON public.ai_business_v4_seals FROM teruisi_ai_writer; "
            "GRANT SELECT ON public.ai_business_v4_seals TO teruisi_ai_writer; END IF; "
            "IF to_regrole('teruisi_ai_reader') IS NOT NULL THEN "
            "REVOKE ALL ON public.ai_business_v4_seals FROM teruisi_ai_reader; "
            "END IF; END $$")
        cursor.execute("CREATE TRIGGER ai_write_fence BEFORE INSERT OR UPDATE OR DELETE "
            "ON public.ai_business_v4_seals FOR EACH ROW "
            "EXECUTE FUNCTION public.ai_runtime_write_fence()")
        cursor.execute("CREATE TRIGGER ai_immutable_v4 BEFORE UPDATE OR DELETE "
            "ON public.ai_business_v4_seals FOR EACH ROW "
            "EXECUTE FUNCTION public.ai_immutable_record_guard()")
        cursor.execute("CREATE TRIGGER ai_v4_seal_state BEFORE INSERT OR UPDATE OR DELETE "
            "ON public.ai_business_v4_seals FOR EACH ROW "
            "EXECUTE FUNCTION public.ai_business_v4_seal_guard()")
        cursor.execute("CREATE TRIGGER ai_v4_seal_no_truncate BEFORE TRUNCATE "
            "ON public.ai_business_v4_seals FOR EACH STATEMENT "
            "EXECUTE FUNCTION public.ai_business_v4_seal_truncate_guard()")
        cursor.execute("REVOKE ALL ON FUNCTION "
            "public.ai_business_v4_seal_truncate_guard() FROM PUBLIC")


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql": return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT EXISTS(SELECT 1 FROM public.ai_business_v4_seals) OR "
            "EXISTS(SELECT 1 FROM public.ai_business_v4_runs WHERE status='sealed')")
        if cursor.fetchone()[0]:
            raise RuntimeError("存在v4封存父任务，不能逆迁移并丢失独立seal边界")
        for trigger in ("ai_v4_seal_no_truncate","ai_v4_seal_state",
                        "ai_immutable_v4","ai_write_fence"):
            cursor.execute(f"DROP TRIGGER {trigger} ON public.ai_business_v4_seals")
        cursor.execute("DROP FUNCTION public.ai_v4_commit_seal(text,text,bigint,text,text,text,text)")
        cursor.execute("DROP FUNCTION public.ai_business_v4_seal_guard()")
        cursor.execute("DROP FUNCTION public.ai_business_v4_seal_truncate_guard()")
        cursor.execute("REVOKE EXECUTE ON FUNCTION "
            "public.ai_v4_lock_source_revisions_for_admission() "
            "FROM teruisi_ai_seal_writer")
        cursor.execute("REVOKE SELECT ON public.ai_business_v4_runs,"
            "public.ai_business_v4_sources FROM teruisi_ai_seal_writer")
        cursor.execute("REVOKE USAGE ON SCHEMA public FROM teruisi_ai_seal_writer")
        cursor.execute(old_run.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION",1))
        cursor.execute(old_probe.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION",1))


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0037_business_v4_seal_admission_read")]
    operations = [
        migrations.CreateModel(name="AiBusinessV4Seal", fields=[
            ("run", models.OneToOneField(primary_key=True, serialize=False,
                db_column="run_id", on_delete=django.db.models.deletion.PROTECT,
                to="ai_assistant.aibusinessv4run")),
            ("attempt", models.ForeignKey(to="ai_assistant.aibusinessv4validationattempt",
                on_delete=django.db.models.deletion.PROTECT)),
            ("evidence_version", models.PositiveBigIntegerField()),
            ("body_json", models.TextField()),
            ("body_digest", models.CharField(max_length=64)),
            ("body_mac", models.CharField(max_length=64)),
            ("key_id", models.CharField(max_length=16)),
            ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
        ], options={"db_table": "ai_business_v4_seals"}),
        migrations.RunPython(install, uninstall),
    ]
