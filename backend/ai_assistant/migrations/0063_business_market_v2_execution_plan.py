"""Independent SQL-owned five-Agent plan; no runnable workflow or job."""
from django.db import migrations, models


ROLE = "teruisi_ai_market_plan_attestor"
READER = "teruisi_ai_reader"
WRITER = "teruisi_ai_writer"
TABLE = "public.ai_business_market_v2_execution_plans"
EXPECTED_SIGNATURE = "public.ai_market_v2_execution_plan_expected(text,text)"
ATTEST_SIGNATURE = "public.ai_market_v2_attest_execution_plan(text,text)"
READ_SIGNATURE = "public.ai_market_v2_execution_plan_receipt(text,text,bigint)"

GUARD = r"""CREATE FUNCTION public.ai_market_v2_execution_plan_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'ai_market_v2_execution_plan_immutable'; END IF;
  IF session_user<>'teruisi_ai_market_plan_attestor'
     OR current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT relowner FROM pg_catalog.pg_class WHERE oid=TG_RELID))
  THEN RAISE EXCEPTION 'ai_market_v2_execution_plan_direct_write_denied'; END IF;
  RETURN NEW;
END $$"""

EXPECTED = r"""CREATE FUNCTION public.ai_market_v2_execution_plan_expected(
  selected_report text, plan_text text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE report public.ai_report_runs%ROWTYPE;
  flow public.ai_workflow_runs%ROWTYPE;
  proof public.ai_business_market_v2_context_proofs%ROWTYPE;
  actor public.access_control_users%ROWTYPE;
  plan jsonb; root jsonb; expected_root jsonb; context_claim jsonb;
  budget_policy jsonb; graph_digest text;
BEGIN
  IF selected_report IS NULL OR selected_report !~ '^[A-Za-z0-9_-]{1,160}$'
     OR plan_text IS NULL OR octet_length(plan_text) NOT BETWEEN 1 AND 16384
  THEN RAISE EXCEPTION 'ai_market_v2_execution_plan_input_invalid'; END IF;
  plan:=plan_text::jsonb;
  IF jsonb_typeof(plan) IS DISTINCT FROM 'object'
     OR plan_text IS DISTINCT FROM public.ai_v4_replay_canonical(plan)
     OR NOT plan ?& ARRAY['schemaVersion','executionProfile','executionRoot',
       'catalogSurface','toolCatalogDigest','graphDigest','proposedTools',
       'fiveAgentRoles','modelPolicy','budgetPolicy','activationStatus',
       'agentJobsAllowed','providerCallsAllowed','readReceiptAuthority',
       'numericCitationAllowed','humanReviewRequired',
       'marketAndOwnSalesAdditive','requiredActivationHooks']
     OR plan-ARRAY['schemaVersion','executionProfile','executionRoot',
       'catalogSurface','toolCatalogDigest','graphDigest','proposedTools',
       'fiveAgentRoles','modelPolicy','budgetPolicy','activationStatus',
       'agentJobsAllowed','providerCallsAllowed','readReceiptAuthority',
       'numericCitationAllowed','humanReviewRequired',
       'marketAndOwnSalesAdditive','requiredActivationHooks'] <> '{}'::jsonb
  THEN RAISE EXCEPTION 'ai_market_v2_execution_plan_shape_invalid'; END IF;
  SELECT * INTO report FROM public.ai_report_runs item
    WHERE item.id=selected_report FOR SHARE;
  IF report.id IS NULL
  THEN RAISE EXCEPTION 'ai_market_v2_execution_plan_report_missing'; END IF;
  SELECT * INTO flow FROM public.ai_workflow_runs item
    WHERE item.id=report.workflow_id FOR SHARE;
  SELECT * INTO proof FROM public.ai_business_market_v2_context_proofs item
    WHERE item.execution_report_id=report.id FOR SHARE;
  SELECT * INTO actor FROM public.access_control_users item
    WHERE item.email=report.owner_email FOR SHARE;
  root:=report.snapshot_json::jsonb->'executionRoot';
  context_claim:=public.ai_market_v2_context_claim(report.id);
  expected_root:=jsonb_build_object(
    'executionReportId',report.id,
    'admittedReportId',root->>'admittedReportId',
    'parkedReportId',root->>'parkedReportId',
    'sourceReportId',root->>'sourceReportId',
    'ownerEmail',report.owner_email,
    'selectorDigest',root->>'selectorDigest',
    'manifestDigest',root->>'manifestDigest',
    'marketContextDigest',root->>'marketContextDigest',
    'contextProofDigest',proof.proof_digest,
    'executionSnapshotDigest',
      encode(sha256(convert_to(report.snapshot_json,'UTF8')),'hex'),
    'withBudget',root->'withBudget');
  budget_policy:=jsonb_build_object('withBudget',root->'withBudget',
    'nativeBudgetSpendAllowed',false,
    'budgetDecisionRequiresHumanReview',true);
  graph_digest:=CASE WHEN root->'withBudget'='true'::jsonb
    THEN '905ae3d59bd4e9554b22c7373274d0569aa2c9e5da7182338351fbf20623f1c2'
    ELSE '6a0b655cec19328b1c1e02e78330320e04f699b5037868faf42e2ac8f6b0f40d' END;
  IF flow.id IS NULL OR proof.execution_report_id IS NULL OR actor.email IS NULL
     OR actor.role IS DISTINCT FROM 'admin'
     OR actor.status IS DISTINCT FROM 'active' OR actor.scope IS NOT NULL
     OR report.scope_json IS DISTINCT FROM 'null'
     OR report.owner_email IS DISTINCT FROM root->>'ownerEmail'
     OR report.snapshot_json::jsonb->>'executionProfile' IS DISTINCT FROM
       'business-agent-screening-promotion-market-execution-v2'
     OR report.snapshot_json::jsonb->>'schemaVersion' IS DISTINCT FROM
       'business-market-v2-execution-snapshot-v1'
     OR report.snapshot_json::jsonb->'runtimeActivated' IS DISTINCT FROM 'false'::jsonb
     OR flow.owner_email IS DISTINCT FROM report.owner_email
     OR flow.scope_json IS DISTINCT FROM 'null'
     OR flow.status IS DISTINCT FROM 'paused'
     OR flow.error_code IS DISTINCT FROM 'market_v2_execution_not_activated'
     OR flow.model_id IS DISTINCT FROM '' OR flow.model_version IS DISTINCT FROM 0
     OR flow.provider_round_count IS DISTINCT FROM 0
     OR flow.tool_call_count IS DISTINCT FROM 0
     OR flow.tool_policy_digest IS DISTINCT FROM
       '3db26b536d59118656f3a5f52638275139b19df24e2afcbf99b8c76cab8ee743'
     OR flow.graph_digest IS DISTINCT FROM graph_digest
     OR EXISTS(SELECT 1 FROM public.ai_agent_jobs job
       WHERE job.workflow_run_id=flow.id)
     OR EXISTS(SELECT 1 FROM public.ai_workflow_node_runs node
       WHERE node.run_id=flow.id)
     OR proof.proof_json::jsonb IS DISTINCT FROM context_claim
     OR proof.proof_digest IS DISTINCT FROM
       encode(sha256(convert_to(proof.proof_json,'UTF8')),'hex')
     OR plan->>'schemaVersion' IS DISTINCT FROM
       'business-market-v2-five-agent-execution-plan-v3'
     OR plan->>'executionProfile' IS DISTINCT FROM
       'business-agent-screening-promotion-market-execution-plan-v3'
     OR plan->'executionRoot' IS DISTINCT FROM expected_root
     OR plan->>'catalogSurface' IS DISTINCT FROM
       'business_agent_screening_promotion_market_v2'
     OR plan->>'toolCatalogDigest' IS DISTINCT FROM flow.tool_policy_digest
     OR plan->>'graphDigest' IS DISTINCT FROM graph_digest
     OR plan->'proposedTools' IS DISTINCT FROM
       '["get_business_market_v2_screening_package","get_business_market_v2_screening_analysis","get_business_market_v2_screening_budget","get_business_market_v2_keyword_sku","get_business_promotion_market_v2"]'::jsonb
     OR plan->'fiveAgentRoles' IS DISTINCT FROM
       '["commerce","promotion","market_b2b","independent_review","report"]'::jsonb
     OR plan->'modelPolicy' IS DISTINCT FROM
       '{"selection":"deferred","modelId":null,"modelVersion":null,"paidCallsAllowed":false,"maxPaidCostCents":0,"maxProviderRoundsNow":0,"maxProviderRoundsAfterApproval":20,"maxToolCallsNow":0,"maxToolCallsAfterApproval":40}'::jsonb
     OR plan->'budgetPolicy' IS DISTINCT FROM budget_policy
     OR plan->>'activationStatus' IS DISTINCT FROM 'deferred'
     OR plan->'agentJobsAllowed' IS DISTINCT FROM 'false'::jsonb
     OR plan->'providerCallsAllowed' IS DISTINCT FROM 'false'::jsonb
     OR plan->'readReceiptAuthority' IS DISTINCT FROM 'false'::jsonb
     OR plan->'numericCitationAllowed' IS DISTINCT FROM 'false'::jsonb
     OR plan->'humanReviewRequired' IS DISTINCT FROM 'true'::jsonb
     OR plan->'marketAndOwnSalesAdditive' IS DISTINCT FROM 'false'::jsonb
     OR plan->'requiredActivationHooks' IS DISTINCT FROM
       '["versioned_active_report_and_graph","approved_model_and_cost_policy","atomic_first_job_creation","provider_tool_result_ownership","independent_numeric_cell_proof"]'::jsonb
  THEN RAISE EXCEPTION 'ai_market_v2_execution_plan_root_or_policy_invalid'; END IF;
  RETURN plan;
END $$"""

ATTEST = r"""CREATE FUNCTION public.ai_market_v2_attest_execution_plan(
  selected_report text, plan_text text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE plan jsonb; plan_id text; plan_digest text;
  saved public.ai_business_market_v2_execution_plans%ROWTYPE;
BEGIN
  IF session_user<>'teruisi_ai_market_plan_attestor'
     OR current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT relowner FROM pg_catalog.pg_class WHERE
         oid='public.ai_business_market_v2_execution_plans'::regclass))
     OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles role WHERE
       role.rolname='teruisi_ai_market_plan_attestor' AND NOT role.rolcanlogin
       AND NOT role.rolinherit AND NOT role.rolsuper AND NOT role.rolcreatedb
       AND NOT role.rolcreaterole AND NOT role.rolreplication
       AND NOT role.rolbypassrls)
     OR EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members member WHERE
       member.roleid='teruisi_ai_market_plan_attestor'::regrole
       OR member.member='teruisi_ai_market_plan_attestor'::regrole)
  THEN RAISE EXCEPTION 'ai_market_v2_execution_plan_attestor_unavailable'; END IF;
  plan:=public.ai_market_v2_execution_plan_expected(selected_report,plan_text);
  plan_id:=encode(sha256(convert_to('market-plan-v3|'||selected_report,'UTF8')),'hex');
  plan_digest:=encode(sha256(convert_to(plan_text,'UTF8')),'hex');
  INSERT INTO public.ai_business_market_v2_execution_plans(
    id,execution_report_id,plan_json,plan_digest,created_at)
  VALUES(plan_id,selected_report,plan_text,plan_digest,clock_timestamp())
  ON CONFLICT(id) DO NOTHING;
  SELECT * INTO saved FROM public.ai_business_market_v2_execution_plans item
    WHERE item.id=plan_id;
  IF saved.id IS NULL OR saved.execution_report_id IS DISTINCT FROM selected_report
     OR saved.plan_json IS DISTINCT FROM plan_text
     OR saved.plan_digest IS DISTINCT FROM plan_digest
  THEN RAISE EXCEPTION 'ai_market_v2_execution_plan_conflicting_replay'; END IF;
  RETURN plan_id;
END $$"""

READ = r"""CREATE FUNCTION public.ai_market_v2_execution_plan_receipt(
  selected_report text, selected_actor text, selected_version bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE actor public.access_control_users%ROWTYPE;
  saved public.ai_business_market_v2_execution_plans%ROWTYPE;
  plan jsonb;
BEGIN
  IF session_user<>'teruisi_ai_reader'
     OR selected_actor IS NULL OR selected_actor<>lower(selected_actor)
     OR selected_version IS NULL OR selected_version<1
  THEN RAISE EXCEPTION 'ai_market_v2_execution_plan_reader_unavailable'; END IF;
  SELECT * INTO actor FROM public.access_control_users item
    WHERE item.email=selected_actor;
  SELECT * INTO saved FROM public.ai_business_market_v2_execution_plans item
    WHERE item.execution_report_id=selected_report;
  IF saved.id IS NULL
  THEN RAISE EXCEPTION 'ai_market_v2_execution_plan_missing'; END IF;
  plan:=public.ai_market_v2_execution_plan_expected(selected_report,saved.plan_json);
  IF actor.email IS NULL OR actor.version IS DISTINCT FROM selected_version
     OR actor.role IS DISTINCT FROM 'admin' OR actor.status IS DISTINCT FROM 'active'
     OR actor.scope IS NOT NULL
     OR plan->'executionRoot'->>'ownerEmail' IS DISTINCT FROM actor.email
     OR saved.plan_digest IS DISTINCT FROM
       encode(sha256(convert_to(saved.plan_json,'UTF8')),'hex')
  THEN RAISE EXCEPTION 'ai_market_v2_execution_plan_receipt_mismatch'; END IF;
  RETURN plan || jsonb_build_object('planId',saved.id,
    'planDigest',saved.plan_digest,'agentDispatchSupported',false);
END $$"""


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        from ai_assistant.business_market_v2_context_catalog import verify
        verify(cursor, RuntimeError)
        from ai_assistant.business_market_v2_read_catalog import verify as verify_read
        verify_read(cursor, RuntimeError)
        cursor.execute("SELECT to_regrole(%s)", [ROLE])
        if cursor.fetchone()[0] is None:
            cursor.execute("CREATE ROLE " + ROLE + " NOLOGIN NOINHERIT NOSUPERUSER "
                "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS")
        cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
            "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
            "WHERE rolname=%s", [ROLE])
        if cursor.fetchone() != (False,) * 7:
            raise RuntimeError("0063 plan attestor must remain independent NOLOGIN")
        cursor.execute("SELECT count(*) FROM pg_catalog.pg_auth_members WHERE "
            "roleid=%s::regrole OR member=%s::regrole", [ROLE, ROLE])
        if cursor.fetchone() != (0,):
            raise RuntimeError("0063 plan attestor membership drift")
        cursor.execute("""CREATE TABLE public.ai_business_market_v2_execution_plans (
          id varchar(64) PRIMARY KEY,
          execution_report_id varchar(160) NOT NULL UNIQUE REFERENCES
            public.ai_report_runs(id) ON DELETE RESTRICT,
          plan_json text NOT NULL,
          plan_digest varchar(64) NOT NULL,
          created_at timestamptz NOT NULL
        )""")
        cursor.execute("REVOKE ALL ON " + TABLE + " FROM PUBLIC")
        cursor.execute("GRANT USAGE ON SCHEMA public TO " + ROLE)
        for account in (READER, WRITER, ROLE):
            cursor.execute("SELECT to_regrole(%s)", [account])
            if cursor.fetchone()[0] is not None:
                cursor.execute("REVOKE ALL ON " + TABLE + " FROM " + account)
        for definition in (GUARD, EXPECTED, ATTEST, READ):
            cursor.execute(definition)
        for signature in ("public.ai_market_v2_execution_plan_guard()",
                EXPECTED_SIGNATURE, ATTEST_SIGNATURE, READ_SIGNATURE):
            cursor.execute("REVOKE ALL ON FUNCTION " + signature + " FROM PUBLIC")
            for account in (ROLE, READER, WRITER):
                cursor.execute("SELECT to_regrole(%s)", [account])
                if cursor.fetchone()[0] is not None:
                    cursor.execute("REVOKE ALL ON FUNCTION " + signature +
                        " FROM " + account)
        cursor.execute("GRANT EXECUTE ON FUNCTION " + ATTEST_SIGNATURE + " TO " + ROLE)
        cursor.execute("SELECT to_regrole(%s)", [READER])
        if cursor.fetchone()[0] is not None:
            cursor.execute("GRANT EXECUTE ON FUNCTION " + READ_SIGNATURE + " TO " + READER)
        cursor.execute("CREATE TRIGGER ai_market_v2_execution_plan_guard BEFORE "
            "INSERT OR UPDATE OR DELETE ON " + TABLE + " FOR EACH ROW "
            "EXECUTE FUNCTION public.ai_market_v2_execution_plan_guard()")
        cursor.execute("CREATE TRIGGER ai_market_v2_execution_plan_no_truncate "
            "BEFORE TRUNCATE ON " + TABLE + " FOR EACH STATEMENT "
            "EXECUTE FUNCTION public.ai_v4_seal_ticket_no_truncate()")


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT EXISTS(SELECT 1 FROM " + TABLE + ")")
        if cursor.fetchone()[0]:
            raise RuntimeError("0063 cannot remove persisted execution plans")
        for name in ("ai_market_v2_execution_plan_no_truncate",
                "ai_market_v2_execution_plan_guard"):
            cursor.execute("DROP TRIGGER " + name + " ON " + TABLE)
        for signature in (READ_SIGNATURE, ATTEST_SIGNATURE, EXPECTED_SIGNATURE,
                "public.ai_market_v2_execution_plan_guard()"):
            cursor.execute("DROP FUNCTION " + signature)
        cursor.execute("DROP TABLE " + TABLE)
        # Retain NOLOGIN role with no usable function grant.


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0062_business_market_v2_read_receipt_candidate")]
    operations = [
        migrations.SeparateDatabaseAndState(database_operations=[], state_operations=[
            migrations.CreateModel(name="AiBusinessMarketV2ExecutionPlan", fields=[
                ("id", models.CharField(max_length=64, primary_key=True,
                    serialize=False)),
                ("execution_report_id", models.CharField(max_length=160,
                    unique=True)),
                ("plan_json", models.TextField()),
                ("plan_digest", models.CharField(max_length=64)),
                ("created_at", models.DateTimeField()),
            ], options={"db_table": "ai_business_market_v2_execution_plans"}),
        ]),
        migrations.RunPython(install, uninstall),
    ]
