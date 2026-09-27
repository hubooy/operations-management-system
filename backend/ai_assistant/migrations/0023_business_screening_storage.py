"""Atomic immutable screening publications; existing reports remain unchanged."""
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


FIELDS = """CREATE FUNCTION ai_screen_fields(value json, expected text[]) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,public AS $$
DECLARE result jsonb;
BEGIN
  IF json_typeof(value) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'ai_screen_object_invalid'; END IF;
  result:=value::jsonb;
  IF (SELECT count(*) FROM json_object_keys(value))<>cardinality(expected)
     OR (SELECT count(*) FROM jsonb_object_keys(result))<>cardinality(expected)
     OR result-expected<>'{}'::jsonb THEN RAISE EXCEPTION 'ai_screen_fields_invalid'; END IF;
  RETURN result;
END $$"""

UINT = """CREATE FUNCTION ai_screen_uint(value json, lo bigint, hi bigint) RETURNS bigint
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,public AS $$
DECLARE raw text; result bigint;
BEGIN
  raw:=value::text;
  IF json_typeof(value) IS DISTINCT FROM 'number' OR length(raw)>16 OR raw !~ '^(0|[1-9][0-9]*)$'
  THEN RAISE EXCEPTION 'ai_screen_integer_invalid'; END IF;
  result:=raw::bigint;
  IF result<lo OR result>hi THEN RAISE EXCEPTION 'ai_screen_integer_bound'; END IF;
  RETURN result;
END $$"""

INITIAL = """CREATE FUNCTION ai_screen_initial_guard() RETURNS trigger
LANGUAGE plpgsql VOLATILE SET search_path=pg_catalog,public AS $$
DECLARE report public.ai_report_runs%ROWTYPE; flow public.ai_workflow_runs%ROWTYPE;
  evidence public.ai_business_evidence_runs%ROWTYPE; b jsonb; raw json; manifest jsonb; mr json; header jsonb;
  snapshot jsonb; expected_input jsonb; k text; expected_request jsonb; global_n bigint; owner_n bigint;
  global_bytes bigint; owner_bytes bigint;
BEGIN
  IF octet_length(NEW.binding_json)>8192 OR octet_length(NEW.manifest_json)>65536
     OR NEW.page_count NOT BETWEEN 1 AND 4096 OR NEW.stored_bytes NOT BETWEEN 1 AND 16777216
  THEN RAISE EXCEPTION 'ai_screen_size_exceeded'; END IF;
  IF current_setting('transaction_isolation')<>'read committed' THEN RAISE EXCEPTION 'ai_screen_isolation_invalid'; END IF;
  PERFORM domain FROM public.ai_data_revisions WHERE domain='ai-assistant' FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'ai_screen_revision_missing'; END IF;
  SELECT * INTO report FROM public.ai_report_runs WHERE id=NEW.report_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'ai_screen_report_missing'; END IF;
  SELECT * INTO flow FROM public.ai_workflow_runs WHERE id=report.workflow_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'ai_screen_workflow_missing'; END IF;
  SELECT * INTO evidence FROM public.ai_business_evidence_runs WHERE id=NEW.evidence_id;
  IF NOT FOUND OR evidence.status<>'sealed' OR NEW.scope_json<>'null'
     OR evidence.owner_email<>NEW.owner_email OR evidence.scope_json<>NEW.scope_json
     OR report.owner_email<>NEW.owner_email OR report.scope_json<>NEW.scope_json
     OR flow.owner_email<>NEW.owner_email OR flow.scope_json<>NEW.scope_json
  THEN RAISE EXCEPTION 'ai_screen_owner_or_evidence_invalid'; END IF;
  header:=public.ai_business_v2_header(evidence.plan_json);
  IF header IS NULL OR NOT header ? 'analysisRequest' THEN RAISE EXCEPTION 'ai_screen_request_missing'; END IF;
  snapshot:=report.snapshot_json::jsonb;
  IF snapshot->>'schemaVersion' IS DISTINCT FROM 'business-report-v1'
     OR snapshot->>'evidenceProtocol' IS DISTINCT FROM 'reference-v2'
     OR snapshot->>'executionProfile' IS NULL
     OR snapshot->>'executionProfile' NOT IN ('business-agent-reference-v2','business-agent-budget-reference-v1','business-agent-integrated-reference-v1')
  THEN RAISE EXCEPTION 'ai_screen_profile_invalid'; END IF;
  raw:=NEW.binding_json::json;
  b:=public.ai_screen_fields(raw,ARRAY['reportId','workflowId','ownerEmail','scope','role','snapshotDigest','workflowInputDigest',
    'executionProfile','evidenceRunId','evidenceVersion','evidencePlanDigest','catalogDigest','sealedDigest','sourceCount',
    'sourceInfosDigest','sourcesDigest','analysisRequestDigest','mappingPlanDigest','budgetRef','algorithmVersion']);
  FOREACH k IN ARRAY ARRAY['reportId','workflowId','ownerEmail','role','snapshotDigest','workflowInputDigest','executionProfile',
      'evidenceRunId','evidencePlanDigest','catalogDigest','sealedDigest','sourceInfosDigest','sourcesDigest','analysisRequestDigest','algorithmVersion'] LOOP
    IF json_typeof(raw->k) IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'ai_screen_binding_type'; END IF;
  END LOOP;
  FOREACH k IN ARRAY ARRAY['snapshotDigest','workflowInputDigest','evidencePlanDigest','catalogDigest','sealedDigest',
      'sourceInfosDigest','sourcesDigest','analysisRequestDigest'] LOOP
    IF b->>k !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'ai_screen_binding_digest'; END IF;
  END LOOP;
  expected_request:=to_jsonb(encode(sha256(convert_to((evidence.plan_json::json->'analysisRequest')::text,'UTF8')),'hex'));
  IF b->>'reportId'<>report.id OR b->>'workflowId'<>flow.id OR b->>'ownerEmail'<>NEW.owner_email
     OR b->'scope' IS DISTINCT FROM 'null'::jsonb OR b->>'role'<>'admin'
     OR b->>'snapshotDigest'<>encode(sha256(convert_to(report.snapshot_json,'UTF8')),'hex')
     OR b->>'workflowInputDigest'<>encode(sha256(convert_to(flow.input_json,'UTF8')),'hex')
     OR b->>'executionProfile' IS DISTINCT FROM snapshot->>'executionProfile'
     OR b->>'evidenceRunId'<>evidence.id OR public.ai_screen_uint(raw->'evidenceVersion',1,9007199254740991)<>evidence.version
     OR b->>'evidencePlanDigest'<>encode(sha256(convert_to(evidence.plan_json,'UTF8')),'hex')
     OR b->>'catalogDigest' IS DISTINCT FROM header->>'catalogDigest'
     OR b->>'sealedDigest' IS DISTINCT FROM evidence.state_json::jsonb->>'sealedDigest'
     OR public.ai_screen_uint(raw->'sourceCount',1,48) IS DISTINCT FROM (header->>'sourceCount')::bigint
     OR b->'analysisRequestDigest' IS DISTINCT FROM expected_request
     OR b->'mappingPlanDigest' IS DISTINCT FROM coalesce(snapshot->'mappingPlanDigest','null'::jsonb)
     OR b->'budgetRef' IS DISTINCT FROM coalesce(snapshot->'budgetRef','null'::jsonb)
     OR b->>'algorithmVersion'<>NEW.algorithm_version
  THEN RAISE EXCEPTION 'ai_screen_fixed_binding_invalid'; END IF;
  FOREACH k IN ARRAY ARRAY['evidenceRunId','evidenceVersion','evidencePlanDigest','catalogDigest','sealedDigest','sourceCount'] LOOP
    IF snapshot->k IS DISTINCT FROM b->k THEN RAISE EXCEPTION 'ai_screen_snapshot_evidence_invalid'; END IF;
  END LOOP;
  expected_input:=jsonb_build_object('inputMode','reference-v2','question',snapshot->'question',
    'evidenceRunId',b->'evidenceRunId','evidenceVersion',b->'evidenceVersion','evidencePlanDigest',b->'evidencePlanDigest',
    'catalogDigest',b->'catalogDigest','sealedDigest',b->'sealedDigest','sourceCount',b->'sourceCount');
  IF snapshot->>'executionProfile'='business-agent-integrated-reference-v1' THEN
    IF snapshot->>'reportId' IS DISTINCT FROM report.id OR jsonb_typeof(snapshot->'mappingPlan') IS DISTINCT FROM 'object'
       OR coalesce(snapshot->>'mappingPlanDigest','') !~ '^[0-9a-f]{64}$'
    THEN RAISE EXCEPTION 'ai_screen_mapping_missing'; END IF;
    expected_input:=expected_input||jsonb_build_object('reportId',report.id,'mappingRef',jsonb_build_object(
      'schemaVersion','business-mapping-reference-v1','planDigest',snapshot->'mappingPlanDigest',
      'pairCount',jsonb_array_length(snapshot->'mappingPlan'->'pairs')));
  ELSIF snapshot ?| ARRAY['mappingPlan','mappingPlanDigest'] THEN RAISE EXCEPTION 'ai_screen_mapping_profile_invalid';
  END IF;
  IF snapshot ? 'budgetRef' THEN
    IF report.budget_plan_id IS NULL OR snapshot->>'executionProfile'='business-agent-reference-v2'
       OR snapshot->>'reportId' IS DISTINCT FROM report.id
       OR snapshot->'budgetRef'->>'id' IS DISTINCT FROM report.budget_plan_id
    THEN RAISE EXCEPTION 'ai_screen_budget_missing'; END IF;
    expected_input:=expected_input||jsonb_build_object('reportId',report.id,'budgetRef',snapshot->'budgetRef');
  ELSIF report.budget_plan_id IS NOT NULL OR snapshot->>'executionProfile'='business-agent-budget-reference-v1'
  THEN RAISE EXCEPTION 'ai_screen_budget_missing'; END IF;
  IF flow.input_json::jsonb IS DISTINCT FROM expected_input THEN RAISE EXCEPTION 'ai_screen_workflow_input_invalid'; END IF;
  mr:=NEW.manifest_json::json;
  manifest:=public.ai_screen_fields(mr,ARRAY['schemaVersion','capacityProfile','bindingDigest','selectionPlanDigest','pureResultDigest',
    'serviceResultDigest','algorithmVersion','selectionPolicy','groups','pageCount','contentRootDigest']);
  FOREACH k IN ARRAY ARRAY['schemaVersion','capacityProfile','bindingDigest','selectionPlanDigest','pureResultDigest',
      'serviceResultDigest','algorithmVersion','selectionPolicy','contentRootDigest'] LOOP
    IF json_typeof(mr->k) IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'ai_screen_manifest_type'; END IF;
  END LOOP;
  IF manifest->>'schemaVersion'<>'business-screening-storage-manifest-v1'
     OR manifest->>'capacityProfile'<>NEW.capacity_profile OR manifest->>'bindingDigest'<>NEW.binding_digest
     OR manifest->>'selectionPlanDigest'<>NEW.selection_plan_digest OR manifest->>'pureResultDigest'<>NEW.pure_result_digest
     OR manifest->>'serviceResultDigest'<>NEW.service_result_digest OR manifest->>'algorithmVersion'<>NEW.algorithm_version
     OR manifest->>'selectionPolicy'<>NEW.selection_policy OR manifest->>'contentRootDigest'<>NEW.content_root_digest
     OR public.ai_screen_uint(mr->'pageCount',1,4096)<>NEW.page_count
     OR json_typeof(mr->'groups') IS DISTINCT FROM 'array' OR json_array_length(mr->'groups') NOT BETWEEN 2 AND 65
  THEN RAISE EXCEPTION 'ai_screen_manifest_invalid'; END IF;
  SELECT count(*),coalesce(sum(stored_bytes),0),count(*) FILTER(WHERE owner_email=NEW.owner_email),
    coalesce(sum(stored_bytes) FILTER(WHERE owner_email=NEW.owner_email),0)
    INTO global_n,global_bytes,owner_n,owner_bytes FROM public.ai_business_screening_runs;
  IF global_n+1>200 OR owner_n+1>20 OR global_bytes+NEW.stored_bytes>268435456 OR owner_bytes+NEW.stored_bytes>67108864
  THEN RAISE EXCEPTION 'ai_screen_quota_exceeded'; END IF;
  RETURN NEW;
END $$"""

PAGE = """CREATE FUNCTION ai_screen_page_guard() RETURNS trigger
LANGUAGE plpgsql VOLATILE SET search_path=pg_catalog,public AS $$
DECLARE parent public.ai_business_screening_runs%ROWTYPE; value jsonb; raw json; pagination jsonb; pr json; n bigint;
BEGIN
  IF octet_length(NEW.payload_json)>38000 THEN RAISE EXCEPTION 'ai_screen_page_size_exceeded'; END IF;
  -- The run is append-only. A successful committed run already owns every
  -- declared sequence, while an uncommitted run is visible only to its own
  -- publishing transaction. Its deferred completeness guard forbids committing
  -- spare slots. No parent UPDATE privilege or row lock is needed here.
  SELECT * INTO parent FROM public.ai_business_screening_runs WHERE id=NEW.run_id;
  IF NOT FOUND THEN RAISE EXCEPTION 'ai_screen_parent_missing'; END IF;
  SELECT count(*) INTO n FROM public.ai_business_screening_pages WHERE run_id=NEW.run_id;
  IF NEW.sequence<>n+1 OR NEW.sequence>parent.page_count THEN RAISE EXCEPTION 'ai_screen_page_sequence_invalid'; END IF;
  raw:=NEW.payload_json::json;
  value:=public.ai_screen_fields(raw,CASE WHEN NEW.kind='coverage'
    THEN ARRAY['schemaVersion','bindingDigest','planDigest','resultDigest','authority','items','pagination','pageDigest']
    ELSE ARRAY['schemaVersion','bindingDigest','planDigest','resultDigest','authority','items','pagination','pageDigest','partition'] END);
  IF json_typeof(raw->'schemaVersion') IS DISTINCT FROM 'string'
     OR value->>'schemaVersion' IS DISTINCT FROM 'business-diagnostic-screening-'||NEW.kind||'-v1'
     OR value->>'bindingDigest' IS DISTINCT FROM parent.binding_digest
     OR value->>'planDigest' IS DISTINCT FROM parent.selection_plan_digest
     OR value->>'resultDigest' IS DISTINCT FROM parent.service_result_digest
     OR json_typeof(raw->'pageDigest') IS DISTINCT FROM 'string' OR coalesce(value->>'pageDigest','') !~ '^[0-9a-f]{64}$'
     OR json_typeof(raw->'authority') IS DISTINCT FROM 'object'
     OR value->'authority'->'executedTablesComplete' IS DISTINCT FROM 'true'::jsonb
     OR value->'authority'->'completeSourceTraversalForExecutedTables' IS DISTINCT FROM 'true'::jsonb
     OR value->'authority'->'binding' IS DISTINCT FROM parent.binding_json::jsonb
     OR value->'authority'->>'pureResultDigest' IS DISTINCT FROM parent.pure_result_digest
     OR value->'authority'->>'selectionPlanDigest' IS DISTINCT FROM parent.selection_plan_digest
     OR value->'authority'->>'selectionPolicy' IS DISTINCT FROM parent.selection_policy
     OR json_typeof(raw->'items') IS DISTINCT FROM 'array' OR json_array_length(raw->'items')<>NEW.returned
  THEN RAISE EXCEPTION 'ai_screen_page_binding_invalid'; END IF;
  IF NEW.kind='candidates' AND (json_typeof(raw->'partition') IS DISTINCT FROM 'object'
     OR value->'partition'->>'partitionKey' IS DISTINCT FROM NEW.partition_key
     OR public.ai_screen_uint(raw->'partition'->'retainedRows',0,16)<>NEW.total
     OR public.ai_screen_uint(raw->'partition'->'matchedRows',0,250000)
        <>NEW.total+public.ai_screen_uint(raw->'partition'->'omittedRows',0,250000))
  THEN RAISE EXCEPTION 'ai_screen_partition_invalid'; END IF;
  pr:=raw->'pagination'; pagination:=public.ai_screen_fields(pr,ARRAY['offset','limit','returned','total','nextOffset']);
  IF public.ai_screen_uint(pr->'offset',0,81920)<>NEW."offset" OR public.ai_screen_uint(pr->'limit',20,20)<>20
     OR public.ai_screen_uint(pr->'returned',0,20)<>NEW.returned OR public.ai_screen_uint(pr->'total',0,81920)<>NEW.total
  THEN RAISE EXCEPTION 'ai_screen_pagination_invalid'; END IF;
  IF NEW.next_offset IS NULL THEN
    IF json_typeof(pr->'nextOffset') IS DISTINCT FROM 'null' THEN RAISE EXCEPTION 'ai_screen_terminal_invalid'; END IF;
  ELSIF public.ai_screen_uint(pr->'nextOffset',1,81920)<>NEW.next_offset THEN RAISE EXCEPTION 'ai_screen_next_invalid'; END IF;
  RETURN NEW;
END $$"""

COMPLETE = """CREATE FUNCTION ai_screen_complete_guard() RETURNS trigger
LANGUAGE plpgsql VOLATILE SET search_path=pg_catalog,public AS $$
DECLARE target text; parent public.ai_business_screening_runs%ROWTYPE; p public.ai_business_screening_pages%ROWTYPE;
  g json; b jsonb; first_seq bigint; last_seq bigint; group_pages bigint; group_total bigint; expected_offset bigint;
  seq bigint:=0; group_index bigint:=0; group_count bigint; n bigint; payload_bytes bigint; actual_bytes bigint;
  root text:=encode(sha256(convert_to('[]','UTF8')),'hex'); chain text; previous_key text:=''; kind text; key text;
  item json; partition jsonb; partition_key text; covered jsonb:='{}'::jsonb;
  actual_authority jsonb; authority_raw json; tables jsonb:='{}'::jsonb; partition_tables jsonb:='{}'::jsonb; table_key text;
BEGIN
  IF TG_TABLE_NAME='ai_business_screening_runs' THEN
    target:=NEW.id;
  ELSE
    target:=NEW.run_id;
  END IF;
  SELECT * INTO parent FROM public.ai_business_screening_runs WHERE id=target;
  IF NOT FOUND THEN RAISE EXCEPTION 'ai_screen_parent_missing'; END IF;
  -- Parent insertion always schedules the final guard. The last child also
  -- does, avoiding a quadratic full scan for every page in one publication.
  IF TG_TABLE_NAME='ai_business_screening_pages' THEN
    IF NEW.sequence<>parent.page_count THEN RETURN NULL; END IF;
  END IF;
  SELECT count(*),coalesce(sum(octet_length(payload_json)),0) INTO n,payload_bytes
    FROM public.ai_business_screening_pages WHERE run_id=target;
  actual_bytes:=octet_length(parent.binding_json)+octet_length(parent.manifest_json)+payload_bytes;
  IF n<>parent.page_count OR actual_bytes<>parent.stored_bytes OR actual_bytes>16777216
  THEN RAISE EXCEPTION 'ai_screen_incomplete_bytes'; END IF;
  FOR g IN SELECT value FROM json_array_elements(parent.manifest_json::json->'groups') LOOP
    b:=public.ai_screen_fields(g,ARRAY['kind','partitionKey','total','pageCount','firstSequence','lastSequence','pagesDigest']);
    group_index:=group_index+1;
    IF json_typeof(g->'kind') IS DISTINCT FROM 'string' OR json_typeof(g->'partitionKey') IS DISTINCT FROM 'string'
       OR json_typeof(g->'pagesDigest') IS DISTINCT FROM 'string' OR coalesce(b->>'pagesDigest','') !~ '^[0-9a-f]{64}$'
    THEN RAISE EXCEPTION 'ai_screen_group_type'; END IF;
    kind:=b->>'kind'; key:=b->>'partitionKey';
    IF (group_index=1 AND (kind<>'coverage' OR key<>''))
       OR (group_index>1 AND (kind<>'candidates' OR key !~ '^[0-9a-f]{64}$' OR key COLLATE "C" <= previous_key COLLATE "C"))
    THEN RAISE EXCEPTION 'ai_screen_group_order'; END IF;
    previous_key:=key;
    group_total:=public.ai_screen_uint(g->'total',0,CASE WHEN kind='candidates' THEN 16 ELSE 81920 END);
    group_pages:=public.ai_screen_uint(g->'pageCount',1,4096);
    first_seq:=public.ai_screen_uint(g->'firstSequence',1,4096);
    last_seq:=public.ai_screen_uint(g->'lastSequence',1,4096);
    IF first_seq<>seq+1 OR last_seq-first_seq+1<>group_pages THEN RAISE EXCEPTION 'ai_screen_group_sequence'; END IF;
    chain:=encode(sha256(convert_to('[]','UTF8')),'hex'); expected_offset:=0; group_count:=0;
    FOR p IN SELECT * FROM public.ai_business_screening_pages
        WHERE run_id=target AND sequence BETWEEN first_seq AND last_seq ORDER BY sequence LOOP
      seq:=seq+1; group_count:=group_count+1;
      IF p.sequence<>seq OR p.kind<>kind OR p.partition_key<>key OR p."offset"<>expected_offset OR p.total<>group_total
         OR (p.sequence=last_seq AND p.next_offset IS NOT NULL) OR (p.sequence<last_seq AND p.next_offset IS NULL)
      THEN RAISE EXCEPTION 'ai_screen_group_page_invalid'; END IF;
      IF seq=1 THEN
        authority_raw:=p.payload_json::json->'authority'; actual_authority:=authority_raw::jsonb;
      ELSIF actual_authority IS DISTINCT FROM p.payload_json::jsonb->'authority'
      THEN RAISE EXCEPTION 'ai_screen_authority_changed'; END IF;
      IF kind='coverage' THEN
        FOR item IN SELECT value FROM json_array_elements(p.payload_json::json->'items') LOOP
          IF item->>'kind'='table' THEN
            table_key:=item->'value'->>'tableKey';
            IF coalesce(table_key,'') !~ '^[0-9a-f]{64}$' OR tables ? table_key
            THEN RAISE EXCEPTION 'ai_screen_coverage_table_invalid'; END IF;
            tables:=tables||jsonb_build_object(table_key,true);
          ELSIF item->>'kind'='partition' THEN
            partition:=item->'value'; partition_key:=partition->>'partitionKey';
            table_key:=partition->>'tableKey';
            IF json_typeof(item->'value') IS DISTINCT FROM 'object' OR coalesce(partition_key,'') !~ '^[0-9a-f]{64}$'
               OR coalesce(table_key,'') !~ '^[0-9a-f]{64}$' OR covered ? partition_key
            THEN RAISE EXCEPTION 'ai_screen_coverage_partition_invalid'; END IF;
            covered:=covered||jsonb_build_object(partition_key,partition);
            partition_tables:=partition_tables||jsonb_build_object(table_key,true);
          END IF;
        END LOOP;
      ELSIF NOT covered ? key OR covered->key IS DISTINCT FROM p.payload_json::jsonb->'partition'
      THEN RAISE EXCEPTION 'ai_screen_coverage_partition_mismatch'; END IF;
      expected_offset:=expected_offset+p.returned;
      chain:=encode(sha256(convert_to(chain||':'||group_count::text||':'||p.payload_digest,'UTF8')),'hex');
      root:=encode(sha256(convert_to(root||':'||seq::text||':'||p.payload_digest,'UTF8')),'hex');
    END LOOP;
    IF group_count<>group_pages OR expected_offset<>group_total OR chain<>b->>'pagesDigest'
    THEN RAISE EXCEPTION 'ai_screen_group_incomplete'; END IF;
  END LOOP;
  IF seq<>parent.page_count OR root<>parent.content_root_digest
     OR (SELECT count(*) FROM jsonb_object_keys(covered))<>group_index-1
     OR tables IS DISTINCT FROM partition_tables
     OR public.ai_screen_uint(authority_raw->'tableCount',1,256)<>(SELECT count(*) FROM jsonb_object_keys(tables))
     OR public.ai_screen_uint(authority_raw->'partitionCount',1,64)<>group_index-1
  THEN RAISE EXCEPTION 'ai_screen_root_invalid'; END IF;
  RETURN NULL;
END $$"""


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for definition in (FIELDS, UINT, INITIAL, PAGE, COMPLETE):
            cursor.execute(definition)
        cursor.execute("""ALTER TABLE ai_business_screening_runs ADD CONSTRAINT ai_screen_run_bound CHECK (
          id ~ '^[A-Za-z0-9_-]{1,160}$' AND owner_email=lower(btrim(owner_email)) AND length(owner_email)>0 AND scope_json='null'
          AND octet_length(binding_json) BETWEEN 1 AND 8192 AND octet_length(manifest_json) BETWEEN 1 AND 65536
          AND page_count BETWEEN 1 AND 4096 AND stored_bytes BETWEEN 1 AND 16777216
          AND algorithm_version='diagnostic-signs-v1' AND selection_policy='screen-selection-v1'
          AND storage_schema='business-screening-storage-v1' AND capacity_profile='screening-storage-v1'
          AND binding_digest ~ '^[0-9a-f]{64}$' AND manifest_digest ~ '^[0-9a-f]{64}$'
          AND selection_plan_digest ~ '^[0-9a-f]{64}$' AND pure_result_digest ~ '^[0-9a-f]{64}$'
          AND service_result_digest ~ '^[0-9a-f]{64}$' AND content_root_digest ~ '^[0-9a-f]{64}$'
          AND binding_digest=encode(sha256(convert_to(binding_json,'UTF8')),'hex')
          AND manifest_digest=encode(sha256(convert_to(manifest_json,'UTF8')),'hex'))""")
        cursor.execute("""ALTER TABLE ai_business_screening_pages ADD CONSTRAINT ai_screen_page_bound CHECK (
          id ~ '^[A-Za-z0-9_-]{1,160}$' AND sequence BETWEEN 1 AND 4096
          AND ((kind='coverage' AND partition_key='') OR (kind='candidates' AND partition_key ~ '^[0-9a-f]{64}$'))
          AND "offset" BETWEEN 0 AND 81920 AND total BETWEEN 0 AND 81920 AND returned BETWEEN 0 AND 20
          AND "offset"+returned<=total AND (returned>0 OR (total=0 AND "offset"=0 AND next_offset IS NULL))
          AND ((next_offset IS NULL AND "offset"+returned=total)
               OR (next_offset IS NOT NULL AND next_offset="offset"+returned AND next_offset<total AND returned>0))
          AND octet_length(payload_json) BETWEEN 1 AND 38000 AND payload_digest ~ '^[0-9a-f]{64}$'
          AND payload_digest=encode(sha256(convert_to(payload_json,'UTF8')),'hex'))""")
        for table in ("ai_business_screening_runs", "ai_business_screening_pages"):
            cursor.execute(f"CREATE TRIGGER ai_write_fence BEFORE INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION ai_runtime_write_fence()")
            cursor.execute(f"CREATE TRIGGER ai_immutable_evidence BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION ai_immutable_record_guard()")
            cursor.execute(f"CREATE CONSTRAINT TRIGGER ai_screen_complete AFTER INSERT ON {table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ai_screen_complete_guard()")
        cursor.execute("CREATE TRIGGER ai_screen_initial BEFORE INSERT ON ai_business_screening_runs FOR EACH ROW EXECUTE FUNCTION ai_screen_initial_guard()")
        cursor.execute("CREATE TRIGGER ai_screen_page_initial BEFORE INSERT ON ai_business_screening_pages FOR EACH ROW EXECUTE FUNCTION ai_screen_page_guard()")


def uninstall(apps, schema_editor):
    if (apps.get_model("ai_assistant", "AiBusinessScreeningRun").objects.exists()
            or apps.get_model("ai_assistant", "AiBusinessScreeningPage").objects.exists()):
        raise RuntimeError("存在 screening 筛查结果或分页，禁止回退筛查存储")
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("""SELECT 1 FROM ai_report_runs WHERE snapshot_json::jsonb ? 'screeningIntent'
            UNION ALL SELECT 1 FROM ai_workflow_runs WHERE input_json::jsonb ? 'screeningIntent' LIMIT 1""")
        if cursor.fetchone():
            raise RuntimeError("存在 screening 筛查引用，禁止回退筛查存储")
        for table in ("ai_business_screening_runs", "ai_business_screening_pages"):
            for trigger in ("ai_write_fence", "ai_immutable_evidence", "ai_screen_complete"):
                cursor.execute(f"DROP TRIGGER {trigger} ON {table}")
        cursor.execute("DROP TRIGGER ai_screen_initial ON ai_business_screening_runs")
        cursor.execute("DROP TRIGGER ai_screen_page_initial ON ai_business_screening_pages")
        for signature in ("ai_screen_complete_guard()", "ai_screen_page_guard()", "ai_screen_initial_guard()",
                "ai_screen_uint(json,bigint,bigint)", "ai_screen_fields(json,text[])"):
            cursor.execute("DROP FUNCTION "+signature)


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0022_business_integrated_reports")]
    operations = [
        migrations.CreateModel(name="AiBusinessScreeningRun", fields=[
            ("id", models.CharField(primary_key=True, max_length=160, serialize=False)),
            ("owner_email", models.CharField(max_length=320)), ("scope_json", models.TextField(default="null")),
            ("binding_json", models.TextField()), ("binding_digest", models.CharField(max_length=64)),
            ("manifest_json", models.TextField()), ("manifest_digest", models.CharField(max_length=64)),
            ("selection_plan_digest", models.CharField(max_length=64)), ("pure_result_digest", models.CharField(max_length=64)),
            ("service_result_digest", models.CharField(max_length=64)), ("content_root_digest", models.CharField(max_length=64)),
            ("algorithm_version", models.CharField(max_length=64)), ("selection_policy", models.CharField(max_length=64)),
            ("storage_schema", models.CharField(max_length=64)), ("capacity_profile", models.CharField(max_length=64)),
            ("page_count", models.PositiveIntegerField()), ("stored_bytes", models.PositiveBigIntegerField()),
            ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
            ("report", models.ForeignKey(to="ai_assistant.aireportrun", on_delete=django.db.models.deletion.PROTECT)),
            ("evidence", models.ForeignKey(to="ai_assistant.aibusinessevidencerun", on_delete=django.db.models.deletion.PROTECT)),
        ], options={"db_table":"ai_business_screening_runs", "indexes":[models.Index(fields=["owner_email","-created_at"],name="ai_screen_owner_idx")],
            "constraints":[models.UniqueConstraint(fields=["report","binding_digest","selection_plan_digest","storage_schema"],name="ai_screen_binding_uq")]}),
        migrations.CreateModel(name="AiBusinessScreeningPage", fields=[
            ("id", models.CharField(primary_key=True,max_length=160,serialize=False)),
            ("sequence", models.PositiveIntegerField()), ("kind", models.CharField(max_length=16)),
            ("partition_key", models.CharField(max_length=64,default="")), ("offset", models.PositiveIntegerField()),
            ("returned", models.PositiveIntegerField()), ("total", models.PositiveIntegerField()),
            ("next_offset", models.PositiveIntegerField(null=True)), ("payload_json", models.TextField()),
            ("payload_digest", models.CharField(max_length=64)),
            ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
            ("run", models.ForeignKey(to="ai_assistant.aibusinessscreeningrun",on_delete=django.db.models.deletion.PROTECT)),
        ], options={"db_table":"ai_business_screening_pages", "constraints":[
            models.UniqueConstraint(fields=["run","sequence"],name="ai_screen_page_sequence_uq"),
            models.UniqueConstraint(fields=["run","kind","partition_key","offset"],name="ai_screen_page_offset_uq")]}),
        migrations.RunPython(install, uninstall),
    ]
