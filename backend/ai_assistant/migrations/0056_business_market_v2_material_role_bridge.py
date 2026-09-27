"""Narrow 0053 material guards and reader metadata without table grants.

The protected 0045 sidecar stays closed to both application roles.  Only the
two already frozen 0053 INSERT guards gain their original database owner's
read authority; the reader receives an account/report-bound digest projection.
"""
from importlib import import_module

from django.db import migrations


previous = import_module(
    "ai_assistant.migrations.0053_business_market_v2_admitted_paused")
sidecar = import_module(
    "ai_assistant.migrations.0045_business_market_v2_material_attestation")
READER = "teruisi_ai_reader"
WRITER = "teruisi_ai_writer"
PROFILE = previous.PROFILE
SIGNATURE = ("public.ai_market_v2_admitted_material_metadata("
    "text,text,bigint,text,text,text)")


def _definer(sql):
    old = "RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$"
    new = "RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$"
    if sql.count(old) != 1:
        raise RuntimeError("0056 requires one exact 0053 trigger header")
    return sql.replace(old, new)


NEW_REPORT = _definer(previous.REPORT_GUARD)
NEW_WORKFLOW = _definer(previous.WORKFLOW_GUARD)

METADATA = r"""CREATE FUNCTION public.ai_market_v2_admitted_material_metadata(
  selected_report text, selected_actor text, selected_actor_version bigint,
  selected_parked text, selected_selector_digest text,
  selected_manifest_digest text)
RETURNS TABLE(source_report_id text,admission_digest text,
  selector_digest text,manifest_digest text,observation_coverage jsonb,
  summary_digest text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE actor public.access_control_users%ROWTYPE;
  report public.ai_report_runs%ROWTYPE;
  flow public.ai_workflow_runs%ROWTYPE;
  parked public.ai_report_runs%ROWTYPE;
  source_report public.ai_report_runs%ROWTYPE;
  source_flow public.ai_workflow_runs%ROWTYPE;
  material public.ai_business_market_v2_materials%ROWTYPE;
  claim jsonb; summary jsonb; manifest jsonb;
BEGIN
  IF session_user IS DISTINCT FROM 'teruisi_ai_reader'
     OR selected_report IS NULL OR selected_report !~ '^[A-Za-z0-9_-]{1,160}$'
     OR selected_parked IS NULL OR selected_parked !~ '^[A-Za-z0-9_-]{1,160}$'
     OR selected_actor IS NULL OR selected_actor<>lower(selected_actor)
     OR selected_actor_version IS NULL OR selected_actor_version<1
     OR selected_selector_digest IS NULL
     OR selected_selector_digest !~ '^[0-9a-f]{64}$'
     OR selected_manifest_digest IS NULL
     OR selected_manifest_digest !~ '^[0-9a-f]{64}$'
  THEN RAISE EXCEPTION 'ai_market_v2_material_claim_invalid'; END IF;
  SELECT * INTO actor FROM public.access_control_users item
    WHERE item.email=selected_actor;
  SELECT * INTO report FROM public.ai_report_runs item
    WHERE item.id=selected_report;
  SELECT * INTO flow FROM public.ai_workflow_runs item
    WHERE item.id=report.workflow_id;
  SELECT * INTO parked FROM public.ai_report_runs item
    WHERE item.id=selected_parked;
  SELECT * INTO material FROM public.ai_business_market_v2_materials item
    WHERE item.report_id=selected_parked;
  SELECT * INTO source_report FROM public.ai_report_runs item
    WHERE item.id=material.source_report_id;
  SELECT * INTO source_flow FROM public.ai_workflow_runs item
    WHERE item.id=source_report.workflow_id;
  claim:=report.snapshot_json::jsonb->'marketAdmission';
  summary:=material.summary_json::jsonb;
  manifest:=material.manifest_json::jsonb;
  IF actor.email IS NULL OR actor.role<>'admin'
     OR actor.status<>'active' OR actor.scope IS NOT NULL
     OR actor.version IS DISTINCT FROM selected_actor_version
     OR report.id IS NULL OR flow.id IS NULL OR parked.id IS NULL
     OR material.report_id IS NULL OR source_report.id IS NULL
     OR source_flow.id IS NULL
     OR report.owner_email IS DISTINCT FROM actor.email
     OR report.scope_json IS DISTINCT FROM 'null'
     OR report.snapshot_json::jsonb->>'executionProfile' IS DISTINCT FROM
       'business-agent-screening-promotion-market-admitted-v2'
     OR report.snapshot_json::jsonb->>'schemaVersion' IS DISTINCT FROM
       'business-market-v2-admitted-paused-snapshot-v1'
     OR report.snapshot_json::jsonb->>'reportId' IS DISTINCT FROM report.id
     OR report.snapshot_json::jsonb->'registered' IS DISTINCT FROM 'false'::jsonb
     OR report.snapshot_json::jsonb->'agentDispatchSupported' IS DISTINCT FROM
       'false'::jsonb
     OR flow.owner_email IS DISTINCT FROM actor.email
     OR flow.scope_json IS DISTINCT FROM 'null'
     OR flow.status IS DISTINCT FROM 'paused'
     OR flow.error_code IS DISTINCT FROM 'market_v2_tool_not_registered'
     OR flow.allowed_tools_json IS DISTINCT FROM '[]'
     OR flow.model_id IS DISTINCT FROM '' OR flow.model_version IS DISTINCT FROM 0
     OR flow.provider_round_count IS DISTINCT FROM 0
     OR flow.tool_call_count IS DISTINCT FROM 0
     OR EXISTS(SELECT 1 FROM public.ai_workflow_node_runs node
       WHERE node.run_id=flow.id)
     OR EXISTS(SELECT 1 FROM public.ai_agent_jobs job
       WHERE job.workflow_run_id=flow.id)
     OR parked.owner_email IS DISTINCT FROM actor.email
     OR parked.scope_json IS DISTINCT FROM 'null'
     OR parked.snapshot_json::jsonb->>'executionProfile' IS DISTINCT FROM
       'business-agent-screening-promotion-market-reference-v2'
     OR claim->>'parkedReportId' IS DISTINCT FROM parked.id
     OR claim->>'selectorDigest' IS DISTINCT FROM selected_selector_digest
     OR claim->>'manifestDigest' IS DISTINCT FROM selected_manifest_digest
     OR material.selector_digest IS DISTINCT FROM selected_selector_digest
     OR material.manifest_digest IS DISTINCT FROM selected_manifest_digest
     OR material.source_report_id IS DISTINCT FROM
       parked.snapshot_json::jsonb->'sourceRoot'->>'sourceReportId'
     OR material.selector_digest IS DISTINCT FROM encode(sha256(convert_to(
       (parked.snapshot_json::jsonb->'marketSelector')::text,'UTF8')),'hex')
     OR material.source_snapshot_digest IS DISTINCT FROM
       encode(sha256(convert_to(source_report.snapshot_json,'UTF8')),'hex')
     OR material.source_workflow_input_digest IS DISTINCT FROM
       encode(sha256(convert_to(source_flow.input_json,'UTF8')),'hex')
     OR octet_length(material.manifest_json)>131072
     OR octet_length(material.summary_json)>16384
     OR material.manifest_json_sha256 IS DISTINCT FROM
       encode(sha256(convert_to(material.manifest_json,'UTF8')),'hex')
     OR material.summary_digest IS DISTINCT FROM
       encode(sha256(convert_to(material.summary_json,'UTF8')),'hex')
     OR manifest->>'manifestDigest' IS DISTINCT FROM material.manifest_digest
     OR summary->>'marketManifestDigest' IS DISTINCT FROM
       material.manifest_digest
     OR summary->>'reportId' IS DISTINCT FROM source_report.id
     OR summary->>'admissionDigest' IS NULL
     OR summary->>'admissionDigest' !~ '^[0-9a-f]{64}$'
     OR summary->'selectedSealedSourcesFullyReplayed' IS DISTINCT FROM
       'true'::jsonb
     OR summary->'typedMarketRowsVerified' IS DISTINCT FROM 'true'::jsonb
     OR summary->'registeredAgentTool' IS DISTINCT FROM 'false'::jsonb
     OR manifest->'rankObservationCoverage' IS DISTINCT FROM
       '{"currentDatePresent":true,"baselineDatePresent":true,"bothDatesPresent":true}'::jsonb
     OR summary->'observationCoverage' IS DISTINCT FROM
       manifest->'rankObservationCoverage'
  THEN RAISE EXCEPTION 'ai_market_v2_material_claim_mismatch'; END IF;
  RETURN QUERY SELECT material.source_report_id::text,
    summary->>'admissionDigest',material.selector_digest::text,
    material.manifest_digest::text,manifest->'rankObservationCoverage',
    material.summary_digest::text;
END $$"""


def _catalog(cursor, expected_report, expected_workflow, *, definer):
    cursor.execute("SELECT pg_catalog.pg_get_userbyid(c.relowner) FROM "
        "pg_catalog.pg_class c WHERE c.oid=to_regclass(%s)", [sidecar.TABLE])
    owner = cursor.fetchone()
    if owner is None:
        raise RuntimeError("0056 requires the immutable 0045 material table")
    identities = {}
    for signature, expected in (("public.ai_market_v2_admitted_report_guard()",
            expected_report), ("public.ai_market_v2_admitted_workflow_guard()",
            expected_workflow)):
        cursor.execute("SELECT p.oid,p.prosrc,p.prosecdef,p.proconfig,p.proacl::text,"
            "pg_catalog.pg_get_userbyid(p.proowner),l.lanname FROM "
            "pg_catalog.pg_proc p JOIN pg_catalog.pg_language l ON l.oid=p.prolang "
            "WHERE p.oid=to_regprocedure(%s)", [signature])
        row = cursor.fetchone()
        if (row is None or row[1] != expected.split("$$")[1]
                or row[2] is not definer
                or {value.replace(" ", "") for value in (row[3] or [])}
                    != {"search_path=pg_catalog,public"}
                or row[5] != owner[0] or row[6] != "plpgsql"):
            raise RuntimeError("0056 admitted guard predecessor/owner drift")
        cursor.execute("SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p,"
            "pg_catalog.aclexplode(p.proacl) acl WHERE p.oid=%s "
            "AND acl.grantee=0 AND acl.privilege_type='EXECUTE')", [row[0]])
        if cursor.fetchone() != (False,):
            raise RuntimeError("0056 admitted guard PUBLIC EXECUTE reopened")
        identities[signature] = row[0], row[4]
    for role in (READER, WRITER):
        cursor.execute("SELECT to_regrole(%s)", [role])
        if cursor.fetchone()[0] is None:
            raise RuntimeError("0056 requires provisioned independent AI roles")
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            cursor.execute("SELECT has_table_privilege(%s,%s,%s)",
                [role, sidecar.TABLE, privilege])
            if cursor.fetchone() != (False,):
                raise RuntimeError("0056 cannot open 0045 sidecar table")
        for privilege in ("SELECT", "INSERT", "UPDATE", "REFERENCES"):
            cursor.execute("SELECT has_any_column_privilege(%s,%s,%s)",
                [role, sidecar.TABLE, privilege])
            if cursor.fetchone() != (False,):
                raise RuntimeError("0056 cannot open 0045 sidecar columns")
    cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
        "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
        "WHERE rolname=%s", [sidecar.ROLE])
    if cursor.fetchone() != (False,) * 7:
        raise RuntimeError("0056 requires unchanged NOLOGIN material attestor")
    return identities


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        before = _catalog(cursor, previous.REPORT_GUARD, previous.WORKFLOW_GUARD,
            definer=False)
        cursor.execute(NEW_REPORT.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
        cursor.execute(NEW_WORKFLOW.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
        if _catalog(cursor, NEW_REPORT, NEW_WORKFLOW, definer=True) != before:
            raise RuntimeError("0056 may not change guard OID or ACL")
        cursor.execute(METADATA)
        cursor.execute("REVOKE ALL ON FUNCTION " + SIGNATURE + " FROM PUBLIC")
        cursor.execute("REVOKE ALL ON FUNCTION " + SIGNATURE + " FROM " + WRITER)
        cursor.execute("GRANT EXECUTE ON FUNCTION " + SIGNATURE + " TO " + READER)


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT EXISTS(SELECT 1 FROM public.ai_report_runs WHERE "
            "snapshot_json::jsonb->>'executionProfile'=%s) OR EXISTS(SELECT 1 "
            "FROM public.ai_workflow_runs WHERE input_json::jsonb->>"
            "'executionProfile'=%s)", [PROFILE, PROFILE])
        if cursor.fetchone()[0]:
            raise RuntimeError("0056 cannot remove narrow material access with admitted roots")
        before = _catalog(cursor, NEW_REPORT, NEW_WORKFLOW, definer=True)
        cursor.execute("DROP FUNCTION " + SIGNATURE)
        cursor.execute(previous.REPORT_GUARD.replace("CREATE FUNCTION",
            "CREATE OR REPLACE FUNCTION", 1))
        cursor.execute(previous.WORKFLOW_GUARD.replace("CREATE FUNCTION",
            "CREATE OR REPLACE FUNCTION", 1))
        if _catalog(cursor, previous.REPORT_GUARD, previous.WORKFLOW_GUARD,
                definer=False) != before:
            raise RuntimeError("0056 reverse may not change guard OID or ACL")


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0055_business_v4_period_plan_candidate")]
    operations = [migrations.RunPython(install, uninstall)]
