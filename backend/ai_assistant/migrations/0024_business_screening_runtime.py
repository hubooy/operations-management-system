"""Prospective screening intent guards; no new tables or runtime permissions."""
from importlib import import_module

from django.db import migrations


PROFILE = "business-agent-screening-reference-v1"
prior = import_module("ai_assistant.migrations.0022_business_integrated_reports")
storage = import_module("ai_assistant.migrations.0023_business_screening_storage")


def _replace(value, old, new):
    if value.count(old) != 1:
        raise RuntimeError("Frozen screening migration SQL predecessor changed")
    return value.replace(old, new, 1)


NEW_BUDGET_GUARD = _replace(prior.NEW_BUDGET_GUARD,
    "('business-agent-budget-reference-v1','business-agent-integrated-reference-v1')",
    "('business-agent-budget-reference-v1','business-agent-integrated-reference-v1','business-agent-screening-reference-v1')")

# Old integrated snapshots retain the complete existing body. The independent
# screening report trigger below validates only the precisely named new profile.
INTEGRATED_REPORT_GUARD = _replace(prior.REPORT_GUARD, "CREATE FUNCTION ", "CREATE OR REPLACE FUNCTION ")
INTEGRATED_REPORT_GUARD = _replace(INTEGRATED_REPORT_GUARD,
    "  original:=NEW.snapshot_json::json; snapshot:=original::jsonb;",
    "  original:=NEW.snapshot_json::json; snapshot:=original::jsonb;\n"
    "  IF snapshot->>'executionProfile'='business-agent-screening-reference-v1' THEN RETURN NEW; END IF;")

INTENT_CHECK = r"""
  intent:=public.ai_screen_fields(original->'screeningIntent',ARRAY['schemaVersion','id','selectionPlanDigest',
    'selectionPolicy','algorithmVersion','capacityPolicy','packagePolicy']);
  FOREACH k IN ARRAY ARRAY['schemaVersion','id','selectionPlanDigest','selectionPolicy','algorithmVersion','capacityPolicy','packagePolicy'] LOOP
    IF json_typeof(original->'screeningIntent'->k) IS DISTINCT FROM 'string'
    THEN RAISE EXCEPTION 'ai_screening_intent_type'; END IF;
  END LOOP;
  IF intent->>'schemaVersion' IS DISTINCT FROM 'business-screening-intent-v1'
     OR intent->>'id' !~ '^[A-Za-z0-9_-]{1,160}$'
     OR intent->>'selectionPlanDigest' !~ '^[0-9a-f]{64}$'
     OR intent->>'selectionPolicy' IS DISTINCT FROM 'screen-selection-v1'
     OR intent->>'algorithmVersion' IS DISTINCT FROM 'diagnostic-signs-v1'
     OR intent->>'capacityPolicy' IS DISTINCT FROM 'screening-storage-v1'
     OR intent->>'packagePolicy' IS DISTINCT FROM 'screening-role-package-policy-v1'
  THEN RAISE EXCEPTION 'ai_screening_intent_invalid'; END IF;
"""

# Reuse the exact immutable directory/query validation from 0022 rather than a
# weaker check of caller-supplied mapping digests. No business fact tables read.
_pair_start = prior.REPORT_GUARD.index("  plan_text:=")
_pair_end = prior.REPORT_GUARD.index("  SELECT * INTO flow", _pair_start)
PAIR_CHECK = prior.REPORT_GUARD[_pair_start:_pair_end]
_base_start = prior.REPORT_GUARD.index("  IF octet_length(NEW.snapshot_json)")
_base_end = prior.REPORT_GUARD.index("  plan_text:=")
BASE_CHECK = prior.REPORT_GUARD[_base_start:_base_end]
BASE_CHECK = _replace(BASE_CHECK,
    "'mappingPlan','mappingPlanDigest','budgetRef','scope','libraryVersion','pipeline','template','skills','previousReportId']",
    "'mappingPlan','mappingPlanDigest','budgetRef','scope','libraryVersion','pipeline','template','skills','previousReportId','screeningIntent']")
BASE_CHECK = _replace(BASE_CHECK,
    "ARRAY['mappingPlanDigest','evidencePlanDigest','catalogDigest','sealedDigest']",
    "ARRAY['evidencePlanDigest','catalogDigest','sealedDigest']")

REPORT_GUARD = r"""CREATE FUNCTION ai_business_screening_report_guard() RETURNS trigger
LANGUAGE plpgsql VOLATILE SET search_path=pg_catalog,public AS $$
DECLARE original json; snapshot jsonb; input_raw json; input jsonb; expected_input jsonb; intent jsonb;
  parent public.ai_business_evidence_runs%ROWTYPE; flow public.ai_workflow_runs%ROWTYPE;
  sales public.ai_business_evidence_sources%ROWTYPE; master public.ai_business_evidence_sources%ROWTYPE;
  header jsonb; plan_text text; plan jsonb; pair jsonb; a jsonb; b jsonb; q json; k text;
  n integer; sha text;
BEGIN
  original:=NEW.snapshot_json::json; snapshot:=original::jsonb;
  SELECT * INTO flow FROM public.ai_workflow_runs WHERE id=NEW.workflow_id;
  IF snapshot->>'executionProfile' IS DISTINCT FROM 'business-agent-screening-reference-v1' THEN
    IF snapshot ? 'screeningIntent' OR flow.input_json::jsonb ? 'screeningIntent'
    THEN RAISE EXCEPTION 'ai_screening_intent_requires_profile'; END IF;
    RETURN NEW;
  END IF;
""" + BASE_CHECK + r"""
  IF NOT snapshot ?& ARRAY['schemaVersion','executionMode','executionProfile','evidenceProtocol','reportId',
      'evidenceRunId','evidenceVersion','evidencePlanDigest','catalogDigest','sealedDigest','sourceCount','question',
      'screeningIntent','scope','libraryVersion','pipeline','template','skills']
     OR NOT header ? 'analysisRequest'
  THEN RAISE EXCEPTION 'ai_screening_required_fields'; END IF;
""" + INTENT_CHECK + r"""
  IF (snapshot ? 'mappingPlan') IS DISTINCT FROM (snapshot ? 'mappingPlanDigest')
  THEN RAISE EXCEPTION 'ai_screening_mapping_incomplete'; END IF;
  IF snapshot ? 'mappingPlan' THEN
    IF json_typeof(original->'mappingPlanDigest') IS DISTINCT FROM 'string'
       OR snapshot->>'mappingPlanDigest' !~ '^[0-9a-f]{64}$'
    THEN RAISE EXCEPTION 'ai_screening_mapping_digest'; END IF;
""" + PAIR_CHECK + r"""
  END IF;
  IF flow.id IS NULL OR flow.owner_email<>NEW.owner_email OR flow.scope_json<>NEW.scope_json
     OR octet_length(flow.input_json)>8000
  THEN RAISE EXCEPTION 'ai_screening_workflow_owner'; END IF;
  input_raw:=flow.input_json::json; input:=input_raw::jsonb;
  IF json_typeof(input_raw) IS DISTINCT FROM 'object'
  THEN RAISE EXCEPTION 'ai_screening_workflow_input'; END IF;
  expected_input:=jsonb_build_object('inputMode','reference-v2','reportId',NEW.id,'question',snapshot->'question',
    'evidenceRunId',snapshot->'evidenceRunId','evidenceVersion',snapshot->'evidenceVersion',
    'evidencePlanDigest',snapshot->'evidencePlanDigest','catalogDigest',snapshot->'catalogDigest',
    'sealedDigest',snapshot->'sealedDigest','sourceCount',snapshot->'sourceCount','screeningIntent',intent);
  IF snapshot ? 'mappingPlan' THEN
    expected_input:=expected_input||jsonb_build_object('mappingRef',jsonb_build_object(
      'schemaVersion','business-mapping-reference-v1','planDigest',sha,'pairCount',n));
  END IF;
  IF snapshot ? 'budgetRef' THEN expected_input:=expected_input||jsonb_build_object('budgetRef',snapshot->'budgetRef'); END IF;
  IF input IS DISTINCT FROM expected_input
     OR (SELECT count(*) FROM json_object_keys(input_raw))<>(SELECT count(*) FROM jsonb_object_keys(input))
     OR input_raw->>'evidenceVersion' IS DISTINCT FROM original->>'evidenceVersion'
     OR input_raw->>'sourceCount' IS DISTINCT FROM original->>'sourceCount'
  THEN RAISE EXCEPTION 'ai_screening_workflow_input'; END IF;
  PERFORM public.ai_screen_fields(input_raw->'screeningIntent',ARRAY['schemaVersion','id','selectionPlanDigest',
    'selectionPolicy','algorithmVersion','capacityPolicy','packagePolicy']);
  IF input ? 'mappingRef' THEN
    PERFORM public.ai_screen_fields(input_raw->'mappingRef',ARRAY['schemaVersion','planDigest','pairCount']);
    IF json_typeof(input_raw->'mappingRef'->'pairCount') IS DISTINCT FROM 'number'
       OR input_raw->'mappingRef'->>'pairCount' IS DISTINCT FROM n::text
    THEN RAISE EXCEPTION 'ai_screening_mapping_reference'; END IF;
  END IF;
  IF input ? 'budgetRef' THEN
    PERFORM public.ai_screen_fields(input_raw->'budgetRef',ARRAY['schemaVersion','id','planDigest','bindingDigest']);
  END IF;
  RETURN NEW;
END $$"""

WORKFLOW_GUARD = r"""CREATE FUNCTION ai_business_screening_workflow_guard() RETURNS trigger
LANGUAGE plpgsql VOLATILE SET search_path=pg_catalog,public AS $$
DECLARE report public.ai_report_runs%ROWTYPE;
BEGIN
  IF NOT (NEW.input_json::jsonb ? 'screeningIntent') THEN RETURN NULL; END IF;
  SELECT * INTO report FROM public.ai_report_runs WHERE workflow_id=NEW.id;
  IF NOT FOUND OR report.owner_email<>NEW.owner_email OR report.scope_json<>NEW.scope_json
     OR report.snapshot_json::jsonb->>'executionProfile' IS DISTINCT FROM 'business-agent-screening-reference-v1'
     OR report.snapshot_json::jsonb->'screeningIntent' IS DISTINCT FROM NEW.input_json::jsonb->'screeningIntent'
  THEN RAISE EXCEPTION 'ai_screening_workflow_orphan'; END IF;
  RETURN NULL;
END $$"""

SCREEN_INITIAL = _replace(storage.INITIAL, "CREATE FUNCTION ", "CREATE OR REPLACE FUNCTION ")
SCREEN_INITIAL = _replace(SCREEN_INITIAL,
    "('business-agent-reference-v2','business-agent-budget-reference-v1','business-agent-integrated-reference-v1')",
    "('business-agent-reference-v2','business-agent-budget-reference-v1','business-agent-integrated-reference-v1','business-agent-screening-reference-v1')")
SCREEN_INITIAL = _replace(SCREEN_INITIAL,
    "  IF snapshot->>'executionProfile'='business-agent-integrated-reference-v1' THEN",
    "  IF snapshot->>'executionProfile'='business-agent-integrated-reference-v1'\n"
    "     OR (snapshot->>'executionProfile'='business-agent-screening-reference-v1' AND snapshot ? 'mappingPlan') THEN")
SCREEN_INITIAL = _replace(SCREEN_INITIAL,
    "  IF flow.input_json::jsonb IS DISTINCT FROM expected_input THEN",
    """  IF snapshot->>'executionProfile'='business-agent-screening-reference-v1' THEN
    IF NEW.id IS DISTINCT FROM snapshot->'screeningIntent'->>'id'
       OR NEW.selection_plan_digest IS DISTINCT FROM snapshot->'screeningIntent'->>'selectionPlanDigest'
       OR NEW.algorithm_version IS DISTINCT FROM snapshot->'screeningIntent'->>'algorithmVersion'
       OR NEW.selection_policy IS DISTINCT FROM snapshot->'screeningIntent'->>'selectionPolicy'
       OR NEW.capacity_profile IS DISTINCT FROM snapshot->'screeningIntent'->>'capacityPolicy'
    THEN RAISE EXCEPTION 'ai_screening_publication_intent'; END IF;
    expected_input:=expected_input||jsonb_build_object('reportId',report.id,'screeningIntent',snapshot->'screeningIntent');
  END IF;
  IF flow.input_json::jsonb IS DISTINCT FROM expected_input THEN""")


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for sql in (NEW_BUDGET_GUARD, INTEGRATED_REPORT_GUARD, SCREEN_INITIAL, REPORT_GUARD, WORKFLOW_GUARD):
            cursor.execute(sql)
        cursor.execute("CREATE TRIGGER ai_business_screening_report_binding BEFORE INSERT ON ai_report_runs FOR EACH ROW EXECUTE FUNCTION ai_business_screening_report_guard()")
        cursor.execute("CREATE CONSTRAINT TRIGGER ai_business_screening_workflow_binding AFTER INSERT ON ai_workflow_runs DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ai_business_screening_workflow_guard()")


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("""SELECT 1 FROM ai_report_runs WHERE snapshot_json::jsonb->>'executionProfile'='business-agent-screening-reference-v1'
          OR snapshot_json::jsonb ? 'screeningIntent'
          UNION ALL SELECT 1 FROM ai_workflow_runs WHERE input_json::jsonb ? 'screeningIntent' LIMIT 1""")
        if cursor.fetchone():
            raise RuntimeError("存在 screening 报告或工作流意图，禁止回退筛查执行协议")
        cursor.execute("DROP TRIGGER ai_business_screening_workflow_binding ON ai_workflow_runs")
        cursor.execute("DROP TRIGGER ai_business_screening_report_binding ON ai_report_runs")
        cursor.execute("DROP FUNCTION ai_business_screening_workflow_guard()")
        cursor.execute("DROP FUNCTION ai_business_screening_report_guard()")
        for sql in (prior.NEW_BUDGET_GUARD, prior.REPORT_GUARD, storage.INITIAL):
            cursor.execute(sql.replace("CREATE FUNCTION ", "CREATE OR REPLACE FUNCTION ", 1))


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0023_business_screening_storage")]
    operations = [migrations.RunPython(install, uninstall)]
