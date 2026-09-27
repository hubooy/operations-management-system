"""Test-only synthetic five-Agent persistence; never a paid model call."""
from importlib import import_module

from django.db import migrations


ROLE = "teruisi_ai_market_synthetic_attestor"
PROFILE = "business-agent-screening-promotion-market-synthetic-v4"
MODEL = "market-v2-synthetic-only"
PAUSE = "market_v2_synthetic_no_provider_permission"
SIGNATURE = "public.ai_market_v2_create_synthetic_chain(text)"
prior = import_module("ai_assistant.migrations.0060_business_market_v2_execution_snapshot")
admitted = import_module("ai_assistant.migrations.0053_business_market_v2_admitted_paused")
_anchor = """  IF value->>'executionProfile'='business-agent-screening-promotion-market-execution-v2'
  THEN RETURN NEW; END IF;
"""
_replacement = _anchor + """  IF TG_OP='INSERT' AND value->>'executionProfile'=
       'business-agent-screening-promotion-market-synthetic-v4'
  THEN RETURN NEW; END IF;
"""
if prior.NEW_PARKED_WORKFLOW.count(_anchor) != 1:
    raise RuntimeError("0064 requires exact 0060 parked workflow predecessor")
NEW_PARKED_WORKFLOW = prior.NEW_PARKED_WORKFLOW.replace(_anchor, _replacement)

_tool_anchor = """  IF NEW.tool_name='get_business_promotion_market_v2'
     OR selected_flow='business-agent-screening-promotion-market-admitted-v2'
"""
_tool_exception = """  IF TG_OP='INSERT'
     AND NEW.tool_name='get_business_promotion_market_v2'
     AND selected_flow='business-agent-screening-promotion-market-synthetic-v4'
     AND session_user='teruisi_ai_market_synthetic_attestor'
     AND current_database() IN ('teruisi_ai_rehearsal','test_teruisi_ai_rehearsal')
     AND inet_server_port() BETWEEN 55440 AND 55999
     AND EXISTS(SELECT 1 FROM public.ai_agent_jobs job
       JOIN public.ai_workflow_runs flow ON flow.id=job.workflow_run_id
       WHERE job.id=NEW.job_id
         AND job.model_id='market-v2-synthetic-only'
         AND flow.model_id='market-v2-synthetic-only'
         AND flow.status='paused'
         AND flow.input_json::jsonb->'syntheticOnly'='true'::jsonb)
  THEN RETURN NEW; END IF;
"""
if admitted.TOOL_GUARD.count(_tool_anchor) != 1:
    raise RuntimeError("0064 requires exact 0053 fifth-tool guard predecessor")
NEW_TOOL_GUARD = admitted.TOOL_GUARD.replace(_tool_anchor,
    _tool_exception + _tool_anchor)

_result_anchor = """  IF selected_tool='get_business_promotion_market_v2'
     OR selected_flow='business-agent-screening-promotion-market-admitted-v2'
"""
_result_exception = """  IF TG_OP='INSERT'
     AND selected_tool='get_business_promotion_market_v2'
     AND selected_flow='business-agent-screening-promotion-market-synthetic-v4'
     AND session_user='teruisi_ai_market_synthetic_attestor'
     AND current_database() IN ('teruisi_ai_rehearsal','test_teruisi_ai_rehearsal')
     AND inet_server_port() BETWEEN 55440 AND 55999
     AND NEW.result_json::jsonb->'data'->'syntheticOnly'='true'::jsonb
     AND NEW.result_json::jsonb->'data'->'persistedRead'='false'::jsonb
     AND NEW.result_json::jsonb->'data'->'numericCitationAllowed'='false'::jsonb
     AND NEW.result_digest=encode(sha256(convert_to(NEW.result_json,'UTF8')),'hex')
     AND EXISTS(SELECT 1 FROM public.ai_agent_tool_dispatches dispatch
       JOIN public.ai_agent_jobs job ON job.id=dispatch.job_id
       JOIN public.ai_workflow_runs flow ON flow.id=job.workflow_run_id
       WHERE dispatch.id=NEW.tool_dispatch_id
         AND dispatch.state='succeeded'
         AND job.model_id='market-v2-synthetic-only'
         AND flow.model_id='market-v2-synthetic-only'
         AND flow.status='paused'
         AND flow.input_json::jsonb->'syntheticOnly'='true'::jsonb)
  THEN RETURN NEW; END IF;
"""
if admitted.RESULT_GUARD.count(_result_anchor) != 1:
    raise RuntimeError("0064 requires exact 0053 fifth-result guard predecessor")
NEW_RESULT_GUARD = admitted.RESULT_GUARD.replace(_result_anchor,
    _result_exception + _result_anchor)

FLOW_GUARD = r"""CREATE FUNCTION public.ai_market_v2_synthetic_flow_guard()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE value jsonb; plan public.ai_business_market_v2_execution_plans%ROWTYPE;
  source public.ai_report_runs%ROWTYPE; source_flow public.ai_workflow_runs%ROWTYPE;
BEGIN
  IF TG_OP='DELETE' THEN
    IF OLD.input_json::jsonb->>'executionProfile'=
       'business-agent-screening-promotion-market-synthetic-v4'
    THEN RAISE EXCEPTION 'ai_market_v2_synthetic_flow_immutable'; END IF;
    RETURN OLD;
  END IF;
  value:=NEW.input_json::jsonb;
  IF TG_OP='UPDATE' AND (OLD.input_json::jsonb->>'executionProfile'=
       'business-agent-screening-promotion-market-synthetic-v4'
       OR value->>'executionProfile'=
       'business-agent-screening-promotion-market-synthetic-v4')
  THEN RAISE EXCEPTION 'ai_market_v2_synthetic_flow_immutable'; END IF;
  IF value->>'executionProfile' IS DISTINCT FROM
     'business-agent-screening-promotion-market-synthetic-v4' THEN
    IF value->>'schemaVersion'='business-market-v2-synthetic-input-v1'
       OR value ? 'syntheticRoot'
    THEN RAISE EXCEPTION 'ai_market_v2_synthetic_profile_required'; END IF;
    RETURN NEW;
  END IF;
  IF session_user<>'teruisi_ai_market_synthetic_attestor'
     OR current_database() NOT IN ('teruisi_ai_rehearsal','test_teruisi_ai_rehearsal')
     OR inet_server_port() NOT BETWEEN 55440 AND 55999
  THEN RAISE EXCEPTION 'ai_market_v2_synthetic_test_only'; END IF;
  SELECT * INTO plan FROM public.ai_business_market_v2_execution_plans item
    WHERE item.id=value->'syntheticRoot'->>'planId';
  SELECT * INTO source FROM public.ai_report_runs item
    WHERE item.id=plan.execution_report_id;
  SELECT * INTO source_flow FROM public.ai_workflow_runs item
    WHERE item.id=source.workflow_id;
  IF plan.id IS NULL OR source.id IS NULL OR source_flow.id IS NULL
     OR value->>'schemaVersion' IS DISTINCT FROM 'business-market-v2-synthetic-input-v1'
     OR value->>'reportId' IS NULL
     OR value->'syntheticOnly' IS DISTINCT FROM 'true'::jsonb
     OR value->'externalProviderCalled' IS DISTINCT FROM 'false'::jsonb
     OR value->'agentReadPersisted' IS DISTINCT FROM 'false'::jsonb
     OR value->'numericCitationAllowed' IS DISTINCT FROM 'false'::jsonb
     OR value->'marketAndOwnSalesAdditive' IS DISTINCT FROM 'false'::jsonb
     OR value->'paidCostCents' IS DISTINCT FROM '0'::jsonb
     OR value->'allowedTools' IS DISTINCT FROM
       '["get_business_market_v2_screening_package","get_business_market_v2_screening_analysis","get_business_market_v2_screening_budget","get_business_market_v2_keyword_sku","get_business_promotion_market_v2"]'::jsonb
     OR value->>'graphDigest' IS DISTINCT FROM source_flow.graph_digest
     OR value->'syntheticRoot'->>'executionReportId' IS DISTINCT FROM source.id
     OR value->'syntheticRoot'->>'ownerEmail' IS DISTINCT FROM NEW.owner_email
     OR value->'syntheticRoot'->>'contextProofDigest' IS DISTINCT FROM
       plan.plan_json::jsonb->'executionRoot'->>'contextProofDigest'
     OR NEW.owner_email IS DISTINCT FROM source.owner_email
     OR NEW.scope_json IS DISTINCT FROM 'null'
     OR NEW.status IS DISTINCT FROM 'paused'
     OR NEW.error_code IS DISTINCT FROM 'market_v2_synthetic_no_provider_permission'
     OR NEW.model_id IS DISTINCT FROM 'market-v2-synthetic-only'
     OR NEW.model_version IS DISTINCT FROM 1 OR NEW.dry_run IS DISTINCT FROM 1
     OR NEW.tool_policy_digest IS DISTINCT FROM
       '3db26b536d59118656f3a5f52638275139b19df24e2afcbf99b8c76cab8ee743'
     OR NEW.graph_json IS DISTINCT FROM source_flow.graph_json
     OR NEW.graph_digest IS DISTINCT FROM source_flow.graph_digest
     OR NEW.allowed_tools_json IS DISTINCT FROM
       '["get_business_market_v2_screening_package","get_business_market_v2_screening_analysis","get_business_market_v2_screening_budget","get_business_market_v2_keyword_sku","get_business_promotion_market_v2"]'
     OR NEW.provider_round_count IS DISTINCT FROM 5
     OR NEW.tool_call_count IS DISTINCT FROM 5
     OR NEW.version IS DISTINCT FROM 1
     OR NEW.mutation_token IS DISTINCT FROM ''
     OR NEW.cancel_requested IS DISTINCT FROM 0
     OR NEW.retryable IS DISTINCT FROM 0
     OR NEW.resume_count IS DISTINCT FROM 0
     OR NEW.attempt_count IS DISTINCT FROM 0
     OR NEW.lease_token IS DISTINCT FROM ''
     OR NEW.lease_epoch IS DISTINCT FROM 0
     OR NEW.error_message IS DISTINCT FROM ''
     OR NEW.output_json IS NOT NULL OR NEW.current_node_key IS NOT NULL
     OR NEW.started_at IS NOT NULL OR NEW.completed_at IS NOT NULL
  THEN RAISE EXCEPTION 'ai_market_v2_synthetic_flow_invalid'; END IF;
  RETURN NEW;
END $$"""

REPORT_GUARD = r"""CREATE FUNCTION public.ai_market_v2_synthetic_report_guard()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE value jsonb; flow public.ai_workflow_runs%ROWTYPE;
  plan public.ai_business_market_v2_execution_plans%ROWTYPE;
BEGIN
  IF TG_OP='DELETE' THEN
    IF OLD.snapshot_json::jsonb->>'executionProfile'=
       'business-agent-screening-promotion-market-synthetic-v4'
    THEN RAISE EXCEPTION 'ai_market_v2_synthetic_report_immutable'; END IF;
    RETURN OLD;
  END IF;
  value:=NEW.snapshot_json::jsonb;
  IF TG_OP='UPDATE' AND (OLD.snapshot_json::jsonb->>'executionProfile'=
       'business-agent-screening-promotion-market-synthetic-v4'
       OR value->>'executionProfile'=
       'business-agent-screening-promotion-market-synthetic-v4')
  THEN RAISE EXCEPTION 'ai_market_v2_synthetic_report_immutable'; END IF;
  IF value->>'executionProfile' IS DISTINCT FROM
     'business-agent-screening-promotion-market-synthetic-v4' THEN
    IF value->>'schemaVersion'='business-market-v2-synthetic-snapshot-v1'
       OR value ? 'syntheticRoot'
    THEN RAISE EXCEPTION 'ai_market_v2_synthetic_profile_required'; END IF;
    RETURN NEW;
  END IF;
  IF session_user<>'teruisi_ai_market_synthetic_attestor'
     OR current_database() NOT IN ('teruisi_ai_rehearsal','test_teruisi_ai_rehearsal')
     OR inet_server_port() NOT BETWEEN 55440 AND 55999
  THEN RAISE EXCEPTION 'ai_market_v2_synthetic_test_only'; END IF;
  SELECT * INTO flow FROM public.ai_workflow_runs item WHERE item.id=NEW.workflow_id;
  SELECT * INTO plan FROM public.ai_business_market_v2_execution_plans item
    WHERE item.id=value->'syntheticRoot'->>'planId';
  IF flow.id IS NULL OR plan.id IS NULL
     OR value->>'schemaVersion' IS DISTINCT FROM 'business-market-v2-synthetic-snapshot-v1'
     OR value->>'reportId' IS DISTINCT FROM NEW.id
     OR value->'syntheticOnly' IS DISTINCT FROM 'true'::jsonb
     OR value->'externalProviderCalled' IS DISTINCT FROM 'false'::jsonb
     OR value->'agentReadPersisted' IS DISTINCT FROM 'false'::jsonb
     OR value->'numericCitationAllowed' IS DISTINCT FROM 'false'::jsonb
     OR value->'paidCostCents' IS DISTINCT FROM '0'::jsonb
     OR value->'humanReviewRequired' IS DISTINCT FROM 'true'::jsonb
     OR value->'syntheticRoot' IS DISTINCT FROM
       flow.input_json::jsonb->'syntheticRoot'
     OR flow.input_json::jsonb->>'reportId' IS DISTINCT FROM NEW.id
     OR flow.owner_email IS DISTINCT FROM NEW.owner_email
     OR flow.status IS DISTINCT FROM 'paused'
     OR flow.model_id IS DISTINCT FROM 'market-v2-synthetic-only'
     OR NEW.owner_email IS DISTINCT FROM
       plan.plan_json::jsonb->'executionRoot'->>'ownerEmail'
     OR NEW.scope_json IS DISTINCT FROM 'null'
     OR NEW.budget_plan_id IS NOT NULL
     OR NEW.request_digest IS DISTINCT FROM flow.request_digest
  THEN RAISE EXCEPTION 'ai_market_v2_synthetic_report_invalid'; END IF;
  RETURN NEW;
END $$"""

CHILD_GUARD = r"""CREATE FUNCTION public.ai_market_v2_synthetic_child_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE target jsonb; targets jsonb[]; flow_id text;
  role_key text; expected_tool text; budget_value jsonb;
BEGIN
  IF TG_OP='INSERT' THEN targets:=ARRAY[to_jsonb(NEW)];
  ELSIF TG_OP='DELETE' THEN targets:=ARRAY[to_jsonb(OLD)];
  ELSE targets:=ARRAY[to_jsonb(OLD),to_jsonb(NEW)]; END IF;
  FOREACH target IN ARRAY targets LOOP
    flow_id:=NULL;
    IF TG_TABLE_NAME='ai_workflow_node_runs' THEN flow_id:=target->>'run_id';
    ELSIF TG_TABLE_NAME='ai_agent_jobs' THEN flow_id:=target->>'workflow_run_id';
    ELSIF TG_TABLE_NAME IN ('ai_agent_provider_dispatches','ai_agent_tool_dispatches') THEN
      SELECT job.workflow_run_id INTO flow_id FROM public.ai_agent_jobs job
        WHERE job.id=target->>'job_id';
    ELSIF TG_TABLE_NAME='ai_agent_provider_results' THEN
      SELECT job.workflow_run_id INTO flow_id FROM public.ai_agent_provider_dispatches d
        JOIN public.ai_agent_jobs job ON job.id=d.job_id
        WHERE d.id=target->>'dispatch_id';
    ELSIF TG_TABLE_NAME='ai_agent_tool_results' THEN
      SELECT job.workflow_run_id INTO flow_id FROM public.ai_agent_tool_dispatches d
        JOIN public.ai_agent_jobs job ON job.id=d.job_id
        WHERE d.id=target->>'tool_dispatch_id';
    ELSE RAISE EXCEPTION 'ai_market_v2_synthetic_child_table_invalid'; END IF;
    IF EXISTS(SELECT 1 FROM public.ai_workflow_runs flow WHERE flow.id=flow_id
       AND flow.input_json::jsonb->>'executionProfile'=
         'business-agent-screening-promotion-market-synthetic-v4')
       AND (TG_OP<>'INSERT'
         OR session_user<>'teruisi_ai_market_synthetic_attestor'
         OR current_database() NOT IN ('teruisi_ai_rehearsal','test_teruisi_ai_rehearsal')
         OR inet_server_port() NOT BETWEEN 55440 AND 55999)
    THEN RAISE EXCEPTION 'ai_market_v2_synthetic_child_denied'; END IF;
    IF TG_OP='INSERT' AND flow_id IS NOT NULL
       AND EXISTS(SELECT 1 FROM public.ai_workflow_runs flow WHERE flow.id=flow_id
         AND flow.input_json::jsonb->>'executionProfile'=
           'business-agent-screening-promotion-market-synthetic-v4') THEN
      IF TG_TABLE_NAME='ai_agent_tool_dispatches' THEN
        SELECT job.workflow_node_key,flow.input_json::jsonb->'withBudget'
          INTO role_key,budget_value FROM public.ai_agent_jobs job
          JOIN public.ai_workflow_runs flow ON flow.id=job.workflow_run_id
          WHERE job.id=target->>'job_id';
        expected_tool:=CASE role_key
          WHEN 'commerce' THEN 'get_business_market_v2_screening_package'
          WHEN 'promotion' THEN 'get_business_market_v2_keyword_sku'
          WHEN 'market_b2b' THEN 'get_business_promotion_market_v2'
          WHEN 'independent_review' THEN 'get_business_market_v2_screening_analysis'
          WHEN 'report' THEN CASE WHEN budget_value='true'::jsonb
            THEN 'get_business_market_v2_screening_budget'
            ELSE 'get_business_market_v2_screening_package' END
          ELSE NULL END;
        IF expected_tool IS NULL OR target->>'tool_name' IS DISTINCT FROM expected_tool
           OR NOT EXISTS(SELECT 1 FROM public.ai_agent_provider_dispatches d
             WHERE d.id=target->>'provider_dispatch_id'
               AND d.job_id=target->>'job_id')
        THEN RAISE EXCEPTION 'ai_market_v2_synthetic_tool_role_mismatch'; END IF;
      ELSIF TG_TABLE_NAME='ai_agent_tool_results' THEN
        SELECT job.workflow_node_key INTO role_key
          FROM public.ai_agent_tool_dispatches d
          JOIN public.ai_agent_jobs job ON job.id=d.job_id
          WHERE d.id=target->>'tool_dispatch_id';
        IF role_key IS NULL
           OR target->'result_json' IS NULL
           OR (target->>'result_json')::jsonb->'data'->>'role' IS DISTINCT FROM role_key
           OR (target->>'result_json')::jsonb->'data'->'syntheticOnly'
              IS DISTINCT FROM 'true'::jsonb
           OR (target->>'result_json')::jsonb->'data'->'persistedRead'
              IS DISTINCT FROM 'false'::jsonb
        THEN RAISE EXCEPTION 'ai_market_v2_synthetic_result_role_mismatch'; END IF;
      END IF;
    END IF;
  END LOOP;
  IF TG_OP='DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END $$"""

ORPHAN = r"""CREATE FUNCTION public.ai_market_v2_synthetic_orphan_guard()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
  IF NEW.input_json::jsonb->>'executionProfile'=
       'business-agent-screening-promotion-market-synthetic-v4'
     AND (SELECT count(*) FROM public.ai_report_runs report
       WHERE report.workflow_id=NEW.id AND report.snapshot_json::jsonb->>
         'executionProfile'='business-agent-screening-promotion-market-synthetic-v4')<>1
  THEN RAISE EXCEPTION 'ai_market_v2_synthetic_workflow_orphan'; END IF;
  RETURN NULL;
END $$"""

CREATE_CHAIN = r"""CREATE FUNCTION public.ai_market_v2_create_synthetic_chain(selected_plan text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE plan public.ai_business_market_v2_execution_plans%ROWTYPE;
  report public.ai_report_runs%ROWTYPE; source_flow public.ai_workflow_runs%ROWTYPE;
  value jsonb; root jsonb; anchor jsonb; common jsonb; snapshot jsonb; input_value jsonb;
  graph jsonb; item jsonb; args jsonb; response jsonb; result_body jsonb;
  flow_id text; report_id text; job_id text; provider_id text; tool_id text;
  node_id text; plan_digest text; role_key text; role_tool text; role_call text;
  position_value integer:=0; agent_count integer:=0;
  job_ids jsonb:='{}'::jsonb; provider_ids jsonb:='{}'::jsonb;
  tool_ids jsonb:='{}'::jsonb;
  args_text text; response_text text; result_text text;
BEGIN
  IF session_user<>'teruisi_ai_market_synthetic_attestor'
     OR current_database() NOT IN ('teruisi_ai_rehearsal','test_teruisi_ai_rehearsal')
     OR inet_server_port() NOT BETWEEN 55440 AND 55999
     OR selected_plan IS NULL OR selected_plan !~ '^[0-9a-f]{64}$'
  THEN RAISE EXCEPTION 'ai_market_v2_synthetic_test_only'; END IF;
  SELECT * INTO plan FROM public.ai_business_market_v2_execution_plans item
    WHERE item.id=selected_plan FOR SHARE;
  IF plan.id IS NULL
  THEN RAISE EXCEPTION 'ai_market_v2_synthetic_plan_missing'; END IF;
  value:=public.ai_market_v2_execution_plan_expected(
    plan.execution_report_id,plan.plan_json);
  SELECT * INTO report FROM public.ai_report_runs item
    WHERE item.id=plan.execution_report_id FOR SHARE;
  SELECT * INTO source_flow FROM public.ai_workflow_runs item
    WHERE item.id=report.workflow_id FOR SHARE;
  root:=value->'executionRoot';
  IF plan.plan_digest IS DISTINCT FROM
       encode(sha256(convert_to(plan.plan_json,'UTF8')),'hex')
     OR value->'modelPolicy'->'paidCallsAllowed' IS DISTINCT FROM 'false'::jsonb
     OR value->'modelPolicy'->'maxPaidCostCents' IS DISTINCT FROM '0'::jsonb
     OR source_flow.id IS NULL OR source_flow.status IS DISTINCT FROM 'paused'
  THEN RAISE EXCEPTION 'ai_market_v2_synthetic_policy_invalid'; END IF;
  flow_id:='market-synth-flow-'||substr(encode(sha256(convert_to(
    'market-synth-flow-|'||plan.id,'UTF8')),'hex'),1,48);
  report_id:='market-synth-report-'||substr(encode(sha256(convert_to(
    'market-synth-report-|'||plan.id,'UTF8')),'hex'),1,48);
  IF EXISTS(SELECT 1 FROM public.ai_report_runs r WHERE r.id=report_id)
     OR EXISTS(SELECT 1 FROM public.ai_workflow_runs f WHERE f.id=flow_id)
  THEN RAISE EXCEPTION 'ai_market_v2_synthetic_duplicate'; END IF;
  anchor:=jsonb_build_object('planId',plan.id,
    'executionReportId',report.id,
    'admittedReportId',root->>'admittedReportId',
    'contextProofDigest',root->>'contextProofDigest',
    'marketContextDigest',root->>'marketContextDigest',
    'ownerEmail',report.owner_email);
  common:=jsonb_build_object('executionProfile',
    'business-agent-screening-promotion-market-synthetic-v4',
    'reportId',report_id,'syntheticRoot',anchor,
    'fiveToolCatalogDigest',source_flow.tool_policy_digest,
    'withBudget',root->'withBudget','syntheticOnly',true,
    'externalProviderCalled',false,'paidCostCents',0,
    'agentReadPersisted',false,'numericCitationAllowed',false,
    'humanReviewRequired',true,'marketAndOwnSalesAdditive',false);
  snapshot:=common||jsonb_build_object('schemaVersion',
    'business-market-v2-synthetic-snapshot-v1');
  input_value:=common||jsonb_build_object('schemaVersion',
    'business-market-v2-synthetic-input-v1',
    'graphDigest',source_flow.graph_digest,'allowedTools',
    value->'proposedTools');
  plan_digest:=plan.plan_digest;
  INSERT INTO public.ai_workflow_runs(id,owner_email,client_request_id,
    request_digest,scope_json,name,graph_json,graph_digest,input_json,
    model_id,model_version,allowed_tools_json,tool_policy_digest,
    provider_round_count,tool_call_count,dry_run,status,version,
    mutation_token,cancel_requested,retryable,resume_count,attempt_count,
    lease_token,lease_epoch,next_run_at,error_code,error_message,
    created_at,updated_at)
  VALUES(flow_id,report.owner_email,flow_id,plan_digest,'null',
    '市场v2合成五Agent持久链（无模型）',source_flow.graph_json,
    source_flow.graph_digest,public.ai_v4_replay_canonical(input_value),
    'market-v2-synthetic-only',1,
    '["get_business_market_v2_screening_package","get_business_market_v2_screening_analysis","get_business_market_v2_screening_budget","get_business_market_v2_keyword_sku","get_business_promotion_market_v2"]',
    source_flow.tool_policy_digest,5,5,1,'paused',1,
    '',0,0,0,0,'',0,clock_timestamp(),
    'market_v2_synthetic_no_provider_permission','',
    clock_timestamp(),clock_timestamp());
  INSERT INTO public.ai_report_runs(id,owner_email,client_request_id,
    request_digest,scope_json,workflow_id,budget_plan_id,snapshot_json,created_at)
  VALUES(report_id,report.owner_email,report_id,plan_digest,'null',flow_id,
    NULL,public.ai_v4_replay_canonical(snapshot),clock_timestamp());
  FOR role_key IN SELECT unnest(ARRAY['commerce','promotion','market_b2b',
      'independent_review','report']::text[]) LOOP
    job_id:='market-synth-job-'||substr(encode(sha256(convert_to(
      'market-synth-job-|'||plan.id||'|'||role_key,'UTF8')),'hex'),1,48);
    job_ids:=job_ids||jsonb_build_object(role_key,job_id);
  INSERT INTO public.ai_agent_jobs(id,owner_email,client_request_id,
    request_digest,scope_json,task,input_json,state_json,model_id,
    model_version,allowed_tools_json,tool_policy_digest,
    provider_round_count,tool_call_count,status,phase,step_index,version,
    mutation_token,cancel_requested,retryable,resume_count,attempt_count,
    lease_token,lease_epoch,next_run_at,workflow_run_id,workflow_node_key,
    error_code,error_message,created_at,updated_at)
  VALUES(job_id,report.owner_email,job_id,plan_digest,'null',
    'synthetic-only-no-network',
    public.ai_v4_replay_canonical(jsonb_build_object('syntheticOnly',true,
      'planId',plan.id)),
    public.ai_v4_replay_canonical(jsonb_build_object('externalProviderCalled',false,
      'numericCitationAllowed',false)),
    'market-v2-synthetic-only',1,
    '["get_business_market_v2_screening_package","get_business_market_v2_screening_analysis","get_business_market_v2_screening_budget","get_business_market_v2_keyword_sku","get_business_promotion_market_v2"]',
    source_flow.tool_policy_digest,1,1,'paused','paused',0,1,
    '',0,0,0,0,'',1,clock_timestamp(),flow_id,role_key,
    '','',clock_timestamp(),clock_timestamp());
  END LOOP;
  graph:=source_flow.graph_json::jsonb;
  FOR item IN SELECT jsonb_array_elements(graph->'nodes') LOOP
    node_id:='market-synth-node-'||substr(encode(sha256(convert_to(
      plan.id||'|'||(item->>'key'),'UTF8')),'hex'),1,48);
    INSERT INTO public.ai_workflow_node_runs(id,run_id,node_key,position,
      node_type,depends_on_json,instruction,input_json,status,version,
      mutation_token,agent_job_id,error_code,error_message,created_at,updated_at)
    VALUES(node_id,flow_id,item->>'key',position_value,item->>'type',
      public.ai_v4_replay_canonical(item->'dependsOn'),item->>'instruction',
      '{}','pending',1,'',
      CASE WHEN item->>'type'='agent' THEN job_ids->>(item->>'key')
        ELSE NULL END,
      '','',clock_timestamp(),clock_timestamp());
    IF item->>'type'='agent' THEN agent_count:=agent_count+1; END IF;
    position_value:=position_value+1;
  END LOOP;
  IF position_value<>6 OR agent_count<>5
  THEN RAISE EXCEPTION 'ai_market_v2_synthetic_graph_invalid'; END IF;
  FOR role_key IN SELECT unnest(ARRAY['commerce','promotion','market_b2b',
      'independent_review','report']::text[]) LOOP
  job_id:=job_ids->>role_key;
  provider_id:='market-synth-provider-'||substr(encode(sha256(convert_to(
    'market-synth-provider-|'||plan.id||'|'||role_key,'UTF8')),'hex'),1,48);
  tool_id:='market-synth-tool-'||substr(encode(sha256(convert_to(
    'market-synth-tool-|'||plan.id||'|'||role_key,'UTF8')),'hex'),1,48);
  provider_ids:=provider_ids||jsonb_build_object(role_key,provider_id);
  tool_ids:=tool_ids||jsonb_build_object(role_key,tool_id);
  role_tool:=CASE role_key
    WHEN 'commerce' THEN 'get_business_market_v2_screening_package'
    WHEN 'promotion' THEN 'get_business_market_v2_keyword_sku'
    WHEN 'market_b2b' THEN 'get_business_promotion_market_v2'
    WHEN 'independent_review' THEN 'get_business_market_v2_screening_analysis'
    WHEN 'report' THEN CASE WHEN root->'withBudget'='true'::jsonb
      THEN 'get_business_market_v2_screening_budget'
      ELSE 'get_business_market_v2_screening_package' END
    ELSE NULL END;
  IF role_tool IS NULL THEN
    RAISE EXCEPTION 'ai_market_v2_synthetic_role_tool_invalid'; END IF;
  role_call:='synthetic-'||role_key||'-call-1';
  -- Synthetic-only arguments intentionally cannot be replayed as a real tool
  -- request; the provider response and dispatch still bind identical bytes.
  args:=jsonb_build_object('reportId',root->>'admittedReportId',
    'marketContextDigest',root->>'marketContextDigest',
    'role',role_key,'syntheticOnly',true);
  args_text:=public.ai_v4_replay_canonical(args);
  response:=jsonb_build_object('calls',jsonb_build_array(jsonb_build_object(
    'id',role_call,'name',role_tool,'arguments',args)),
    'syntheticOnly',true,'externalProviderCalled',false);
  response_text:=public.ai_v4_replay_canonical(response);
  result_body:=jsonb_build_object('toolName',role_tool,
    'auditStatus','recorded','ok',true,'data',jsonb_build_object(
      'syntheticOnly',true,'persistedRead',false,'numericCitationAllowed',false,
      'marketManifestDigest',root->>'manifestDigest','role',role_key,
      'budgetUnavailable',role_key='report' AND root->'withBudget'='false'::jsonb));
  result_text:=public.ai_v4_replay_canonical(result_body);
  INSERT INTO public.ai_agent_provider_dispatches(id,job_id,dispatch_ordinal,
    owner_email,actor_role,model_id,model_version,tool_policy_digest,
    request_digest,state,lease_epoch,reserved_at,provider_called_at,
    error_code,error_message,completed_at)
  VALUES(provider_id,job_id,1,report.owner_email,'admin',
    'market-v2-synthetic-only',1,source_flow.tool_policy_digest,
    plan_digest,'succeeded',1,clock_timestamp(),clock_timestamp(),
    '','',clock_timestamp());
  INSERT INTO public.ai_agent_provider_results(dispatch_id,response_json,
    response_digest,usage_json,provider_request_id,completed_at)
  VALUES(provider_id,response_text,
    encode(sha256(convert_to(response_text,'UTF8')),'hex'),
    '{"syntheticOnly":true,"paidCostCents":0}',
    'synthetic-no-network-'||role_key,clock_timestamp());
  INSERT INTO public.ai_agent_tool_dispatches(id,job_id,
    provider_dispatch_id,tool_call_ordinal,provider_call_id,tool_name,
    arguments_json,arguments_digest,invocation_id,state,lease_epoch,
    reserved_at,tool_called_at,error_code,error_message,completed_at)
  VALUES(tool_id,job_id,provider_id,1,role_call,
    role_tool,args_text,
    encode(sha256(convert_to(args_text,'UTF8')),'hex'),
    'synthetic-no-network-'||role_key,'succeeded',1,
    clock_timestamp(),clock_timestamp(),'','',clock_timestamp());
  INSERT INTO public.ai_agent_tool_results(tool_dispatch_id,result_json,
    result_digest,completed_at)
  VALUES(tool_id,result_text,
    encode(sha256(convert_to(result_text,'UTF8')),'hex'),clock_timestamp());
  END LOOP;
  RETURN jsonb_build_object('reportId',report_id,'workflowId',flow_id,
    'jobIds',job_ids,'providerDispatchIds',provider_ids,
    'toolDispatchIds',tool_ids,'syntheticOnly',true,
    'externalProviderCalled',false,'persistedRead',false,
    'numericCitationAllowed',false,'paidCostCents',0);
END $$"""


def _version_function(cursor, signature, before, after):
    cursor.execute("SELECT prosrc,proacl::text,proowner,oid,prosecdef,proconfig "
        "FROM pg_catalog.pg_proc WHERE oid=to_regprocedure(%s)", [signature])
    old = cursor.fetchone()
    if (old is None or old[0] != before.split("$$",2)[1]
            or old[4] is not False
            or {part.replace(" ","") for part in (old[5] or [])}
                != {"search_path=pg_catalog,public"}):
        raise RuntimeError("0064 protected predecessor guard drift: " + signature)
    cursor.execute(after.replace("CREATE FUNCTION","CREATE OR REPLACE FUNCTION",1))
    cursor.execute("SELECT prosrc,proacl::text,proowner,oid,prosecdef,proconfig "
        "FROM pg_catalog.pg_proc WHERE oid=to_regprocedure(%s)", [signature])
    now = cursor.fetchone()
    if now is None or now[0] != after.split("$$",2)[1] or now[1:] != old[1:]:
        raise RuntimeError("0064 may not change old guard OID/ACL/owner")


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT to_regrole(%s)", [ROLE])
        if cursor.fetchone()[0] is None:
            cursor.execute("CREATE ROLE " + ROLE + " NOLOGIN NOINHERIT NOSUPERUSER "
                "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS")
        cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
            "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
            "WHERE rolname=%s", [ROLE])
        if cursor.fetchone() != (False,) * 7:
            raise RuntimeError("0064 synthetic role must remain NOLOGIN")
        cursor.execute("SELECT count(*) FROM pg_catalog.pg_auth_members WHERE "
            "roleid=%s::regrole OR member=%s::regrole", [ROLE, ROLE])
        if cursor.fetchone() != (0,):
            raise RuntimeError("0064 synthetic role membership drift")
        for signature, before, after in (
            ("public.ai_market_v2_parked_workflow_guard()",
                prior.NEW_PARKED_WORKFLOW, NEW_PARKED_WORKFLOW),
            ("public.ai_market_v2_admitted_tool_guard()",
                admitted.TOOL_GUARD, NEW_TOOL_GUARD),
            ("public.ai_market_v2_admitted_result_guard()",
                admitted.RESULT_GUARD, NEW_RESULT_GUARD)):
            _version_function(cursor,signature,before,after)
        for definition in (FLOW_GUARD, REPORT_GUARD, CHILD_GUARD, ORPHAN, CREATE_CHAIN):
            cursor.execute(definition)
        for signature in ("public.ai_market_v2_synthetic_flow_guard()",
                "public.ai_market_v2_synthetic_report_guard()",
                "public.ai_market_v2_synthetic_child_guard()",
                "public.ai_market_v2_synthetic_orphan_guard()", SIGNATURE):
            cursor.execute("REVOKE ALL ON FUNCTION " + signature + " FROM PUBLIC")
            cursor.execute("REVOKE ALL ON FUNCTION " + signature + " FROM " + ROLE)
        cursor.execute("GRANT EXECUTE ON FUNCTION " + SIGNATURE + " TO " + ROLE)
        for table, name, function in (
            ("ai_workflow_runs","ai_market_v2_synthetic_flow_guard",
             "ai_market_v2_synthetic_flow_guard"),
            ("ai_report_runs","ai_market_v2_synthetic_report_guard",
             "ai_market_v2_synthetic_report_guard"),
            ("ai_agent_jobs","ai_market_v2_synthetic_job_guard",
             "ai_market_v2_synthetic_child_guard"),
            ("ai_workflow_node_runs","ai_market_v2_synthetic_node_guard",
             "ai_market_v2_synthetic_child_guard"),
            ("ai_agent_provider_dispatches","ai_market_v2_synthetic_provider_guard",
             "ai_market_v2_synthetic_child_guard"),
            ("ai_agent_provider_results","ai_market_v2_synthetic_provider_result_guard",
             "ai_market_v2_synthetic_child_guard"),
            ("ai_agent_tool_dispatches","ai_market_v2_synthetic_tool_guard",
             "ai_market_v2_synthetic_child_guard"),
            ("ai_agent_tool_results","ai_market_v2_synthetic_tool_result_guard",
             "ai_market_v2_synthetic_child_guard")):
            cursor.execute("CREATE TRIGGER " + name + " BEFORE INSERT OR UPDATE OR "
                "DELETE ON public." + table + " FOR EACH ROW EXECUTE FUNCTION public." +
                function + "()")
        cursor.execute("CREATE CONSTRAINT TRIGGER ai_market_v2_synthetic_complete "
            "AFTER INSERT ON public.ai_workflow_runs DEFERRABLE INITIALLY DEFERRED "
            "FOR EACH ROW EXECUTE FUNCTION public.ai_market_v2_synthetic_orphan_guard()")


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT EXISTS(SELECT 1 FROM public.ai_report_runs WHERE "
            "snapshot_json::jsonb->>'executionProfile'=%s) OR EXISTS(SELECT 1 "
            "FROM public.ai_workflow_runs WHERE input_json::jsonb->>"
            "'executionProfile'=%s)", [PROFILE, PROFILE])
        if cursor.fetchone()[0]:
            raise RuntimeError("0064 cannot reverse persisted synthetic chain")
        for table,name in (
            ("ai_workflow_runs","ai_market_v2_synthetic_complete"),
            ("ai_agent_tool_results","ai_market_v2_synthetic_tool_result_guard"),
            ("ai_agent_tool_dispatches","ai_market_v2_synthetic_tool_guard"),
            ("ai_agent_provider_results","ai_market_v2_synthetic_provider_result_guard"),
            ("ai_agent_provider_dispatches","ai_market_v2_synthetic_provider_guard"),
            ("ai_workflow_node_runs","ai_market_v2_synthetic_node_guard"),
            ("ai_agent_jobs","ai_market_v2_synthetic_job_guard"),
            ("ai_report_runs","ai_market_v2_synthetic_report_guard"),
            ("ai_workflow_runs","ai_market_v2_synthetic_flow_guard")):
            cursor.execute("DROP TRIGGER " + name + " ON public." + table)
        for signature in (SIGNATURE,"public.ai_market_v2_synthetic_orphan_guard()",
                "public.ai_market_v2_synthetic_child_guard()",
                "public.ai_market_v2_synthetic_report_guard()",
                "public.ai_market_v2_synthetic_flow_guard()"):
            cursor.execute("DROP FUNCTION " + signature)
        for signature, before, after in (
            ("public.ai_market_v2_admitted_result_guard()",
                NEW_RESULT_GUARD, admitted.RESULT_GUARD),
            ("public.ai_market_v2_admitted_tool_guard()",
                NEW_TOOL_GUARD, admitted.TOOL_GUARD),
            ("public.ai_market_v2_parked_workflow_guard()",
                NEW_PARKED_WORKFLOW, prior.NEW_PARKED_WORKFLOW)):
            _version_function(cursor,signature,before,after)
        # Retain the NOLOGIN role, without any remaining callable function.


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0063_business_market_v2_execution_plan")]
    operations = [migrations.RunPython(install,uninstall)]
