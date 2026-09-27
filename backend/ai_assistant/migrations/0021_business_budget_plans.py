"""Immutable budget references; old report bytes and runtime profiles unchanged."""
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("""ALTER TABLE ai_business_budget_plans ADD CONSTRAINT ai_business_budget_bound CHECK (
          id ~ '^[A-Za-z0-9_-]{1,160}$' AND owner_email=lower(btrim(owner_email)) AND length(owner_email)>0
          AND scope_json='null' AND evidence_version>=1
          AND octet_length(plan_json) BETWEEN 1 AND 48000 AND jsonb_typeof(plan_json::jsonb) IS NOT DISTINCT FROM 'object'
          AND jsonb_typeof(plan_json::jsonb->'targets') IS NOT DISTINCT FROM 'array' AND jsonb_array_length(plan_json::jsonb->'targets') BETWEEN 1 AND 100
          AND octet_length(binding_json) BETWEEN 1 AND 4096 AND jsonb_typeof(binding_json::jsonb) IS NOT DISTINCT FROM 'object'
          AND plan_digest ~ '^[0-9a-f]{64}$' AND binding_digest ~ '^[0-9a-f]{64}$'
          AND plan_digest=encode(sha256(convert_to(plan_json,'UTF8')),'hex')
          AND binding_digest=encode(sha256(convert_to(binding_json,'UTF8')),'hex')
        )""")
        cursor.execute("CREATE TRIGGER ai_write_fence BEFORE INSERT OR UPDATE OR DELETE ON ai_business_budget_plans FOR EACH ROW EXECUTE FUNCTION ai_runtime_write_fence()")
        cursor.execute("CREATE TRIGGER ai_immutable_evidence BEFORE UPDATE OR DELETE ON ai_business_budget_plans FOR EACH ROW EXECUTE FUNCTION ai_immutable_record_guard()")
        cursor.execute("""CREATE FUNCTION ai_business_budget_initial_guard() RETURNS trigger
        LANGUAGE plpgsql VOLATILE SET search_path=pg_catalog,public AS $$
        DECLARE parent public.ai_business_evidence_runs%ROWTYPE; header jsonb; b jsonb; raw json; k text;
          owner_count bigint; global_count bigint; owner_bytes bigint; global_bytes bigint; extra bigint; expected_request jsonb;
        BEGIN
          IF octet_length(NEW.plan_json)>48000 OR octet_length(NEW.binding_json)>4096 THEN RAISE EXCEPTION 'ai_budget_size_exceeded'; END IF;
          -- Same lock order as mutation(): revision first. VOLATILE's separate
          -- SQL statements get fresh READ COMMITTED snapshots after lock wait.
          IF current_setting('transaction_isolation')<>'read committed' THEN RAISE EXCEPTION 'ai_budget_isolation_invalid'; END IF;
          PERFORM domain FROM public.ai_data_revisions WHERE domain='ai-assistant' FOR UPDATE;
          IF NOT FOUND THEN RAISE EXCEPTION 'ai_budget_revision_missing'; END IF;
          SELECT * INTO parent FROM public.ai_business_evidence_runs WHERE id=NEW.evidence_id;
          IF NOT FOUND OR parent.status<>'sealed' OR parent.version<>NEW.evidence_version
             OR parent.owner_email<>NEW.owner_email OR parent.scope_json<>NEW.scope_json OR NEW.scope_json<>'null'
             OR NEW.owner_email<>lower(btrim(NEW.owner_email)) OR NEW.owner_email=''
          THEN RAISE EXCEPTION 'ai_budget_evidence_binding_invalid'; END IF;
          header:=public.ai_business_v2_header(parent.plan_json);
          IF header IS NULL THEN RAISE EXCEPTION 'ai_budget_requires_v2'; END IF;
          raw:=NEW.binding_json::json; b:=raw::jsonb;
          IF json_typeof(raw) IS DISTINCT FROM 'object'
             OR (SELECT count(*) FROM json_object_keys(raw))<>13
             OR (SELECT count(*) FROM jsonb_object_keys(b))<>13
             OR (b-ARRAY['schemaVersion','capacityProfile','calculatorVersion','reportId','evidenceRunId','evidenceVersion',
                 'evidencePlanDigest','catalogDigest','sealedDigest','analysisRequestDigest','planDigest','ownerEmail','scopeDigest'])<>'{}'::jsonb
          THEN RAISE EXCEPTION 'ai_budget_binding_shape'; END IF;
          FOREACH k IN ARRAY ARRAY['schemaVersion','capacityProfile','calculatorVersion','reportId','evidenceRunId',
              'evidencePlanDigest','catalogDigest','sealedDigest','planDigest','ownerEmail','scopeDigest'] LOOP
            IF json_typeof(raw->k) IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'ai_budget_binding_type'; END IF;
          END LOOP;
          expected_request:=CASE WHEN header ? 'analysisRequest'
            THEN to_jsonb(encode(sha256(convert_to((parent.plan_json::json->'analysisRequest')::text,'UTF8')),'hex'))
            ELSE 'null'::jsonb END;
          IF b->>'schemaVersion'<>'business-budget-binding-v1' OR b->>'capacityProfile'<>'budget-parameters-v1'
             OR b->>'calculatorVersion'<>'business-budget-calculator-v1' OR (b->>'reportId') !~ '^[A-Za-z0-9_-]{1,160}$'
             OR b->>'evidenceRunId'<>parent.id OR json_typeof(raw->'evidenceVersion') IS DISTINCT FROM 'number'
             OR raw->>'evidenceVersion' IS DISTINCT FROM NEW.evidence_version::text
             OR b->>'evidencePlanDigest'<>encode(sha256(convert_to(parent.plan_json,'UTF8')),'hex')
             OR b->>'catalogDigest' IS DISTINCT FROM header->>'catalogDigest'
             OR b->>'sealedDigest' IS DISTINCT FROM parent.state_json::jsonb->>'sealedDigest'
             OR (b->>'sealedDigest') !~ '^[0-9a-f]{64}$'
             OR b->'analysisRequestDigest' IS DISTINCT FROM expected_request
             OR b->>'planDigest'<>NEW.plan_digest OR b->>'ownerEmail'<>NEW.owner_email
             OR b->>'scopeDigest'<>encode(sha256(convert_to(NEW.scope_json,'UTF8')),'hex')
          THEN RAISE EXCEPTION 'ai_budget_binding_invalid'; END IF;
          SELECT count(*),coalesce(sum(octet_length(plan_json)+octet_length(binding_json)),0),
            count(*) FILTER(WHERE owner_email=NEW.owner_email),
            coalesce(sum(octet_length(plan_json)+octet_length(binding_json)) FILTER(WHERE owner_email=NEW.owner_email),0)
          INTO global_count,global_bytes,owner_count,owner_bytes FROM public.ai_business_budget_plans;
          extra:=octet_length(NEW.plan_json)+octet_length(NEW.binding_json);
          IF owner_count+1>200 OR global_count+1>2000 OR owner_bytes+extra>8388608 OR global_bytes+extra>67108864
          THEN RAISE EXCEPTION 'ai_budget_quota_exceeded'; END IF;
          RETURN NEW;
        END $$""")
        cursor.execute("CREATE TRIGGER ai_business_budget_initial BEFORE INSERT ON ai_business_budget_plans FOR EACH ROW EXECUTE FUNCTION ai_business_budget_initial_guard()")
        cursor.execute("""CREATE FUNCTION ai_business_budget_report_guard() RETURNS trigger
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
        END $$""")
        cursor.execute("CREATE TRIGGER ai_business_budget_report_binding BEFORE INSERT ON ai_report_runs FOR EACH ROW EXECUTE FUNCTION ai_business_budget_report_guard()")
        cursor.execute("""CREATE FUNCTION ai_business_budget_complete_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
        DECLARE target text; planned_report text; actual_report text; n bigint;
        BEGIN
          IF TG_TABLE_NAME='ai_business_budget_plans' THEN target:=NEW.id; ELSE target:=NEW.budget_plan_id; END IF;
          IF target IS NULL THEN RETURN NULL; END IF;
          SELECT binding_json::jsonb->>'reportId' INTO planned_report FROM public.ai_business_budget_plans WHERE id=target;
          SELECT count(*),min(id) INTO n,actual_report FROM public.ai_report_runs WHERE budget_plan_id=target;
          IF planned_report IS NULL OR n<>1 OR actual_report IS DISTINCT FROM planned_report
          THEN RAISE EXCEPTION 'ai_budget_orphan_or_report_mismatch'; END IF;
          RETURN NULL;
        END $$""")
        for table in ("ai_business_budget_plans", "ai_report_runs"):
            cursor.execute(f"CREATE CONSTRAINT TRIGGER ai_business_budget_complete AFTER INSERT ON {table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ai_business_budget_complete_guard()")


def uninstall(apps, schema_editor):
    budgets = apps.get_model("ai_assistant", "AiBusinessBudgetPlan")
    reports = apps.get_model("ai_assistant", "AiReportRun")
    if budgets.objects.exists() or reports.objects.filter(budget_plan_id__isnull=False).exists():
        raise RuntimeError("存在固定预算参数或报告引用，禁止回退预算结构")
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM ai_report_runs WHERE snapshot_json::jsonb->>'executionProfile'='business-agent-budget-reference-v1' LIMIT 1")
        if cursor.fetchone():
            raise RuntimeError("存在预算执行协议报告，禁止回退预算结构")
        for table, trigger in (("ai_business_budget_plans", "ai_business_budget_complete"),
            ("ai_report_runs", "ai_business_budget_complete"), ("ai_report_runs", "ai_business_budget_report_binding"),
            ("ai_business_budget_plans", "ai_business_budget_initial"), ("ai_business_budget_plans", "ai_immutable_evidence"),
            ("ai_business_budget_plans", "ai_write_fence")):
            cursor.execute(f"DROP TRIGGER {trigger} ON {table}")
        for function in ("ai_business_budget_complete_guard", "ai_business_budget_report_guard", "ai_business_budget_initial_guard"):
            cursor.execute(f"DROP FUNCTION {function}()")


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0020_business_volume_files")]
    operations = [
        migrations.CreateModel(name="AiBusinessBudgetPlan", fields=[
            ("id", models.CharField(primary_key=True, max_length=160, serialize=False)),
            ("owner_email", models.CharField(max_length=320)), ("scope_json", models.TextField(default="null")),
            ("evidence_version", models.PositiveIntegerField()), ("plan_json", models.TextField()),
            ("plan_digest", models.CharField(max_length=64)), ("binding_json", models.TextField()),
            ("binding_digest", models.CharField(max_length=64)), ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
            ("evidence", models.ForeignKey(to="ai_assistant.aibusinessevidencerun", on_delete=django.db.models.deletion.PROTECT)),
        ], options={"db_table": "ai_business_budget_plans", "indexes": [models.Index(fields=["owner_email"], name="ai_budget_owner_idx")]}),
        migrations.AddField(model_name="aireportrun", name="budget_plan",
            field=models.OneToOneField(to="ai_assistant.aibusinessbudgetplan", null=True, blank=True, on_delete=django.db.models.deletion.PROTECT)),
        # Reverse order executes the rejection gate before removing FK/table.
        migrations.RunPython(install, uninstall),
    ]
