"""Exact persistent promotion profile over frozen 0024 guards; no file version 7.

The old profile branches are retained from their already applied SQL. This
migration only grants the separate report/workflow/screening profile once its
selector can be reconstructed from the sealed source directory in PostgreSQL.
"""
from importlib import import_module

from django.db import migrations


previous = import_module("ai_assistant.migrations.0024_business_screening_runtime")
PROFILE = "business-agent-screening-promotion-reference-v1"
OLD_PROFILE = previous.PROFILE
ALGORITHM = "promotion-keyword-promoted-sku-v1"
REF_SCHEMA = "business-promotion-workflow-reference-candidate-v1"


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise RuntimeError("Frozen promotion migration SQL predecessor changed")
    return source.replace(old, new, 1)


# The budget and integrated guards retain the complete old bodies. They skip
# only the new exact profile, which has a separate report guard below.
BUDGET_GUARD = replace_once(previous.NEW_BUDGET_GUARD,
    "'business-agent-screening-reference-v1')",
    "'business-agent-screening-reference-v1','business-agent-screening-promotion-reference-v1')")
INTEGRATED_GUARD = replace_once(previous.INTEGRATED_REPORT_GUARD,
    "IF snapshot->>'executionProfile'='business-agent-screening-reference-v1' THEN RETURN NEW; END IF;",
    "IF snapshot->>'executionProfile' IN ('business-agent-screening-reference-v1','business-agent-screening-promotion-reference-v1') THEN RETURN NEW; END IF;")

SCREENING_GUARD = replace_once(previous.REPORT_GUARD,
    "  IF snapshot->>'executionProfile' IS DISTINCT FROM 'business-agent-screening-reference-v1' THEN\n",
    "  IF snapshot->>'executionProfile'='business-agent-screening-promotion-reference-v1' THEN RETURN NEW; END IF;\n"
    "  IF snapshot->>'executionProfile' IS DISTINCT FROM 'business-agent-screening-reference-v1' THEN\n")
SCREENING_GUARD = replace_once(SCREENING_GUARD,
    "CREATE FUNCTION ai_business_screening_report_guard()",
    "CREATE OR REPLACE FUNCTION ai_business_screening_report_guard()")

# The second trigger rejects promotion fields on every old profile and checks
# the new profile with the exact 0024 report body plus a directory-derived
# selector proof. No caller-supplied digest is treated as its own authority.
PROMOTION_GUARD = replace_once(previous.REPORT_GUARD,
    "CREATE FUNCTION ai_business_screening_report_guard()",
    "CREATE FUNCTION ai_business_promotion_report_guard()")
PROMOTION_GUARD = replace_once(PROMOTION_GUARD,
    "  n integer; sha text;",
    """  n integer; sha text; selector jsonb; selector_raw json; promotion_ref jsonb;
  chosen public.ai_business_evidence_sources%ROWTYPE; base public.ai_business_evidence_sources%ROWTYPE;
  current_query jsonb; baseline_query jsonb; directory_text text; chosen_text text; base_text text;
  selector_text text; context_text text; binding_text text; promotion_catalog text;
  directory_count integer; directory_first integer; directory_last integer; directory_valid boolean;""")
PROMOTION_GUARD = replace_once(PROMOTION_GUARD,
    """  IF snapshot->>'executionProfile' IS DISTINCT FROM 'business-agent-screening-reference-v1' THEN
    IF snapshot ? 'screeningIntent' OR flow.input_json::jsonb ? 'screeningIntent'
    THEN RAISE EXCEPTION 'ai_screening_intent_requires_profile'; END IF;
    RETURN NEW;
  END IF;""",
    """  IF snapshot->>'executionProfile' IS DISTINCT FROM 'business-agent-screening-promotion-reference-v1' THEN
    IF snapshot ?| ARRAY['promotionSelector','contextDigest','promotionCatalogDigest','promotionAlgorithmVersion']
       OR (flow.id IS NOT NULL AND flow.input_json::jsonb ? 'promotionRef')
    THEN RAISE EXCEPTION 'ai_promotion_fields_require_profile'; END IF;
    RETURN NEW;
  END IF;""")
PROMOTION_GUARD = replace_once(PROMOTION_GUARD,
    "'previousReportId','screeningIntent']",
    "'previousReportId','screeningIntent','promotionSelector','contextDigest','promotionCatalogDigest','promotionAlgorithmVersion']")
PROMOTION_GUARD = replace_once(PROMOTION_GUARD,
    "'screeningIntent','scope','libraryVersion','pipeline','template','skills']",
    "'screeningIntent','scope','libraryVersion','pipeline','template','skills','promotionSelector','contextDigest','promotionCatalogDigest','promotionAlgorithmVersion']")

PROMOTION_CHECK = r"""
  selector_raw:=original->'promotionSelector';
  IF json_typeof(selector_raw) IS DISTINCT FROM 'object'
     OR (SELECT count(*) FROM json_object_keys(selector_raw)) NOT IN (2,3)
  THEN RAISE EXCEPTION 'ai_promotion_selector_shape'; END IF;
  IF selector_raw::jsonb ? 'baselineKey' THEN
    selector:=public.ai_screen_fields(selector_raw,ARRAY['sourceKey','baselineKey','views']);
  ELSE
    selector:=public.ai_screen_fields(selector_raw,ARRAY['sourceKey','views']);
  END IF;
  IF json_typeof(selector_raw->'sourceKey') IS DISTINCT FROM 'string'
     OR selector->>'sourceKey' !~ '^[A-Za-z0-9_-]{1,160}$'
     OR selector->'views' IS DISTINCT FROM '["keyword_sku","keyword_sku_context"]'::jsonb
     OR json_typeof(selector_raw->'views') IS DISTINCT FROM 'array'
     OR (selector ? 'baselineKey' AND (json_typeof(selector_raw->'baselineKey') IS DISTINCT FROM 'string'
       OR selector->>'baselineKey' !~ '^[A-Za-z0-9_-]{1,160}$'
       OR selector->>'baselineKey'=selector->>'sourceKey'))
  THEN RAISE EXCEPTION 'ai_promotion_selector_invalid'; END IF;
  FOREACH k IN ARRAY ARRAY['contextDigest','promotionCatalogDigest','promotionAlgorithmVersion'] LOOP
    IF json_typeof(original->k) IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'ai_promotion_digest_type'; END IF;
  END LOOP;
  IF snapshot->>'contextDigest' !~ '^[0-9a-f]{64}$'
     OR snapshot->>'promotionCatalogDigest' !~ '^[0-9a-f]{64}$'
     OR snapshot->>'promotionAlgorithmVersion' IS DISTINCT FROM 'promotion-keyword-promoted-sku-v1'
  THEN RAISE EXCEPTION 'ai_promotion_algorithm_invalid'; END IF;

  -- The original source rows are immutable. Their canonical JSON, ordinal and
  -- SHA must reproduce both independent directory digests before selection.
  SELECT count(*),min(ordinal),max(ordinal),
    bool_and(query_digest=encode(sha256(convert_to(query_json,'UTF8')),'hex')
      AND domain IN ('sales','netshop','market')
      AND source_key ~ '^[A-Za-z0-9_-]{1,160}$'),
    '['||string_agg('{"domain":"'||domain||'","key":"'||source_key||'","ordinal":'||ordinal::text||
      ',"query":'||query_json||',"queryDigest":"'||query_digest||'"}',',' ORDER BY ordinal)||']'
    INTO directory_count,directory_first,directory_last,directory_valid,directory_text
    FROM public.ai_business_evidence_sources WHERE run_id=parent.id;
  IF directory_count<>(header->>'sourceCount')::integer OR directory_first<>1
     OR directory_last<>directory_count OR directory_valid IS DISTINCT FROM true
     OR snapshot->>'promotionCatalogDigest' IS DISTINCT FROM encode(sha256(convert_to(directory_text,'UTF8')),'hex')
     OR header->>'catalogDigest' IS DISTINCT FROM encode(sha256(convert_to(
        '{"entries":'||directory_text||',"schemaVersion":"business-evidence-directory-v2"}','UTF8')),'hex')
  THEN RAISE EXCEPTION 'ai_promotion_directory_invalid'; END IF;
  SELECT * INTO chosen FROM public.ai_business_evidence_sources
    WHERE run_id=parent.id AND source_key=selector->>'sourceKey';
  IF NOT FOUND OR chosen.domain<>'netshop' OR NOT chosen.finished
     OR chosen.checkpoint_run_version>parent.version
  THEN RAISE EXCEPTION 'ai_promotion_source_invalid'; END IF;
  current_query:=public.ai_screen_fields(chosen.query_json::json,
    ARRAY['platform','shop','dataset','startDate','endDate','window']);
  IF current_query->>'platform' IS DISTINCT FROM '京东'
     OR current_query->>'dataset' IS DISTINCT FROM 'promotion'
     OR current_query->>'window' IS DISTINCT FROM 'current'
  THEN RAISE EXCEPTION 'ai_promotion_source_scope'; END IF;
  chosen_text:='{"domain":"netshop","key":"'||chosen.source_key||'","ordinal":'||chosen.ordinal::text||
    ',"query":'||chosen.query_json||',"queryDigest":"'||chosen.query_digest||'"}';
  base_text:='null';
  IF selector ? 'baselineKey' THEN
    SELECT * INTO base FROM public.ai_business_evidence_sources
      WHERE run_id=parent.id AND source_key=selector->>'baselineKey';
    IF NOT FOUND OR base.domain<>'netshop' OR NOT base.finished
       OR base.checkpoint_run_version>parent.version
    THEN RAISE EXCEPTION 'ai_promotion_baseline_invalid'; END IF;
    baseline_query:=public.ai_screen_fields(base.query_json::json,
      ARRAY['platform','shop','dataset','startDate','endDate','window']);
    IF baseline_query->>'window' NOT IN ('previous','yearAgo')
       OR (current_query-'window') IS DISTINCT FROM (baseline_query-'window')
    THEN RAISE EXCEPTION 'ai_promotion_baseline_scope'; END IF;
    base_text:='{"domain":"netshop","key":"'||base.source_key||'","ordinal":'||base.ordinal::text||
      ',"query":'||base.query_json||',"queryDigest":"'||base.query_digest||'"}';
    selector_text:='{"baselineKey":"'||base.source_key||'","sourceKey":"'||chosen.source_key||
      '","views":["keyword_sku","keyword_sku_context"]}';
  ELSE
    selector_text:='{"sourceKey":"'||chosen.source_key||'","views":["keyword_sku","keyword_sku_context"]}';
  END IF;
  context_text:='{"reportId":"'||NEW.id||'","runId":"'||parent.id||'","screeningId":"'||(intent->>'id')||
    '","sealedDigest":"'||(snapshot->>'sealedDigest')||'"}';
  binding_text:='{"algorithmVersion":"promotion-keyword-promoted-sku-v1","baseline":'||base_text||
    ',"catalogDigest":"'||(snapshot->>'promotionCatalogDigest')||'","context":'||context_text||
    ',"executionProfile":"business-agent-screening-promotion-reference-v1","promotionSelector":'||selector_text||
    ',"source":'||chosen_text||'}';
  IF snapshot->>'contextDigest' IS DISTINCT FROM encode(sha256(convert_to(binding_text,'UTF8')),'hex')
  THEN RAISE EXCEPTION 'ai_promotion_context_digest'; END IF;
"""
PROMOTION_GUARD = replace_once(PROMOTION_GUARD,
    "  IF (snapshot ? 'mappingPlan') IS DISTINCT FROM (snapshot ? 'mappingPlanDigest')",
    PROMOTION_CHECK + "  IF (snapshot ? 'mappingPlan') IS DISTINCT FROM (snapshot ? 'mappingPlanDigest')")

PROMOTION_REF = r"""
  promotion_ref:=jsonb_build_object('schemaVersion','business-promotion-workflow-reference-candidate-v1',
    'promotionSelector',selector,'contextDigest',snapshot->'contextDigest',
    'sealedDigest',snapshot->'sealedDigest','catalogDigest',snapshot->'catalogDigest',
    'promotionCatalogDigest',snapshot->'promotionCatalogDigest',
    'promotionAlgorithmVersion',snapshot->'promotionAlgorithmVersion');
  expected_input:=expected_input||jsonb_build_object('promotionRef',promotion_ref);
"""
PROMOTION_GUARD = replace_once(PROMOTION_GUARD,
    "  IF input IS DISTINCT FROM expected_input",
    PROMOTION_REF + "  IF input IS DISTINCT FROM expected_input")
PROMOTION_GUARD = replace_once(PROMOTION_GUARD,
    "  input_raw:=flow.input_json::json; input:=input_raw::jsonb;",
    """  IF flow.dry_run IS DISTINCT FROM 0
     OR flow.allowed_tools_json IS DISTINCT FROM
       '["get_business_promotion_screening_package_v1","get_business_promotion_screening_analysis_v1","get_business_promotion_screening_budget_v1","get_business_promotion_keyword_sku_v1"]'
     OR flow.graph_digest IS DISTINCT FROM encode(sha256(convert_to(flow.graph_json,'UTF8')),'hex')
     OR (snapshot ? 'budgetRef' AND flow.graph_digest IS DISTINCT FROM
       '7973cd77c1c144158bfd212b7743d9e94c9b4a1345e3507218d96dc5ced3f42b')
     OR (NOT (snapshot ? 'budgetRef') AND flow.graph_digest IS DISTINCT FROM
       'eb3f12e66e532b3ed40aad19bc20da700cdf450f6db6efcd2c6a37b30882de5b')
  THEN RAISE EXCEPTION 'ai_promotion_workflow_profile'; END IF;
  input_raw:=flow.input_json::json; input:=input_raw::jsonb;""")
PROMOTION_GUARD = replace_once(PROMOTION_GUARD,
    "  IF input ? 'mappingRef' THEN",
    """  PERFORM public.ai_screen_fields(input_raw->'promotionRef',ARRAY['schemaVersion','promotionSelector',
    'contextDigest','sealedDigest','catalogDigest','promotionCatalogDigest','promotionAlgorithmVersion']);
  PERFORM public.ai_screen_fields(input_raw->'promotionRef'->'promotionSelector',
    CASE WHEN selector ? 'baselineKey' THEN ARRAY['sourceKey','baselineKey','views']
      ELSE ARRAY['sourceKey','views'] END);
  IF input ? 'mappingRef' THEN""")

WORKFLOW_GUARD = replace_once(previous.WORKFLOW_GUARD,
    "CREATE FUNCTION ai_business_screening_workflow_guard()",
    "CREATE OR REPLACE FUNCTION ai_business_screening_workflow_guard()")
WORKFLOW_GUARD = replace_once(WORKFLOW_GUARD,
    "  IF NOT (NEW.input_json::jsonb ? 'screeningIntent') THEN RETURN NULL; END IF;",
    """  IF NOT (NEW.input_json::jsonb ? 'screeningIntent') THEN
    IF NEW.input_json::jsonb ? 'promotionRef' THEN RAISE EXCEPTION 'ai_promotion_workflow_orphan'; END IF;
    RETURN NULL;
  END IF;""")
WORKFLOW_GUARD = replace_once(WORKFLOW_GUARD,
    "report.snapshot_json::jsonb->>'executionProfile' IS DISTINCT FROM 'business-agent-screening-reference-v1'",
    "report.snapshot_json::jsonb->>'executionProfile' NOT IN ('business-agent-screening-reference-v1','business-agent-screening-promotion-reference-v1')")
WORKFLOW_GUARD = replace_once(WORKFLOW_GUARD,
    "  THEN RAISE EXCEPTION 'ai_screening_workflow_orphan'; END IF;",
    """    OR (report.snapshot_json::jsonb->>'executionProfile'='business-agent-screening-promotion-reference-v1'
      AND NEW.input_json::jsonb->'promotionRef' IS DISTINCT FROM jsonb_build_object(
        'schemaVersion','business-promotion-workflow-reference-candidate-v1',
        'promotionSelector',report.snapshot_json::jsonb->'promotionSelector',
        'contextDigest',report.snapshot_json::jsonb->'contextDigest',
        'sealedDigest',report.snapshot_json::jsonb->'sealedDigest',
        'catalogDigest',report.snapshot_json::jsonb->'catalogDigest',
        'promotionCatalogDigest',report.snapshot_json::jsonb->'promotionCatalogDigest',
        'promotionAlgorithmVersion',report.snapshot_json::jsonb->'promotionAlgorithmVersion'))
    OR (report.snapshot_json::jsonb->>'executionProfile'='business-agent-screening-reference-v1'
      AND NEW.input_json::jsonb ? 'promotionRef')
  THEN RAISE EXCEPTION 'ai_screening_workflow_orphan'; END IF;""")

SCREEN_INITIAL = replace_once(previous.SCREEN_INITIAL,
    "'business-agent-screening-reference-v1')",
    "'business-agent-screening-reference-v1','business-agent-screening-promotion-reference-v1')")
SCREEN_INITIAL = replace_once(SCREEN_INITIAL,
    "OR (snapshot->>'executionProfile'='business-agent-screening-reference-v1' AND snapshot ? 'mappingPlan')",
    "OR (snapshot->>'executionProfile' IN ('business-agent-screening-reference-v1','business-agent-screening-promotion-reference-v1') AND snapshot ? 'mappingPlan')")
SCREEN_INITIAL = replace_once(SCREEN_INITIAL,
    "IF snapshot->>'executionProfile'='business-agent-screening-reference-v1' THEN",
    "IF snapshot->>'executionProfile' IN ('business-agent-screening-reference-v1','business-agent-screening-promotion-reference-v1') THEN")
SCREEN_INITIAL = replace_once(SCREEN_INITIAL,
    "    expected_input:=expected_input||jsonb_build_object('reportId',report.id,'screeningIntent',snapshot->'screeningIntent');",
    """    expected_input:=expected_input||jsonb_build_object('reportId',report.id,'screeningIntent',snapshot->'screeningIntent');
    IF snapshot->>'executionProfile'='business-agent-screening-promotion-reference-v1' THEN
      expected_input:=expected_input||jsonb_build_object('promotionRef',jsonb_build_object(
        'schemaVersion','business-promotion-workflow-reference-candidate-v1',
        'promotionSelector',snapshot->'promotionSelector','contextDigest',snapshot->'contextDigest',
        'sealedDigest',snapshot->'sealedDigest','catalogDigest',snapshot->'catalogDigest',
        'promotionCatalogDigest',snapshot->'promotionCatalogDigest',
        'promotionAlgorithmVersion',snapshot->'promotionAlgorithmVersion'));
    END IF;""")


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for sql in (BUDGET_GUARD, INTEGRATED_GUARD, SCREENING_GUARD, WORKFLOW_GUARD, SCREEN_INITIAL,
                    PROMOTION_GUARD):
            cursor.execute(sql)
        cursor.execute("CREATE TRIGGER ai_business_promotion_report_binding BEFORE INSERT ON ai_report_runs "
                       "FOR EACH ROW EXECUTE FUNCTION ai_business_promotion_report_guard()")


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("""SELECT 1 FROM public.ai_report_runs
          WHERE snapshot_json::jsonb->>'executionProfile'='business-agent-screening-promotion-reference-v1'
             OR snapshot_json::jsonb ?| ARRAY['promotionSelector','contextDigest','promotionCatalogDigest','promotionAlgorithmVersion']
          UNION ALL SELECT 1 FROM public.ai_workflow_runs WHERE input_json::jsonb ? 'promotionRef'
          LIMIT 1""")
        if cursor.fetchone():
            raise RuntimeError("存在词货报告或工作流，即使未完成也禁止逆迁移")
        cursor.execute("DROP TRIGGER ai_business_promotion_report_binding ON public.ai_report_runs")
        cursor.execute("DROP FUNCTION ai_business_promotion_report_guard()")
        for sql in (previous.NEW_BUDGET_GUARD, previous.INTEGRATED_REPORT_GUARD,
                    previous.SCREEN_INITIAL):
            if not sql.startswith("CREATE OR REPLACE FUNCTION "):
                raise RuntimeError("Frozen replace-function predecessor changed")
            cursor.execute(sql)
        for sql in (previous.REPORT_GUARD, previous.WORKFLOW_GUARD):
            if not sql.startswith("CREATE FUNCTION "):
                raise RuntimeError("Frozen create-function predecessor changed")
            cursor.execute(replace_once(sql, "CREATE FUNCTION ", "CREATE OR REPLACE FUNCTION "))


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0025_business_file_opc")]
    operations = [migrations.RunPython(install, uninstall)]
