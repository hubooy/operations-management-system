"""Dormant SQL-owned same-job read receipt, with numeric citations closed."""
from django.db import migrations, models


ROLE = "teruisi_ai_market_read_attestor"
READER = "teruisi_ai_reader"
WRITER = "teruisi_ai_writer"
TABLE = "public.ai_business_market_v2_read_receipts"
CLAIM_SIGNATURE = "public.ai_market_v2_read_claim(text)"
ATTEST_SIGNATURE = "public.ai_market_v2_attest_read(text)"
READ_SIGNATURE = "public.ai_market_v2_read_receipt(text,text,bigint)"
TOOLS = '["get_business_market_v2_screening_package","get_business_market_v2_screening_analysis","get_business_market_v2_screening_budget","get_business_market_v2_keyword_sku","get_business_promotion_market_v2"]'

GUARD = r"""CREATE FUNCTION public.ai_market_v2_read_receipt_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'ai_market_v2_read_receipt_immutable'; END IF;
  IF session_user<>'teruisi_ai_market_read_attestor'
     OR current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT relowner FROM pg_catalog.pg_class WHERE oid=TG_RELID))
  THEN RAISE EXCEPTION 'ai_market_v2_read_direct_write_denied'; END IF;
  RETURN NEW;
END $$"""

CLAIM = r"""CREATE FUNCTION public.ai_market_v2_read_claim(selected_tool text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE tool public.ai_agent_tool_dispatches%ROWTYPE;
  tool_result public.ai_agent_tool_results%ROWTYPE;
  job public.ai_agent_jobs%ROWTYPE;
  node public.ai_workflow_node_runs%ROWTYPE;
  provider public.ai_agent_provider_dispatches%ROWTYPE;
  provider_result public.ai_agent_provider_results%ROWTYPE;
  flow public.ai_workflow_runs%ROWTYPE;
  report public.ai_report_runs%ROWTYPE;
  proof public.ai_business_market_v2_context_proofs%ROWTYPE;
  root jsonb; context_claim jsonb; calls jsonb; args jsonb; result jsonb;
  mode text;
BEGIN
  IF selected_tool IS NULL OR selected_tool !~ '^[A-Za-z0-9_-]{1,160}$'
  THEN RAISE EXCEPTION 'ai_market_v2_read_dispatch_invalid'; END IF;
  SELECT * INTO tool FROM public.ai_agent_tool_dispatches item
    WHERE item.id=selected_tool FOR SHARE;
  IF tool.id IS NULL
  THEN RAISE EXCEPTION 'ai_market_v2_read_dispatch_missing'; END IF;
  SELECT * INTO tool_result FROM public.ai_agent_tool_results item
    WHERE item.tool_dispatch_id=tool.id FOR SHARE;
  SELECT * INTO job FROM public.ai_agent_jobs item
    WHERE item.id=tool.job_id FOR SHARE;
  SELECT * INTO provider FROM public.ai_agent_provider_dispatches item
    WHERE item.id=tool.provider_dispatch_id FOR SHARE;
  SELECT * INTO provider_result FROM public.ai_agent_provider_results item
    WHERE item.dispatch_id=provider.id FOR SHARE;
  SELECT * INTO flow FROM public.ai_workflow_runs item
    WHERE item.id=job.workflow_run_id FOR SHARE;
  SELECT * INTO report FROM public.ai_report_runs item
    WHERE item.workflow_id=flow.id AND item.snapshot_json::jsonb->>'executionProfile'=
      'business-agent-screening-promotion-market-execution-v2' FOR SHARE;
  SELECT * INTO node FROM public.ai_workflow_node_runs item
    WHERE item.run_id=flow.id AND item.node_key=job.workflow_node_key FOR SHARE;
  SELECT * INTO proof FROM public.ai_business_market_v2_context_proofs item
    WHERE item.execution_report_id=report.id FOR SHARE;
  root:=report.snapshot_json::jsonb->'executionRoot';
  IF tool_result.tool_dispatch_id IS NULL OR job.id IS NULL
     OR provider.id IS NULL OR provider_result.dispatch_id IS NULL
     OR flow.id IS NULL OR report.id IS NULL OR node.id IS NULL
     OR proof.execution_report_id IS NULL
     OR report.owner_email IS DISTINCT FROM job.owner_email
     OR report.scope_json IS DISTINCT FROM 'null'
     OR report.snapshot_json::jsonb->>'schemaVersion' IS DISTINCT FROM
       'business-market-v2-execution-snapshot-v1'
     OR report.snapshot_json::jsonb->'runtimeActivated' IS DISTINCT FROM 'false'::jsonb
     OR report.snapshot_json::jsonb->'agentDispatchSupported' IS DISTINCT FROM 'false'::jsonb
     OR flow.owner_email IS DISTINCT FROM report.owner_email
     OR flow.scope_json IS DISTINCT FROM 'null'
     OR flow.status IS DISTINCT FROM 'paused'
     OR flow.error_code IS DISTINCT FROM 'market_v2_execution_not_activated'
     OR flow.tool_policy_digest IS DISTINCT FROM
       '3db26b536d59118656f3a5f52638275139b19df24e2afcbf99b8c76cab8ee743'
     OR flow.allowed_tools_json IS DISTINCT FROM
       '["get_business_market_v2_screening_package","get_business_market_v2_screening_analysis","get_business_market_v2_screening_budget","get_business_market_v2_keyword_sku","get_business_promotion_market_v2"]'
     OR job.workflow_run_id IS DISTINCT FROM flow.id
     OR job.owner_email IS DISTINCT FROM report.owner_email
     OR job.scope_json IS DISTINCT FROM 'null'
     OR job.status IS NULL OR job.status NOT IN ('running','completed')
     OR job.phase IS NULL OR job.phase NOT IN ('executing','completed')
     OR job.model_id IS DISTINCT FROM provider.model_id
     OR job.model_version IS DISTINCT FROM provider.model_version
     OR job.tool_policy_digest IS DISTINCT FROM flow.tool_policy_digest
     OR job.allowed_tools_json IS DISTINCT FROM flow.allowed_tools_json
     OR node.agent_job_id IS DISTINCT FROM job.id
     OR node.node_type IS DISTINCT FROM 'agent'
     OR node.node_key IS DISTINCT FROM job.workflow_node_key
     OR node.status IS NULL OR node.status NOT IN ('running','completed')
     OR provider.job_id IS DISTINCT FROM job.id
     OR provider.owner_email IS DISTINCT FROM job.owner_email
     OR provider.actor_role IS DISTINCT FROM 'admin'
     OR provider.model_id IS DISTINCT FROM job.model_id
     OR provider.model_version IS DISTINCT FROM job.model_version
     OR provider.tool_policy_digest IS DISTINCT FROM job.tool_policy_digest
     OR provider.state IS DISTINCT FROM 'succeeded'
     OR provider_result.provider_request_id IS NULL
     OR provider_result.provider_request_id IS NOT DISTINCT FROM ''
     OR provider_result.response_digest IS DISTINCT FROM
       encode(sha256(convert_to(provider_result.response_json,'UTF8')),'hex')
     OR tool.job_id IS DISTINCT FROM job.id
     OR tool.provider_dispatch_id IS DISTINCT FROM provider.id
     OR tool.state IS DISTINCT FROM 'succeeded'
     OR tool.provider_call_id IS NULL
     OR length(tool.provider_call_id) NOT BETWEEN 1 AND 160
     OR tool.provider_call_id ~ '[[:cntrl:]]'
     OR tool.tool_name IS NULL
     OR tool.tool_name NOT IN (
       'get_business_market_v2_screening_package',
       'get_business_market_v2_screening_analysis',
       'get_business_market_v2_screening_budget',
       'get_business_market_v2_keyword_sku',
       'get_business_promotion_market_v2')
     OR tool.arguments_digest IS DISTINCT FROM
       encode(sha256(convert_to(tool.arguments_json,'UTF8')),'hex')
     OR tool_result.result_digest IS DISTINCT FROM
       encode(sha256(convert_to(tool_result.result_json,'UTF8')),'hex')
     OR octet_length(tool_result.result_json)>40000
     OR proof.proof_digest IS DISTINCT FROM
       encode(sha256(convert_to(proof.proof_json,'UTF8')),'hex')
  THEN RAISE EXCEPTION 'ai_market_v2_read_chain_invalid'; END IF;
  context_claim:=public.ai_market_v2_context_claim(report.id);
  IF proof.proof_json::jsonb IS DISTINCT FROM context_claim
     OR context_claim->>'contextDigest' IS DISTINCT FROM root->>'marketContextDigest'
  THEN RAISE EXCEPTION 'ai_market_v2_read_context_invalid'; END IF;
  args:=tool.arguments_json::jsonb;
  result:=tool_result.result_json::jsonb;
  calls:=COALESCE(provider_result.response_json::jsonb->'calls',
    provider_result.response_json::jsonb->'toolCalls');
  IF jsonb_typeof(args) IS DISTINCT FROM 'object'
     OR jsonb_typeof(result) IS DISTINCT FROM 'object'
     OR jsonb_typeof(calls) IS DISTINCT FROM 'array'
     OR result->>'toolName' IS DISTINCT FROM tool.tool_name
     OR result->>'auditStatus' IS DISTINCT FROM 'recorded'
     OR result->'ok' IS DISTINCT FROM 'true'::jsonb
     OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(calls) item
       WHERE item->>'id'=tool.provider_call_id
         AND item->>'name'=tool.tool_name
         AND item->'arguments'=args)
  THEN RAISE EXCEPTION 'ai_market_v2_read_provider_result_invalid'; END IF;
  IF tool.tool_name='get_business_promotion_market_v2' THEN
    mode:=args->>'mode';
    IF mode IS NULL OR mode NOT IN ('summary','page','row')
       OR args->>'reportId' IS DISTINCT FROM root->>'admittedReportId'
       OR args->>'marketContextDigest' IS DISTINCT FROM root->>'marketContextDigest'
       OR result->'data'->>'marketManifestDigest' IS DISTINCT FROM
          root->>'manifestDigest'
       OR result->'data'->>'role' IS DISTINCT FROM node.node_key
    THEN RAISE EXCEPTION 'ai_market_v2_read_market_result_invalid'; END IF;
  ELSE
    mode:='read';
    IF args->>'role' IS DISTINCT FROM node.node_key
       OR args->>'reportId' IS DISTINCT FROM root->>'admittedReportId'
    THEN RAISE EXCEPTION 'ai_market_v2_read_base_result_invalid'; END IF;
  END IF;
  RETURN jsonb_build_object('schemaVersion',
    'business-market-v2-same-job-read-receipt-v1',
    'executionReportId',report.id,
    'admittedReportId',root->>'admittedReportId',
    'ownerEmail',report.owner_email,'jobId',job.id,
    'providerDispatchId',provider.id,'providerCallId',tool.provider_call_id,
    'toolDispatchId',tool.id,'toolName',tool.tool_name,
    'role',node.node_key,'mode',mode,
    'contextProofDigest',proof.proof_digest,
    'toolResultDigest',tool_result.result_digest,
    'persistedRead',true,'numericCitationAllowed',false,
    'agentExecutionAuthorized',false);
END $$"""

ATTEST = r"""CREATE FUNCTION public.ai_market_v2_attest_read(selected_tool text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE claim jsonb; body text; digest_value text;
  saved public.ai_business_market_v2_read_receipts%ROWTYPE;
BEGIN
  IF session_user<>'teruisi_ai_market_read_attestor'
     OR current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT relowner FROM pg_catalog.pg_class WHERE
         oid='public.ai_business_market_v2_read_receipts'::regclass))
     OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles role WHERE
       role.rolname='teruisi_ai_market_read_attestor' AND NOT role.rolcanlogin
       AND NOT role.rolinherit AND NOT role.rolsuper AND NOT role.rolcreatedb
       AND NOT role.rolcreaterole AND NOT role.rolreplication
       AND NOT role.rolbypassrls)
     OR EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members member WHERE
       member.roleid='teruisi_ai_market_read_attestor'::regrole
       OR member.member='teruisi_ai_market_read_attestor'::regrole)
  THEN RAISE EXCEPTION 'ai_market_v2_read_attestor_unavailable'; END IF;
  claim:=public.ai_market_v2_read_claim(selected_tool);
  body:=claim::text;
  digest_value:=encode(sha256(convert_to(body,'UTF8')),'hex');
  INSERT INTO public.ai_business_market_v2_read_receipts(
    tool_dispatch_id,execution_report_id,job_id,provider_dispatch_id,
    receipt_json,receipt_digest,recorded_at)
  VALUES(selected_tool,claim->>'executionReportId',claim->>'jobId',
    claim->>'providerDispatchId',body,digest_value,clock_timestamp())
  ON CONFLICT(tool_dispatch_id) DO NOTHING;
  SELECT * INTO saved FROM public.ai_business_market_v2_read_receipts item
    WHERE item.tool_dispatch_id=selected_tool;
  IF saved.tool_dispatch_id IS NULL
     OR saved.execution_report_id IS DISTINCT FROM claim->>'executionReportId'
     OR saved.job_id IS DISTINCT FROM claim->>'jobId'
     OR saved.provider_dispatch_id IS DISTINCT FROM claim->>'providerDispatchId'
     OR saved.receipt_json IS DISTINCT FROM body
     OR saved.receipt_digest IS DISTINCT FROM digest_value
  THEN RAISE EXCEPTION 'ai_market_v2_read_conflicting_replay'; END IF;
  RETURN digest_value;
END $$"""

READ = r"""CREATE FUNCTION public.ai_market_v2_read_receipt(
  selected_tool text, selected_actor text, selected_version bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE actor public.access_control_users%ROWTYPE;
  claim jsonb; saved public.ai_business_market_v2_read_receipts%ROWTYPE;
BEGIN
  IF session_user<>'teruisi_ai_reader'
     OR selected_actor IS NULL OR selected_actor<>lower(selected_actor)
     OR selected_version IS NULL OR selected_version<1
  THEN RAISE EXCEPTION 'ai_market_v2_read_reader_unavailable'; END IF;
  SELECT * INTO actor FROM public.access_control_users item
    WHERE item.email=selected_actor;
  claim:=public.ai_market_v2_read_claim(selected_tool);
  SELECT * INTO saved FROM public.ai_business_market_v2_read_receipts item
    WHERE item.tool_dispatch_id=selected_tool;
  IF actor.email IS NULL OR actor.version IS DISTINCT FROM selected_version
     OR actor.role IS DISTINCT FROM 'admin' OR actor.status IS DISTINCT FROM 'active'
     OR actor.scope IS NOT NULL
     OR claim->>'ownerEmail' IS DISTINCT FROM actor.email
     OR saved.tool_dispatch_id IS NULL
     OR saved.receipt_json::jsonb IS DISTINCT FROM claim
     OR saved.receipt_digest IS DISTINCT FROM
       encode(sha256(convert_to(saved.receipt_json,'UTF8')),'hex')
  THEN RAISE EXCEPTION 'ai_market_v2_read_receipt_mismatch'; END IF;
  RETURN claim || jsonb_build_object('receiptDigest',saved.receipt_digest);
END $$"""


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT EXISTS(SELECT 1 FROM django_migrations WHERE "
            "app='ai_assistant' AND name='0061_business_market_v2_context_proof')")
        if cursor.fetchone() != (True,):
            raise RuntimeError("0062 requires applied SQL-owned context proof")
        from ai_assistant.business_market_v2_context_catalog import verify
        verify(cursor, RuntimeError)
        cursor.execute("SELECT to_regrole(%s)", [ROLE])
        if cursor.fetchone()[0] is None:
            cursor.execute("CREATE ROLE " + ROLE + " NOLOGIN NOINHERIT NOSUPERUSER "
                "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS")
        cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
            "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
            "WHERE rolname=%s", [ROLE])
        if cursor.fetchone() != (False,) * 7:
            raise RuntimeError("0062 read attestor must be independent NOLOGIN")
        cursor.execute("SELECT count(*) FROM pg_catalog.pg_auth_members WHERE "
            "roleid=%s::regrole OR member=%s::regrole", [ROLE, ROLE])
        if cursor.fetchone() != (0,):
            raise RuntimeError("0062 read attestor membership drift")
        cursor.execute("""CREATE TABLE public.ai_business_market_v2_read_receipts (
          tool_dispatch_id varchar(160) PRIMARY KEY REFERENCES
            public.ai_agent_tool_dispatches(id) ON DELETE RESTRICT,
          execution_report_id varchar(160) NOT NULL REFERENCES
            public.ai_report_runs(id) ON DELETE RESTRICT,
          job_id varchar(160) NOT NULL REFERENCES
            public.ai_agent_jobs(id) ON DELETE RESTRICT,
          provider_dispatch_id varchar(160) NOT NULL REFERENCES
            public.ai_agent_provider_dispatches(id) ON DELETE RESTRICT,
          receipt_json text NOT NULL,
          receipt_digest varchar(64) NOT NULL,
          recorded_at timestamptz NOT NULL
        )""")
        cursor.execute("REVOKE ALL ON " + TABLE + " FROM PUBLIC")
        cursor.execute("GRANT USAGE ON SCHEMA public TO " + ROLE)
        for account in (READER, WRITER, ROLE):
            cursor.execute("SELECT to_regrole(%s)", [account])
            if cursor.fetchone()[0] is not None:
                cursor.execute("REVOKE ALL ON " + TABLE + " FROM " + account)
        for definition in (GUARD, CLAIM, ATTEST, READ):
            cursor.execute(definition)
        for signature in ("public.ai_market_v2_read_receipt_guard()",
                CLAIM_SIGNATURE, ATTEST_SIGNATURE, READ_SIGNATURE):
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
        cursor.execute("CREATE TRIGGER ai_market_v2_read_receipt_guard BEFORE "
            "INSERT OR UPDATE OR DELETE ON " + TABLE + " FOR EACH ROW "
            "EXECUTE FUNCTION public.ai_market_v2_read_receipt_guard()")
        cursor.execute("CREATE TRIGGER ai_market_v2_read_receipt_no_truncate "
            "BEFORE TRUNCATE ON " + TABLE + " FOR EACH STATEMENT "
            "EXECUTE FUNCTION public.ai_v4_seal_ticket_no_truncate()")


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT EXISTS(SELECT 1 FROM " + TABLE + ")")
        if cursor.fetchone()[0]:
            raise RuntimeError("0062 cannot remove genuine read receipts")
        for name in ("ai_market_v2_read_receipt_no_truncate",
                "ai_market_v2_read_receipt_guard"):
            cursor.execute("DROP TRIGGER " + name + " ON " + TABLE)
        for signature in (READ_SIGNATURE, ATTEST_SIGNATURE, CLAIM_SIGNATURE,
                "public.ai_market_v2_read_receipt_guard()"):
            cursor.execute("DROP FUNCTION " + signature)
        cursor.execute("DROP TABLE " + TABLE)
        # Retain the empty NOLOGIN role for audit.


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0061_business_market_v2_context_proof")]
    operations = [
        migrations.SeparateDatabaseAndState(database_operations=[], state_operations=[
            migrations.CreateModel(name="AiBusinessMarketV2ReadReceipt", fields=[
                ("tool_dispatch_id", models.CharField(max_length=160,
                    primary_key=True, serialize=False)),
                ("execution_report_id", models.CharField(max_length=160)),
                ("job_id", models.CharField(max_length=160)),
                ("provider_dispatch_id", models.CharField(max_length=160)),
                ("receipt_json", models.TextField()),
                ("receipt_digest", models.CharField(max_length=64)),
                ("recorded_at", models.DateTimeField()),
            ], options={"db_table": "ai_business_market_v2_read_receipts"}),
        ]),
        migrations.RunPython(install, uninstall),
    ]
