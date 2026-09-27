"""Inert v3 finance directory gate; collection and reports remain forbidden.

Old v1/v2 rows, checks and the v2 branch of ai_business_source_guard retain
their original meaning. A later migration must explicitly enable v3 facts.
"""
from django.db import migrations


SOURCE_BOUND = """CHECK (
  source_key ~ '^[A-Za-z0-9_-]{1,160}$' AND ordinal BETWEEN 1 AND 48
  AND domain IN ('sales','netshop','market','finance') AND query_digest ~ '^[0-9a-f]{64}$'
  AND octet_length(query_json)<=4096 AND jsonb_typeof(query_json::jsonb)='object'
  AND octet_length(checkpoint_json)<=32768 AND jsonb_typeof(checkpoint_json::jsonb)='object'
  AND version>=1 AND checkpoint_run_version>=1 AND page_count<=2000
  AND stored_bytes<=67108864 AND row_count<=9007199254740991 AND updated_at>=created_at
)"""

SOURCE_GUARD_BASE = """CREATE OR REPLACE FUNCTION ai_business_source_guard() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,public AS $$
DECLARE parent public.ai_business_evidence_runs%ROWTYPE; header jsonb;
BEGIN
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'ai_business_source_delete_denied'; END IF;
  SELECT * INTO parent FROM public.ai_business_evidence_runs WHERE id=NEW.run_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'ai_business_source_parent_missing'; END IF;
  header:=public.ai_business_v2_header(parent.plan_json);
  IF header IS NULL OR parent.status<>'collecting' THEN RAISE EXCEPTION 'ai_business_source_parent_unavailable'; END IF;
  IF TG_OP='INSERT' THEN
    IF parent.version<>1 OR EXISTS(SELECT 1 FROM public.ai_business_evidence_chunks WHERE run_id=parent.id)
       OR NEW.version<>1 OR NEW.checkpoint_run_version<>1 OR NEW.checkpoint_json<>'{}'
       OR NEW.page_count<>0 OR NEW.stored_bytes<>0 OR NEW.row_count<>0 OR NEW.finished
    THEN RAISE EXCEPTION 'ai_business_source_initial_state'; END IF;
  ELSE
    IF OLD.finished OR NEW.version<>OLD.version+1 OR NEW.checkpoint_run_version<>parent.version+1
       OR NEW.page_count<OLD.page_count OR NEW.stored_bytes<OLD.stored_bytes OR NEW.row_count<OLD.row_count
       OR NEW.updated_at<OLD.updated_at
    THEN RAISE EXCEPTION 'ai_business_source_checkpoint_fence'; END IF;
  END IF;
  RETURN NEW;
END $$"""

# 0019's installed function body carries eight leading spaces on every line
# after its signature. Restore the exact bytes, not merely equivalent SQL.
_parts = SOURCE_GUARD_BASE.split("\n")
OLD_SOURCE_GUARD = _parts[0] + "\n" + "\n".join("        " + part for part in _parts[1:])

V3_HEADER = """CREATE FUNCTION ai_business_v3_header(raw text) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,public AS $$
DECLARE value json; header jsonb;
BEGIN
  BEGIN value:=raw::json;
  EXCEPTION WHEN invalid_text_representation THEN
    IF position('business-evidence-v3' in raw)>0 THEN RAISE EXCEPTION 'ai_business_v3_header_invalid'; END IF;
    RETURN NULL;
  END;
  IF value->>'schemaVersion' IS DISTINCT FROM 'business-evidence-v3' THEN RETURN NULL; END IF;
  header:=public.ai_screen_fields(value,ARRAY['schemaVersion','sourceCount','catalogDigest','capacityProfile',
    'collector','limits','analysisRequest','dailyContractSchema','financeSourceSchema','financePolicy',
    'reportGenerationSupported','sourceAuthorityVerified','persistentEvidenceVerified',
    'businessCoverageVerified','modelAnalysisCompleted']);
  IF public.ai_screen_uint(value->'sourceCount',2,48) IS NULL
     OR json_typeof(value->'catalogDigest') IS DISTINCT FROM 'string'
     OR header->>'catalogDigest' !~ '^[0-9a-f]{64}$'
     OR header->>'capacityProfile' IS DISTINCT FROM 'catalog-48-facts-monthly-context-v1'
     OR public.ai_screen_fields(value->'collector',ARRAY['version','surface','pageSize'])
        IS DISTINCT FROM '{"version":2,"surface":"business_collection","pageSize":100}'::jsonb
     OR public.ai_screen_fields(value->'limits',ARRAY['factBytes','factPages'])
        IS DISTINCT FROM '{"factBytes":67108864,"factPages":2000}'::jsonb
     OR public.ai_screen_fields(value->'analysisRequest',ARRAY['schemaVersion','question','requestedDimensions','requestedWindows'])
        ->>'schemaVersion' IS DISTINCT FROM 'business-analysis-request-v1'
     OR header->>'dailyContractSchema' IS DISTINCT FROM 'business-evidence-v2'
     OR header->>'financeSourceSchema' IS DISTINCT FROM 'business-finance-monthly-source-v1'
     OR public.ai_screen_fields(value->'financePolicy',ARRAY['temporalRole','missingMonthsAreGaps',
          'dailyProrationAllowed','sumSourceRates','sumTotalsAndDetails','skuProfitAttributionAllowed',
          'crossSourceAmountAdditivityVerified','shopIdentityMappingVerified']) IS DISTINCT FROM
        '{"temporalRole":"monthly_context","missingMonthsAreGaps":true,"dailyProrationAllowed":false,
          "sumSourceRates":false,"sumTotalsAndDetails":false,"skuProfitAttributionAllowed":false,
          "crossSourceAmountAdditivityVerified":false,"shopIdentityMappingVerified":false}'::jsonb
     OR header->'reportGenerationSupported' IS DISTINCT FROM 'false'::jsonb
     OR header->'sourceAuthorityVerified' IS DISTINCT FROM 'false'::jsonb
     OR header->'persistentEvidenceVerified' IS DISTINCT FROM 'false'::jsonb
     OR header->'businessCoverageVerified' IS DISTINCT FROM 'false'::jsonb
     OR header->'modelAnalysisCompleted' IS DISTINCT FROM 'false'::jsonb
  THEN RAISE EXCEPTION 'ai_business_v3_header_invalid'; END IF;
  RETURN header;
END $$"""

V3_RUN_GUARD = """CREATE FUNCTION ai_business_v3_run_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE header jsonb;
BEGIN
  header:=public.ai_business_v3_header(NEW.plan_json);
  IF header IS NULL THEN RETURN NEW; END IF;
  IF TG_OP<>'INSERT' OR NEW.status<>'collecting' OR NEW.version<>1 OR NEW.scope_json<>'null'
     OR NEW.stored_bytes<>0 OR NEW.state_json<>'{}' OR lower(NEW.owner_email)<>NEW.owner_email
     OR NOT EXISTS (SELECT 1 FROM public.access_control_users u WHERE u.email=NEW.owner_email
       AND u.role='admin' AND u.status='active' AND u.scope IS NULL AND u.version>=1)
  THEN RAISE EXCEPTION 'ai_business_v3_initial_owner_or_state_invalid'; END IF;
  RETURN NEW;
END $$"""

V3_SOURCE_GUARD = SOURCE_GUARD_BASE.replace(
    "DECLARE parent public.ai_business_evidence_runs%ROWTYPE; header jsonb;",
    "DECLARE parent public.ai_business_evidence_runs%ROWTYPE; header jsonb; q jsonb; raw json; s jsonb; period jsonb; m json; month_text text; month_id integer; previous_id integer; first_id integer; last_id integer; i integer; first_day date; last_day date;")
V3_SOURCE_GUARD = V3_SOURCE_GUARD.replace(
    "  IF header IS NULL OR parent.status<>'collecting' THEN RAISE EXCEPTION 'ai_business_source_parent_unavailable'; END IF;",
    """  IF header IS NULL THEN
    header:=public.ai_business_v3_header(parent.plan_json);
    IF header IS NULL OR TG_OP<>'INSERT' OR parent.status<>'collecting' OR parent.version<>1
       OR EXISTS(SELECT 1 FROM public.ai_business_evidence_chunks WHERE run_id=parent.id)
       OR NEW.version<>1 OR NEW.checkpoint_run_version<>1 OR NEW.checkpoint_json<>'{}'
       OR NEW.page_count<>0 OR NEW.stored_bytes<>0 OR NEW.row_count<>0 OR NEW.finished
       OR NEW.query_digest<>encode(sha256(convert_to(NEW.query_json,'UTF8')),'hex')
       OR NOT EXISTS (SELECT 1 FROM public.access_control_users u WHERE u.email=parent.owner_email
         AND u.role='admin' AND u.status='active' AND u.scope IS NULL AND u.version>=1)
    THEN RAISE EXCEPTION 'ai_business_v3_source_initial_invalid'; END IF;
    raw:=NEW.query_json::json;
    IF NEW.domain='finance' THEN
      q:=public.ai_screen_fields(raw,ARRAY['months','scope','analysisPeriod']);
      s:=public.ai_screen_fields(raw->'scope',ARRAY['scope_key','scope_type','scope_name','group_name']);
      period:=public.ai_screen_fields(raw->'analysisPeriod',ARRAY['startDate','endDate']);
      IF json_typeof(raw->'months') IS DISTINCT FROM 'array'
         OR json_array_length(raw->'months') NOT BETWEEN 1 AND 24
         OR s->>'scope_type' NOT IN ('business','group','shop')
         OR json_typeof(raw->'scope'->'scope_key') IS DISTINCT FROM 'string'
         OR json_typeof(raw->'scope'->'scope_type') IS DISTINCT FROM 'string'
         OR json_typeof(raw->'scope'->'scope_name') IS DISTINCT FROM 'string'
         OR json_typeof(raw->'scope'->'group_name') IS DISTINCT FROM 'string'
         OR length(s->>'scope_key') NOT BETWEEN 1 AND 2000
         OR length(s->>'scope_name')>1000 OR length(s->>'group_name')>1000
         OR json_typeof(raw->'analysisPeriod'->'startDate') IS DISTINCT FROM 'string'
         OR json_typeof(raw->'analysisPeriod'->'endDate') IS DISTINCT FROM 'string'
         OR period->>'startDate' !~ '^(20[0-9]{2})-[0-9]{2}-[0-9]{2}$'
         OR period->>'endDate' !~ '^(20[0-9]{2})-[0-9]{2}-[0-9]{2}$'
      THEN RAISE EXCEPTION 'ai_business_v3_finance_scope_invalid'; END IF;
      first_day:=(period->>'startDate')::date; last_day:=(period->>'endDate')::date;
      IF first_day>last_day OR first_day<DATE '2000-01-01' OR last_day>DATE '2098-12-31'
         OR to_char(first_day,'YYYY-MM-DD')<>period->>'startDate'
         OR to_char(last_day,'YYYY-MM-DD')<>period->>'endDate'
      THEN RAISE EXCEPTION 'ai_business_v3_finance_period_invalid'; END IF;
      previous_id:=NULL; first_id:=NULL;
      FOR m IN SELECT value FROM json_array_elements(raw->'months') AS t(value) LOOP
        IF json_typeof(m) IS DISTINCT FROM 'string' OR m#>>'{}' !~ '^(19|20|21)[0-9]{2}-(0[1-9]|1[0-2])$'
        THEN RAISE EXCEPTION 'ai_business_v3_finance_month_invalid'; END IF;
        month_text:=m#>>'{}'; month_id:=substring(month_text,1,4)::integer*12+substring(month_text,6,2)::integer-1;
        IF previous_id IS NOT NULL AND month_id<>previous_id+1
        THEN RAISE EXCEPTION 'ai_business_v3_finance_month_gap'; END IF;
        IF first_id IS NULL THEN first_id:=month_id; END IF;
        previous_id:=month_id;
      END LOOP;
      last_id:=previous_id;
      IF first_id>extract(year from first_day)::integer*12+extract(month from first_day)::integer-1
         OR last_id<extract(year from last_day)::integer*12+extract(month from last_day)::integer-1
      THEN RAISE EXCEPTION 'ai_business_v3_finance_month_coverage'; END IF;
    ELSIF NEW.domain IN ('sales','netshop','market') THEN
      IF json_typeof(raw->'startDate') IS DISTINCT FROM 'string'
         OR json_typeof(raw->'endDate') IS DISTINCT FROM 'string'
         OR json_typeof(raw->'window') IS DISTINCT FROM 'string'
         OR raw->>'window' NOT IN ('current','previous','yearAgo')
         OR raw->>'startDate' !~ '^20[0-9]{2}-[0-9]{2}-[0-9]{2}$'
         OR raw->>'endDate' !~ '^20[0-9]{2}-[0-9]{2}-[0-9]{2}$'
      THEN RAISE EXCEPTION 'ai_business_v3_daily_query_invalid'; END IF;
      IF NEW.domain='sales' THEN
        q:=public.ai_screen_fields(raw,ARRAY['platform','shop','channel','startDate','endDate','window']);
      ELSIF NEW.domain='netshop' THEN
        q:=public.ai_screen_fields(raw,ARRAY['platform','shop','dataset','startDate','endDate','window']);
      ELSE
        q:=public.ai_screen_fields(raw,ARRAY['platform','category','scope','rankingDimension','priceBandFilter','startDate','endDate','window']);
      END IF;
      first_day:=(raw->>'startDate')::date; last_day:=(raw->>'endDate')::date;
      IF first_day>last_day OR first_day<DATE '2000-01-01' OR last_day>DATE '2098-12-31'
         OR last_day-first_day>92 OR to_char(first_day,'YYYY-MM-DD')<>raw->>'startDate'
         OR to_char(last_day,'YYYY-MM-DD')<>raw->>'endDate'
      THEN RAISE EXCEPTION 'ai_business_v3_daily_period_invalid'; END IF;
    ELSE RAISE EXCEPTION 'ai_business_v3_domain_invalid'; END IF;
    RETURN NEW;
  END IF;
  IF NEW.domain='finance' THEN RAISE EXCEPTION 'ai_business_v2_finance_domain_denied'; END IF;
  IF parent.status<>'collecting' THEN RAISE EXCEPTION 'ai_business_source_parent_unavailable'; END IF;""")

V3_DIRECTORY_GUARD = """CREATE FUNCTION ai_business_v3_directory_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE parent public.ai_business_evidence_runs%ROWTYPE; header jsonb; target text;
  total bigint; first_ordinal integer; last_ordinal integer; finance_count bigint; daily_count bigint;
  query_bytes bigint; date_pair text; one_pair text; mismatched boolean;
BEGIN
  IF TG_TABLE_NAME='ai_business_evidence_runs' THEN
    target:=NEW.id;
  ELSIF TG_TABLE_NAME='ai_business_evidence_sources' THEN
    target:=NEW.run_id;
  ELSE
    RAISE EXCEPTION 'ai_business_v3_directory_table_invalid';
  END IF;
  SELECT * INTO parent FROM public.ai_business_evidence_runs WHERE id=target FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'ai_business_v3_parent_missing'; END IF;
  header:=public.ai_business_v3_header(parent.plan_json);
  IF header IS NULL THEN RETURN NULL; END IF;
  SELECT count(*),min(ordinal),max(ordinal),count(*) FILTER (WHERE domain='finance'),
    count(*) FILTER (WHERE domain<>'finance'),COALESCE(sum(octet_length(query_json)),0),
    bool_or(page_count<>0 OR stored_bytes<>0 OR row_count<>0 OR finished OR checkpoint_json<>'{}')
  INTO total,first_ordinal,last_ordinal,finance_count,daily_count,query_bytes,mismatched
  FROM public.ai_business_evidence_sources WHERE run_id=target;
  IF total<>(header->>'sourceCount')::integer OR first_ordinal<>1 OR last_ordinal<>total
     OR finance_count<1 OR daily_count<1 OR query_bytes>131072 OR mismatched
     OR parent.version<>1 OR parent.status<>'collecting' OR parent.stored_bytes<>0 OR parent.state_json<>'{}'
     OR EXISTS(SELECT 1 FROM public.ai_business_evidence_chunks WHERE run_id=target)
     OR NOT EXISTS (SELECT 1 FROM public.access_control_users u WHERE u.email=parent.owner_email
       AND u.role='admin' AND u.status='active' AND u.scope IS NULL AND u.version>=1)
  THEN RAISE EXCEPTION 'ai_business_v3_directory_incomplete'; END IF;
  SELECT (query_json::jsonb->>'startDate')||'/'||(query_json::jsonb->>'endDate') INTO date_pair
    FROM public.ai_business_evidence_sources WHERE run_id=target AND domain<>'finance' LIMIT 1;
  FOR one_pair IN SELECT (query_json::jsonb->>'startDate')||'/'||(query_json::jsonb->>'endDate')
    FROM public.ai_business_evidence_sources WHERE run_id=target AND domain<>'finance' LOOP
    IF one_pair IS DISTINCT FROM date_pair THEN RAISE EXCEPTION 'ai_business_v3_daily_dates_mismatch'; END IF;
  END LOOP;
  FOR one_pair IN SELECT (query_json::jsonb->'analysisPeriod'->>'startDate')||'/'||
    (query_json::jsonb->'analysisPeriod'->>'endDate')
    FROM public.ai_business_evidence_sources WHERE run_id=target AND domain='finance' LOOP
    IF one_pair IS DISTINCT FROM date_pair THEN RAISE EXCEPTION 'ai_business_v3_finance_dates_mismatch'; END IF;
  END LOOP;
  RETURN NULL;
END $$"""

V3_CHUNK_GUARD = """CREATE FUNCTION ai_business_v3_chunk_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE parent public.ai_business_evidence_runs%ROWTYPE;
BEGIN
  SELECT * INTO parent FROM public.ai_business_evidence_runs WHERE id=NEW.run_id FOR UPDATE;
  IF FOUND AND public.ai_business_v3_header(parent.plan_json) IS NOT NULL
  THEN RAISE EXCEPTION 'ai_business_v3_collection_disabled'; END IF;
  RETURN NEW;
END $$"""


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("ALTER TABLE ai_business_evidence_sources DROP CONSTRAINT ai_business_source_bound")
        cursor.execute("ALTER TABLE ai_business_evidence_sources ADD CONSTRAINT ai_business_source_bound " + SOURCE_BOUND)
        for definition in (V3_HEADER, V3_RUN_GUARD, V3_SOURCE_GUARD, V3_DIRECTORY_GUARD, V3_CHUNK_GUARD):
            cursor.execute(definition)
        cursor.execute("CREATE TRIGGER ai_business_v3_run BEFORE INSERT OR UPDATE ON ai_business_evidence_runs FOR EACH ROW EXECUTE FUNCTION ai_business_v3_run_guard()")
        for table in ("ai_business_evidence_runs", "ai_business_evidence_sources"):
            cursor.execute(f"CREATE CONSTRAINT TRIGGER ai_business_v3_directory_complete AFTER INSERT OR UPDATE ON {table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ai_business_v3_directory_guard()")
        cursor.execute("CREATE TRIGGER ai_business_v3_chunk BEFORE INSERT ON ai_business_evidence_chunks FOR EACH ROW EXECUTE FUNCTION ai_business_v3_chunk_guard()")


def uninstall(apps, schema_editor):
    runs = apps.get_model("ai_assistant", "AiBusinessEvidenceRun")
    sources = apps.get_model("ai_assistant", "AiBusinessEvidenceSource")
    if runs.objects.filter(plan_json__contains='"schemaVersion":"business-evidence-v3"').exists() or sources.objects.filter(domain="finance").exists():
        raise RuntimeError("存在 v3 财务证据计划或来源，即使未采集也不能逆迁移")
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("DROP TRIGGER ai_business_v3_chunk ON ai_business_evidence_chunks")
        for table in ("ai_business_evidence_runs", "ai_business_evidence_sources"):
            cursor.execute(f"DROP TRIGGER ai_business_v3_directory_complete ON {table}")
        cursor.execute("DROP TRIGGER ai_business_v3_run ON ai_business_evidence_runs")
        cursor.execute(OLD_SOURCE_GUARD)
        for function in ("ai_business_v3_chunk_guard()", "ai_business_v3_directory_guard()",
                         "ai_business_v3_run_guard()", "ai_business_v3_header(text)"):
            cursor.execute("DROP FUNCTION " + function)
        cursor.execute("ALTER TABLE ai_business_evidence_sources DROP CONSTRAINT ai_business_source_bound")
        cursor.execute("ALTER TABLE ai_business_evidence_sources ADD CONSTRAINT ai_business_source_bound " + SOURCE_BOUND.replace("'market','finance'", "'market'"))


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0027_business_promotion_file_guard")]
    operations = [migrations.RunPython(install, uninstall)]
