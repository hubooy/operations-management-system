"""New immutable market-v2 five-tool report root, execution still denied."""
from importlib import import_module

from django.db import migrations


parked = import_module("ai_assistant.migrations.0044_business_market_v2_profile")
admitted = import_module("ai_assistant.migrations.0053_business_market_v2_admitted_paused")
PROFILE = "business-agent-screening-promotion-market-execution-v2"
SNAPSHOT = "business-market-v2-execution-snapshot-v1"
INPUT = "business-market-v2-execution-input-v1"
PAUSE = "market_v2_execution_not_activated"
SURFACE = "business_agent_screening_promotion_market_v2"
CATALOG = "3db26b536d59118656f3a5f52638275139b19df24e2afcbf99b8c76cab8ee743"
TOOLS = '["get_business_market_v2_screening_package","get_business_market_v2_screening_analysis","get_business_market_v2_screening_budget","get_business_market_v2_keyword_sku","get_business_promotion_market_v2"]'
GRAPH = {False: "6a0b655cec19328b1c1e02e78330320e04f699b5037868faf42e2ac8f6b0f40d",
    True: "905ae3d59bd4e9554b22c7373274d0569aa2c9e5da7182338351fbf20623f1c2"}

_report_anchor = """  snapshot:=NEW.snapshot_json::jsonb;
  IF snapshot->>'executionProfile' IS DISTINCT FROM
"""
_report_replacement = """  IF TG_OP='UPDATE' AND OLD.snapshot_json::jsonb->>'executionProfile'=
       'business-agent-screening-promotion-market-reference-v2'
  THEN RAISE EXCEPTION 'ai_market_v2_parked_report_immutable'; END IF;
""" + _report_anchor
if parked.REPORT_GUARD.count(_report_anchor) != 1:
    raise RuntimeError("0060 requires exact 0044 report guard predecessor")
NEW_PARKED_REPORT = parked.REPORT_GUARD.replace(_report_anchor, _report_replacement)

_anchor = """  IF value->>'executionProfile' IS DISTINCT FROM
      'business-agent-screening-promotion-market-reference-v2' THEN
"""
_replacement = """  IF TG_OP='UPDATE' AND OLD.input_json::jsonb->>'executionProfile'=
       'business-agent-screening-promotion-market-reference-v2'
  THEN RAISE EXCEPTION 'ai_market_v2_parked_workflow_immutable'; END IF;
  IF value->>'executionProfile'='business-agent-screening-promotion-market-execution-v2'
  THEN RETURN NEW; END IF;
""" + _anchor
if parked.WORKFLOW_GUARD.count(_anchor) != 1:
    raise RuntimeError("0060 requires exact 0044 workflow guard predecessor")
NEW_PARKED_WORKFLOW = parked.WORKFLOW_GUARD.replace(_anchor, _replacement)

WORKFLOW_GUARD = r"""CREATE FUNCTION public.ai_market_v2_execution_workflow_guard()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE value jsonb; root jsonb;
  prior public.ai_report_runs%ROWTYPE;
  material public.ai_business_market_v2_materials%ROWTYPE;
  parked_report public.ai_report_runs%ROWTYPE;
  source_report public.ai_report_runs%ROWTYPE;
  source_flow public.ai_workflow_runs%ROWTYPE;
BEGIN
  IF TG_OP='DELETE' THEN
    IF OLD.input_json::jsonb->>'executionProfile'=
       'business-agent-screening-promotion-market-execution-v2'
    THEN RAISE EXCEPTION 'ai_market_v2_execution_workflow_immutable'; END IF;
    RETURN OLD;
  END IF;
  value:=NEW.input_json::jsonb;
  IF TG_OP='UPDATE' AND (OLD.input_json::jsonb->>'executionProfile'=
       'business-agent-screening-promotion-market-execution-v2'
       OR value->>'executionProfile'='business-agent-screening-promotion-market-execution-v2')
  THEN RAISE EXCEPTION 'ai_market_v2_execution_workflow_immutable'; END IF;
  IF value->>'executionProfile' IS DISTINCT FROM
     'business-agent-screening-promotion-market-execution-v2' THEN
    IF value->>'schemaVersion'='business-market-v2-execution-input-v1'
       OR value ? 'executionRoot' THEN
      RAISE EXCEPTION 'ai_market_v2_execution_profile_required'; END IF;
    RETURN NEW;
  END IF;
  IF octet_length(NEW.input_json)>32768 THEN
    RAISE EXCEPTION 'ai_market_v2_execution_input_capacity'; END IF;
  value:=public.ai_screen_fields(NEW.input_json::json,ARRAY[
    'schemaVersion','executionProfile','reportId','executionRoot',
    'catalogSurface','toolCatalogDigest','proposedTools','withBudget',
    'registeredCatalog','runtimeActivated','agentDispatchSupported',
    'agentReadPersisted','humanReviewRequired','marketAndOwnSalesAdditive',
    'graphDigest','allowedTools']);
  root:=public.ai_screen_fields((value->'executionRoot')::json,ARRAY[
    'admittedReportId','parkedReportId','sourceReportId','ownerEmail',
    'selectorDigest','manifestDigest','marketContextDigest','withBudget']);
  SELECT * INTO prior FROM public.ai_report_runs item
    WHERE item.id=root->>'admittedReportId';
  SELECT * INTO parked_report FROM public.ai_report_runs item
    WHERE item.id=root->>'parkedReportId';
  SELECT * INTO material FROM public.ai_business_market_v2_materials item
    WHERE item.report_id=parked_report.id;
  SELECT * INTO source_report FROM public.ai_report_runs item
    WHERE item.id=root->>'sourceReportId';
  SELECT * INTO source_flow FROM public.ai_workflow_runs item
    WHERE item.id=source_report.workflow_id;
  IF value->>'schemaVersion' IS DISTINCT FROM 'business-market-v2-execution-input-v1'
     OR value->>'reportId' IS NULL
     OR value->>'catalogSurface' IS DISTINCT FROM
       'business_agent_screening_promotion_market_v2'
     OR value->>'toolCatalogDigest' IS DISTINCT FROM
       '3db26b536d59118656f3a5f52638275139b19df24e2afcbf99b8c76cab8ee743'
     OR value->'proposedTools' IS DISTINCT FROM
       '["get_business_market_v2_screening_package","get_business_market_v2_screening_analysis","get_business_market_v2_screening_budget","get_business_market_v2_keyword_sku","get_business_promotion_market_v2"]'::jsonb
     OR value->'allowedTools' IS DISTINCT FROM value->'proposedTools'
     OR value->'registeredCatalog' IS DISTINCT FROM 'true'::jsonb
     OR value->'runtimeActivated' IS DISTINCT FROM 'false'::jsonb
     OR value->'agentDispatchSupported' IS DISTINCT FROM 'false'::jsonb
     OR value->'agentReadPersisted' IS DISTINCT FROM 'false'::jsonb
     OR value->'humanReviewRequired' IS DISTINCT FROM 'true'::jsonb
     OR value->'marketAndOwnSalesAdditive' IS DISTINCT FROM 'false'::jsonb
     OR value->'withBudget' IS NULL
     OR value->'withBudget' NOT IN ('true'::jsonb,'false'::jsonb)
     OR root->>'admittedReportId' IS NULL OR root->>'parkedReportId' IS NULL
     OR root->>'sourceReportId' IS NULL OR root->>'ownerEmail' IS DISTINCT FROM NEW.owner_email
     OR root->>'selectorDigest' IS NULL
     OR root->>'selectorDigest' !~ '^[0-9a-f]{64}$'
     OR root->>'manifestDigest' IS NULL
     OR root->>'manifestDigest' !~ '^[0-9a-f]{64}$'
     OR root->>'marketContextDigest' IS NULL
     OR root->>'marketContextDigest' !~ '^[0-9a-f]{64}$'
     OR root->'withBudget' IS DISTINCT FROM value->'withBudget'
     OR prior.id IS NULL OR parked_report.id IS NULL OR material.report_id IS NULL
     OR source_report.id IS NULL OR source_flow.id IS NULL
     OR prior.id=NEW.id OR parked_report.id=NEW.id
     OR prior.owner_email IS DISTINCT FROM NEW.owner_email
     OR parked_report.owner_email IS DISTINCT FROM NEW.owner_email
     OR source_report.owner_email IS DISTINCT FROM NEW.owner_email
     OR prior.scope_json IS DISTINCT FROM 'null'
     OR parked_report.scope_json IS DISTINCT FROM 'null'
     OR source_report.scope_json IS DISTINCT FROM 'null'
     OR prior.snapshot_json::jsonb->>'executionProfile' IS DISTINCT FROM
       'business-agent-screening-promotion-market-admitted-v2'
     OR prior.snapshot_json::jsonb->'marketAdmission'->>'parkedReportId' IS DISTINCT FROM parked_report.id
     OR prior.snapshot_json::jsonb->'marketAdmission'->>'selectorDigest' IS DISTINCT FROM root->>'selectorDigest'
     OR prior.snapshot_json::jsonb->'marketAdmission'->>'manifestDigest' IS DISTINCT FROM root->>'manifestDigest'
     OR parked_report.snapshot_json::jsonb->>'executionProfile' IS DISTINCT FROM
       'business-agent-screening-promotion-market-reference-v2'
     OR parked_report.snapshot_json::jsonb->'sourceRoot'->>'sourceReportId' IS DISTINCT FROM source_report.id
     OR material.source_report_id IS DISTINCT FROM source_report.id
     OR material.selector_digest IS DISTINCT FROM root->>'selectorDigest'
     OR material.manifest_digest IS DISTINCT FROM root->>'manifestDigest'
     OR material.selector_digest IS DISTINCT FROM encode(sha256(convert_to(
       (parked_report.snapshot_json::jsonb->'marketSelector')::text,'UTF8')),'hex')
     OR material.source_snapshot_digest IS DISTINCT FROM
       encode(sha256(convert_to(source_report.snapshot_json,'UTF8')),'hex')
     OR material.source_workflow_input_digest IS DISTINCT FROM
       encode(sha256(convert_to(source_flow.input_json,'UTF8')),'hex')
     OR source_report.snapshot_json::jsonb->>'executionProfile' IS DISTINCT FROM
       'business-agent-screening-promotion-reference-v1'
     OR source_report.snapshot_json::jsonb->>'reportId' IS DISTINCT FROM source_report.id
     OR material.manifest_json_sha256 IS DISTINCT FROM
       encode(sha256(convert_to(material.manifest_json,'UTF8')),'hex')
     OR material.summary_digest IS DISTINCT FROM
       encode(sha256(convert_to(material.summary_json,'UTF8')),'hex')
     OR root->'withBudget' IS DISTINCT FROM prior.snapshot_json::jsonb->'withBudget'
     OR NEW.status IS DISTINCT FROM 'paused'
     OR NEW.error_code IS DISTINCT FROM 'market_v2_execution_not_activated'
     OR NEW.owner_email IS NULL OR NEW.scope_json IS DISTINCT FROM 'null'
     OR NEW.model_id IS DISTINCT FROM '' OR NEW.model_version IS DISTINCT FROM 0
     OR NEW.allowed_tools_json IS DISTINCT FROM
       '["get_business_market_v2_screening_package","get_business_market_v2_screening_analysis","get_business_market_v2_screening_budget","get_business_market_v2_keyword_sku","get_business_promotion_market_v2"]'
     OR NEW.tool_policy_digest IS DISTINCT FROM
       '3db26b536d59118656f3a5f52638275139b19df24e2afcbf99b8c76cab8ee743'
     OR NEW.graph_digest IS DISTINCT FROM value->>'graphDigest'
     OR NEW.graph_digest IS DISTINCT FROM
       encode(sha256(convert_to(NEW.graph_json,'UTF8')),'hex')
     OR NEW.graph_digest IS DISTINCT FROM (CASE WHEN value->'withBudget'='true'::jsonb
       THEN '905ae3d59bd4e9554b22c7373274d0569aa2c9e5da7182338351fbf20623f1c2'
       ELSE '6a0b655cec19328b1c1e02e78330320e04f699b5037868faf42e2ac8f6b0f40d' END)
     OR NEW.provider_round_count IS DISTINCT FROM 0
     OR NEW.tool_call_count IS DISTINCT FROM 0
     OR NEW.dry_run IS DISTINCT FROM 0 OR NEW.retryable IS DISTINCT FROM 0
     OR NEW.output_json IS NOT NULL OR NEW.current_node_key IS NOT NULL
     OR NEW.provider_dispatch_started_at IS NOT NULL
     OR NEW.cancel_requested IS DISTINCT FROM 0
     OR NEW.resume_count IS DISTINCT FROM 0
     OR NEW.attempt_count IS DISTINCT FROM 0
     OR NEW.lease_token IS DISTINCT FROM ''
     OR NEW.lease_epoch IS DISTINCT FROM 0
     OR NEW.lease_expires_at IS NOT NULL
     OR NEW.started_at IS NOT NULL OR NEW.completed_at IS NOT NULL
     OR NEW.error_message IS DISTINCT FROM ''
     OR NEW.version IS DISTINCT FROM 1
     OR NEW.mutation_token IS DISTINCT FROM ''
     OR EXISTS(SELECT 1 FROM public.ai_workflow_node_runs node WHERE node.run_id=NEW.id)
     OR EXISTS(SELECT 1 FROM public.ai_agent_jobs job WHERE job.workflow_run_id=NEW.id)
  THEN RAISE EXCEPTION 'ai_market_v2_execution_workflow_invalid'; END IF;
  RETURN NEW;
END $$"""

REPORT_GUARD = r"""CREATE FUNCTION public.ai_market_v2_execution_report_guard()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE value jsonb; root jsonb; flow public.ai_workflow_runs%ROWTYPE;
  prior public.ai_report_runs%ROWTYPE;
  material public.ai_business_market_v2_materials%ROWTYPE;
  parked_report public.ai_report_runs%ROWTYPE;
  source_report public.ai_report_runs%ROWTYPE;
  source_flow public.ai_workflow_runs%ROWTYPE;
  actor public.access_control_users%ROWTYPE;
BEGIN
  IF TG_OP='DELETE' THEN
    IF OLD.snapshot_json::jsonb->>'executionProfile'=
       'business-agent-screening-promotion-market-execution-v2'
    THEN RAISE EXCEPTION 'ai_market_v2_execution_report_immutable'; END IF;
    RETURN OLD;
  END IF;
  value:=NEW.snapshot_json::jsonb;
  IF TG_OP='UPDATE' AND (OLD.snapshot_json::jsonb->>'executionProfile'=
       'business-agent-screening-promotion-market-execution-v2'
       OR value->>'executionProfile'='business-agent-screening-promotion-market-execution-v2')
  THEN RAISE EXCEPTION 'ai_market_v2_execution_report_immutable'; END IF;
  IF value->>'executionProfile' IS DISTINCT FROM
     'business-agent-screening-promotion-market-execution-v2' THEN
    IF value->>'schemaVersion'='business-market-v2-execution-snapshot-v1'
       OR value ? 'executionRoot' THEN
      RAISE EXCEPTION 'ai_market_v2_execution_profile_required'; END IF;
    RETURN NEW;
  END IF;
  IF octet_length(NEW.snapshot_json)>32768 THEN
    RAISE EXCEPTION 'ai_market_v2_execution_snapshot_capacity'; END IF;
  value:=public.ai_screen_fields(NEW.snapshot_json::json,ARRAY[
    'schemaVersion','executionProfile','reportId','executionRoot',
    'catalogSurface','toolCatalogDigest','proposedTools','withBudget',
    'registeredCatalog','runtimeActivated','agentDispatchSupported',
    'agentReadPersisted','humanReviewRequired','marketAndOwnSalesAdditive']);
  root:=public.ai_screen_fields((value->'executionRoot')::json,ARRAY[
    'admittedReportId','parkedReportId','sourceReportId','ownerEmail',
    'selectorDigest','manifestDigest','marketContextDigest','withBudget']);
  SELECT * INTO flow FROM public.ai_workflow_runs item WHERE item.id=NEW.workflow_id;
  SELECT * INTO prior FROM public.ai_report_runs item WHERE item.id=root->>'admittedReportId';
  SELECT * INTO parked_report FROM public.ai_report_runs item WHERE item.id=root->>'parkedReportId';
  SELECT * INTO material FROM public.ai_business_market_v2_materials item
    WHERE item.report_id=parked_report.id;
  SELECT * INTO source_report FROM public.ai_report_runs item WHERE item.id=root->>'sourceReportId';
  SELECT * INTO source_flow FROM public.ai_workflow_runs item
    WHERE item.id=source_report.workflow_id;
  SELECT * INTO actor FROM public.access_control_users item WHERE item.email=NEW.owner_email;
  IF value->>'schemaVersion' IS DISTINCT FROM 'business-market-v2-execution-snapshot-v1'
     OR value->>'reportId' IS DISTINCT FROM NEW.id
     OR value->>'catalogSurface' IS DISTINCT FROM
       'business_agent_screening_promotion_market_v2'
     OR value->>'toolCatalogDigest' IS DISTINCT FROM
       '3db26b536d59118656f3a5f52638275139b19df24e2afcbf99b8c76cab8ee743'
     OR value->'proposedTools' IS DISTINCT FROM
       '["get_business_market_v2_screening_package","get_business_market_v2_screening_analysis","get_business_market_v2_screening_budget","get_business_market_v2_keyword_sku","get_business_promotion_market_v2"]'::jsonb
     OR value->'registeredCatalog' IS DISTINCT FROM 'true'::jsonb
     OR value->'runtimeActivated' IS DISTINCT FROM 'false'::jsonb
     OR value->'agentDispatchSupported' IS DISTINCT FROM 'false'::jsonb
     OR value->'agentReadPersisted' IS DISTINCT FROM 'false'::jsonb
     OR value->'humanReviewRequired' IS DISTINCT FROM 'true'::jsonb
     OR value->'marketAndOwnSalesAdditive' IS DISTINCT FROM 'false'::jsonb
     OR value->'withBudget' IS NULL
     OR value->'withBudget' NOT IN ('true'::jsonb,'false'::jsonb)
     OR root->>'admittedReportId' IS NULL OR root->>'parkedReportId' IS NULL
     OR root->>'sourceReportId' IS NULL OR root->>'ownerEmail' IS DISTINCT FROM NEW.owner_email
     OR root->>'selectorDigest' IS NULL
     OR root->>'selectorDigest' !~ '^[0-9a-f]{64}$'
     OR root->>'manifestDigest' IS NULL
     OR root->>'manifestDigest' !~ '^[0-9a-f]{64}$'
     OR root->>'marketContextDigest' IS NULL
     OR root->>'marketContextDigest' !~ '^[0-9a-f]{64}$'
     OR root->'withBudget' IS DISTINCT FROM value->'withBudget'
     OR flow.id IS NULL OR prior.id IS NULL OR parked_report.id IS NULL
     OR material.report_id IS NULL OR source_report.id IS NULL OR source_flow.id IS NULL
     OR prior.id=NEW.id OR parked_report.id=NEW.id OR source_report.id=NEW.id
     OR prior.owner_email IS DISTINCT FROM NEW.owner_email
     OR parked_report.owner_email IS DISTINCT FROM NEW.owner_email
     OR source_report.owner_email IS DISTINCT FROM NEW.owner_email
     OR prior.scope_json IS DISTINCT FROM 'null'
     OR parked_report.scope_json IS DISTINCT FROM 'null'
     OR source_report.scope_json IS DISTINCT FROM 'null'
     OR prior.snapshot_json::jsonb->>'executionProfile' IS DISTINCT FROM
       'business-agent-screening-promotion-market-admitted-v2'
     OR prior.snapshot_json::jsonb->'marketAdmission'->>'parkedReportId' IS DISTINCT FROM parked_report.id
     OR prior.snapshot_json::jsonb->'marketAdmission'->>'selectorDigest' IS DISTINCT FROM root->>'selectorDigest'
     OR prior.snapshot_json::jsonb->'marketAdmission'->>'manifestDigest' IS DISTINCT FROM root->>'manifestDigest'
     OR parked_report.snapshot_json::jsonb->>'executionProfile' IS DISTINCT FROM
       'business-agent-screening-promotion-market-reference-v2'
     OR material.source_report_id IS DISTINCT FROM source_report.id
     OR material.selector_digest IS DISTINCT FROM root->>'selectorDigest'
     OR material.manifest_digest IS DISTINCT FROM root->>'manifestDigest'
     OR material.selector_digest IS DISTINCT FROM encode(sha256(convert_to(
       (parked_report.snapshot_json::jsonb->'marketSelector')::text,'UTF8')),'hex')
     OR material.source_snapshot_digest IS DISTINCT FROM
       encode(sha256(convert_to(source_report.snapshot_json,'UTF8')),'hex')
     OR material.source_workflow_input_digest IS DISTINCT FROM
       encode(sha256(convert_to(source_flow.input_json,'UTF8')),'hex')
     OR source_report.snapshot_json::jsonb->>'executionProfile' IS DISTINCT FROM
       'business-agent-screening-promotion-reference-v1'
     OR source_report.snapshot_json::jsonb->>'reportId' IS DISTINCT FROM source_report.id
     OR material.manifest_json_sha256 IS DISTINCT FROM
       encode(sha256(convert_to(material.manifest_json,'UTF8')),'hex')
     OR material.summary_digest IS DISTINCT FROM
       encode(sha256(convert_to(material.summary_json,'UTF8')),'hex')
     OR root->'withBudget' IS DISTINCT FROM prior.snapshot_json::jsonb->'withBudget'
     OR actor.email IS NULL OR actor.role IS DISTINCT FROM 'admin'
     OR actor.status IS DISTINCT FROM 'active' OR actor.scope IS NOT NULL
     OR flow.owner_email IS DISTINCT FROM NEW.owner_email
     OR flow.scope_json IS DISTINCT FROM 'null'
     OR flow.status IS DISTINCT FROM 'paused'
     OR flow.error_code IS DISTINCT FROM 'market_v2_execution_not_activated'
     OR flow.model_id IS DISTINCT FROM '' OR flow.model_version IS DISTINCT FROM 0
     OR flow.allowed_tools_json IS DISTINCT FROM
       '["get_business_market_v2_screening_package","get_business_market_v2_screening_analysis","get_business_market_v2_screening_budget","get_business_market_v2_keyword_sku","get_business_promotion_market_v2"]'
     OR flow.tool_policy_digest IS DISTINCT FROM value->>'toolCatalogDigest'
     OR flow.request_digest IS DISTINCT FROM NEW.request_digest
     OR flow.input_json::jsonb->>'executionProfile' IS DISTINCT FROM
       'business-agent-screening-promotion-market-execution-v2'
     OR flow.input_json::jsonb->>'reportId' IS DISTINCT FROM NEW.id
     OR flow.input_json::jsonb->'executionRoot' IS DISTINCT FROM root
     OR flow.input_json::jsonb->>'graphDigest' IS DISTINCT FROM flow.graph_digest
     OR flow.provider_round_count IS DISTINCT FROM 0
     OR flow.tool_call_count IS DISTINCT FROM 0
     OR NEW.owner_email IS NULL OR NEW.scope_json IS DISTINCT FROM 'null'
     OR NEW.budget_plan_id IS NOT NULL
  THEN RAISE EXCEPTION 'ai_market_v2_execution_report_invalid'; END IF;
  RETURN NEW;
END $$"""

ORPHAN_GUARD = r"""CREATE FUNCTION public.ai_market_v2_execution_orphan_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
  IF NEW.input_json::jsonb->>'executionProfile'=
       'business-agent-screening-promotion-market-execution-v2'
     AND (SELECT count(*) FROM public.ai_report_runs report
       WHERE report.workflow_id=NEW.id AND report.snapshot_json::jsonb->>
         'executionProfile'='business-agent-screening-promotion-market-execution-v2')<>1
  THEN RAISE EXCEPTION 'ai_market_v2_execution_workflow_orphan'; END IF;
  RETURN NULL;
END $$"""

CHILD_GUARD = r"""CREATE FUNCTION public.ai_market_v2_execution_child_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE flow_id text; target jsonb; targets jsonb[];
BEGIN
  IF TG_OP='INSERT' THEN targets:=ARRAY[to_jsonb(NEW)];
  ELSIF TG_OP='DELETE' THEN targets:=ARRAY[to_jsonb(OLD)];
  ELSE targets:=ARRAY[to_jsonb(OLD),to_jsonb(NEW)]; END IF;
  FOREACH target IN ARRAY targets LOOP
  flow_id:=NULL;
  IF TG_TABLE_NAME='ai_workflow_node_runs' THEN
    flow_id:=target->>'run_id';
  ELSIF TG_TABLE_NAME='ai_agent_jobs' THEN
    flow_id:=target->>'workflow_run_id';
  ELSIF TG_TABLE_NAME IN ('ai_agent_provider_dispatches','ai_agent_tool_dispatches') THEN
    SELECT job.workflow_run_id INTO flow_id FROM public.ai_agent_jobs job
      WHERE job.id=target->>'job_id';
  ELSIF TG_TABLE_NAME='ai_agent_provider_results' THEN
    SELECT job.workflow_run_id INTO flow_id FROM public.ai_agent_provider_dispatches dispatch
      JOIN public.ai_agent_jobs job ON job.id=dispatch.job_id
      WHERE dispatch.id=target->>'dispatch_id';
  ELSIF TG_TABLE_NAME='ai_agent_tool_results' THEN
    SELECT job.workflow_run_id INTO flow_id FROM public.ai_agent_tool_dispatches dispatch
      JOIN public.ai_agent_jobs job ON job.id=dispatch.job_id
      WHERE dispatch.id=target->>'tool_dispatch_id';
  ELSE RAISE EXCEPTION 'ai_market_v2_execution_child_table_invalid'; END IF;
  IF EXISTS(SELECT 1 FROM public.ai_workflow_runs flow
     WHERE flow.id=flow_id AND flow.input_json::jsonb->>'executionProfile'=
       'business-agent-screening-promotion-market-execution-v2')
  THEN RAISE EXCEPTION 'ai_market_v2_execution_child_dispatch_disabled'; END IF;
  END LOOP;
  IF TG_OP='DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END $$"""


def _check_previous(cursor, signature, definition):
    cursor.execute("SELECT p.oid,p.prosrc,p.proacl::text,p.prosecdef,p.proconfig,"
        "pg_catalog.pg_get_userbyid(p.proowner) FROM pg_catalog.pg_proc p "
        "WHERE p.oid=to_regprocedure(%s)", [signature])
    row = cursor.fetchone()
    if (row is None or row[1] != definition.split("$$")[1]
            or row[3] is not False
            or {part.replace(" ", "") for part in (row[4] or [])}
                != {"search_path=pg_catalog,public"}):
        raise RuntimeError("0060 requires frozen 0044 guard predecessor: " + signature)
    cursor.execute("SELECT pg_catalog.pg_get_userbyid(c.relowner) FROM "
        "pg_catalog.pg_class c WHERE c.oid=to_regclass(%s)",
        ["public.ai_business_market_v2_materials"])
    sidecar_owner = cursor.fetchone()
    if sidecar_owner is None or row[5] != sidecar_owner[0]:
        raise RuntimeError("0060 requires same protected market material owner")
    cursor.execute("SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p,"
        "pg_catalog.aclexplode(p.proacl) a WHERE p.oid=%s AND a.grantee=0 "
        "AND a.privilege_type='EXECUTE')", [row[0]])
    if cursor.fetchone() != (False,):
        raise RuntimeError("0060 predecessor PUBLIC EXECUTE reopened")
    return row[0], row[2], row[5]


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for signature, before, after in (
            ("public.ai_market_v2_parked_report_guard()",
                parked.REPORT_GUARD, NEW_PARKED_REPORT),
            ("public.ai_market_v2_parked_workflow_guard()",
                parked.WORKFLOW_GUARD, NEW_PARKED_WORKFLOW)):
            old = _check_previous(cursor, signature, before)
            cursor.execute(after.replace("CREATE FUNCTION",
                "CREATE OR REPLACE FUNCTION", 1))
            if _check_previous(cursor, signature, after) != old:
                raise RuntimeError("0060 may not change 0044 guard OID/ACL/owner")
        for definition in (WORKFLOW_GUARD, REPORT_GUARD, ORPHAN_GUARD,
                           CHILD_GUARD):
            cursor.execute(definition)
        for table, name, function in (
            ("ai_workflow_runs", "ai_market_v2_execution_workflow_guard",
             "ai_market_v2_execution_workflow_guard"),
            ("ai_report_runs", "ai_market_v2_execution_report_guard",
             "ai_market_v2_execution_report_guard"),
            ("ai_agent_jobs", "ai_market_v2_execution_job_guard",
             "ai_market_v2_execution_child_guard"),
            ("ai_workflow_node_runs", "ai_market_v2_execution_node_guard",
             "ai_market_v2_execution_child_guard"),
            ("ai_agent_provider_dispatches", "ai_market_v2_execution_provider_guard",
             "ai_market_v2_execution_child_guard"),
            ("ai_agent_tool_dispatches", "ai_market_v2_execution_tool_guard",
             "ai_market_v2_execution_child_guard"),
            ("ai_agent_provider_results", "ai_market_v2_execution_provider_result_guard",
             "ai_market_v2_execution_child_guard"),
            ("ai_agent_tool_results", "ai_market_v2_execution_tool_result_guard",
             "ai_market_v2_execution_child_guard"),
        ):
            cursor.execute("CREATE TRIGGER " + name + " BEFORE INSERT OR UPDATE OR "
                "DELETE ON public." + table + " FOR EACH ROW EXECUTE FUNCTION "
                "public." + function + "()")
        cursor.execute("CREATE CONSTRAINT TRIGGER ai_market_v2_execution_complete "
            "AFTER INSERT ON public.ai_workflow_runs DEFERRABLE INITIALLY "
            "DEFERRED FOR EACH ROW EXECUTE FUNCTION "
            "public.ai_market_v2_execution_orphan_guard()")
        for signature in ("ai_market_v2_execution_workflow_guard()",
                "ai_market_v2_execution_report_guard()",
                "ai_market_v2_execution_orphan_guard()",
                "ai_market_v2_execution_child_guard()"):
            cursor.execute("REVOKE ALL ON FUNCTION public." + signature + " FROM PUBLIC")


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT EXISTS(SELECT 1 FROM public.ai_report_runs WHERE "
            "snapshot_json::jsonb->>'executionProfile'=%s) OR EXISTS(SELECT 1 "
            "FROM public.ai_workflow_runs WHERE input_json::jsonb->>"
            "'executionProfile'=%s)", [PROFILE, PROFILE])
        if cursor.fetchone()[0]:
            raise RuntimeError("0060 cannot remove execution guards with persisted roots")
        for signature, definition in (
            ("public.ai_market_v2_parked_report_guard()", NEW_PARKED_REPORT),
            ("public.ai_market_v2_parked_workflow_guard()", NEW_PARKED_WORKFLOW)):
            _check_previous(cursor, signature, definition)
        for table, name in (
            ("ai_workflow_runs", "ai_market_v2_execution_complete"),
            ("ai_agent_tool_results", "ai_market_v2_execution_tool_result_guard"),
            ("ai_agent_provider_results", "ai_market_v2_execution_provider_result_guard"),
            ("ai_agent_tool_dispatches", "ai_market_v2_execution_tool_guard"),
            ("ai_agent_provider_dispatches", "ai_market_v2_execution_provider_guard"),
            ("ai_workflow_node_runs", "ai_market_v2_execution_node_guard"),
            ("ai_agent_jobs", "ai_market_v2_execution_job_guard"),
            ("ai_report_runs", "ai_market_v2_execution_report_guard"),
            ("ai_workflow_runs", "ai_market_v2_execution_workflow_guard"),
        ):
            cursor.execute("DROP TRIGGER " + name + " ON public." + table)
        for signature in ("ai_market_v2_execution_child_guard()",
                "ai_market_v2_execution_orphan_guard()",
                "ai_market_v2_execution_report_guard()",
                "ai_market_v2_execution_workflow_guard()"):
            cursor.execute("DROP FUNCTION public." + signature)
        for signature, definition in (
            ("public.ai_market_v2_parked_report_guard()", parked.REPORT_GUARD),
            ("public.ai_market_v2_parked_workflow_guard()", parked.WORKFLOW_GUARD)):
            cursor.execute(definition.replace("CREATE FUNCTION",
                "CREATE OR REPLACE FUNCTION", 1))
            _check_previous(cursor, signature, definition)


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0059_business_promotion_budget_v10_reader_fence")]
    operations = [migrations.RunPython(install, uninstall)]
