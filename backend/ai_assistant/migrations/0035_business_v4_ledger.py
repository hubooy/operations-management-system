"""Isolated v4 physical ledger; no runtime collector, seal or public route."""
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


RUN_GUARD = """CREATE FUNCTION ai_business_v4_run_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE plan jsonb; owner_count bigint; all_count bigint;
BEGIN
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'ai_business_v4_delete_denied'; END IF;
  IF TG_OP='INSERT' THEN
    PERFORM pg_advisory_xact_lock(179,2048);
    SELECT count(*) INTO owner_count FROM public.ai_business_v4_runs WHERE owner_email=NEW.owner_email;
    SELECT count(*) INTO all_count FROM public.ai_business_v4_runs;
    plan:=NEW.plan_json::jsonb;
    IF owner_count>=2 OR all_count>=8 OR NEW.owner_email<>lower(NEW.owner_email)
       OR NEW.scope_json<>'null' OR NEW.status<>'collecting' OR NEW.collection_status<>'manual'
       OR NEW.version<>1 OR NEW.page_count<>0 OR NEW.row_count<>0 OR NEW.stored_bytes<>0
       OR octet_length(NEW.plan_json)>131072
       OR NEW.plan_digest IS DISTINCT FROM encode(sha256(convert_to(NEW.plan_json,'UTF8')),'hex')
       OR plan->>'schemaVersion' IS DISTINCT FROM 'business-evidence-v4-capacity-plan-v1'
       OR plan->>'capacityProfile' IS DISTINCT FROM 'business-evidence-v4-provisional-16k-2g-65k-8g-v1'
       OR plan->>'clientRequestId' IS DISTINCT FROM NEW.client_request_id
       OR plan->>'runIdentityDigest' IS DISTINCT FROM NEW.run_identity_digest
       OR coalesce(NEW.run_identity_digest,'') !~ '^[0-9a-f]{64}$'
       OR coalesce(plan->>'planDigest','') !~ '^[0-9a-f]{64}$'
       OR plan->'runCapacitySupported' IS DISTINCT FROM 'true'::jsonb
       OR plan->'sourceAuthorityVerified' IS DISTINCT FROM 'false'::jsonb
       OR plan->'measurementAuthorityVerified' IS DISTINCT FROM 'false'::jsonb
       OR plan->'productionRowWidthApprovalRequired' IS DISTINCT FROM 'true'::jsonb
       OR plan->'reportGenerationSupported' IS DISTINCT FROM 'false'::jsonb
       OR plan->'modelDispatchSupported' IS DISTINCT FROM 'false'::jsonb
       OR (plan->>'sourcePageCap')::bigint IS DISTINCT FROM 16384
       OR (plan->>'sourceByteCap')::bigint IS DISTINCT FROM 2147483648
       OR (plan->>'runPageCap')::bigint IS DISTINCT FROM 65536
       OR (plan->>'runByteCap')::bigint IS DISTINCT FROM 8589934592
       OR jsonb_typeof(plan->'sourcePlans') IS DISTINCT FROM 'array'
       OR jsonb_array_length(plan->'sourcePlans') NOT BETWEEN 2 AND 48
       OR (plan->>'sourceCount')::integer IS DISTINCT FROM jsonb_array_length(plan->'sourcePlans')
       OR NEW.client_request_id !~ '^[A-Za-z0-9_-]{1,160}$'
       OR NOT EXISTS (SELECT 1 FROM public.access_control_users u WHERE u.email=NEW.owner_email
         AND u.role='admin' AND u.status='active' AND u.scope IS NULL AND u.version>=1)
    THEN RAISE EXCEPTION 'ai_business_v4_initial_invalid'; END IF;
    RETURN NEW;
  END IF;
  IF OLD.status<>'collecting' OR NEW.status<>'collecting'
     OR OLD.collection_status<>'manual' OR NEW.collection_status<>'manual'
     OR NEW.version<>OLD.version+1 OR NEW.page_count<>OLD.page_count+1
     OR NEW.page_count>65536 OR NEW.stored_bytes<=OLD.stored_bytes
     OR NEW.stored_bytes-OLD.stored_bytes>131072 OR NEW.stored_bytes>8589934592
     OR NEW.row_count<OLD.row_count OR NEW.row_count-OLD.row_count>100
     OR NEW.row_count>6553600
     OR to_jsonb(NEW)-ARRAY['version','page_count','stored_bytes','row_count']
        IS DISTINCT FROM to_jsonb(OLD)-ARRAY['version','page_count','stored_bytes','row_count']
     OR NOT EXISTS (SELECT 1 FROM public.access_control_users u WHERE u.email=NEW.owner_email
       AND u.role='admin' AND u.status='active' AND u.scope IS NULL AND u.version>=1)
  THEN RAISE EXCEPTION 'ai_business_v4_parent_advance_invalid'; END IF;
  RETURN NEW;
END $$"""


SOURCE_GUARD = """CREATE FUNCTION ai_business_v4_source_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE parent public.ai_business_v4_runs%ROWTYPE; plan jsonb; entry jsonb; q jsonb;
  chunk public.ai_business_v4_chunks%ROWTYPE; checkpoint jsonb; page jsonb;
  first_day date; last_day date; month_text text; previous_month_id integer;
  first_month_id integer; last_month_id integer; month_id integer;
BEGIN
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'ai_business_v4_source_delete_denied'; END IF;
  IF octet_length(NEW.checkpoint_json)>32768
  THEN RAISE EXCEPTION 'ai_business_v4_checkpoint_capacity_invalid'; END IF;
  SELECT * INTO parent FROM public.ai_business_v4_runs WHERE id=NEW.run_id FOR UPDATE;
  IF NOT FOUND OR parent.status<>'collecting' OR parent.collection_status<>'manual'
     OR NOT EXISTS (SELECT 1 FROM public.access_control_users u WHERE u.email=parent.owner_email
       AND u.role='admin' AND u.status='active' AND u.scope IS NULL AND u.version>=1)
  THEN RAISE EXCEPTION 'ai_business_v4_source_parent_invalid'; END IF;
  IF TG_OP='INSERT' THEN
    plan:=parent.plan_json::jsonb;
    entry:=plan->'sourcePlans'->(NEW.ordinal-1);
    q:=NEW.query_json::jsonb;
    IF parent.version<>1 OR parent.page_count<>0 OR NEW.ordinal NOT BETWEEN 1 AND 48
       OR NEW.version<>1 OR NEW.page_count<>0 OR NEW.row_count<>0 OR NEW.stored_bytes<>0
       OR NEW.checkpoint_json<>'{}' OR NEW.finished OR NEW.source_ref<>'' OR NEW.source_revision<>''
       OR octet_length(NEW.query_json)>4096
       OR NEW.query_digest IS DISTINCT FROM encode(sha256(convert_to(NEW.query_json,'UTF8')),'hex')
       OR entry->>'sourceKey' IS DISTINCT FROM NEW.source_key
       OR (entry->>'ordinal')::integer IS DISTINCT FROM NEW.ordinal
       OR entry->>'domain' IS DISTINCT FROM NEW.domain
       OR entry->>'temporalRole' IS DISTINCT FROM NEW.temporal_role
       OR entry->'query' IS DISTINCT FROM q
       OR entry->>'queryDigest' IS DISTINCT FROM NEW.query_digest
       OR entry->>'sourceIdentityDigest' IS DISTINCT FROM NEW.source_identity_digest
       OR entry->>'sourceRevisionHint' IS DISTINCT FROM NEW.source_revision_hint
       OR entry->'sourceCapacitySupported' IS DISTINCT FROM 'true'::jsonb
       OR NEW.source_key !~ '^[A-Za-z0-9_-]{1,160}$'
       OR coalesce(NEW.source_identity_digest,'') !~ '^[0-9a-f]{64}$'
       OR length(NEW.source_revision_hint) NOT BETWEEN 1 AND 128
       OR NEW.domain NOT IN ('sales','netshop','market','finance')
       OR (NEW.domain='finance' AND NEW.temporal_role<>'monthly_context')
       OR (NEW.domain<>'finance' AND NEW.temporal_role<>'daily_fact')
    THEN RAISE EXCEPTION 'ai_business_v4_source_initial_invalid'; END IF;
    IF NEW.domain='finance' THEN
      PERFORM public.ai_screen_fields(q::json,ARRAY['months','scope','analysisPeriod']);
      PERFORM public.ai_screen_fields((q->'scope')::json,
        ARRAY['scope_key','scope_type','scope_name','group_name']);
      PERFORM public.ai_screen_fields((q->'analysisPeriod')::json,
        ARRAY['startDate','endDate']);
      IF jsonb_typeof(q->'months') IS DISTINCT FROM 'array'
         OR jsonb_array_length(q->'months') NOT BETWEEN 1 AND 24
         OR jsonb_typeof(q->'scope'->'scope_key') IS DISTINCT FROM 'string'
         OR length(q->'scope'->>'scope_key') NOT BETWEEN 1 AND 2000
         OR jsonb_typeof(q->'scope'->'scope_type') IS DISTINCT FROM 'string'
         OR q->'scope'->>'scope_type' NOT IN ('business','group','shop')
         OR jsonb_typeof(q->'scope'->'scope_name') IS DISTINCT FROM 'string'
         OR length(q->'scope'->>'scope_name') NOT BETWEEN 1 AND 1000
         OR jsonb_typeof(q->'scope'->'group_name') IS DISTINCT FROM 'string'
         OR jsonb_typeof(q->'analysisPeriod'->'startDate') IS DISTINCT FROM 'string'
         OR jsonb_typeof(q->'analysisPeriod'->'endDate') IS DISTINCT FROM 'string'
         OR q->'analysisPeriod'->>'startDate' !~ '^20[0-9]{2}-[0-9]{2}-[0-9]{2}$'
         OR q->'analysisPeriod'->>'endDate' !~ '^20[0-9]{2}-[0-9]{2}-[0-9]{2}$'
      THEN RAISE EXCEPTION 'ai_business_v4_finance_query_invalid'; END IF;
      first_day:=(q->'analysisPeriod'->>'startDate')::date;
      last_day:=(q->'analysisPeriod'->>'endDate')::date;
      previous_month_id:=NULL; first_month_id:=NULL;
      FOR month_text IN SELECT jsonb_array_elements_text(q->'months') LOOP
        IF month_text !~ '^(19|20|21)[0-9]{2}-(0[1-9]|1[0-2])$'
        THEN RAISE EXCEPTION 'ai_business_v4_finance_month_invalid'; END IF;
        month_id:=substring(month_text,1,4)::integer*12+substring(month_text,6,2)::integer-1;
        IF previous_month_id IS NOT NULL AND month_id<>previous_month_id+1
        THEN RAISE EXCEPTION 'ai_business_v4_finance_month_gap'; END IF;
        IF first_month_id IS NULL THEN first_month_id:=month_id; END IF;
        previous_month_id:=month_id;
      END LOOP;
      last_month_id:=previous_month_id;
    ELSE
      IF NEW.domain='sales' THEN
        PERFORM public.ai_screen_fields(q::json,
          ARRAY['platform','shop','channel','startDate','endDate','window']);
      ELSIF NEW.domain='netshop' THEN
        PERFORM public.ai_screen_fields(q::json,
          ARRAY['platform','shop','dataset','startDate','endDate','window']);
      ELSE
        PERFORM public.ai_screen_fields(q::json,
          ARRAY['platform','category','scope','rankingDimension','priceBandFilter',
            'startDate','endDate','window']);
      END IF;
      IF q->>'window' NOT IN ('current','previous','yearAgo')
         OR jsonb_typeof(q->'window') IS DISTINCT FROM 'string'
         OR jsonb_typeof(q->'platform') IS DISTINCT FROM 'string'
         OR length(q->>'platform')<1
         OR jsonb_typeof(q->'startDate') IS DISTINCT FROM 'string'
         OR jsonb_typeof(q->'endDate') IS DISTINCT FROM 'string'
         OR q->>'startDate' !~ '^20[0-9]{2}-[0-9]{2}-[0-9]{2}$'
         OR q->>'endDate' !~ '^20[0-9]{2}-[0-9]{2}-[0-9]{2}$'
         OR (NEW.domain IN ('sales','netshop') AND
             (jsonb_typeof(q->'shop') IS DISTINCT FROM 'string' OR length(q->>'shop')<1))
         OR (NEW.domain='sales' AND
             (jsonb_typeof(q->'channel') IS DISTINCT FROM 'string' OR length(q->>'channel')<1))
         OR (NEW.domain='netshop' AND (jsonb_typeof(q->'dataset') IS DISTINCT FROM 'string'
             OR q->>'dataset' NOT IN ('master','sku','spu','promotion','b2b')))
         OR (NEW.domain='market' AND (jsonb_typeof(q->'category') IS DISTINCT FROM 'string'
             OR length(q->>'category')<1
             OR jsonb_typeof(q->'scope') IS DISTINCT FROM 'string'
             OR length(q->>'scope')<1
             OR jsonb_typeof(q->'priceBandFilter') IS DISTINCT FROM 'string'
             OR length(q->>'priceBandFilter')<1
             OR jsonb_typeof(q->'rankingDimension') IS DISTINCT FROM 'string'
             OR q->>'rankingDimension' NOT IN ('SKU','SPU')))
      THEN RAISE EXCEPTION 'ai_business_v4_daily_query_invalid'; END IF;
      first_day:=(q->>'startDate')::date; last_day:=(q->>'endDate')::date;
    END IF;
    IF first_day>last_day OR first_day<DATE '2000-01-01' OR last_day>DATE '2098-12-31'
       OR last_day-first_day+1 NOT BETWEEN 1 AND 93
    THEN RAISE EXCEPTION 'ai_business_v4_period_invalid'; END IF;
    IF NEW.domain='finance' AND (
         first_month_id>extract(year from first_day)::integer*12+extract(month from first_day)::integer-1
         OR last_month_id<extract(year from last_day)::integer*12+extract(month from last_day)::integer-1)
    THEN RAISE EXCEPTION 'ai_business_v4_finance_month_coverage'; END IF;
    RETURN NEW;
  END IF;
  SELECT * INTO chunk FROM public.ai_business_v4_chunks
    WHERE run_id=NEW.run_id AND source_id=NEW.id AND sequence=NEW.page_count;
  checkpoint:=NEW.checkpoint_json::jsonb;
  page:=chunk.payload_json::jsonb;
  IF OLD.finished OR NEW.version<>OLD.version+1 OR NEW.page_count<>OLD.page_count+1
     OR NEW.page_count>16384 OR NEW.stored_bytes<=OLD.stored_bytes
     OR NEW.stored_bytes>2147483648 OR NEW.row_count<OLD.row_count
     OR NEW.row_count>1638400 OR NEW.updated_at<OLD.updated_at
     OR to_jsonb(NEW)-ARRAY['version','page_count','stored_bytes','row_count','finished',
         'checkpoint_json','source_ref','source_revision','updated_at']
        IS DISTINCT FROM to_jsonb(OLD)-ARRAY['version','page_count','stored_bytes','row_count','finished',
         'checkpoint_json','source_ref','source_revision','updated_at']
     OR chunk.id IS NULL OR NEW.stored_bytes-OLD.stored_bytes<>octet_length(chunk.payload_json)
     OR NEW.row_count-OLD.row_count<>chunk.row_count
     OR NEW.source_ref IS DISTINCT FROM chunk.source_ref
     OR NEW.source_revision IS DISTINCT FROM chunk.source_revision
     OR (OLD.page_count>0 AND (NEW.source_ref<>OLD.source_ref OR NEW.source_revision<>OLD.source_revision))
     OR checkpoint->>'schemaVersion' IS DISTINCT FROM 'business-v4-checkpoint-v1'
     OR checkpoint->>'sourceRef' IS DISTINCT FROM NEW.source_ref
     OR checkpoint->>'sourceRevision' IS DISTINCT FROM NEW.source_revision
     OR checkpoint->>'lastChunkDigest' IS DISTINCT FROM chunk.payload_digest
     OR (checkpoint->>'pageCount')::bigint IS DISTINCT FROM NEW.page_count
     OR (checkpoint->>'rowCount')::bigint IS DISTINCT FROM NEW.row_count
     OR (checkpoint->>'storedBytes')::bigint IS DISTINCT FROM NEW.stored_bytes
     OR checkpoint->'finished' IS DISTINCT FROM to_jsonb(NEW.finished)
  THEN RAISE EXCEPTION 'ai_business_v4_source_advance_invalid'; END IF;
  IF NEW.domain='finance' THEN
    IF NEW.finished IS DISTINCT FROM (page->'pagination'->'nextOffset'='null'::jsonb)
    THEN RAISE EXCEPTION 'ai_business_v4_finance_terminal_invalid'; END IF;
  ELSE
    IF NEW.finished IS DISTINCT FROM NOT (page->'pagination'->>'hasMore')::boolean
    THEN RAISE EXCEPTION 'ai_business_v4_daily_terminal_invalid'; END IF;
  END IF;
  RETURN NEW;
END $$"""


CHUNK_GUARD = """CREATE FUNCTION ai_business_v4_chunk_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE parent public.ai_business_v4_runs%ROWTYPE; source public.ai_business_v4_sources%ROWTYPE;
  page jsonb; q jsonb; filters jsonb; rows jsonb; maximum integer;
BEGIN
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'ai_business_v4_chunk_immutable'; END IF;
  SELECT * INTO parent FROM public.ai_business_v4_runs WHERE id=NEW.run_id FOR UPDATE;
  SELECT * INTO source FROM public.ai_business_v4_sources WHERE id=NEW.source_id FOR UPDATE;
  IF parent.id IS NULL OR source.id IS NULL OR source.run_id<>parent.id
     OR parent.status<>'collecting' OR parent.collection_status<>'manual' OR source.finished
     OR NEW.sequence<>source.page_count+1 OR NEW.sequence>16384
     OR parent.page_count>=65536
     OR NEW.source_ref !~ '^[0-9a-f]{64}$'
     OR length(NEW.source_revision) NOT BETWEEN 1 AND 128
     OR NEW.payload_digest IS DISTINCT FROM encode(sha256(convert_to(NEW.payload_json,'UTF8')),'hex')
     OR NEW.row_count>100 OR NEW.row_count<0
     OR (source.page_count>0 AND (NEW.source_ref<>source.source_ref
         OR NEW.source_revision<>source.source_revision))
     OR NOT EXISTS (SELECT 1 FROM public.access_control_users u WHERE u.email=parent.owner_email
       AND u.role='admin' AND u.status='active' AND u.scope IS NULL AND u.version>=1)
  THEN RAISE EXCEPTION 'ai_business_v4_chunk_identity_invalid'; END IF;
  maximum:=CASE WHEN source.domain='finance' THEN 38000 ELSE 131072 END;
  IF octet_length(NEW.payload_json)>maximum
     OR source.stored_bytes+octet_length(NEW.payload_json)>2147483648
     OR parent.stored_bytes+octet_length(NEW.payload_json)>8589934592
  THEN RAISE EXCEPTION 'ai_business_v4_chunk_capacity_invalid'; END IF;
  page:=NEW.payload_json::jsonb; q:=source.query_json::jsonb;
  rows:=CASE WHEN source.domain='finance' THEN page->'rows' ELSE page->'items' END;
  IF jsonb_typeof(rows) IS DISTINCT FROM 'array' OR jsonb_array_length(rows)<>NEW.row_count
     OR page->>'sourceRef' IS DISTINCT FROM NEW.source_ref
     OR page->>'sourceRevision' IS DISTINCT FROM NEW.source_revision
     OR jsonb_typeof(page->'pagination') IS DISTINCT FROM 'object'
  THEN RAISE EXCEPTION 'ai_business_v4_chunk_shape_invalid'; END IF;
  IF source.domain='finance' THEN
    IF page->>'schemaVersion' IS DISTINCT FROM 'business-finance-owned-page-v1'
       OR page->'query' IS DISTINCT FROM q
       OR page->'periodAlignment'->'analysisPeriod' IS DISTINCT FROM q->'analysisPeriod'
       OR (page->'pagination'->>'offset')::bigint IS DISTINCT FROM source.row_count
    THEN RAISE EXCEPTION 'ai_business_v4_finance_page_invalid'; END IF;
  ELSE
    filters:=page->'filters';
    IF page->>'schemaVersion' IS DISTINCT FROM 'business-analysis-v1'
       OR jsonb_typeof(filters) IS DISTINCT FROM 'object'
       OR filters->>'platform' IS DISTINCT FROM q->>'platform'
       OR filters->>'window' IS DISTINCT FROM q->>'window'
       OR filters->'periods'->'current'->>'startDate' IS DISTINCT FROM q->>'startDate'
       OR filters->'periods'->'current'->>'endDate' IS DISTINCT FROM q->>'endDate'
       OR page->'pagination'->>'limit' IS DISTINCT FROM '100'
       OR (source.domain='sales' AND (filters->>'shop' IS DISTINCT FROM q->>'shop'
           OR filters->>'channel' IS DISTINCT FROM q->>'channel'))
       OR (source.domain='netshop' AND (filters->>'shop' IS DISTINCT FROM q->>'shop'
           OR filters->>'dataset' IS DISTINCT FROM q->>'dataset'))
       OR (source.domain='market' AND (filters->>'category' IS DISTINCT FROM q->>'category'
           OR filters->>'scope' IS DISTINCT FROM q->>'scope'
           OR filters->>'rankingDimension' IS DISTINCT FROM q->>'rankingDimension'
           OR filters->>'priceBandFilter' IS DISTINCT FROM q->>'priceBandFilter'))
    THEN RAISE EXCEPTION 'ai_business_v4_daily_page_invalid'; END IF;
  END IF;
  RETURN NEW;
END $$"""


RECEIPT_GUARD = """CREATE FUNCTION ai_business_v4_receipt_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE parent public.ai_business_v4_runs%ROWTYPE; source public.ai_business_v4_sources%ROWTYPE;
  chunk public.ai_business_v4_chunks%ROWTYPE; audit public.ai_tool_audit_logs%ROWTYPE; expected_tool text;
BEGIN
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'ai_business_v4_receipt_immutable'; END IF;
  SELECT * INTO parent FROM public.ai_business_v4_runs WHERE id=NEW.run_id FOR UPDATE;
  SELECT * INTO source FROM public.ai_business_v4_sources WHERE id=NEW.source_id FOR UPDATE;
  SELECT * INTO chunk FROM public.ai_business_v4_chunks WHERE id=NEW.chunk_id;
  SELECT * INTO audit FROM public.ai_tool_audit_logs WHERE id=NEW.audit_id;
  IF parent.id IS NULL OR source.id IS NULL OR chunk.id IS NULL OR audit.id IS NULL
     OR parent.status<>'collecting' OR source.run_id<>parent.id
     OR chunk.run_id<>parent.id OR chunk.source_id<>source.id
     OR NEW.sequence<>chunk.sequence OR NEW.sequence<>source.page_count+1
     OR NEW.actor_email<>parent.owner_email OR NEW.actor_email<>audit.actor_email
     OR audit.actor_role<>'admin' OR audit.status<>'succeeded' OR audit.error_code IS NOT NULL
     OR NEW.surface<>'business_collection' OR audit.surface<>'business_collection'
     OR NEW.request_id<>audit.request_id OR NEW.invocation_id<>audit.invocation_id
     OR NEW.tool_name<>audit.tool_name
     OR NEW.response_digest IS DISTINCT FROM audit.response_digest
     OR NEW.response_digest<>chunk.payload_digest
     OR NEW.payload_bytes<>octet_length(chunk.payload_json)
     OR NEW.created_at<audit.created_at
     OR NOT EXISTS (SELECT 1 FROM public.access_control_users u WHERE u.email=parent.owner_email
       AND u.role='admin' AND u.status='active' AND u.scope IS NULL AND u.version>=1)
     OR EXISTS (SELECT 1 FROM public.ai_business_source_tool_receipts prior_receipt
       WHERE prior_receipt.audit_id=NEW.audit_id)
  THEN RAISE EXCEPTION 'ai_business_v4_receipt_binding_invalid'; END IF;
  IF source.domain='finance' THEN expected_tool:='get_business_finance_source_page';
  ELSIF NEW.sequence=1 THEN expected_tool:='get_business_source_page';
  ELSIF source.domain='sales' THEN expected_tool:='get_business_sales_continuation_page';
  ELSIF source.domain='netshop' THEN expected_tool:='get_business_netshop_continuation_page';
  ELSE expected_tool:='get_business_market_continuation_page'; END IF;
  IF NEW.tool_name<>expected_tool THEN RAISE EXCEPTION 'ai_business_v4_receipt_tool_invalid'; END IF;
  RETURN NEW;
END $$"""


COMPLETE_GUARD = """CREATE FUNCTION ai_business_v4_chunk_complete_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE parent public.ai_business_v4_runs%ROWTYPE; source public.ai_business_v4_sources%ROWTYPE;
  receipt public.ai_business_v4_tool_receipts%ROWTYPE; pages bigint; bytes bigint; rows bigint;
BEGIN
  SELECT * INTO parent FROM public.ai_business_v4_runs WHERE id=NEW.run_id;
  SELECT * INTO source FROM public.ai_business_v4_sources WHERE id=NEW.source_id;
  SELECT * INTO receipt FROM public.ai_business_v4_tool_receipts WHERE chunk_id=NEW.id;
  SELECT coalesce(sum(page_count),0),coalesce(sum(stored_bytes),0),coalesce(sum(row_count),0)
    INTO pages,bytes,rows FROM public.ai_business_v4_sources WHERE run_id=NEW.run_id;
  IF parent.id IS NULL OR source.id IS NULL OR receipt.chunk_id IS NULL
     OR source.page_count<NEW.sequence OR receipt.run_id<>parent.id
     OR receipt.source_id<>source.id OR receipt.sequence<>NEW.sequence
     OR parent.page_count<>pages OR parent.stored_bytes<>bytes OR parent.row_count<>rows
  THEN RAISE EXCEPTION 'ai_business_v4_chunk_uncommitted_or_missing_receipt'; END IF;
  RETURN NULL;
END $$"""


PARENT_COMPLETE_GUARD = """CREATE FUNCTION ai_business_v4_parent_complete_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE parent public.ai_business_v4_runs%ROWTYPE; pages bigint; bytes bigint; rows bigint;
BEGIN
  SELECT * INTO parent FROM public.ai_business_v4_runs WHERE id=NEW.id;
  SELECT coalesce(sum(page_count),0),coalesce(sum(stored_bytes),0),coalesce(sum(row_count),0)
    INTO pages,bytes,rows FROM public.ai_business_v4_sources WHERE run_id=NEW.id;
  IF parent.id IS NULL OR parent.page_count<>pages OR parent.stored_bytes<>bytes
     OR parent.row_count<>rows
  THEN RAISE EXCEPTION 'ai_business_v4_parent_source_counters_mismatch'; END IF;
  RETURN NULL;
END $$"""


DIRECTORY_GUARD = """CREATE FUNCTION ai_business_v4_initial_directory_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE parent public.ai_business_v4_runs%ROWTYPE; source_count bigint; first_ordinal integer;
  last_ordinal integer; one_pair text; first_pair text;
BEGIN
  SELECT * INTO parent FROM public.ai_business_v4_runs WHERE id=NEW.id;
  SELECT count(*),min(ordinal),max(ordinal) INTO source_count,first_ordinal,last_ordinal
    FROM public.ai_business_v4_sources WHERE run_id=NEW.id;
  IF source_count<>(parent.plan_json::jsonb->>'sourceCount')::integer
     OR first_ordinal<>1 OR last_ordinal<>source_count
     OR NOT EXISTS (SELECT 1 FROM public.ai_business_v4_sources WHERE run_id=NEW.id AND domain='finance')
     OR NOT EXISTS (SELECT 1 FROM public.ai_business_v4_sources WHERE run_id=NEW.id AND domain<>'finance')
  THEN RAISE EXCEPTION 'ai_business_v4_directory_incomplete'; END IF;
  SELECT CASE WHEN domain='finance' THEN (query_json::jsonb->'analysisPeriod'->>'startDate')||'/'||
      (query_json::jsonb->'analysisPeriod'->>'endDate')
      ELSE (query_json::jsonb->>'startDate')||'/'||(query_json::jsonb->>'endDate') END
    INTO first_pair FROM public.ai_business_v4_sources WHERE run_id=NEW.id ORDER BY ordinal LIMIT 1;
  FOR one_pair IN SELECT CASE WHEN domain='finance' THEN (query_json::jsonb->'analysisPeriod'->>'startDate')||'/'||
      (query_json::jsonb->'analysisPeriod'->>'endDate')
      ELSE (query_json::jsonb->>'startDate')||'/'||(query_json::jsonb->>'endDate') END
    FROM public.ai_business_v4_sources WHERE run_id=NEW.id LOOP
    IF one_pair IS DISTINCT FROM first_pair THEN RAISE EXCEPTION 'ai_business_v4_period_mismatch'; END IF;
  END LOOP;
  RETURN NULL;
END $$"""


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql": return
    with schema_editor.connection.cursor() as cursor:
        for table in ("ai_business_v4_runs", "ai_business_v4_sources", "ai_business_v4_chunks",
                      "ai_business_v4_tool_receipts"):
            cursor.execute(f"CREATE TRIGGER ai_write_fence BEFORE INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION ai_runtime_write_fence()")
        for table in ("ai_business_v4_chunks", "ai_business_v4_tool_receipts"):
            cursor.execute(f"CREATE TRIGGER ai_immutable_v4 BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION ai_immutable_record_guard()")
        for definition in (RUN_GUARD, SOURCE_GUARD, CHUNK_GUARD, RECEIPT_GUARD,
                           COMPLETE_GUARD, PARENT_COMPLETE_GUARD, DIRECTORY_GUARD):
            cursor.execute(definition)
        for table, function in (("ai_business_v4_runs", "ai_business_v4_run_guard"),
                                ("ai_business_v4_sources", "ai_business_v4_source_guard"),
                                ("ai_business_v4_chunks", "ai_business_v4_chunk_guard"),
                                ("ai_business_v4_tool_receipts", "ai_business_v4_receipt_guard")):
            cursor.execute(f"CREATE TRIGGER ai_v4_state BEFORE INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION {function}()")
        cursor.execute("CREATE CONSTRAINT TRIGGER ai_v4_chunk_complete AFTER INSERT ON ai_business_v4_chunks DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ai_business_v4_chunk_complete_guard()")
        cursor.execute("CREATE CONSTRAINT TRIGGER ai_v4_parent_complete AFTER UPDATE ON ai_business_v4_runs DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ai_business_v4_parent_complete_guard()")
        cursor.execute("CREATE CONSTRAINT TRIGGER ai_v4_directory_complete AFTER INSERT ON ai_business_v4_runs DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ai_business_v4_initial_directory_guard()")


def uninstall(apps, schema_editor):
    for name in ("AiBusinessV4ToolReceipt", "AiBusinessV4Chunk", "AiBusinessV4Source", "AiBusinessV4Run"):
        if apps.get_model("ai_assistant", name).objects.exists():
            raise RuntimeError("存在v4物理证据，不能逆迁移并丢弃来源账本")
    if schema_editor.connection.vendor != "postgresql": return
    with schema_editor.connection.cursor() as cursor:
        for table in ("ai_business_v4_runs", "ai_business_v4_sources", "ai_business_v4_chunks",
                      "ai_business_v4_tool_receipts"):
            for trigger in ("ai_v4_state", "ai_write_fence"):
                cursor.execute(f"DROP TRIGGER {trigger} ON {table}")
        for table in ("ai_business_v4_chunks", "ai_business_v4_tool_receipts"):
            cursor.execute(f"DROP TRIGGER ai_immutable_v4 ON {table}")
        cursor.execute("DROP TRIGGER ai_v4_chunk_complete ON ai_business_v4_chunks")
        cursor.execute("DROP TRIGGER ai_v4_parent_complete ON ai_business_v4_runs")
        cursor.execute("DROP TRIGGER ai_v4_directory_complete ON ai_business_v4_runs")
        for name in ("ai_business_v4_run_guard", "ai_business_v4_source_guard",
                     "ai_business_v4_chunk_guard", "ai_business_v4_receipt_guard",
                     "ai_business_v4_chunk_complete_guard", "ai_business_v4_parent_complete_guard",
                     "ai_business_v4_initial_directory_guard"):
            cursor.execute(f"DROP FUNCTION {name}()")


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0034_business_v3_report_intent")]
    operations = [
        migrations.CreateModel(name="AiBusinessV4Run", fields=[
            ("id", models.CharField(primary_key=True, serialize=False, max_length=160)),
            ("owner_email", models.CharField(max_length=320)),
            ("scope_json", models.TextField(default="null")),
            ("client_request_id", models.CharField(max_length=160)),
            ("plan_json", models.TextField()),
            ("plan_digest", models.CharField(max_length=64)),
            ("run_identity_digest", models.CharField(max_length=64)),
            ("status", models.CharField(max_length=20, default="collecting")),
            ("collection_status", models.CharField(max_length=20, default="manual")),
            ("version", models.PositiveBigIntegerField(default=1)),
            ("stored_bytes", models.PositiveBigIntegerField(default=0)),
            ("page_count", models.PositiveBigIntegerField(default=0)),
            ("row_count", models.PositiveBigIntegerField(default=0)),
            ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
        ], options={"db_table": "ai_business_v4_runs",
            "constraints": [models.UniqueConstraint(fields=("owner_email", "client_request_id"), name="ai_v4_run_client_uq")],
            "indexes": [models.Index(fields=("owner_email", "-created_at"), name="ai_v4_run_owner_idx")]}),
        migrations.CreateModel(name="AiBusinessV4Source", fields=[
            ("id", models.CharField(primary_key=True, serialize=False, max_length=160)),
            ("run", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                to="ai_assistant.aibusinessv4run")),
            ("source_key", models.CharField(max_length=160)),
            ("ordinal", models.PositiveIntegerField()),
            ("domain", models.CharField(max_length=20)),
            ("temporal_role", models.CharField(max_length=30)),
            ("query_json", models.TextField()),
            ("query_digest", models.CharField(max_length=64)),
            ("source_identity_digest", models.CharField(max_length=64)),
            ("source_revision_hint", models.CharField(max_length=128)),
            ("source_ref", models.CharField(max_length=64, default="")),
            ("source_revision", models.CharField(max_length=128, default="")),
            ("checkpoint_json", models.TextField(default="{}")),
            ("version", models.PositiveBigIntegerField(default=1)),
            ("page_count", models.PositiveBigIntegerField(default=0)),
            ("stored_bytes", models.PositiveBigIntegerField(default=0)),
            ("row_count", models.PositiveBigIntegerField(default=0)),
            ("finished", models.BooleanField(default=False)),
            ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
            ("updated_at", models.DateTimeField(default=django.utils.timezone.now)),
        ], options={"db_table": "ai_business_v4_sources",
            "constraints": [
                models.UniqueConstraint(fields=("run", "source_key"), name="ai_v4_source_key_uq"),
                models.UniqueConstraint(fields=("run", "ordinal"), name="ai_v4_source_ord_uq"),
                models.UniqueConstraint(fields=("run", "domain", "query_digest"), name="ai_v4_source_query_uq")],
            "indexes": [models.Index(fields=("run", "finished", "ordinal"), name="ai_v4_source_next_idx")]}),
        migrations.CreateModel(name="AiBusinessV4Chunk", fields=[
            ("id", models.CharField(primary_key=True, serialize=False, max_length=160)),
            ("run", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                to="ai_assistant.aibusinessv4run")),
            ("source", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                to="ai_assistant.aibusinessv4source")),
            ("sequence", models.PositiveBigIntegerField()),
            ("payload_json", models.TextField()),
            ("payload_digest", models.CharField(max_length=64)),
            ("source_ref", models.CharField(max_length=64)),
            ("source_revision", models.CharField(max_length=128)),
            ("row_count", models.PositiveIntegerField()),
            ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
        ], options={"db_table": "ai_business_v4_chunks",
            "constraints": [models.UniqueConstraint(fields=("run", "source", "sequence"), name="ai_v4_chunk_sequence_uq")]}),
        migrations.CreateModel(name="AiBusinessV4ToolReceipt", fields=[
            ("chunk", models.OneToOneField(primary_key=True, serialize=False, db_column="chunk_id",
                on_delete=django.db.models.deletion.PROTECT, to="ai_assistant.aibusinessv4chunk")),
            ("audit", models.OneToOneField(db_column="audit_id", on_delete=django.db.models.deletion.PROTECT,
                to="ai_assistant.aitoolauditlogs")),
            ("run", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                to="ai_assistant.aibusinessv4run")),
            ("source", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                to="ai_assistant.aibusinessv4source")),
            ("sequence", models.PositiveBigIntegerField()),
            ("actor_email", models.CharField(max_length=320)),
            ("request_id", models.CharField(max_length=128)),
            ("invocation_id", models.CharField(max_length=160)),
            ("tool_name", models.CharField(max_length=100)),
            ("surface", models.CharField(max_length=40, default="business_collection")),
            ("response_digest", models.CharField(max_length=64)),
            ("payload_bytes", models.PositiveIntegerField()),
            ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
        ], options={"db_table": "ai_business_v4_tool_receipts",
            "constraints": [models.UniqueConstraint(fields=("run", "source", "sequence"), name="ai_v4_receipt_seq_uq")]}),
        migrations.RunPython(install, uninstall),
    ]
