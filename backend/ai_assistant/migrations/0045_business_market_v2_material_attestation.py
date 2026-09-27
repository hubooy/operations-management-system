"""Parked market-v2 material sidecar. The independent attestor stays NOLOGIN."""
from django.db import migrations, models


ROLE = "teruisi_ai_market_attestor"
TABLE = "public.ai_business_market_v2_materials"

GUARD = r"""CREATE FUNCTION public.ai_market_v2_material_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'ai_market_v2_material_immutable'; END IF;
  IF session_user<>'teruisi_ai_market_attestor'
     OR current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT c.relowner FROM pg_catalog.pg_class c WHERE c.oid=TG_RELID))
  THEN RAISE EXCEPTION 'ai_market_v2_material_direct_write_denied'; END IF;
  RETURN NEW;
END $$"""

ATTEST = r"""CREATE FUNCTION public.ai_market_v2_attest_material(
  selected_report text, manifest_text text, manifest_body_text text,
  summary_text text, summary_body_text text,
  first_spec_text text, second_spec_text text, third_spec_text text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE report public.ai_report_runs%ROWTYPE;
  flow public.ai_workflow_runs%ROWTYPE;
  source_report public.ai_report_runs%ROWTYPE;
  source_flow public.ai_workflow_runs%ROWTYPE;
  evidence public.ai_business_evidence_runs%ROWTYPE;
  current_source public.ai_business_evidence_sources%ROWTYPE;
  baseline_source public.ai_business_evidence_sources%ROWTYPE;
  snapshot jsonb; root jsonb; selector jsonb; manifest jsonb; summary jsonb;
  specs jsonb; existing public.ai_business_market_v2_materials%ROWTYPE;
  manifest_hash text; summary_hash text; spec_hashes text;
  total_rows bigint; total_pages bigint; total_bytes bigint;
BEGIN
  IF session_user<>'teruisi_ai_market_attestor'
     OR selected_report IS NULL OR length(selected_report) NOT BETWEEN 1 AND 160
     OR selected_report !~ '^[A-Za-z0-9_-]+$'
     OR octet_length(manifest_text) NOT BETWEEN 1 AND 131072
     OR octet_length(manifest_body_text) NOT BETWEEN 1 AND 131072
     OR octet_length(summary_text) NOT BETWEEN 1 AND 16384
     OR octet_length(summary_body_text) NOT BETWEEN 1 AND 16384
     OR octet_length(first_spec_text) NOT BETWEEN 1 AND 65536
     OR octet_length(second_spec_text) NOT BETWEEN 1 AND 65536
     OR octet_length(third_spec_text) NOT BETWEEN 1 AND 65536
  THEN RAISE EXCEPTION 'ai_market_v2_attestor_unavailable'; END IF;
  manifest:=manifest_text::jsonb; summary:=summary_text::jsonb;
  specs:=jsonb_build_array(first_spec_text::jsonb, second_spec_text::jsonb,
    third_spec_text::jsonb);
  IF manifest_body_text::jsonb IS DISTINCT FROM manifest-'manifestDigest'
     OR summary_body_text::jsonb IS DISTINCT FROM summary-'summaryDigest'
     OR manifest->>'manifestDigest' IS DISTINCT FROM
       encode(sha256(convert_to(manifest_body_text,'UTF8')),'hex')
     OR summary->>'summaryDigest' IS DISTINCT FROM
       encode(sha256(convert_to(summary_body_text,'UTF8')),'hex')
  THEN RAISE EXCEPTION 'ai_market_v2_attestation_digest_mismatch'; END IF;
  SELECT * INTO report FROM public.ai_report_runs item
    WHERE item.id=selected_report FOR SHARE;
  IF report.id IS NULL THEN RAISE EXCEPTION 'ai_market_v2_report_missing'; END IF;
  SELECT * INTO flow FROM public.ai_workflow_runs item
    WHERE item.id=report.workflow_id FOR SHARE;
  snapshot:=report.snapshot_json::jsonb;
  root:=snapshot->'sourceRoot'; selector:=snapshot->'marketSelector';
  SELECT * INTO source_report FROM public.ai_report_runs item
    WHERE item.id=root->>'sourceReportId' FOR SHARE;
  SELECT * INTO source_flow FROM public.ai_workflow_runs item
    WHERE item.id=source_report.workflow_id FOR SHARE;
  SELECT * INTO evidence FROM public.ai_business_evidence_runs item
    WHERE item.id=root->>'evidenceRunId' FOR SHARE;
  SELECT * INTO current_source FROM public.ai_business_evidence_sources item
    WHERE item.run_id=evidence.id
      AND item.source_key=selector->>'rankCurrentSourceKey' FOR SHARE;
  SELECT * INTO baseline_source FROM public.ai_business_evidence_sources item
    WHERE item.run_id=evidence.id
      AND item.source_key=selector->>'rankBaselineKey' FOR SHARE;
  IF flow.id IS NULL OR source_report.id IS NULL OR source_flow.id IS NULL
     OR evidence.id IS NULL
     OR snapshot->>'schemaVersion' IS DISTINCT FROM
       'business-market-v2-parked-snapshot-v1'
     OR snapshot->>'executionProfile' IS DISTINCT FROM
       'business-agent-screening-promotion-market-reference-v2'
     OR snapshot->>'reportId' IS DISTINCT FROM report.id
     OR snapshot->'marketMaterialReady' IS DISTINCT FROM 'false'::jsonb
     OR snapshot->'registered' IS DISTINCT FROM 'false'::jsonb
     OR flow.status<>'paused' OR flow.error_code<>'market_material_not_admitted'
     OR flow.allowed_tools_json<>'[]' OR flow.model_id<>''
     OR flow.provider_round_count<>0 OR flow.tool_call_count<>0
     OR EXISTS (SELECT 1 FROM public.ai_agent_jobs job
       WHERE job.workflow_run_id=flow.id)
     OR report.owner_email IS DISTINCT FROM source_report.owner_email
     OR report.owner_email IS DISTINCT FROM evidence.owner_email
     OR source_report.workflow_id IS DISTINCT FROM root->>'sourceWorkflowId'
     OR root->>'sourceSnapshotDigest' IS DISTINCT FROM
       encode(sha256(convert_to(source_report.snapshot_json,'UTF8')),'hex')
     OR root->>'sourceWorkflowInputDigest' IS DISTINCT FROM
       encode(sha256(convert_to(source_flow.input_json,'UTF8')),'hex')
     OR source_report.snapshot_json::jsonb->>'evidenceRunId' IS DISTINCT FROM evidence.id
     OR source_report.snapshot_json::jsonb->>'sealedDigest' IS DISTINCT FROM
       root->>'sealedDigest'
     OR evidence.version::text IS DISTINCT FROM root->>'evidenceVersion'
     OR evidence.status<>'sealed'
     OR evidence.state_json::jsonb->>'schemaVersion' IS DISTINCT FROM
       'business-evidence-seal-v2'
     OR evidence.state_json::jsonb->>'sealedDigest' IS DISTINCT FROM
       root->>'sealedDigest'
     OR current_source.id IS NULL OR baseline_source.id IS NULL
     OR current_source.domain<>'market' OR baseline_source.domain<>'market'
     OR NOT current_source.finished OR NOT baseline_source.finished
     OR current_source.page_count<1 OR baseline_source.page_count<1
     OR current_source.query_digest IS DISTINCT FROM
       encode(sha256(convert_to(current_source.query_json,'UTF8')),'hex')
     OR baseline_source.query_digest IS DISTINCT FROM
       encode(sha256(convert_to(baseline_source.query_json,'UTF8')),'hex')
     OR current_source.checkpoint_run_version>evidence.version
     OR baseline_source.checkpoint_run_version>evidence.version
     OR NOT EXISTS(SELECT 1 FROM public.access_control_users actor
       WHERE actor.email=report.owner_email AND actor.role='admin'
         AND actor.status='active' AND actor.scope IS NULL)
  THEN RAISE EXCEPTION 'ai_market_v2_attestation_root_changed'; END IF;
  IF manifest->>'schemaVersion' IS DISTINCT FROM 'business-market-composite-materials-v2'
     OR manifest->>'manifestDigest' IS NULL
     OR manifest->>'manifestDigest' !~ '^[0-9a-f]{64}$'
     OR manifest->'reportBinding'->>'reportId' IS DISTINCT FROM source_report.id
     OR manifest->>'priceBandSourceKey' IS DISTINCT FROM selector->>'priceBandSourceKey'
     OR manifest->>'rankCurrentSourceKey' IS DISTINCT FROM selector->>'rankCurrentSourceKey'
     OR manifest->>'rankBaselineKey' IS DISTINCT FROM selector->>'rankBaselineKey'
     OR manifest->'bands' IS DISTINCT FROM selector->'bands'
     OR manifest->'observationDates' IS DISTINCT FROM jsonb_build_object(
       'current', selector->>'currentObservationDate',
       'baseline',selector->>'baselineObservationDate')
     OR manifest->'algorithms' IS DISTINCT FROM snapshot->'marketAlgorithms'
     OR manifest->'sourceDescriptors'->'priceBand' IS DISTINCT FROM
       manifest->'sourceDescriptors'->'rankCurrent'
     OR manifest->'sourceDescriptors'->'rankCurrent'->>'key' IS DISTINCT FROM
       current_source.source_key
     OR manifest->'sourceDescriptors'->'rankBaseline'->>'key' IS DISTINCT FROM
       baseline_source.source_key
     OR manifest->'sourceDescriptors'->'rankCurrent'->'query' IS DISTINCT FROM
       current_source.query_json::jsonb
     OR manifest->'sourceDescriptors'->'rankBaseline'->'query' IS DISTINCT FROM
       baseline_source.query_json::jsonb
     OR manifest->'sourceDescriptors'->'rankCurrent'->>'queryDigest' IS DISTINCT FROM
       current_source.query_digest
     OR manifest->'sourceDescriptors'->'rankBaseline'->>'queryDigest' IS DISTINCT FROM
       baseline_source.query_digest
     OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(
       current_source.checkpoint_json::jsonb->'metadata'->'coverage'->'presentDates')
       AS observed(day) WHERE observed.day=selector->>'currentObservationDate')
     OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(
       baseline_source.checkpoint_json::jsonb->'metadata'->'coverage'->'presentDates')
       AS observed(day) WHERE observed.day=selector->>'baselineObservationDate')
     OR manifest->'tables' IS DISTINCT FROM specs
     OR specs->0->>'view' IS DISTINCT FROM 'price_band_summary'
     OR specs->1->>'view' IS DISTINCT FROM 'price_band_members'
     OR specs->2->>'view' IS DISTINCT FROM 'rank_entry_exit'
     OR specs->0->>'sourceTableDigest' IS DISTINCT FROM
       specs->1->>'sourceTableDigest'
     OR summary->>'schemaVersion' IS DISTINCT FROM
       'business-market-report-owning-material-candidate-v1'
     OR summary->>'reportId' IS DISTINCT FROM source_report.id
     OR summary->>'marketManifestDigest' IS DISTINCT FROM
       manifest->>'manifestDigest'
     OR summary->'selectedSealedSourcesFullyReplayed' IS DISTINCT FROM 'true'::jsonb
     OR summary->'typedMarketRowsVerified' IS DISTINCT FROM 'true'::jsonb
     OR summary->'authorityVerified' IS DISTINCT FROM 'false'::jsonb
     OR manifest->'authorityVerified' IS DISTINCT FROM 'false'::jsonb
     OR summary->'registeredAgentTool' IS DISTINCT FROM 'false'::jsonb
     OR summary->'registeredRenderer' IS DISTINCT FROM 'false'::jsonb
  THEN RAISE EXCEPTION 'ai_market_v2_attestation_material_mismatch'; END IF;
  IF manifest->'rankObservationCoverage' IS DISTINCT FROM
       '{"currentDatePresent":true,"baselineDatePresent":true,"bothDatesPresent":true}'::jsonb
     OR summary->'observationCoverage' IS DISTINCT FROM
       manifest->'rankObservationCoverage'
     OR summary->'tableViews' IS DISTINCT FROM
       '["price_band_summary","price_band_members","rank_entry_exit"]'::jsonb
     OR summary->'rowCount' IS DISTINCT FROM manifest->'rowCount'
     OR summary->'marketAndOwnSalesAdditive' IS DISTINCT FROM 'false'::jsonb
     OR summary->'priceSummaryAndMembersAdditive' IS DISTINCT FROM 'false'::jsonb
     OR manifest->'authority' IS DISTINCT FROM
       '{"selectedTopSampleReconciled":true,"wholeMarketCoverageVerified":false,"ownProductIdentityVerified":false,"priceSummaryAndMembersAdditive":false,"marketAndOwnSalesAdditive":false}'::jsonb
     OR EXISTS(SELECT 1 FROM jsonb_array_elements(specs) AS item(spec)
       WHERE item.spec->>'rowCount' IS NULL
         OR item.spec->>'rowCount' !~ '^[0-9]{1,15}$'
         OR item.spec->>'pageCount' IS NULL
         OR item.spec->>'pageCount' !~ '^[0-9]{1,15}$'
         OR item.spec->>'ndjsonBytes' IS NULL
         OR item.spec->>'ndjsonBytes' !~ '^[0-9]{1,15}$'
         OR item.spec->>'ndjsonSha256' IS NULL
         OR item.spec->>'ndjsonSha256' !~ '^[0-9a-f]{64}$'
         OR item.spec->>'sourceTableDigest' IS NULL
         OR item.spec->>'sourceTableDigest' !~ '^[0-9a-f]{64}$'
         OR item.spec->>'bindingDigest' IS NULL
         OR item.spec->>'bindingDigest' !~ '^[0-9a-f]{64}$')
  THEN RAISE EXCEPTION 'ai_market_v2_attestation_totals_invalid'; END IF;
  SELECT sum((item.spec->>'rowCount')::bigint),
      sum((item.spec->>'pageCount')::bigint),
      sum((item.spec->>'ndjsonBytes')::bigint)
    INTO total_rows,total_pages,total_bytes
    FROM jsonb_array_elements(specs) AS item(spec);
  IF manifest->>'rowCount' IS DISTINCT FROM total_rows::text
     OR manifest->>'pageCount' IS DISTINCT FROM total_pages::text
     OR manifest->>'ndjsonBytes' IS DISTINCT FROM total_bytes::text
     OR total_rows>200000 OR total_pages>20000 OR total_bytes>67108864
  THEN RAISE EXCEPTION 'ai_market_v2_attestation_totals_invalid'; END IF;
  manifest_hash:=encode(sha256(convert_to(manifest_text,'UTF8')),'hex');
  summary_hash:=encode(sha256(convert_to(summary_text,'UTF8')),'hex');
  spec_hashes:=jsonb_build_array(
    encode(sha256(convert_to(first_spec_text,'UTF8')),'hex'),
    encode(sha256(convert_to(second_spec_text,'UTF8')),'hex'),
    encode(sha256(convert_to(third_spec_text,'UTF8')),'hex'))::text;
  INSERT INTO public.ai_business_market_v2_materials (
    report_id,source_report_id,source_snapshot_digest,source_workflow_input_digest,
    selector_digest,algorithms_digest,manifest_digest,manifest_json_sha256,
    summary_digest,table_spec_digests_json,manifest_json,summary_json,created_at)
  VALUES (selected_report,source_report.id,root->>'sourceSnapshotDigest',
    root->>'sourceWorkflowInputDigest',
    encode(sha256(convert_to((selector)::text,'UTF8')),'hex'),
    encode(sha256(convert_to((snapshot->'marketAlgorithms')::text,'UTF8')),'hex'),
    manifest->>'manifestDigest',manifest_hash,summary_hash,spec_hashes,
    manifest_text,summary_text,clock_timestamp())
  ON CONFLICT (report_id) DO NOTHING;
  SELECT * INTO existing FROM public.ai_business_market_v2_materials item
    WHERE item.report_id=selected_report;
  IF existing.report_id IS NULL
     OR existing.source_report_id IS DISTINCT FROM source_report.id
     OR existing.source_snapshot_digest IS DISTINCT FROM root->>'sourceSnapshotDigest'
     OR existing.source_workflow_input_digest IS DISTINCT FROM
       root->>'sourceWorkflowInputDigest'
     OR existing.manifest_digest IS DISTINCT FROM manifest->>'manifestDigest'
     OR existing.manifest_json_sha256 IS DISTINCT FROM manifest_hash
     OR existing.summary_digest IS DISTINCT FROM summary_hash
     OR existing.table_spec_digests_json::jsonb IS DISTINCT FROM spec_hashes::jsonb
  THEN RAISE EXCEPTION 'ai_market_v2_material_conflicting_replay'; END IF;
END $$"""


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT to_regrole(%s)", [ROLE])
        if cursor.fetchone()[0] is None:
            cursor.execute("CREATE ROLE " + ROLE + " NOLOGIN NOINHERIT NOSUPERUSER "
                "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS")
        cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
            "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
            "WHERE rolname=%s", [ROLE])
        if cursor.fetchone() != (False,) * 7:
            raise RuntimeError("0045 attestor must remain independent NOLOGIN")
        cursor.execute("SELECT 1 FROM pg_catalog.pg_auth_members membership "
            "JOIN pg_catalog.pg_roles member ON member.oid=membership.member "
            "JOIN pg_catalog.pg_roles parent ON parent.oid=membership.roleid "
            "WHERE member.rolname=%s OR parent.rolname=%s", [ROLE, ROLE])
        if cursor.fetchone():
            raise RuntimeError("0045 attestor cannot inherit another role")
        cursor.execute("""CREATE TABLE public.ai_business_market_v2_materials (
          report_id varchar(160) PRIMARY KEY REFERENCES public.ai_report_runs(id)
            ON DELETE RESTRICT,
          source_report_id varchar(160) NOT NULL REFERENCES public.ai_report_runs(id)
            ON DELETE RESTRICT,
          source_snapshot_digest varchar(64) NOT NULL,
          source_workflow_input_digest varchar(64) NOT NULL,
          selector_digest varchar(64) NOT NULL,
          algorithms_digest varchar(64) NOT NULL,
          manifest_digest varchar(64) NOT NULL,
          manifest_json_sha256 varchar(64) NOT NULL,
          summary_digest varchar(64) NOT NULL,
          table_spec_digests_json text NOT NULL,
          manifest_json text NOT NULL,
          summary_json text NOT NULL,
          created_at timestamptz NOT NULL
        )""")
        cursor.execute("REVOKE ALL ON " + TABLE + " FROM PUBLIC")
        cursor.execute("GRANT USAGE ON SCHEMA public TO " + ROLE)
        for role in ("teruisi_ai_reader", "teruisi_ai_writer", ROLE):
            cursor.execute("SELECT to_regrole(%s)", [role])
            if cursor.fetchone()[0] is not None:
                cursor.execute("REVOKE ALL ON " + TABLE + " FROM " + role)
        cursor.execute(GUARD)
        cursor.execute(ATTEST)
        cursor.execute("REVOKE ALL ON FUNCTION public.ai_market_v2_material_guard() "
            "FROM PUBLIC")
        cursor.execute("REVOKE ALL ON FUNCTION public.ai_market_v2_attest_material("
            "text,text,text,text,text,text,text,text) FROM PUBLIC")
        cursor.execute("GRANT EXECUTE ON FUNCTION public.ai_market_v2_attest_material("
            "text,text,text,text,text,text,text,text) TO " + ROLE)
        cursor.execute("CREATE TRIGGER ai_market_v2_material_guard BEFORE INSERT OR "
            "UPDATE OR DELETE ON " + TABLE + " FOR EACH ROW EXECUTE FUNCTION "
            "public.ai_market_v2_material_guard()")
        cursor.execute("CREATE TRIGGER ai_market_v2_material_no_truncate BEFORE "
            "TRUNCATE ON " + TABLE + " FOR EACH STATEMENT EXECUTE FUNCTION "
            "public.ai_v4_seal_ticket_no_truncate()")


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT EXISTS(SELECT 1 FROM " + TABLE + ")")
        if cursor.fetchone()[0]:
            raise RuntimeError("0045 cannot discard attested market material")
        for trigger in ("ai_market_v2_material_no_truncate",
                "ai_market_v2_material_guard"):
            cursor.execute("DROP TRIGGER " + trigger + " ON " + TABLE)
        cursor.execute("DROP FUNCTION public.ai_market_v2_attest_material("
            "text,text,text,text,text,text,text,text)")
        cursor.execute("DROP FUNCTION public.ai_market_v2_material_guard()")
        cursor.execute("DROP TABLE " + TABLE)
        # The NOLOGIN role is retained for audit and has no operational grant.


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0044_business_market_v2_profile")]
    operations = [
        migrations.SeparateDatabaseAndState(database_operations=[], state_operations=[
            migrations.CreateModel(name="AiBusinessMarketV2Material", fields=[
                ("report_id", models.CharField(max_length=160, primary_key=True,
                    serialize=False)),
                ("source_report_id", models.CharField(max_length=160)),
                ("source_snapshot_digest", models.CharField(max_length=64)),
                ("source_workflow_input_digest", models.CharField(max_length=64)),
                ("selector_digest", models.CharField(max_length=64)),
                ("algorithms_digest", models.CharField(max_length=64)),
                ("manifest_digest", models.CharField(max_length=64)),
                ("manifest_json_sha256", models.CharField(max_length=64)),
                ("summary_digest", models.CharField(max_length=64)),
                ("table_spec_digests_json", models.TextField()),
                ("manifest_json", models.TextField()),
                ("summary_json", models.TextField()),
                ("created_at", models.DateTimeField()),
            ], options={"db_table": "ai_business_market_v2_materials"}),
        ]),
        migrations.RunPython(install, uninstall),
    ]
