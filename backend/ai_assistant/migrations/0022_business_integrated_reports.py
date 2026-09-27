"""Fixed integrated report guards only; no new runtime or old data rewrite."""
from django.db import migrations


# Frozen copy of 0021, so downgrade restores the exact prior function body.
OLD_BUDGET_GUARD = """CREATE FUNCTION ai_business_budget_report_guard() RETURNS trigger
        LANGUAGE plpgsql VOLATILE SET search_path=pg_catalog,public AS $$
        DECLARE budget public.ai_business_budget_plans%ROWTYPE; flow public.ai_workflow_runs%ROWTYPE;
          snapshot jsonb; raw json; ref jsonb; binding jsonb; k text;
        BEGIN
          snapshot:=NEW.snapshot_json::jsonb; raw:=NEW.snapshot_json::json;
          IF NEW.budget_plan_id IS NULL THEN
            IF snapshot->>'executionProfile'='business-agent-budget-reference-v1' OR snapshot ? 'budgetRef'
            THEN RAISE EXCEPTION 'ai_budget_report_reference_missing'; END IF;
            RETURN NEW;
          END IF;
          SELECT * INTO budget FROM public.ai_business_budget_plans WHERE id=NEW.budget_plan_id;
          IF NOT FOUND THEN RAISE EXCEPTION 'ai_budget_report_parameter_missing'; END IF;
          SELECT * INTO flow FROM public.ai_workflow_runs WHERE id=NEW.workflow_id;
          IF NOT FOUND OR flow.owner_email<>budget.owner_email OR flow.scope_json<>budget.scope_json
             OR NEW.owner_email<>budget.owner_email OR NEW.scope_json<>budget.scope_json
          THEN RAISE EXCEPTION 'ai_budget_report_owner_invalid'; END IF;
          binding:=budget.binding_json::jsonb; ref:=snapshot->'budgetRef';
          IF snapshot->>'schemaVersion' IS DISTINCT FROM 'business-report-v1'
             OR snapshot->>'executionProfile' IS DISTINCT FROM 'business-agent-budget-reference-v1'
             OR snapshot->>'evidenceProtocol' IS DISTINCT FROM 'reference-v2' OR snapshot ? 'budgetPlan'
             OR snapshot->>'reportId' IS DISTINCT FROM NEW.id OR NEW.id<>binding->>'reportId'
             OR ref IS DISTINCT FROM jsonb_build_object('schemaVersion','business-budget-reference-v1',
                'id',budget.id,'planDigest',budget.plan_digest,'bindingDigest',budget.binding_digest)
             OR json_typeof(raw->'budgetRef') IS DISTINCT FROM 'object'
          THEN RAISE EXCEPTION 'ai_budget_report_reference_invalid'; END IF;
          IF (SELECT count(*) FROM json_object_keys(raw->'budgetRef'))<>4
             OR (SELECT count(*) FROM json_object_keys(raw))<>(SELECT count(*) FROM jsonb_object_keys(snapshot))
          THEN RAISE EXCEPTION 'ai_budget_report_duplicate_keys'; END IF;
          FOREACH k IN ARRAY ARRAY['evidenceRunId','evidencePlanDigest','catalogDigest','sealedDigest'] LOOP
            IF snapshot->k IS DISTINCT FROM binding->k THEN RAISE EXCEPTION 'ai_budget_report_evidence_invalid'; END IF;
          END LOOP;
          IF json_typeof(raw->'evidenceVersion') IS DISTINCT FROM 'number'
             OR raw->>'evidenceVersion' IS DISTINCT FROM budget.evidence_version::text
          THEN RAISE EXCEPTION 'ai_budget_report_version_invalid'; END IF;
          RETURN NEW;
        END $$"""

NEW_BUDGET_GUARD = OLD_BUDGET_GUARD.replace(
    "CREATE FUNCTION ", "CREATE OR REPLACE FUNCTION ", 1
).replace(
    "snapshot->>'executionProfile' IS DISTINCT FROM 'business-agent-budget-reference-v1'",
    "(snapshot->>'executionProfile' IS NULL OR snapshot->>'executionProfile' NOT IN ('business-agent-budget-reference-v1','business-agent-integrated-reference-v1'))",
    1,
)


PLAN_GUARD = r"""CREATE FUNCTION ai_business_mapping_plan_json(raw text) RETURNS text
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,public AS $$
DECLARE original json; plan jsonb; pair json; p jsonb; sales text; master text; hash text;
  prior text; seen text[]:=ARRAY[]::text[]; pieces text[]:=ARRAY[]::text[]; expected text;
BEGIN
  IF raw IS NULL OR octet_length(raw) NOT BETWEEN 1 AND 16000 THEN
    RAISE EXCEPTION 'ai_integrated_plan_size';
  END IF;
  original:=raw::json; plan:=original::jsonb;
  IF json_typeof(original) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'ai_integrated_plan_shape'; END IF;
  IF (SELECT count(*) FROM json_object_keys(original))<>3
     OR (SELECT count(*) FROM jsonb_object_keys(plan))<>3
     OR (plan-ARRAY['schemaVersion','algorithmVersion','pairs'])<>'{}'::jsonb
     OR plan->>'schemaVersion' IS DISTINCT FROM 'business-mapping-plan-v1'
     OR plan->>'algorithmVersion' IS DISTINCT FROM 'exact-product-partition-v1'
     OR json_typeof(original->'pairs') IS DISTINCT FROM 'array'
  THEN RAISE EXCEPTION 'ai_integrated_plan_shape'; END IF;
  IF json_array_length(original->'pairs') NOT BETWEEN 1 AND 47 THEN RAISE EXCEPTION 'ai_integrated_pair_count'; END IF;
  FOR pair IN SELECT value FROM json_array_elements(original->'pairs') LOOP
    p:=pair::jsonb;
    IF json_typeof(pair) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'ai_integrated_pair_shape'; END IF;
    IF (SELECT count(*) FROM json_object_keys(pair))<>3
       OR (SELECT count(*) FROM jsonb_object_keys(p))<>3
       OR (p-ARRAY['pairKey','salesKey','masterKey'])<>'{}'::jsonb
       OR json_typeof(pair->'salesKey') IS DISTINCT FROM 'string'
       OR json_typeof(pair->'masterKey') IS DISTINCT FROM 'string'
       OR json_typeof(pair->'pairKey') IS DISTINCT FROM 'string'
    THEN RAISE EXCEPTION 'ai_integrated_pair_shape'; END IF;
    sales:=p->>'salesKey'; master:=p->>'masterKey';
    IF sales !~ '^[A-Za-z0-9_-]{1,160}$' OR master !~ '^[A-Za-z0-9_-]{1,160}$'
       OR (p->>'pairKey') !~ '^[0-9a-f]{64}$' OR sales=master
       OR sales=ANY(seen) OR (prior IS NOT NULL AND sales COLLATE "C" <= prior COLLATE "C")
    THEN RAISE EXCEPTION 'ai_integrated_pair_identity'; END IF;
    -- All interpolated values have been confined to ASCII identifiers. These
    -- exact sorted templates are Python canonical(), not jsonb::text.
    hash:=encode(sha256(convert_to('["exact-product-partition-v1","'||sales||'","'||master||'"]','UTF8')),'hex');
    IF p->>'pairKey' IS DISTINCT FROM hash THEN RAISE EXCEPTION 'ai_integrated_pair_digest'; END IF;
    pieces:=array_append(pieces,'{"masterKey":"'||master||'","pairKey":"'||hash||'","salesKey":"'||sales||'"}');
    seen:=array_append(seen,sales); prior:=sales;
  END LOOP;
  expected:='{"algorithmVersion":"exact-product-partition-v1","pairs":['||array_to_string(pieces,',')||'],"schemaVersion":"business-mapping-plan-v1"}';
  IF raw IS DISTINCT FROM expected THEN RAISE EXCEPTION 'ai_integrated_plan_not_canonical'; END IF;
  RETURN expected;
END $$"""


REPORT_GUARD = r"""CREATE FUNCTION ai_business_integrated_report_guard() RETURNS trigger
LANGUAGE plpgsql VOLATILE SET search_path=pg_catalog,public AS $$
DECLARE original json; snapshot jsonb; input_raw json; input jsonb; expected_input jsonb;
  parent public.ai_business_evidence_runs%ROWTYPE; flow public.ai_workflow_runs%ROWTYPE;
  sales public.ai_business_evidence_sources%ROWTYPE; master public.ai_business_evidence_sources%ROWTYPE;
  header jsonb; plan_text text; plan jsonb; pair jsonb; a jsonb; b jsonb; q json; k text;
  n integer; sha text;
BEGIN
  original:=NEW.snapshot_json::json; snapshot:=original::jsonb;
  IF snapshot->>'executionProfile' IS DISTINCT FROM 'business-agent-integrated-reference-v1' THEN
    IF snapshot ?| ARRAY['mappingPlan','mappingPlanDigest','mappingRef'] THEN
      RAISE EXCEPTION 'ai_integrated_fields_require_profile';
    END IF;
    RETURN NEW;
  END IF;
  IF octet_length(NEW.snapshot_json)>32768 OR json_typeof(original) IS DISTINCT FROM 'object' THEN
    RAISE EXCEPTION 'ai_integrated_snapshot_shape';
  END IF;
  IF (SELECT count(*) FROM json_object_keys(original))<>(SELECT count(*) FROM jsonb_object_keys(snapshot))
     OR (snapshot-ARRAY['schemaVersion','executionMode','executionProfile','evidenceProtocol','reportId',
       'evidenceRunId','evidenceVersion','evidencePlanDigest','catalogDigest','sealedDigest','sourceCount','question',
       'mappingPlan','mappingPlanDigest','budgetRef','scope','libraryVersion','pipeline','template','skills','previousReportId'])<>'{}'::jsonb
     OR snapshot->>'schemaVersion' IS DISTINCT FROM 'business-report-v1'
     OR snapshot->>'executionMode' IS DISTINCT FROM 'parallel-v1'
     OR snapshot->>'evidenceProtocol' IS DISTINCT FROM 'reference-v2'
     OR json_typeof(original->'reportId') IS DISTINCT FROM 'string'
     OR snapshot->>'reportId' IS DISTINCT FROM NEW.id
     OR NEW.id !~ '^[A-Za-z0-9_-]{1,160}$'
     OR json_typeof(original->'evidenceRunId') IS DISTINCT FROM 'string'
     OR (snapshot->>'evidenceRunId') !~ '^[A-Za-z0-9_-]{1,160}$'
     OR json_typeof(original->'question') IS DISTINCT FROM 'string'
     OR length(snapshot->>'question') NOT BETWEEN 1 AND 1000
     OR NEW.owner_email<>lower(btrim(NEW.owner_email)) OR NEW.owner_email='' OR NEW.scope_json<>'null'
  THEN RAISE EXCEPTION 'ai_integrated_snapshot_shape'; END IF;
  FOREACH k IN ARRAY ARRAY['mappingPlanDigest','evidencePlanDigest','catalogDigest','sealedDigest'] LOOP
    IF json_typeof(original->k) IS DISTINCT FROM 'string' OR (snapshot->>k) !~ '^[0-9a-f]{64}$'
    THEN RAISE EXCEPTION 'ai_integrated_digest_type'; END IF;
  END LOOP;
  SELECT * INTO parent FROM public.ai_business_evidence_runs WHERE id=snapshot->>'evidenceRunId';
  IF NOT FOUND OR parent.status<>'sealed' OR parent.owner_email<>NEW.owner_email OR parent.scope_json<>NEW.scope_json
  THEN RAISE EXCEPTION 'ai_integrated_evidence_owner'; END IF;
  header:=public.ai_business_v2_header(parent.plan_json);
  IF header IS NULL OR json_typeof(original->'evidenceVersion') IS DISTINCT FROM 'number'
     OR original->>'evidenceVersion' IS DISTINCT FROM parent.version::text
     OR snapshot->>'evidencePlanDigest' IS DISTINCT FROM encode(sha256(convert_to(parent.plan_json,'UTF8')),'hex')
     OR snapshot->>'catalogDigest' IS DISTINCT FROM header->>'catalogDigest'
     OR snapshot->>'sealedDigest' IS DISTINCT FROM parent.state_json::jsonb->>'sealedDigest'
     OR json_typeof(original->'sourceCount') IS DISTINCT FROM 'number'
     OR original->>'sourceCount' IS DISTINCT FROM parent.plan_json::json->>'sourceCount'
  THEN RAISE EXCEPTION 'ai_integrated_evidence_binding'; END IF;
  plan_text:=public.ai_business_mapping_plan_json((original->'mappingPlan')::text);
  plan:=plan_text::jsonb; n:=jsonb_array_length(plan->'pairs');
  sha:=encode(sha256(convert_to(plan_text,'UTF8')),'hex');
  IF snapshot->>'mappingPlanDigest' IS DISTINCT FROM sha THEN RAISE EXCEPTION 'ai_integrated_plan_digest'; END IF;
  FOR pair IN SELECT value FROM jsonb_array_elements(plan->'pairs') LOOP
    SELECT * INTO sales FROM public.ai_business_evidence_sources WHERE run_id=parent.id AND source_key=pair->>'salesKey';
    IF NOT FOUND OR sales.domain<>'sales' OR NOT sales.finished OR sales.checkpoint_run_version>parent.version
    THEN RAISE EXCEPTION 'ai_integrated_sales_source'; END IF;
    SELECT * INTO master FROM public.ai_business_evidence_sources WHERE run_id=parent.id AND source_key=pair->>'masterKey';
    IF NOT FOUND OR master.domain<>'netshop' OR NOT master.finished OR master.checkpoint_run_version>parent.version
    THEN RAISE EXCEPTION 'ai_integrated_master_source'; END IF;
    IF sales.query_digest<>encode(sha256(convert_to(sales.query_json,'UTF8')),'hex')
       OR master.query_digest<>encode(sha256(convert_to(master.query_json,'UTF8')),'hex')
    THEN RAISE EXCEPTION 'ai_integrated_source_digest'; END IF;
    a:=sales.query_json::jsonb; b:=master.query_json::jsonb;
    -- Query semantics come from immutable directory rows, never the caller's plan.
    FOREACH q IN ARRAY ARRAY[sales.query_json::json,master.query_json::json] LOOP
      IF json_typeof(q) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'ai_integrated_source_query'; END IF;
      IF (SELECT count(*) FROM json_object_keys(q))<>(SELECT count(*) FROM jsonb_object_keys(q::jsonb))
      THEN RAISE EXCEPTION 'ai_integrated_source_query'; END IF;
      FOREACH k IN ARRAY ARRAY['platform','shop','startDate','endDate'] LOOP
        IF json_typeof(q->k) IS DISTINCT FROM 'string' OR length(q->>k) NOT BETWEEN 1 AND 100
           OR q->>k IS DISTINCT FROM btrim(q->>k)
        THEN RAISE EXCEPTION 'ai_integrated_source_query'; END IF;
      END LOOP;
      IF (q->>'startDate') !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
         OR (q->>'endDate') !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
      THEN RAISE EXCEPTION 'ai_integrated_source_query'; END IF;
      IF (q->>'endDate')::date-(q->>'startDate')::date NOT BETWEEN 0 AND 92
      THEN RAISE EXCEPTION 'ai_integrated_source_query'; END IF;
      IF (q::jsonb ? 'window') AND (json_typeof(q->'window') IS DISTINCT FROM 'string'
          OR q->>'window' NOT IN ('current','previous','yearAgo')) THEN RAISE EXCEPTION 'ai_integrated_source_query'; END IF;
    END LOOP;
    IF (a-ARRAY['platform','shop','channel','startDate','endDate','window'])<>'{}'::jsonb
       OR (b-ARRAY['platform','shop','dataset','startDate','endDate','window'])<>'{}'::jsonb
       OR jsonb_typeof(a->'channel') IS DISTINCT FROM 'string' OR length(a->>'channel') NOT BETWEEN 1 AND 100
       OR a->>'channel' IS DISTINCT FROM btrim(a->>'channel') OR b->>'platform' NOT IN ('京东','天猫')
       OR b->>'dataset' IS DISTINCT FROM 'master' OR coalesce(b->>'window','current')<>'current'
       OR a->'platform' IS DISTINCT FROM b->'platform' OR a->'shop' IS DISTINCT FROM b->'shop'
    THEN RAISE EXCEPTION 'ai_integrated_pair_scope'; END IF;
  END LOOP;
  SELECT * INTO flow FROM public.ai_workflow_runs WHERE id=NEW.workflow_id;
  IF NOT FOUND OR flow.owner_email<>NEW.owner_email OR flow.scope_json<>NEW.scope_json OR octet_length(flow.input_json)>8000
  THEN RAISE EXCEPTION 'ai_integrated_workflow_owner'; END IF;
  input_raw:=flow.input_json::json; input:=input_raw::jsonb;
  IF json_typeof(input_raw) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'ai_integrated_workflow_input'; END IF;
  expected_input:=jsonb_build_object('inputMode','reference-v2','reportId',NEW.id,'question',snapshot->'question',
    'evidenceRunId',snapshot->'evidenceRunId','evidenceVersion',snapshot->'evidenceVersion',
    'evidencePlanDigest',snapshot->'evidencePlanDigest','catalogDigest',snapshot->'catalogDigest',
    'sealedDigest',snapshot->'sealedDigest','sourceCount',snapshot->'sourceCount',
    'mappingRef',jsonb_build_object('schemaVersion','business-mapping-reference-v1','planDigest',sha,'pairCount',n));
  IF snapshot ? 'budgetRef' THEN expected_input:=expected_input||jsonb_build_object('budgetRef',snapshot->'budgetRef'); END IF;
  IF input IS DISTINCT FROM expected_input
     OR (SELECT count(*) FROM json_object_keys(input_raw))<>(SELECT count(*) FROM jsonb_object_keys(input))
     OR input_raw->>'evidenceVersion' IS DISTINCT FROM original->>'evidenceVersion'
     OR input_raw->>'sourceCount' IS DISTINCT FROM original->>'sourceCount'
  THEN RAISE EXCEPTION 'ai_integrated_workflow_input'; END IF;
  IF json_typeof(input_raw->'mappingRef') IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'ai_integrated_workflow_reference'; END IF;
  IF (SELECT count(*) FROM json_object_keys(input_raw->'mappingRef'))<>3
     OR input_raw->'mappingRef'->>'pairCount' IS DISTINCT FROM n::text
  THEN RAISE EXCEPTION 'ai_integrated_workflow_reference'; END IF;
  IF input ? 'budgetRef' THEN
    IF json_typeof(input_raw->'budgetRef') IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'ai_integrated_workflow_budget'; END IF;
    IF (SELECT count(*) FROM json_object_keys(input_raw->'budgetRef'))<>4 THEN RAISE EXCEPTION 'ai_integrated_workflow_budget'; END IF;
  END IF;
  RETURN NEW;
END $$"""


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(PLAN_GUARD)
        cursor.execute(REPORT_GUARD)
        cursor.execute("CREATE TRIGGER ai_business_integrated_report_binding BEFORE INSERT ON ai_report_runs FOR EACH ROW EXECUTE FUNCTION ai_business_integrated_report_guard()")
        cursor.execute(NEW_BUDGET_GUARD)


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("""SELECT 1 FROM ai_report_runs WHERE
            snapshot_json::jsonb->>'executionProfile'='business-agent-integrated-reference-v1'
            OR snapshot_json::jsonb ?| ARRAY['mappingPlan','mappingPlanDigest','mappingRef']
            UNION ALL SELECT 1 FROM ai_workflow_runs WHERE input_json::jsonb ? 'mappingRef' LIMIT 1""")
        if cursor.fetchone():
            raise RuntimeError("存在 integrated 报告或工作流引用，禁止回退关联协议")
        cursor.execute("DROP TRIGGER ai_business_integrated_report_binding ON ai_report_runs")
        cursor.execute("DROP FUNCTION ai_business_integrated_report_guard()")
        cursor.execute("DROP FUNCTION ai_business_mapping_plan_json(text)")
        cursor.execute(OLD_BUDGET_GUARD.replace("CREATE FUNCTION ", "CREATE OR REPLACE FUNCTION ", 1))


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0021_business_budget_plans")]
    operations = [migrations.RunPython(install, uninstall)]
