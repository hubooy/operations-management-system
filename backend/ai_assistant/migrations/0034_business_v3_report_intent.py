"""Inert paused v3 report intent, fenced away from runnable workflow tables."""
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


GUARD = """CREATE FUNCTION ai_business_v3_report_intent_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE parent public.ai_business_evidence_runs%ROWTYPE; snapshot jsonb; input_value jsonb; plan jsonb;
BEGIN
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'ai_business_v3_intent_immutable'; END IF;
  SELECT * INTO parent FROM public.ai_business_evidence_runs WHERE id=NEW.evidence_run_id FOR SHARE;
  IF NOT FOUND OR parent.status<>'sealed' OR parent.collection_status<>'manual'
     OR public.ai_business_v3_header(parent.plan_json) IS NULL
     OR parent.owner_email<>NEW.owner_email OR NEW.scope_json<>'null'
     OR NEW.evidence_version<>parent.version
     OR NEW.sealed_digest IS DISTINCT FROM parent.state_json::jsonb->>'sealedDigest'
     OR parent.state_json::jsonb->>'schemaVersion' IS DISTINCT FROM 'business-evidence-seal-v3'
     OR NEW.status<>'paused' OR NEW.pause_reason<>'v3_agents_not_registered'
     OR NEW.owner_email<>lower(NEW.owner_email)
     OR NEW.client_request_id !~ '^[A-Za-z0-9_-]{1,160}$'
     OR NEW.request_digest !~ '^[0-9a-f]{64}$'
     OR NEW.sealed_digest !~ '^[0-9a-f]{64}$'
     OR NEW.candidate_digest !~ '^[0-9a-f]{64}$'
     OR NOT EXISTS (SELECT 1 FROM public.access_control_users u WHERE u.email=NEW.owner_email
       AND u.role='admin' AND u.status='active' AND u.scope IS NULL AND u.version>=1)
  THEN RAISE EXCEPTION 'ai_business_v3_intent_parent_invalid'; END IF;
  IF octet_length(NEW.snapshot_json)>65536
     OR octet_length(NEW.workflow_input_json)>131072
     OR octet_length(NEW.workflow_plan_json)>131072
     OR NEW.snapshot_digest IS DISTINCT FROM encode(sha256(convert_to(NEW.snapshot_json,'UTF8')),'hex')
     OR NEW.workflow_input_digest IS DISTINCT FROM encode(sha256(convert_to(NEW.workflow_input_json,'UTF8')),'hex')
     OR NEW.workflow_plan_digest IS DISTINCT FROM encode(sha256(convert_to(NEW.workflow_plan_json,'UTF8')),'hex')
  THEN RAISE EXCEPTION 'ai_business_v3_intent_bytes_invalid'; END IF;
  snapshot:=NEW.snapshot_json::jsonb;
  input_value:=NEW.workflow_input_json::jsonb;
  plan:=NEW.workflow_plan_json::jsonb;
  IF snapshot->>'schemaVersion' IS DISTINCT FROM 'business-report-admission-candidate-v3'
     OR snapshot->>'executionProfile' IS DISTINCT FROM 'business-agent-reference-v3-candidate'
     OR snapshot->>'candidateDigest' IS DISTINCT FROM NEW.candidate_digest
     OR snapshot->'reference'->>'evidenceRunId' IS DISTINCT FROM parent.id
     OR (snapshot->'reference'->>'evidenceVersion')::integer IS DISTINCT FROM parent.version
     OR snapshot->'reference'->>'sealedDigest' IS DISTINCT FROM NEW.sealed_digest
     OR snapshot->'reference'->>'evidencePlanDigest' IS DISTINCT FROM
        encode(sha256(convert_to(parent.plan_json,'UTF8')),'hex')
     OR snapshot->'reference'->>'catalogDigest' IS DISTINCT FROM
        parent.plan_json::jsonb->>'catalogDigest'
     OR snapshot->'reference'->>'sourcesDigest' IS DISTINCT FROM
        parent.state_json::jsonb->>'sourcesDigest'
     OR snapshot->'modelDispatchSupported' IS DISTINCT FROM 'false'::jsonb
     OR snapshot->'reportGenerationSupported' IS DISTINCT FROM 'false'::jsonb
     OR snapshot->'fileGenerationSupported' IS DISTINCT FROM 'false'::jsonb
     OR snapshot->'policy'->'financeDailyProrationAllowed' IS DISTINCT FROM 'false'::jsonb
     OR snapshot->'policy'->'financeSkuProfitAttributionAllowed' IS DISTINCT FROM 'false'::jsonb
     OR snapshot->'policy'->'sumOverlappingErpB2bAdsAllowed' IS DISTINCT FROM 'false'::jsonb
     OR snapshot->'policy'->'crossDomainSnapshotAtomic' IS DISTINCT FROM 'false'::jsonb
     OR jsonb_typeof(snapshot->'dailyFacts') IS DISTINCT FROM 'array'
     OR jsonb_typeof(snapshot->'financeMonthlyContext') IS DISTINCT FROM 'array'
     OR jsonb_array_length(snapshot->'dailyFacts')<1
     OR jsonb_array_length(snapshot->'financeMonthlyContext')<1
  THEN RAISE EXCEPTION 'ai_business_v3_intent_snapshot_invalid'; END IF;
  IF input_value->>'schemaVersion' IS DISTINCT FROM 'business-v3-paused-workflow-input-v1'
     OR input_value->>'executionProfile' IS DISTINCT FROM 'business-agent-reference-v3-candidate'
     OR input_value->>'pauseReason' IS DISTINCT FROM 'v3_agents_not_registered'
     OR input_value->>'candidateDigest' IS DISTINCT FROM NEW.candidate_digest
     OR input_value->'candidate' IS DISTINCT FROM snapshot
     OR input_value->'modelDispatchSupported' IS DISTINCT FROM 'false'::jsonb
     OR input_value->'reportGenerationSupported' IS DISTINCT FROM 'false'::jsonb
     OR input_value->'fileGenerationSupported' IS DISTINCT FROM 'false'::jsonb
     OR plan->>'schemaVersion' IS DISTINCT FROM 'business-v3-paused-workflow-plan-v1'
     OR plan->>'executionProfile' IS DISTINCT FROM 'business-agent-reference-v3-candidate'
     OR plan->>'status' IS DISTINCT FROM 'paused'
     OR plan->>'pauseReason' IS DISTINCT FROM 'v3_agents_not_registered'
     OR plan->'agentDispatchRegistered' IS DISTINCT FROM 'false'::jsonb
     OR plan->'modelAdmissionPerformed' IS DISTINCT FROM 'false'::jsonb
     OR plan->'humanReviewRequired' IS DISTINCT FROM 'true'::jsonb
     OR jsonb_typeof(plan->'nodes') IS DISTINCT FROM 'array'
     OR jsonb_array_length(plan->'nodes')<>6
  THEN RAISE EXCEPTION 'ai_business_v3_intent_workflow_invalid'; END IF;
  RETURN NEW;
END $$"""


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql": return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("CREATE TRIGGER ai_write_fence BEFORE INSERT OR UPDATE OR DELETE ON ai_business_v3_report_intents FOR EACH ROW EXECUTE FUNCTION ai_runtime_write_fence()")
        cursor.execute("CREATE TRIGGER ai_immutable_intent BEFORE UPDATE OR DELETE ON ai_business_v3_report_intents FOR EACH ROW EXECUTE FUNCTION ai_immutable_record_guard()")
        cursor.execute(GUARD)
        cursor.execute("CREATE TRIGGER ai_business_v3_report_intent BEFORE INSERT ON ai_business_v3_report_intents FOR EACH ROW EXECUTE FUNCTION ai_business_v3_report_intent_guard()")


def uninstall(apps, schema_editor):
    if apps.get_model("ai_assistant", "AiBusinessV3ReportIntent").objects.exists():
        raise RuntimeError("存在v3暂停报告意图，不能逆迁移并丢弃固定封存引用")
    if schema_editor.connection.vendor != "postgresql": return
    with schema_editor.connection.cursor() as cursor:
        for trigger in ("ai_business_v3_report_intent", "ai_immutable_intent", "ai_write_fence"):
            cursor.execute(f"DROP TRIGGER {trigger} ON ai_business_v3_report_intents")
        cursor.execute("DROP FUNCTION ai_business_v3_report_intent_guard()")


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0033_business_v3_parent_seal")]
    operations = [
        migrations.CreateModel(name="AiBusinessV3ReportIntent", fields=[
            ("id", models.CharField(primary_key=True, serialize=False, max_length=160)),
            ("owner_email", models.CharField(max_length=320)),
            ("scope_json", models.TextField(default="null")),
            ("client_request_id", models.CharField(max_length=160)),
            ("request_digest", models.CharField(max_length=64)),
            ("evidence_run", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                to="ai_assistant.aibusinessevidencerun")),
            ("evidence_version", models.PositiveIntegerField()),
            ("sealed_digest", models.CharField(max_length=64)),
            ("candidate_digest", models.CharField(max_length=64)),
            ("snapshot_digest", models.CharField(max_length=64)),
            ("snapshot_json", models.TextField()),
            ("workflow_input_digest", models.CharField(max_length=64)),
            ("workflow_input_json", models.TextField()),
            ("workflow_plan_digest", models.CharField(max_length=64)),
            ("workflow_plan_json", models.TextField()),
            ("status", models.CharField(max_length=20, default="paused")),
            ("pause_reason", models.CharField(max_length=64, default="v3_agents_not_registered")),
            ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
        ], options={"db_table": "ai_business_v3_report_intents",
            "constraints": [models.UniqueConstraint(fields=("owner_email", "client_request_id"),
                name="ai_v3_intent_client_uq")],
            "indexes": [models.Index(fields=("owner_email", "-created_at"),
                name="ai_v3_intent_owner_idx")]}),
        migrations.RunPython(install, uninstall),
    ]
