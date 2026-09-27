"""SQL-derived immutable context proof for the still-paused market v2 plan."""
from importlib import import_module

from django.db import migrations, models


ROLE = "teruisi_ai_market_context_attestor"
READER = "teruisi_ai_reader"
WRITER = "teruisi_ai_writer"
TABLE = "public.ai_business_market_v2_context_proofs"
CLAIM_SIGNATURE = "public.ai_market_v2_context_claim(text)"
ATTEST_SIGNATURE = "public.ai_market_v2_attest_context(text)"
READ_SIGNATURE = "public.ai_market_v2_context_receipt(text,text,bigint)"
SCHEMA = "business-market-v2-context-proof-v1"

GUARD = r"""CREATE FUNCTION public.ai_market_v2_context_proof_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'ai_market_v2_context_proof_immutable'; END IF;
  IF session_user<>'teruisi_ai_market_context_attestor'
     OR current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT c.relowner FROM pg_catalog.pg_class c WHERE c.oid=TG_RELID))
  THEN RAISE EXCEPTION 'ai_market_v2_context_direct_write_denied'; END IF;
  RETURN NEW;
END $$"""

CLAIM = r"""CREATE FUNCTION public.ai_market_v2_context_claim(selected_report text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE execution_report public.ai_report_runs%ROWTYPE;
  execution_flow public.ai_workflow_runs%ROWTYPE;
  admitted_report public.ai_report_runs%ROWTYPE;
  admitted_flow public.ai_workflow_runs%ROWTYPE;
  parked_report public.ai_report_runs%ROWTYPE;
  parked_flow public.ai_workflow_runs%ROWTYPE;
  source_report public.ai_report_runs%ROWTYPE;
  source_flow public.ai_workflow_runs%ROWTYPE;
  material public.ai_business_market_v2_materials%ROWTYPE;
  evidence public.ai_business_evidence_runs%ROWTYPE;
  actor public.access_control_users%ROWTYPE;
  snapshot jsonb; root jsonb; admission jsonb; source_snapshot jsonb;
  run_id text; screening_id text; sealed_digest text; context_json text;
  context_digest text;
BEGIN
  IF selected_report IS NULL OR selected_report !~ '^[A-Za-z0-9_-]{1,160}$'
  THEN RAISE EXCEPTION 'ai_market_v2_context_report_invalid'; END IF;
  SELECT * INTO execution_report FROM public.ai_report_runs item
    WHERE item.id=selected_report FOR SHARE;
  IF execution_report.id IS NULL
  THEN RAISE EXCEPTION 'ai_market_v2_context_report_missing'; END IF;
  snapshot:=execution_report.snapshot_json::jsonb;
  root:=snapshot->'executionRoot';
  SELECT * INTO execution_flow FROM public.ai_workflow_runs item
    WHERE item.id=execution_report.workflow_id FOR SHARE;
  SELECT * INTO admitted_report FROM public.ai_report_runs item
    WHERE item.id=root->>'admittedReportId' FOR SHARE;
  SELECT * INTO admitted_flow FROM public.ai_workflow_runs item
    WHERE item.id=admitted_report.workflow_id FOR SHARE;
  admission:=admitted_report.snapshot_json::jsonb->'marketAdmission';
  SELECT * INTO parked_report FROM public.ai_report_runs item
    WHERE item.id=root->>'parkedReportId' FOR SHARE;
  SELECT * INTO parked_flow FROM public.ai_workflow_runs item
    WHERE item.id=parked_report.workflow_id FOR SHARE;
  SELECT * INTO material FROM public.ai_business_market_v2_materials item
    WHERE item.report_id=parked_report.id FOR SHARE;
  SELECT * INTO source_report FROM public.ai_report_runs item
    WHERE item.id=root->>'sourceReportId' FOR SHARE;
  SELECT * INTO source_flow FROM public.ai_workflow_runs item
    WHERE item.id=source_report.workflow_id FOR SHARE;
  source_snapshot:=source_report.snapshot_json::jsonb;
  run_id:=source_snapshot->>'evidenceRunId';
  screening_id:=source_snapshot->'screeningIntent'->>'id';
  sealed_digest:=source_snapshot->>'sealedDigest';
  SELECT * INTO evidence FROM public.ai_business_evidence_runs item
    WHERE item.id=run_id FOR SHARE;
  SELECT * INTO actor FROM public.access_control_users item
    WHERE item.email=execution_report.owner_email FOR SHARE;
  IF execution_flow.id IS NULL OR admitted_report.id IS NULL
     OR admitted_flow.id IS NULL OR parked_report.id IS NULL
     OR parked_flow.id IS NULL OR material.report_id IS NULL
     OR source_report.id IS NULL OR source_flow.id IS NULL
     OR evidence.id IS NULL OR actor.email IS NULL
     OR actor.role IS DISTINCT FROM 'admin'
     OR actor.status IS DISTINCT FROM 'active' OR actor.scope IS NOT NULL
     OR execution_report.owner_email IS DISTINCT FROM root->>'ownerEmail'
     OR execution_report.scope_json IS DISTINCT FROM 'null'
     OR execution_report.budget_plan_id IS NOT NULL
     OR snapshot->>'schemaVersion' IS DISTINCT FROM
       'business-market-v2-execution-snapshot-v1'
     OR snapshot->>'executionProfile' IS DISTINCT FROM
       'business-agent-screening-promotion-market-execution-v2'
     OR snapshot->>'reportId' IS DISTINCT FROM execution_report.id
     OR snapshot->'runtimeActivated' IS DISTINCT FROM 'false'::jsonb
     OR snapshot->'agentDispatchSupported' IS DISTINCT FROM 'false'::jsonb
     OR snapshot->'agentReadPersisted' IS DISTINCT FROM 'false'::jsonb
     OR snapshot->'humanReviewRequired' IS DISTINCT FROM 'true'::jsonb
     OR snapshot->>'toolCatalogDigest' IS DISTINCT FROM
       '3db26b536d59118656f3a5f52638275139b19df24e2afcbf99b8c76cab8ee743'
     OR execution_flow.owner_email IS DISTINCT FROM execution_report.owner_email
     OR execution_flow.scope_json IS DISTINCT FROM 'null'
     OR execution_flow.status IS DISTINCT FROM 'paused'
     OR execution_flow.error_code IS DISTINCT FROM 'market_v2_execution_not_activated'
     OR execution_flow.model_id IS DISTINCT FROM ''
     OR execution_flow.model_version IS DISTINCT FROM 0
     OR execution_flow.allowed_tools_json IS DISTINCT FROM
       '["get_business_market_v2_screening_package","get_business_market_v2_screening_analysis","get_business_market_v2_screening_budget","get_business_market_v2_keyword_sku","get_business_promotion_market_v2"]'
     OR execution_flow.tool_policy_digest IS DISTINCT FROM
       '3db26b536d59118656f3a5f52638275139b19df24e2afcbf99b8c76cab8ee743'
     OR execution_flow.provider_round_count IS DISTINCT FROM 0
     OR execution_flow.tool_call_count IS DISTINCT FROM 0
     OR execution_flow.input_json::jsonb->'executionRoot' IS DISTINCT FROM root
     OR EXISTS(SELECT 1 FROM public.ai_agent_jobs job
       WHERE job.workflow_run_id=execution_flow.id)
     OR EXISTS(SELECT 1 FROM public.ai_workflow_node_runs node
       WHERE node.run_id=execution_flow.id)
     OR admitted_report.owner_email IS DISTINCT FROM execution_report.owner_email
     OR admitted_report.scope_json IS DISTINCT FROM 'null'
     OR admitted_flow.owner_email IS DISTINCT FROM execution_report.owner_email
     OR admitted_flow.scope_json IS DISTINCT FROM 'null'
     OR admitted_report.snapshot_json::jsonb->>'executionProfile' IS DISTINCT FROM
       'business-agent-screening-promotion-market-admitted-v2'
     OR admitted_flow.status IS DISTINCT FROM 'paused'
     OR admitted_flow.error_code IS DISTINCT FROM 'market_v2_tool_not_registered'
     OR admitted_flow.model_id IS DISTINCT FROM ''
     OR admitted_flow.allowed_tools_json IS DISTINCT FROM '[]'
     OR admitted_report.snapshot_json::jsonb->'withBudget' IS DISTINCT FROM
       root->'withBudget'
     OR admission->>'parkedReportId' IS DISTINCT FROM parked_report.id
     OR admission->>'selectorDigest' IS DISTINCT FROM root->>'selectorDigest'
     OR admission->>'manifestDigest' IS DISTINCT FROM root->>'manifestDigest'
     OR parked_report.owner_email IS DISTINCT FROM execution_report.owner_email
     OR parked_report.scope_json IS DISTINCT FROM 'null'
     OR parked_flow.owner_email IS DISTINCT FROM execution_report.owner_email
     OR parked_flow.scope_json IS DISTINCT FROM 'null'
     OR parked_report.snapshot_json::jsonb->>'executionProfile' IS DISTINCT FROM
       'business-agent-screening-promotion-market-reference-v2'
     OR parked_flow.status IS DISTINCT FROM 'paused'
     OR parked_flow.error_code IS DISTINCT FROM 'market_material_not_admitted'
     OR parked_report.snapshot_json::jsonb->'withBudget' IS DISTINCT FROM root->'withBudget'
     OR parked_report.snapshot_json::jsonb->'sourceRoot'->>'sourceReportId'
        IS DISTINCT FROM source_report.id
     OR material.source_report_id IS DISTINCT FROM source_report.id
     OR material.selector_digest IS DISTINCT FROM root->>'selectorDigest'
     OR material.manifest_digest IS DISTINCT FROM root->>'manifestDigest'
     OR material.selector_digest IS DISTINCT FROM encode(sha256(convert_to(
       (parked_report.snapshot_json::jsonb->'marketSelector')::text,'UTF8')),'hex')
     OR material.source_snapshot_digest IS DISTINCT FROM
       encode(sha256(convert_to(source_report.snapshot_json,'UTF8')),'hex')
     OR material.source_workflow_input_digest IS DISTINCT FROM
       encode(sha256(convert_to(source_flow.input_json,'UTF8')),'hex')
     OR material.manifest_json_sha256 IS DISTINCT FROM
       encode(sha256(convert_to(material.manifest_json,'UTF8')),'hex')
     OR material.summary_digest IS DISTINCT FROM
       encode(sha256(convert_to(material.summary_json,'UTF8')),'hex')
     OR source_report.owner_email IS DISTINCT FROM execution_report.owner_email
     OR source_report.scope_json IS DISTINCT FROM 'null'
     OR source_flow.owner_email IS DISTINCT FROM execution_report.owner_email
     OR source_flow.scope_json IS DISTINCT FROM 'null'
     OR source_snapshot->>'executionProfile' IS DISTINCT FROM
       'business-agent-screening-promotion-reference-v1'
     OR source_snapshot->>'reportId' IS DISTINCT FROM source_report.id
     OR source_report.id !~ '^[A-Za-z0-9_-]{1,160}$'
     OR evidence.owner_email IS DISTINCT FROM execution_report.owner_email
     OR evidence.scope_json IS DISTINCT FROM 'null'
     OR evidence.status IS DISTINCT FROM 'sealed'
     OR evidence.state_json::jsonb->>'sealedDigest' IS DISTINCT FROM sealed_digest
     OR run_id IS NULL OR run_id !~ '^[A-Za-z0-9_-]{1,160}$'
     OR screening_id IS NULL OR screening_id !~ '^[A-Za-z0-9_-]{1,160}$'
     OR sealed_digest IS NULL OR sealed_digest !~ '^[0-9a-f]{64}$'
  THEN RAISE EXCEPTION 'ai_market_v2_context_root_mismatch'; END IF;
  -- All interpolated IDs are already limited to ASCII identifier characters.
  -- This is byte-for-byte Python canonical(...), with sorted fixed keys.
  context_json:='{"reportId":"'||source_report.id||'","runId":"'||run_id||
    '","screeningId":"'||screening_id||'","sealedDigest":"'||sealed_digest||'"}';
  context_digest:=encode(sha256(convert_to(context_json,'UTF8')),'hex');
  IF root->>'marketContextDigest' IS DISTINCT FROM context_digest
  THEN RAISE EXCEPTION 'ai_market_v2_context_digest_mismatch'; END IF;
  RETURN jsonb_build_object('schemaVersion','business-market-v2-context-proof-v1',
    'reportId',execution_report.id,'ownerEmail',execution_report.owner_email,
    'admittedReportId',admitted_report.id,'parkedReportId',parked_report.id,
    'sourceReportId',source_report.id,'selectorDigest',material.selector_digest,
    'manifestDigest',material.manifest_digest,'contextDigest',context_digest);
END $$"""

ATTEST = r"""CREATE FUNCTION public.ai_market_v2_attest_context(selected_report text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE claim jsonb; claim_text text; claim_digest text;
  saved public.ai_business_market_v2_context_proofs%ROWTYPE;
BEGIN
  IF session_user<>'teruisi_ai_market_context_attestor'
     OR current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT c.relowner FROM pg_catalog.pg_class c
        WHERE c.oid='public.ai_business_market_v2_context_proofs'::regclass))
     OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles r WHERE
       r.rolname='teruisi_ai_market_context_attestor' AND NOT r.rolcanlogin
       AND NOT r.rolinherit AND NOT r.rolsuper AND NOT r.rolcreatedb
       AND NOT r.rolcreaterole AND NOT r.rolreplication AND NOT r.rolbypassrls)
     OR EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members membership WHERE
       membership.roleid='teruisi_ai_market_context_attestor'::regrole
       OR membership.member='teruisi_ai_market_context_attestor'::regrole)
  THEN RAISE EXCEPTION 'ai_market_v2_context_attestor_unavailable'; END IF;
  claim:=public.ai_market_v2_context_claim(selected_report);
  claim_text:=claim::text;
  claim_digest:=encode(sha256(convert_to(claim_text,'UTF8')),'hex');
  INSERT INTO public.ai_business_market_v2_context_proofs(
    execution_report_id,proof_json,proof_digest,recorded_at)
  VALUES(selected_report,claim_text,claim_digest,clock_timestamp())
  ON CONFLICT(execution_report_id) DO NOTHING;
  SELECT * INTO saved FROM public.ai_business_market_v2_context_proofs item
    WHERE item.execution_report_id=selected_report;
  IF saved.execution_report_id IS NULL
     OR saved.proof_json IS DISTINCT FROM claim_text
     OR saved.proof_digest IS DISTINCT FROM claim_digest
  THEN RAISE EXCEPTION 'ai_market_v2_context_conflicting_replay'; END IF;
  RETURN claim_digest;
END $$"""

READ = r"""CREATE FUNCTION public.ai_market_v2_context_receipt(
  selected_report text, selected_actor text, selected_version bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE claim jsonb; saved public.ai_business_market_v2_context_proofs%ROWTYPE;
  actor public.access_control_users%ROWTYPE;
BEGIN
  IF session_user<>'teruisi_ai_reader'
     OR selected_actor IS NULL OR selected_actor<>lower(selected_actor)
     OR selected_version IS NULL OR selected_version<1
  THEN RAISE EXCEPTION 'ai_market_v2_context_reader_unavailable'; END IF;
  SELECT * INTO actor FROM public.access_control_users item
    WHERE item.email=selected_actor;
  claim:=public.ai_market_v2_context_claim(selected_report);
  SELECT * INTO saved FROM public.ai_business_market_v2_context_proofs item
    WHERE item.execution_report_id=selected_report;
  IF actor.email IS NULL OR actor.version IS DISTINCT FROM selected_version
     OR actor.role IS DISTINCT FROM 'admin' OR actor.status IS DISTINCT FROM 'active'
     OR actor.scope IS NOT NULL
     OR claim->>'ownerEmail' IS DISTINCT FROM actor.email
     OR saved.execution_report_id IS NULL
     OR saved.proof_json::jsonb IS DISTINCT FROM claim
     OR saved.proof_digest IS DISTINCT FROM
       encode(sha256(convert_to(saved.proof_json,'UTF8')),'hex')
  THEN RAISE EXCEPTION 'ai_market_v2_context_receipt_mismatch'; END IF;
  RETURN claim || jsonb_build_object('proofDigest',saved.proof_digest,
    'proofPersisted',true,'agentReadPersisted',false,'executionReady',false);
END $$"""


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        from ai_assistant.market_v2_admitted_catalog import verify as verify_admitted
        verify_admitted(cursor, RuntimeError)
        previous = import_module(
            "ai_assistant.migrations.0060_business_market_v2_execution_snapshot")
        material = import_module(
            "ai_assistant.migrations.0045_business_market_v2_material_attestation")
        cursor.execute("SELECT EXISTS(SELECT 1 FROM django_migrations WHERE "
            "app='ai_assistant' AND name='0060_business_market_v2_execution_snapshot')")
        if cursor.fetchone() != (True,):
            raise RuntimeError("0061 requires applied 0060 paused profile")
        for signature, definition in (
                ("public.ai_market_v2_parked_report_guard()",
                    previous.NEW_PARKED_REPORT),
                ("public.ai_market_v2_parked_workflow_guard()",
                    previous.NEW_PARKED_WORKFLOW),
                ("public.ai_market_v2_material_guard()", material.GUARD)):
            cursor.execute("SELECT prosrc FROM pg_catalog.pg_proc WHERE "
                "oid=to_regprocedure(%s)", [signature])
            row = cursor.fetchone()
            if row is None or row[0] != definition.split("$$", 2)[1]:
                raise RuntimeError("0061 protected predecessor guard drift")
        for signature, definition in (
                ("public.ai_market_v2_execution_report_guard()",
                    previous.REPORT_GUARD),
                ("public.ai_market_v2_execution_workflow_guard()",
                    previous.WORKFLOW_GUARD),
                ("public.ai_market_v2_execution_orphan_guard()",
                    previous.ORPHAN_GUARD),
                ("public.ai_market_v2_execution_child_guard()",
                    previous.CHILD_GUARD)):
            cursor.execute("SELECT prosrc,prosecdef,proconfig FROM "
                "pg_catalog.pg_proc WHERE oid=to_regprocedure(%s)", [signature])
            row = cursor.fetchone()
            should_define = signature in {
                "public.ai_market_v2_execution_report_guard()",
                "public.ai_market_v2_execution_workflow_guard()"}
            if (row is None or row[0] != definition.split("$$", 2)[1]
                    or row[1] is not should_define
                    or {item.replace(" ", "") for item in (row[2] or [])}
                        != {"search_path=pg_catalog,public"}):
                raise RuntimeError("0061 execution predecessor guard drift")
        cursor.execute("SELECT to_regrole(%s)", [ROLE])
        if cursor.fetchone()[0] is None:
            cursor.execute("CREATE ROLE " + ROLE + " NOLOGIN NOINHERIT NOSUPERUSER "
                "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS")
        cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
            "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
            "WHERE rolname=%s", [ROLE])
        if cursor.fetchone() != (False,) * 7:
            raise RuntimeError("0061 context attestor role must remain NOLOGIN")
        cursor.execute("SELECT 1 FROM pg_catalog.pg_auth_members membership WHERE "
            "membership.roleid=%s::regrole OR membership.member=%s::regrole",
            [ROLE, ROLE])
        if cursor.fetchone():
            raise RuntimeError("0061 context attestor must have no memberships")
        cursor.execute("""CREATE TABLE public.ai_business_market_v2_context_proofs (
          execution_report_id varchar(160) PRIMARY KEY REFERENCES public.ai_report_runs(id)
            ON DELETE RESTRICT,
          proof_json text NOT NULL,
          proof_digest varchar(64) NOT NULL,
          recorded_at timestamptz NOT NULL
        )""")
        cursor.execute("REVOKE ALL ON " + TABLE + " FROM PUBLIC")
        cursor.execute("GRANT USAGE ON SCHEMA public TO " + ROLE)
        for role in (READER, WRITER, ROLE):
            cursor.execute("SELECT to_regrole(%s)", [role])
            if cursor.fetchone()[0] is not None:
                cursor.execute("REVOKE ALL ON " + TABLE + " FROM " + role)
        for definition in (GUARD, CLAIM, ATTEST, READ):
            cursor.execute(definition)
        for signature in ("public.ai_market_v2_context_proof_guard()",
                CLAIM_SIGNATURE, ATTEST_SIGNATURE, READ_SIGNATURE):
            cursor.execute("REVOKE ALL ON FUNCTION " + signature + " FROM PUBLIC")
            for role in (ROLE, READER, WRITER):
                cursor.execute("SELECT to_regrole(%s)", [role])
                if cursor.fetchone()[0] is not None:
                    cursor.execute("REVOKE ALL ON FUNCTION " + signature +
                        " FROM " + role)
        cursor.execute("GRANT EXECUTE ON FUNCTION " + ATTEST_SIGNATURE + " TO " + ROLE)
        cursor.execute("SELECT to_regrole(%s)", [READER])
        if cursor.fetchone()[0] is not None:
            cursor.execute("GRANT EXECUTE ON FUNCTION " + READ_SIGNATURE + " TO " + READER)
        cursor.execute("CREATE TRIGGER ai_market_v2_context_proof_guard BEFORE "
            "INSERT OR UPDATE OR DELETE ON " + TABLE + " FOR EACH ROW "
            "EXECUTE FUNCTION public.ai_market_v2_context_proof_guard()")
        cursor.execute("CREATE TRIGGER ai_market_v2_context_proof_no_truncate "
            "BEFORE TRUNCATE ON " + TABLE + " FOR EACH STATEMENT "
            "EXECUTE FUNCTION public.ai_v4_seal_ticket_no_truncate()")


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT EXISTS(SELECT 1 FROM " + TABLE + ")")
        if cursor.fetchone()[0]:
            raise RuntimeError("0061 cannot remove persisted market context proofs")
        for name in ("ai_market_v2_context_proof_no_truncate",
                "ai_market_v2_context_proof_guard"):
            cursor.execute("DROP TRIGGER " + name + " ON " + TABLE)
        for signature in (READ_SIGNATURE, ATTEST_SIGNATURE, CLAIM_SIGNATURE,
                "public.ai_market_v2_context_proof_guard()"):
            cursor.execute("DROP FUNCTION " + signature)
        cursor.execute("DROP TABLE " + TABLE)
        # Retain the independent role, with no remaining executable grant.


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0060_business_market_v2_execution_snapshot")]
    operations = [
        migrations.SeparateDatabaseAndState(database_operations=[], state_operations=[
            migrations.CreateModel(name="AiBusinessMarketV2ContextProof", fields=[
                ("execution_report_id", models.CharField(max_length=160,
                    primary_key=True, serialize=False)),
                ("proof_json", models.TextField()),
                ("proof_digest", models.CharField(max_length=64)),
                ("recorded_at", models.DateTimeField()),
            ], options={"db_table": "ai_business_market_v2_context_proofs"}),
        ]),
        migrations.RunPython(install, uninstall),
    ]
