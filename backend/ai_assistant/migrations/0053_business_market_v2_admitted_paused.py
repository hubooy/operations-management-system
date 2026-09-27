"""Versioned, material-admitted market v2 snapshot with execution still closed.

The 0044 parked rows and 0045 immutable attestation remain untouched.  A new
report/workflow pair may cite that attestation, but the model/tool catalog is
still unregistered: no node, job, dispatch, or result is permitted here.
"""
from django.db import migrations


PROFILE = "business-agent-screening-promotion-market-admitted-v2"
PARKED = "business-agent-screening-promotion-market-reference-v2"
SNAPSHOT = "business-market-v2-admitted-paused-snapshot-v1"
INPUT = "business-market-v2-admitted-paused-input-v1"
PAUSE = "market_v2_tool_not_registered"
TOOLS = '["get_business_promotion_screening_package_v1","get_business_promotion_screening_analysis_v1","get_business_promotion_screening_budget_v1","get_business_promotion_keyword_sku_v1","get_business_promotion_market_v2"]'
GRAPH = {False: "ff32c5d92bb2920f18acea71823670cc13b9cba71030a00cf9c8b3e78bc9e54b",
         True: "2d930107c35daa345ace5ca411b65c64012209fc5fcd12fd2ec97ca682711e5e"}
EMPTY_POLICY = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"

WORKFLOW_GUARD = r"""CREATE FUNCTION public.ai_market_v2_admitted_workflow_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE value jsonb; admission jsonb; parked public.ai_report_runs%ROWTYPE;
  parked_flow public.ai_workflow_runs%ROWTYPE;
  material public.ai_business_market_v2_materials%ROWTYPE;
BEGIN
  IF TG_OP='DELETE' THEN
    IF OLD.input_json::jsonb->>'executionProfile'='business-agent-screening-promotion-market-admitted-v2'
    THEN RAISE EXCEPTION 'ai_market_v2_admitted_workflow_immutable'; END IF;
    RETURN OLD;
  END IF;
  value:=NEW.input_json::jsonb;
  IF TG_OP='UPDATE' AND (OLD.input_json::jsonb->>'executionProfile'=
       'business-agent-screening-promotion-market-admitted-v2'
       OR value->>'executionProfile'='business-agent-screening-promotion-market-admitted-v2')
  THEN RAISE EXCEPTION 'ai_market_v2_admitted_workflow_immutable'; END IF;
  IF value->>'executionProfile' IS DISTINCT FROM
      'business-agent-screening-promotion-market-admitted-v2' THEN
    IF value->>'schemaVersion'='business-market-v2-admitted-paused-input-v1'
       OR value ? 'marketAdmission' THEN
      RAISE EXCEPTION 'ai_market_v2_admitted_profile_required'; END IF;
    RETURN NEW;
  END IF;
  IF octet_length(NEW.input_json)>32768 THEN
    RAISE EXCEPTION 'ai_market_v2_admitted_input_capacity'; END IF;
  value:=public.ai_screen_fields(NEW.input_json::json,ARRAY[
    'schemaVersion','executionProfile','reportId','marketAdmission',
    'withBudget','proposedTools','registered','agentDispatchSupported',
    'humanReviewRequired','marketAndOwnSalesAdditive','graphDigest','allowedTools']);
  admission:=public.ai_screen_fields((value->'marketAdmission')::json,ARRAY[
    'parkedReportId','selectorDigest','manifestDigest']);
  SELECT * INTO parked FROM public.ai_report_runs item
    WHERE item.id=admission->>'parkedReportId' FOR SHARE;
  SELECT * INTO parked_flow FROM public.ai_workflow_runs item
    WHERE item.id=parked.workflow_id FOR SHARE;
  SELECT * INTO material FROM public.ai_business_market_v2_materials item
    WHERE item.report_id=parked.id FOR SHARE;
  IF value->>'schemaVersion' IS DISTINCT FROM
       'business-market-v2-admitted-paused-input-v1'
     OR value->>'reportId' IS NULL
     OR value->'withBudget' IS NULL
     OR value->'withBudget' NOT IN ('true'::jsonb,'false'::jsonb)
     OR value->'proposedTools' IS DISTINCT FROM
       '["get_business_promotion_screening_package_v1","get_business_promotion_screening_analysis_v1","get_business_promotion_screening_budget_v1","get_business_promotion_keyword_sku_v1","get_business_promotion_market_v2"]'::jsonb
     OR value->'registered' IS DISTINCT FROM 'false'::jsonb
     OR value->'agentDispatchSupported' IS DISTINCT FROM 'false'::jsonb
     OR value->'humanReviewRequired' IS DISTINCT FROM 'true'::jsonb
     OR value->'marketAndOwnSalesAdditive' IS DISTINCT FROM 'false'::jsonb
     OR value->'allowedTools' IS DISTINCT FROM '[]'::jsonb
     OR admission->>'selectorDigest' IS NULL
     OR admission->>'selectorDigest' !~ '^[0-9a-f]{64}$'
     OR admission->>'manifestDigest' IS NULL
     OR admission->>'manifestDigest' !~ '^[0-9a-f]{64}$'
     OR parked.id IS NULL OR material.report_id IS NULL OR parked_flow.id IS NULL
     OR parked.snapshot_json::jsonb->>'executionProfile' IS DISTINCT FROM
       'business-agent-screening-promotion-market-reference-v2'
     OR parked.owner_email IS DISTINCT FROM NEW.owner_email
     OR parked.scope_json IS DISTINCT FROM 'null'
     OR NEW.scope_json IS DISTINCT FROM 'null'
     OR parked_flow.status IS DISTINCT FROM 'paused'
     OR parked_flow.error_code IS DISTINCT FROM 'market_material_not_admitted'
     OR material.selector_digest IS DISTINCT FROM admission->>'selectorDigest'
     OR material.manifest_digest IS DISTINCT FROM admission->>'manifestDigest'
     OR material.source_report_id IS DISTINCT FROM
       parked.snapshot_json::jsonb->'sourceRoot'->>'sourceReportId'
     OR material.selector_digest IS DISTINCT FROM encode(sha256(convert_to(
       (parked.snapshot_json::jsonb->'marketSelector')::text,'UTF8')),'hex')
     OR value->'withBudget' IS DISTINCT FROM
       parked.snapshot_json::jsonb->'withBudget'
     OR NEW.status IS DISTINCT FROM 'paused'
     OR NEW.error_code IS DISTINCT FROM 'market_v2_tool_not_registered'
     OR NEW.owner_email IS NULL OR NEW.model_id IS DISTINCT FROM ''
     OR NEW.model_version IS DISTINCT FROM 0
     OR NEW.allowed_tools_json IS DISTINCT FROM '[]'
     OR NEW.tool_policy_digest IS DISTINCT FROM
       '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'
     OR NEW.provider_round_count IS DISTINCT FROM 0
     OR NEW.tool_call_count IS DISTINCT FROM 0
     OR NEW.dry_run IS DISTINCT FROM 0
     OR NEW.retryable IS DISTINCT FROM 0
     OR NEW.graph_json IS DISTINCT FROM parked_flow.graph_json
     OR NEW.graph_digest IS DISTINCT FROM parked_flow.graph_digest
     OR NEW.graph_digest IS DISTINCT FROM value->>'graphDigest'
     OR NEW.graph_digest IS DISTINCT FROM
       encode(sha256(convert_to(NEW.graph_json,'UTF8')),'hex')
     OR NEW.graph_digest IS DISTINCT FROM (CASE
       WHEN value->'withBudget'='true'::jsonb
       THEN '2d930107c35daa345ace5ca411b65c64012209fc5fcd12fd2ec97ca682711e5e'
       ELSE 'ff32c5d92bb2920f18acea71823670cc13b9cba71030a00cf9c8b3e78bc9e54b' END)
     OR EXISTS(SELECT 1 FROM public.ai_workflow_node_runs node WHERE node.run_id=NEW.id)
  THEN RAISE EXCEPTION 'ai_market_v2_admitted_workflow_invalid'; END IF;
  RETURN NEW;
END $$"""

REPORT_GUARD = r"""CREATE FUNCTION public.ai_market_v2_admitted_report_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE value jsonb; admission jsonb; flow public.ai_workflow_runs%ROWTYPE;
  parked public.ai_report_runs%ROWTYPE; source_report public.ai_report_runs%ROWTYPE;
  parked_flow public.ai_workflow_runs%ROWTYPE;
  source_flow public.ai_workflow_runs%ROWTYPE;
  evidence public.ai_business_evidence_runs%ROWTYPE;
  material public.ai_business_market_v2_materials%ROWTYPE;
BEGIN
  IF TG_OP='DELETE' THEN
    IF OLD.snapshot_json::jsonb->>'executionProfile'=
       'business-agent-screening-promotion-market-admitted-v2'
    THEN RAISE EXCEPTION 'ai_market_v2_admitted_report_immutable'; END IF;
    RETURN OLD;
  END IF;
  value:=NEW.snapshot_json::jsonb;
  IF TG_OP='UPDATE' AND (OLD.snapshot_json::jsonb->>'executionProfile'=
       'business-agent-screening-promotion-market-admitted-v2'
       OR value->>'executionProfile'='business-agent-screening-promotion-market-admitted-v2')
  THEN RAISE EXCEPTION 'ai_market_v2_admitted_report_immutable'; END IF;
  IF value->>'executionProfile' IS DISTINCT FROM
      'business-agent-screening-promotion-market-admitted-v2' THEN
    IF value->>'schemaVersion'='business-market-v2-admitted-paused-snapshot-v1'
       OR value ? 'marketAdmission' THEN
      RAISE EXCEPTION 'ai_market_v2_admitted_profile_required'; END IF;
    RETURN NEW;
  END IF;
  IF octet_length(NEW.snapshot_json)>32768 THEN
    RAISE EXCEPTION 'ai_market_v2_admitted_snapshot_capacity'; END IF;
  value:=public.ai_screen_fields(NEW.snapshot_json::json,ARRAY[
    'schemaVersion','executionProfile','reportId','marketAdmission',
    'withBudget','proposedTools','registered','agentDispatchSupported',
    'humanReviewRequired','marketAndOwnSalesAdditive']);
  admission:=public.ai_screen_fields((value->'marketAdmission')::json,ARRAY[
    'parkedReportId','selectorDigest','manifestDigest']);
  SELECT * INTO flow FROM public.ai_workflow_runs item
    WHERE item.id=NEW.workflow_id FOR SHARE;
  SELECT * INTO parked FROM public.ai_report_runs item
    WHERE item.id=admission->>'parkedReportId' FOR SHARE;
  SELECT * INTO parked_flow FROM public.ai_workflow_runs item
    WHERE item.id=parked.workflow_id FOR SHARE;
  SELECT * INTO material FROM public.ai_business_market_v2_materials item
    WHERE item.report_id=parked.id FOR SHARE;
  SELECT * INTO source_report FROM public.ai_report_runs item
    WHERE item.id=material.source_report_id FOR SHARE;
  SELECT * INTO source_flow FROM public.ai_workflow_runs item
    WHERE item.id=source_report.workflow_id FOR SHARE;
  SELECT * INTO evidence FROM public.ai_business_evidence_runs item
    WHERE item.id=parked.snapshot_json::jsonb->'sourceRoot'->>'evidenceRunId' FOR SHARE;
  IF value->>'schemaVersion' IS DISTINCT FROM
       'business-market-v2-admitted-paused-snapshot-v1'
     OR value->>'reportId' IS DISTINCT FROM NEW.id
     OR value->'withBudget' IS NULL
     OR value->'withBudget' NOT IN ('true'::jsonb,'false'::jsonb)
     OR value->'proposedTools' IS DISTINCT FROM
       '["get_business_promotion_screening_package_v1","get_business_promotion_screening_analysis_v1","get_business_promotion_screening_budget_v1","get_business_promotion_keyword_sku_v1","get_business_promotion_market_v2"]'::jsonb
     OR value->'registered' IS DISTINCT FROM 'false'::jsonb
     OR value->'agentDispatchSupported' IS DISTINCT FROM 'false'::jsonb
     OR value->'humanReviewRequired' IS DISTINCT FROM 'true'::jsonb
     OR value->'marketAndOwnSalesAdditive' IS DISTINCT FROM 'false'::jsonb
     OR admission->>'selectorDigest' IS NULL
     OR admission->>'selectorDigest' !~ '^[0-9a-f]{64}$'
     OR admission->>'manifestDigest' IS NULL
     OR admission->>'manifestDigest' !~ '^[0-9a-f]{64}$'
     OR NEW.owner_email IS NULL OR NEW.scope_json IS DISTINCT FROM 'null'
     OR NEW.budget_plan_id IS NOT NULL OR flow.id IS NULL OR parked.id IS NULL
     OR material.report_id IS NULL OR parked_flow.id IS NULL OR source_report.id IS NULL
     OR source_flow.id IS NULL OR evidence.id IS NULL
     OR parked.id=NEW.id OR parked.owner_email IS DISTINCT FROM NEW.owner_email
     OR parked.scope_json IS DISTINCT FROM 'null'
     OR source_report.owner_email IS DISTINCT FROM NEW.owner_email
     OR evidence.owner_email IS DISTINCT FROM NEW.owner_email
     OR parked.snapshot_json::jsonb->>'executionProfile' IS DISTINCT FROM
       'business-agent-screening-promotion-market-reference-v2'
     OR parked.workflow_id=flow.id
     OR parked_flow.status IS DISTINCT FROM 'paused'
     OR parked_flow.error_code IS DISTINCT FROM 'market_material_not_admitted'
     OR material.selector_digest IS DISTINCT FROM admission->>'selectorDigest'
     OR material.manifest_digest IS DISTINCT FROM admission->>'manifestDigest'
     OR material.source_report_id IS DISTINCT FROM
       parked.snapshot_json::jsonb->'sourceRoot'->>'sourceReportId'
     OR material.source_snapshot_digest IS DISTINCT FROM
       encode(sha256(convert_to(source_report.snapshot_json,'UTF8')),'hex')
     OR material.source_workflow_input_digest IS DISTINCT FROM
       encode(sha256(convert_to(source_flow.input_json,'UTF8')),'hex')
     OR material.selector_digest IS DISTINCT FROM encode(sha256(convert_to(
       (parked.snapshot_json::jsonb->'marketSelector')::text,'UTF8')),'hex')
     OR material.manifest_json_sha256 IS DISTINCT FROM
       encode(sha256(convert_to(material.manifest_json,'UTF8')),'hex')
     OR material.manifest_json::jsonb->>'manifestDigest' IS DISTINCT FROM
       material.manifest_digest
     OR evidence.status IS DISTINCT FROM 'sealed'
     OR evidence.version::text IS DISTINCT FROM
       parked.snapshot_json::jsonb->'sourceRoot'->>'evidenceVersion'
     OR evidence.state_json::jsonb->>'sealedDigest' IS DISTINCT FROM
       parked.snapshot_json::jsonb->'sourceRoot'->>'sealedDigest'
     OR flow.owner_email IS DISTINCT FROM NEW.owner_email
     OR flow.scope_json IS DISTINCT FROM 'null'
     OR flow.status IS DISTINCT FROM 'paused'
     OR flow.error_code IS DISTINCT FROM 'market_v2_tool_not_registered'
     OR flow.request_digest IS DISTINCT FROM NEW.request_digest
     OR flow.input_json::jsonb->>'executionProfile' IS DISTINCT FROM
       'business-agent-screening-promotion-market-admitted-v2'
     OR flow.input_json::jsonb->>'reportId' IS DISTINCT FROM NEW.id
     OR (flow.input_json::jsonb->'marketAdmission') IS DISTINCT FROM admission
     OR (flow.input_json::jsonb->'withBudget') IS DISTINCT FROM value->'withBudget'
     OR (flow.input_json::jsonb->'proposedTools') IS DISTINCT FROM value->'proposedTools'
     OR flow.input_json::jsonb->'allowedTools' IS DISTINCT FROM '[]'::jsonb
     OR flow.allowed_tools_json IS DISTINCT FROM '[]'
     OR flow.model_id IS DISTINCT FROM ''
     OR flow.provider_round_count IS DISTINCT FROM 0
     OR flow.tool_call_count IS DISTINCT FROM 0
     OR value->'withBudget' IS DISTINCT FROM
       parked.snapshot_json::jsonb->'withBudget'
     OR NOT EXISTS(SELECT 1 FROM public.access_control_users actor
       WHERE actor.email=NEW.owner_email AND actor.role='admin'
         AND actor.status='active' AND actor.scope IS NULL)
  THEN RAISE EXCEPTION 'ai_market_v2_admitted_report_invalid'; END IF;
  RETURN NEW;
END $$"""

ORPHAN_GUARD = r"""CREATE FUNCTION public.ai_market_v2_admitted_orphan_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
  IF NEW.input_json::jsonb->>'executionProfile'=
       'business-agent-screening-promotion-market-admitted-v2'
     AND (SELECT count(*) FROM public.ai_report_runs report
       WHERE report.workflow_id=NEW.id AND report.snapshot_json::jsonb->>
         'executionProfile'='business-agent-screening-promotion-market-admitted-v2')<>1
  THEN RAISE EXCEPTION 'ai_market_v2_admitted_workflow_orphan'; END IF;
  RETURN NULL;
END $$"""

JOB_GUARD = r"""CREATE FUNCTION public.ai_market_v2_admitted_job_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE selected_flow text;
BEGIN
  SELECT input_json::jsonb->>'executionProfile' INTO selected_flow
    FROM public.ai_workflow_runs WHERE id=(CASE WHEN TG_OP='DELETE'
      THEN OLD.workflow_run_id ELSE NEW.workflow_run_id END);
  IF selected_flow='business-agent-screening-promotion-market-admitted-v2'
  THEN RAISE EXCEPTION 'ai_market_v2_admitted_agent_dispatch_disabled'; END IF;
  IF TG_OP='DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END $$"""

NODE_GUARD = r"""CREATE FUNCTION public.ai_market_v2_admitted_node_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE selected_flow text;
BEGIN
  SELECT input_json::jsonb->>'executionProfile' INTO selected_flow
    FROM public.ai_workflow_runs WHERE id=(CASE WHEN TG_OP='DELETE'
      THEN OLD.run_id ELSE NEW.run_id END);
  IF selected_flow='business-agent-screening-promotion-market-admitted-v2'
  THEN RAISE EXCEPTION 'ai_market_v2_admitted_node_dispatch_disabled'; END IF;
  IF TG_OP='DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END $$"""

TOOL_GUARD = r"""CREATE FUNCTION public.ai_market_v2_admitted_tool_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE selected_flow text;
BEGIN
  SELECT flow.input_json::jsonb->>'executionProfile' INTO selected_flow
    FROM public.ai_agent_jobs job JOIN public.ai_workflow_runs flow
      ON flow.id=job.workflow_run_id WHERE job.id=NEW.job_id;
  IF NEW.tool_name='get_business_promotion_market_v2'
     OR selected_flow='business-agent-screening-promotion-market-admitted-v2'
  THEN RAISE EXCEPTION 'ai_market_v2_admitted_tool_dispatch_disabled'; END IF;
  RETURN NEW;
END $$"""

RESULT_GUARD = r"""CREATE FUNCTION public.ai_market_v2_admitted_result_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE selected_flow text; selected_tool text;
BEGIN
  SELECT flow.input_json::jsonb->>'executionProfile', dispatch.tool_name
    INTO selected_flow,selected_tool
    FROM public.ai_agent_tool_dispatches dispatch
    JOIN public.ai_agent_jobs job ON job.id=dispatch.job_id
    JOIN public.ai_workflow_runs flow ON flow.id=job.workflow_run_id
    WHERE dispatch.id=NEW.tool_dispatch_id;
  IF selected_tool='get_business_promotion_market_v2'
     OR selected_flow='business-agent-screening-promotion-market-admitted-v2'
  THEN RAISE EXCEPTION 'ai_market_v2_admitted_tool_result_disabled'; END IF;
  RETURN NEW;
END $$"""


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT EXISTS(SELECT 1 FROM public.ai_agent_tool_dispatches "
            "WHERE tool_name='get_business_promotion_market_v2')")
        if cursor.fetchone()[0]:
            raise RuntimeError("0053 requires no preexisting market v2 tool dispatch")
        for definition in (WORKFLOW_GUARD, REPORT_GUARD, ORPHAN_GUARD,
                           JOB_GUARD, NODE_GUARD, TOOL_GUARD, RESULT_GUARD):
            cursor.execute(definition)
        cursor.execute("CREATE TRIGGER ai_market_v2_admitted_workflow_guard "
            "BEFORE INSERT OR UPDATE OR DELETE ON public.ai_workflow_runs "
            "FOR EACH ROW EXECUTE FUNCTION public.ai_market_v2_admitted_workflow_guard()")
        cursor.execute("CREATE TRIGGER ai_market_v2_admitted_report_guard "
            "BEFORE INSERT OR UPDATE OR DELETE ON public.ai_report_runs "
            "FOR EACH ROW EXECUTE FUNCTION public.ai_market_v2_admitted_report_guard()")
        cursor.execute("CREATE CONSTRAINT TRIGGER ai_market_v2_admitted_complete "
            "AFTER INSERT ON public.ai_workflow_runs DEFERRABLE INITIALLY DEFERRED "
            "FOR EACH ROW EXECUTE FUNCTION public.ai_market_v2_admitted_orphan_guard()")
        cursor.execute("CREATE TRIGGER ai_market_v2_admitted_job_guard "
            "BEFORE INSERT OR UPDATE OR DELETE ON public.ai_agent_jobs "
            "FOR EACH ROW EXECUTE FUNCTION public.ai_market_v2_admitted_job_guard()")
        cursor.execute("CREATE TRIGGER ai_market_v2_admitted_node_guard "
            "BEFORE INSERT OR UPDATE OR DELETE ON public.ai_workflow_node_runs "
            "FOR EACH ROW EXECUTE FUNCTION public.ai_market_v2_admitted_node_guard()")
        cursor.execute("CREATE TRIGGER ai_market_v2_admitted_tool_guard "
            "BEFORE INSERT OR UPDATE ON public.ai_agent_tool_dispatches "
            "FOR EACH ROW EXECUTE FUNCTION public.ai_market_v2_admitted_tool_guard()")
        cursor.execute("CREATE TRIGGER ai_market_v2_admitted_result_guard "
            "BEFORE INSERT ON public.ai_agent_tool_results "
            "FOR EACH ROW EXECUTE FUNCTION public.ai_market_v2_admitted_result_guard()")
        for signature in ("ai_market_v2_admitted_workflow_guard()",
                "ai_market_v2_admitted_report_guard()",
                "ai_market_v2_admitted_orphan_guard()",
                "ai_market_v2_admitted_job_guard()",
                "ai_market_v2_admitted_node_guard()",
                "ai_market_v2_admitted_tool_guard()",
                "ai_market_v2_admitted_result_guard()"):
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
            raise RuntimeError("0053 cannot discard admitted market v2 roots")
        for table, trigger in (("ai_agent_tool_results", "ai_market_v2_admitted_result_guard"),
                ("ai_agent_tool_dispatches", "ai_market_v2_admitted_tool_guard"),
                ("ai_workflow_node_runs", "ai_market_v2_admitted_node_guard"),
                ("ai_agent_jobs", "ai_market_v2_admitted_job_guard"),
                ("ai_workflow_runs", "ai_market_v2_admitted_complete"),
                ("ai_report_runs", "ai_market_v2_admitted_report_guard"),
                ("ai_workflow_runs", "ai_market_v2_admitted_workflow_guard")):
            cursor.execute("DROP TRIGGER " + trigger + " ON public." + table)
        for signature in ("ai_market_v2_admitted_result_guard()",
                "ai_market_v2_admitted_tool_guard()",
                "ai_market_v2_admitted_node_guard()",
                "ai_market_v2_admitted_job_guard()",
                "ai_market_v2_admitted_orphan_guard()",
                "ai_market_v2_admitted_report_guard()",
                "ai_market_v2_admitted_workflow_guard()"):
            cursor.execute("DROP FUNCTION public." + signature)


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0052_business_v4_commit_consumption")]
    operations = [migrations.RunPython(install, uninstall)]
