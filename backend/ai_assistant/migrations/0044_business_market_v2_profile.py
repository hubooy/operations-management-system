"""Parked market-v2 report root; no derived table or Agent authority."""
from django.db import migrations


PROFILE = "business-agent-screening-promotion-market-reference-v2"

REPORT_GUARD = r"""CREATE FUNCTION public.ai_market_v2_parked_report_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE snapshot jsonb; input_value jsonb; root jsonb; selector jsonb;
  source_report public.ai_report_runs%ROWTYPE;
  source_flow public.ai_workflow_runs%ROWTYPE;
  flow public.ai_workflow_runs%ROWTYPE;
  evidence public.ai_business_evidence_runs%ROWTYPE;
  current_source public.ai_business_evidence_sources%ROWTYPE;
  baseline_source public.ai_business_evidence_sources%ROWTYPE;
  current_query jsonb; baseline_query jsonb; bands jsonb; band jsonb;
  previous_upper bigint:=0; lower_value bigint; upper_value bigint;
  current_day date; baseline_day date; start_day date; end_day date;
  baseline_expected date; month_last date; expected_budget boolean;
  band_key text; seen_keys text[]:=ARRAY[]::text[]; n integer;
  query_field text; query_value text;
BEGIN
  IF TG_OP='DELETE' THEN
    IF OLD.snapshot_json::jsonb->>'executionProfile'='business-agent-screening-promotion-market-reference-v2'
    THEN RAISE EXCEPTION 'ai_market_v2_parked_report_immutable'; END IF;
    RETURN OLD;
  END IF;
  snapshot:=NEW.snapshot_json::jsonb;
  IF snapshot->>'executionProfile' IS DISTINCT FROM
      'business-agent-screening-promotion-market-reference-v2' THEN
    IF snapshot->>'schemaVersion'='business-market-v2-parked-snapshot-v1'
       OR snapshot ? 'marketSelector' OR snapshot ? 'marketMaterialReady'
    THEN RAISE EXCEPTION 'ai_market_v2_profile_required'; END IF;
    RETURN NEW;
  END IF;
  IF TG_OP='UPDATE' THEN
    RAISE EXCEPTION 'ai_market_v2_parked_report_immutable';
  END IF;
  IF octet_length(NEW.snapshot_json)>32768
  THEN RAISE EXCEPTION 'ai_market_v2_snapshot_capacity'; END IF;
  snapshot:=public.ai_screen_fields(NEW.snapshot_json::json,ARRAY[
    'schemaVersion','executionProfile','evidenceProtocol','reportId',
    'sourceRoot','marketSelector','marketAlgorithms','proposedTools',
    'roleReadPolicyDigest','withBudget',
    'marketMaterialReady','marketAndOwnSalesAdditive',
    'humanReviewRequired','registered']);
  root:=public.ai_screen_fields(NEW.snapshot_json::json->'sourceRoot',ARRAY[
    'sourceReportId','sourceWorkflowId','sourceSnapshotDigest',
    'sourceWorkflowInputDigest','evidenceRunId','evidenceVersion','sealedDigest']);
  selector:=public.ai_screen_fields(NEW.snapshot_json::json->'marketSelector',ARRAY[
    'priceBandSourceKey','rankCurrentSourceKey','rankBaselineKey','bands',
    'currentObservationDate','baselineObservationDate']);
  IF snapshot->>'schemaVersion' IS DISTINCT FROM 'business-market-v2-parked-snapshot-v1'
     OR snapshot->>'evidenceProtocol' IS DISTINCT FROM 'reference-v2'
     OR snapshot->>'reportId' IS DISTINCT FROM NEW.id
     OR snapshot->'withBudget' NOT IN ('true'::jsonb,'false'::jsonb)
     OR snapshot->'marketMaterialReady' IS DISTINCT FROM 'false'::jsonb
     OR snapshot->'marketAndOwnSalesAdditive' IS DISTINCT FROM 'false'::jsonb
     OR snapshot->'humanReviewRequired' IS DISTINCT FROM 'true'::jsonb
     OR snapshot->'registered' IS DISTINCT FROM 'false'::jsonb
     OR snapshot->'marketAlgorithms' IS DISTINCT FROM
       '{"priceBand":"market-daily-dynamics-v1","rankEntryExit":"market-daily-observation-v2"}'::jsonb
     OR snapshot->'proposedTools' IS DISTINCT FROM
       '["get_business_promotion_screening_package_v1","get_business_promotion_screening_analysis_v1","get_business_promotion_screening_budget_v1","get_business_promotion_keyword_sku_v1","get_business_promotion_market_v2"]'::jsonb
     OR snapshot->>'roleReadPolicyDigest' IS DISTINCT FROM
       '3675571c6748ee4f9600c3bf98e52bfe509ff2b9b444cbb40f5580f43810c98f'
     OR NEW.owner_email IS NULL OR NEW.scope_json<>'null'
     OR NEW.budget_plan_id IS NOT NULL
  THEN RAISE EXCEPTION 'ai_market_v2_snapshot_identity_invalid'; END IF;
  SELECT * INTO flow FROM public.ai_workflow_runs selected_flow
    WHERE selected_flow.id=NEW.workflow_id;
  IF flow.id IS NULL OR flow.owner_email<>NEW.owner_email
     OR flow.request_digest IS DISTINCT FROM NEW.request_digest
     OR flow.scope_json<>'null' OR flow.status<>'paused'
     OR flow.error_code<>'market_material_not_admitted'
     OR flow.graph_digest IS DISTINCT FROM
       encode(sha256(convert_to(flow.graph_json,'UTF8')),'hex')
     OR flow.graph_digest IS DISTINCT FROM
       (CASE WHEN snapshot->'withBudget'='true'::jsonb
             THEN '2d930107c35daa345ace5ca411b65c64012209fc5fcd12fd2ec97ca682711e5e'
             ELSE 'ff32c5d92bb2920f18acea71823670cc13b9cba71030a00cf9c8b3e78bc9e54b' END)
     OR flow.allowed_tools_json IS DISTINCT FROM '[]'
     OR flow.tool_policy_digest IS DISTINCT FROM
       '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'
     OR flow.model_id<>'' OR flow.model_version<>0
     OR flow.provider_round_count<>0 OR flow.tool_call_count<>0
     OR flow.dry_run<>0 OR flow.retryable<>0
     OR EXISTS(SELECT 1 FROM public.ai_workflow_node_runs node WHERE node.run_id=flow.id)
  THEN RAISE EXCEPTION 'ai_market_v2_workflow_not_parked'; END IF;
  input_value:=public.ai_screen_fields(flow.input_json::json,ARRAY[
    'schemaVersion','executionProfile','evidenceProtocol','reportId',
    'sourceRoot','marketSelector','marketAlgorithms','proposedTools',
    'roleReadPolicyDigest','withBudget',
    'marketMaterialReady','marketAndOwnSalesAdditive',
    'humanReviewRequired','registered','graphDigest','allowedTools']);
  IF input_value->>'schemaVersion' IS DISTINCT FROM 'business-market-v2-parked-input-v1'
     OR (input_value-ARRAY['schemaVersion','graphDigest','allowedTools'])
        IS DISTINCT FROM (snapshot-'schemaVersion')
     OR input_value->>'graphDigest' IS DISTINCT FROM flow.graph_digest
     OR input_value->'allowedTools' IS DISTINCT FROM flow.allowed_tools_json::jsonb
     OR input_value->>'roleReadPolicyDigest' IS DISTINCT FROM
       snapshot->>'roleReadPolicyDigest'
  THEN RAISE EXCEPTION 'ai_market_v2_workflow_input_forged'; END IF;
  SELECT * INTO source_report FROM public.ai_report_runs selected_report
    WHERE selected_report.id=root->>'sourceReportId';
  SELECT * INTO source_flow FROM public.ai_workflow_runs selected_flow
    WHERE selected_flow.id=source_report.workflow_id;
  SELECT * INTO evidence FROM public.ai_business_evidence_runs selected_evidence
    WHERE selected_evidence.id=root->>'evidenceRunId';
  IF source_report.id IS NULL OR source_flow.id IS NULL OR evidence.id IS NULL
     OR source_report.id=NEW.id OR source_report.owner_email<>NEW.owner_email
     OR source_report.scope_json<>'null'
     OR source_report.workflow_id IS DISTINCT FROM root->>'sourceWorkflowId'
     OR source_report.workflow_id<>source_flow.id
     OR source_flow.owner_email<>NEW.owner_email OR source_flow.scope_json<>'null'
     OR source_report.snapshot_json::jsonb->>'executionProfile' IS DISTINCT FROM
       'business-agent-screening-promotion-reference-v1'
     OR source_report.snapshot_json::jsonb->>'reportId' IS DISTINCT FROM source_report.id
     OR root->>'sourceSnapshotDigest' IS DISTINCT FROM
       encode(sha256(convert_to(source_report.snapshot_json,'UTF8')),'hex')
     OR root->>'sourceWorkflowInputDigest' IS DISTINCT FROM
       encode(sha256(convert_to(source_flow.input_json,'UTF8')),'hex')
     OR source_report.snapshot_json::jsonb->>'evidenceRunId' IS DISTINCT FROM evidence.id
     OR source_report.snapshot_json::jsonb->>'sealedDigest' IS DISTINCT FROM
       root->>'sealedDigest'
     OR public.ai_screen_uint((root->'evidenceVersion')::json,1,9007199254740991)
       IS DISTINCT FROM evidence.version
     OR source_report.snapshot_json::jsonb->>'evidenceVersion' IS DISTINCT FROM
       evidence.version::text
     OR evidence.owner_email<>NEW.owner_email OR evidence.scope_json<>'null'
     OR evidence.status<>'sealed'
     OR evidence.plan_json::jsonb->>'schemaVersion' IS DISTINCT FROM 'business-evidence-v2'
     OR evidence.state_json::jsonb->>'schemaVersion' IS DISTINCT FROM 'business-evidence-seal-v2'
     OR evidence.state_json::jsonb->>'sealedDigest' IS DISTINCT FROM root->>'sealedDigest'
     OR source_report.snapshot_json::jsonb->>'catalogDigest' IS DISTINCT FROM
       evidence.plan_json::jsonb->>'catalogDigest'
     OR NOT EXISTS(SELECT 1 FROM public.access_control_users actor
       WHERE actor.email=NEW.owner_email AND actor.role='admin'
         AND actor.status='active' AND actor.scope IS NULL)
  THEN RAISE EXCEPTION 'ai_market_v2_source_report_or_seal_invalid'; END IF;
  expected_budget:=source_report.budget_plan_id IS NOT NULL;
  IF snapshot->'withBudget' IS DISTINCT FROM to_jsonb(expected_budget)
  THEN RAISE EXCEPTION 'ai_market_v2_source_budget_invalid'; END IF;
  IF json_typeof(NEW.snapshot_json::json->'marketSelector'->'priceBandSourceKey')
       IS DISTINCT FROM 'string'
     OR json_typeof(NEW.snapshot_json::json->'marketSelector'->'rankCurrentSourceKey')
       IS DISTINCT FROM 'string'
     OR json_typeof(NEW.snapshot_json::json->'marketSelector'->'rankBaselineKey')
       IS DISTINCT FROM 'string'
     OR json_typeof(NEW.snapshot_json::json->'marketSelector'->'currentObservationDate')
       IS DISTINCT FROM 'string'
     OR json_typeof(NEW.snapshot_json::json->'marketSelector'->'baselineObservationDate')
       IS DISTINCT FROM 'string'
     OR selector->>'priceBandSourceKey' IS DISTINCT FROM selector->>'rankCurrentSourceKey'
     OR selector->>'rankCurrentSourceKey' IS NOT DISTINCT FROM selector->>'rankBaselineKey'
     OR selector->>'rankCurrentSourceKey' !~ '^[A-Za-z0-9_-]{1,160}$'
     OR selector->>'rankBaselineKey' !~ '^[A-Za-z0-9_-]{1,160}$'
  THEN RAISE EXCEPTION 'ai_market_v2_selector_keys_invalid'; END IF;
  SELECT * INTO current_source FROM public.ai_business_evidence_sources selected_source
    WHERE selected_source.run_id=evidence.id
      AND selected_source.source_key=selector->>'rankCurrentSourceKey';
  SELECT * INTO baseline_source FROM public.ai_business_evidence_sources selected_source
    WHERE selected_source.run_id=evidence.id
      AND selected_source.source_key=selector->>'rankBaselineKey';
  IF current_source.id IS NULL OR baseline_source.id IS NULL
     OR current_source.domain<>'market' OR baseline_source.domain<>'market'
     OR NOT current_source.finished OR NOT baseline_source.finished
     OR current_source.page_count<1 OR baseline_source.page_count<1
     OR current_source.checkpoint_run_version>evidence.version
     OR baseline_source.checkpoint_run_version>evidence.version
     OR current_source.query_digest IS DISTINCT FROM
       encode(sha256(convert_to(current_source.query_json,'UTF8')),'hex')
     OR baseline_source.query_digest IS DISTINCT FROM
       encode(sha256(convert_to(baseline_source.query_json,'UTF8')),'hex')
     OR current_source.query_json::jsonb->>'platform' IS DISTINCT FROM '京东'
     OR baseline_source.query_json::jsonb->>'platform' IS DISTINCT FROM '京东'
  THEN RAISE EXCEPTION 'ai_market_v2_sources_invalid'; END IF;
  current_query:=public.ai_screen_fields(current_source.query_json::json,ARRAY[
    'platform','category','scope','rankingDimension','priceBandFilter',
    'startDate','endDate','window']);
  baseline_query:=public.ai_screen_fields(baseline_source.query_json::json,ARRAY[
    'platform','category','scope','rankingDimension','priceBandFilter',
    'startDate','endDate','window']);
  FOREACH query_field IN ARRAY ARRAY['platform','category','scope',
      'rankingDimension','priceBandFilter','startDate','endDate','window'] LOOP
    query_value:=current_query->>query_field;
    IF json_typeof(current_source.query_json::json->query_field)
         IS DISTINCT FROM 'string'
       OR length(query_value) NOT BETWEEN 1 AND
         (CASE WHEN query_field IN ('category','scope','priceBandFilter')
               THEN 200 ELSE 100 END)
       OR json_typeof(baseline_source.query_json::json->query_field)
         IS DISTINCT FROM 'string'
    THEN RAISE EXCEPTION 'ai_market_v2_source_query_value_invalid'; END IF;
  END LOOP;
  IF current_query->>'rankingDimension' NOT IN ('SKU','SPU')
     OR current_query->>'startDate' !~ '^20[0-9]{2}-[0-9]{2}-[0-9]{2}$'
     OR current_query->>'endDate' !~ '^20[0-9]{2}-[0-9]{2}-[0-9]{2}$'
  THEN RAISE EXCEPTION 'ai_market_v2_source_query_date_invalid'; END IF;
  IF current_query->>'window' IS DISTINCT FROM 'current'
     OR baseline_query->>'window' IS NULL
     OR baseline_query->>'window' NOT IN ('previous','yearAgo')
     OR (current_query-'window') IS DISTINCT FROM (baseline_query-'window')
  THEN RAISE EXCEPTION 'ai_market_v2_source_scope_or_window_invalid'; END IF;
  IF selector->>'currentObservationDate' !~ '^20[0-9]{2}-[0-9]{2}-[0-9]{2}$'
     OR selector->>'baselineObservationDate' !~ '^20[0-9]{2}-[0-9]{2}-[0-9]{2}$'
  THEN RAISE EXCEPTION 'ai_market_v2_observation_format_invalid'; END IF;
  current_day:=(selector->>'currentObservationDate')::date;
  baseline_day:=(selector->>'baselineObservationDate')::date;
  start_day:=(current_query->>'startDate')::date;
  end_day:=(current_query->>'endDate')::date;
  IF start_day IS NULL OR end_day IS NULL OR start_day>end_day
     OR end_day-start_day NOT BETWEEN 0 AND 92
     OR extract(year FROM start_day) NOT BETWEEN 2000 AND 2098
     OR extract(year FROM end_day) NOT BETWEEN 2000 AND 2098
     OR extract(year FROM current_day) NOT BETWEEN 2000 AND 2098
     OR extract(year FROM baseline_day) NOT BETWEEN 2000 AND 2098
     OR current_day NOT BETWEEN start_day AND end_day
  THEN RAISE EXCEPTION 'ai_market_v2_observation_period_invalid'; END IF;
  IF baseline_query->>'window'='previous' THEN
    baseline_expected:=current_day-(end_day-start_day+1);
  ELSE
    month_last:=(date_trunc('month',make_date(extract(year FROM current_day)::integer-1,
      extract(month FROM current_day)::integer,1)) + interval '1 month - 1 day')::date;
    baseline_expected:=make_date(extract(year FROM current_day)::integer-1,
      extract(month FROM current_day)::integer,
      least(extract(day FROM current_day)::integer,
            extract(day FROM month_last)::integer));
  END IF;
  IF baseline_day IS DISTINCT FROM baseline_expected
     OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(
       current_source.checkpoint_json::jsonb->'metadata'->'coverage'->'presentDates') AS observed(day)
       WHERE observed.day=current_day::text)
     OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(
       baseline_source.checkpoint_json::jsonb->'metadata'->'coverage'->'presentDates') AS observed(day)
       WHERE observed.day=baseline_day::text)
  THEN RAISE EXCEPTION 'ai_market_v2_observation_uncovered'; END IF;
  bands:=selector->'bands';
  IF jsonb_typeof(bands) IS DISTINCT FROM 'array'
     OR jsonb_array_length(bands) NOT BETWEEN 1 AND 20
  THEN RAISE EXCEPTION 'ai_market_v2_price_bands_invalid'; END IF;
  FOR n IN 0..jsonb_array_length(bands)-1 LOOP
    band:=public.ai_screen_fields((NEW.snapshot_json::json->'marketSelector'->'bands'->n),
      ARRAY['key','lowerCents','upperExclusiveCents']);
    band_key:=band->>'key';
    lower_value:=public.ai_screen_uint((NEW.snapshot_json::json->'marketSelector'->'bands'->n->'lowerCents'),
      0,9007199254740991);
    IF band_key IS NULL OR length(band_key) NOT BETWEEN 1 AND 100
       OR band_key=ANY(seen_keys) OR left(band_key,12)='unallocated_'
       OR lower_value<previous_upper
    THEN RAISE EXCEPTION 'ai_market_v2_price_band_order'; END IF;
    IF band->'upperExclusiveCents'='null'::jsonb THEN
      IF n<>jsonb_array_length(bands)-1
      THEN RAISE EXCEPTION 'ai_market_v2_open_band_not_last'; END IF;
      previous_upper:=NULL;
    ELSE
      upper_value:=public.ai_screen_uint(
        (NEW.snapshot_json::json->'marketSelector'->'bands'->n->'upperExclusiveCents'),
        1,9007199254740991);
      IF upper_value<=lower_value
      THEN RAISE EXCEPTION 'ai_market_v2_band_upper_invalid'; END IF;
      previous_upper:=upper_value;
    END IF;
    seen_keys:=array_append(seen_keys,band_key);
  END LOOP;
  RETURN NEW;
END $$"""


WORKFLOW_GUARD = """CREATE FUNCTION public.ai_market_v2_parked_workflow_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE value jsonb;
BEGIN
  IF TG_OP='DELETE' THEN
    IF OLD.input_json::jsonb->>'executionProfile'='business-agent-screening-promotion-market-reference-v2'
    THEN RAISE EXCEPTION 'ai_market_v2_parked_workflow_immutable'; END IF;
    RETURN OLD;
  END IF;
  value:=NEW.input_json::jsonb;
  IF value->>'executionProfile' IS DISTINCT FROM
      'business-agent-screening-promotion-market-reference-v2' THEN
    IF value->>'schemaVersion'='business-market-v2-parked-input-v1'
       OR value ? 'marketSelector' OR value ? 'marketMaterialReady'
       OR NEW.allowed_tools_json::jsonb @>
          '["get_business_promotion_market_v2"]'::jsonb
    THEN RAISE EXCEPTION 'ai_market_v2_workflow_profile_required'; END IF;
    RETURN NEW;
  END IF;
  IF TG_OP='UPDATE' THEN
    RAISE EXCEPTION 'ai_market_v2_workflow_transition_disabled';
  END IF;
  IF NEW.status<>'paused' OR NEW.error_code<>'market_material_not_admitted'
     OR NEW.owner_email IS NULL OR NEW.scope_json<>'null'
     OR NEW.model_id<>'' OR NEW.model_version<>0
     OR NEW.dry_run<>0 OR NEW.retryable<>0
     OR NEW.provider_round_count<>0 OR NEW.tool_call_count<>0
     OR NEW.allowed_tools_json IS DISTINCT FROM '[]'
     OR NEW.tool_policy_digest IS DISTINCT FROM
       '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'
     OR NEW.graph_digest IS DISTINCT FROM
       encode(sha256(convert_to(NEW.graph_json,'UTF8')),'hex')
  THEN RAISE EXCEPTION 'ai_market_v2_workflow_must_be_parked'; END IF;
  RETURN NEW;
END $$"""


ORPHAN_GUARD = """CREATE FUNCTION public.ai_market_v2_parked_orphan_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
  IF NEW.input_json::jsonb->>'executionProfile'=
       'business-agent-screening-promotion-market-reference-v2'
     AND (SELECT count(*) FROM public.ai_report_runs report
       WHERE report.workflow_id=NEW.id
         AND report.snapshot_json::jsonb->>'executionProfile'=
           'business-agent-screening-promotion-market-reference-v2')<>1
  THEN RAISE EXCEPTION 'ai_market_v2_parked_workflow_orphan'; END IF;
  RETURN NULL;
END $$"""


JOB_GUARD = """CREATE FUNCTION public.ai_market_v2_parked_job_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
  IF EXISTS(SELECT 1 FROM public.ai_workflow_runs flow
    WHERE flow.id=NEW.workflow_run_id
      AND flow.input_json::jsonb->>'executionProfile'=
        'business-agent-screening-promotion-market-reference-v2')
  THEN RAISE EXCEPTION 'ai_market_v2_agent_dispatch_disabled'; END IF;
  RETURN NEW;
END $$"""


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for definition in (REPORT_GUARD, WORKFLOW_GUARD, ORPHAN_GUARD, JOB_GUARD):
            cursor.execute(definition)
        cursor.execute("CREATE TRIGGER ai_market_v2_report_guard BEFORE INSERT OR UPDATE "
            "OR DELETE ON public.ai_report_runs FOR EACH ROW "
            "EXECUTE FUNCTION public.ai_market_v2_parked_report_guard()")
        cursor.execute("CREATE TRIGGER ai_market_v2_workflow_guard BEFORE INSERT OR UPDATE "
            "OR DELETE ON public.ai_workflow_runs FOR EACH ROW "
            "EXECUTE FUNCTION public.ai_market_v2_parked_workflow_guard()")
        cursor.execute("CREATE CONSTRAINT TRIGGER ai_market_v2_workflow_complete "
            "AFTER INSERT ON public.ai_workflow_runs DEFERRABLE INITIALLY DEFERRED "
            "FOR EACH ROW EXECUTE FUNCTION public.ai_market_v2_parked_orphan_guard()")
        cursor.execute("CREATE TRIGGER ai_market_v2_job_guard BEFORE INSERT ON "
            "public.ai_agent_jobs FOR EACH ROW "
            "EXECUTE FUNCTION public.ai_market_v2_parked_job_guard()")
        for signature in ("ai_market_v2_parked_report_guard()",
                "ai_market_v2_parked_workflow_guard()",
                "ai_market_v2_parked_orphan_guard()",
                "ai_market_v2_parked_job_guard()"):
            cursor.execute("REVOKE ALL ON FUNCTION public." + signature + " FROM PUBLIC")


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT EXISTS(SELECT 1 FROM public.ai_report_runs WHERE "
            "snapshot_json::jsonb->>'executionProfile'= %s) OR "
            "EXISTS(SELECT 1 FROM public.ai_workflow_runs WHERE "
            "input_json::jsonb->>'executionProfile'= %s)", [PROFILE, PROFILE])
        if cursor.fetchone()[0]:
            raise RuntimeError("存在市场v2停放报告，不能逆迁移并移除创建/派发门禁")
        for table, trigger in (("ai_agent_jobs","ai_market_v2_job_guard"),
                ("ai_workflow_runs","ai_market_v2_workflow_complete"),
                ("ai_workflow_runs","ai_market_v2_workflow_guard"),
                ("ai_report_runs","ai_market_v2_report_guard")):
            cursor.execute("DROP TRIGGER " + trigger + " ON public." + table)
        for signature in ("ai_market_v2_parked_job_guard()",
                "ai_market_v2_parked_orphan_guard()",
                "ai_market_v2_parked_workflow_guard()",
                "ai_market_v2_parked_report_guard()"):
            cursor.execute("DROP FUNCTION public." + signature)


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0043_business_v4_seal_consumption_candidate")]
    operations = [migrations.RunPython(install, uninstall)]
