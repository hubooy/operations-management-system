"""Closed, append-only renderer-11 owning attestation; publication still denied.

The SQL rechecks current staged bytes and business roots. HTML decompression,
fresh owning regeneration, XLSX OPC and formula semantics are process claims
verified by the default-off protected preflight, never by this SQL alone.
"""
from django.db import migrations, models
from ai_assistant import business_promotion_budget_v11_stage_sql as stage_sql


ROLE = "teruisi_ai_budget_v11_attestor"
TABLE = "public.ai_business_promotion_budget_v11_attestations"
SIGNATURE = "public.ai_budget_v11_attest_staged(text,integer,text)"
REQUIREMENTS_SIGNATURE = "public.ai_budget_v11_attestation_requirements(text,integer,text,text)"

# A new function is required: 0066's exact stage function must remain writer-
# only, including its OID/body/ACL. Reuse its frozen checks under this distinct
# NOLOGIN session identity without weakening the original writer guard.
REQUIREMENTS = stage_sql.STAGE_REQUIREMENTS.replace(
    "public.ai_budget_v11_stage_requirements(",
    "public.ai_budget_v11_attestation_requirements(", 1).replace(
    "session_user<>'teruisi_ai_writer'",
    "session_user<>'teruisi_ai_budget_v11_attestor'", 1)


def _frozen(cursor):
    result = []
    for signature in (*stage_sql.FILE_SIGNATURES,
            stage_sql.STAGE_SIGNATURE):
        cursor.execute("SELECT p.oid,p.proacl::text,p.proowner,p.prosrc,"
            "p.prosecdef FROM pg_catalog.pg_proc p "
            "WHERE p.oid=to_regprocedure(%s)", [signature])
        value = cursor.fetchone()
        if value is None:
            raise RuntimeError("0067 requires exact 0066 file guard")
        result.append(value)
    return result


GUARD = r"""CREATE FUNCTION public.ai_budget_v11_attestation_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'ai_budget_v11_attestation_immutable'; END IF;
  IF session_user<>'teruisi_ai_budget_v11_attestor'
     OR current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT c.relowner FROM pg_catalog.pg_class c WHERE c.oid=TG_RELID))
  THEN RAISE EXCEPTION 'ai_budget_v11_attestation_direct_write_denied'; END IF;
  RETURN NEW;
END $$"""


ATTEST = r"""CREATE FUNCTION public.ai_budget_v11_attest_staged(
  selected_run text, selected_attempt integer, attestation_text text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE parent public.ai_business_file_runs%ROWTYPE;
  report public.ai_report_runs%ROWTYPE;
  flow public.ai_workflow_runs%ROWTYPE;
  snapshot jsonb; compact jsonb; full_manifest jsonb; budget jsonb; slim jsonb;
  att jsonb; descriptors jsonb; raw_manifest bytea; receipt_id text;
  existing public.ai_business_promotion_budget_v11_attestations%ROWTYPE;
  present boolean; plan_digest text; att_sha text;
BEGIN
  IF session_user<>'teruisi_ai_budget_v11_attestor'
     OR selected_run IS NULL OR selected_run !~ '^[A-Za-z0-9_-]{1,160}$'
     OR selected_attempt NOT BETWEEN 1 AND 5
     OR attestation_text IS NULL
     OR octet_length(attestation_text) NOT BETWEEN 1 AND 131072
     OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r
       WHERE r.rolname='teruisi_ai_budget_v11_attestor'
         AND NOT r.rolcanlogin AND NOT r.rolinherit
         AND NOT r.rolsuper AND NOT r.rolcreatedb
         AND NOT r.rolcreaterole AND NOT r.rolreplication
         AND NOT r.rolbypassrls)
     OR EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members membership
       WHERE membership.roleid='teruisi_ai_budget_v11_attestor'::regrole
          OR membership.member='teruisi_ai_budget_v11_attestor'::regrole)
     OR current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT c.relowner FROM pg_catalog.pg_class c
        WHERE c.oid='public.ai_business_promotion_budget_v11_attestations'::regclass))
  THEN RAISE EXCEPTION 'ai_budget_v11_attestor_unavailable'; END IF;
  att:=attestation_text::jsonb;
  IF jsonb_typeof(att) IS DISTINCT FROM 'object'
     OR NOT att ?& ARRAY['schemaVersion','verifierVersion','runId','attempt',
       'runVersion','bindingDigest','compactJsonSha256',
       'fullManifestSha256','fullManifestDigest','files',
       'approvedContentDigest','humanReviewDigest','budgetPresent',
       'budgetPlanDigest','budgetProofDigest','slimProofDigest',
       'fileByteVerificationDigest','htmlRowsDigest','xlsxOpcFormulaDigest',
       'owningVerificationDigest','reportSnapshotSha256',
       'workflowInputSha256','actorVersion']
     OR (att - ARRAY['schemaVersion','verifierVersion','runId','attempt',
       'runVersion','bindingDigest','compactJsonSha256',
       'fullManifestSha256','fullManifestDigest','files',
       'approvedContentDigest','humanReviewDigest','budgetPresent',
       'budgetPlanDigest','budgetProofDigest','slimProofDigest',
       'fileByteVerificationDigest','htmlRowsDigest','xlsxOpcFormulaDigest',
       'owningVerificationDigest','reportSnapshotSha256',
       'workflowInputSha256','actorVersion'])<>'{}'::jsonb
     OR att->>'schemaVersion' IS DISTINCT FROM
       'business-promotion-budget-v11-staged-attestation-v1'
     OR att->>'verifierVersion' IS DISTINCT FROM
       'business-promotion-budget-v11-owning-verifier-v1'
     OR att->>'runId' IS DISTINCT FROM selected_run
     OR att->>'attempt' IS DISTINCT FROM selected_attempt::text
     OR jsonb_typeof(att->'attempt') IS DISTINCT FROM 'number'
     OR jsonb_typeof(att->'runVersion') IS DISTINCT FROM 'number'
     OR jsonb_typeof(att->'actorVersion') IS DISTINCT FROM 'number'
     OR jsonb_typeof(att->'budgetPresent') IS DISTINCT FROM 'boolean'
     OR jsonb_typeof(att->'files') IS DISTINCT FROM 'object'
     OR EXISTS (SELECT 1 FROM unnest(ARRAY[
       att->>'bindingDigest',att->>'compactJsonSha256',
       att->>'fullManifestSha256',att->>'fullManifestDigest',
       att->>'approvedContentDigest',att->>'humanReviewDigest',
       att->>'budgetProofDigest',att->>'slimProofDigest',
       att->>'fileByteVerificationDigest',att->>'htmlRowsDigest',
       att->>'xlsxOpcFormulaDigest',att->>'owningVerificationDigest',
       att->>'reportSnapshotSha256',att->>'workflowInputSha256']) AS item(value)
       WHERE item.value IS NULL OR item.value !~ '^[0-9a-f]{64}$')
  THEN RAISE EXCEPTION 'ai_budget_v11_attestation_shape_invalid'; END IF;
  SELECT * INTO parent FROM public.ai_business_file_runs item
    WHERE item.id=selected_run FOR UPDATE;
  IF parent.id IS NULL OR parent.renderer_version<>11 OR parent.draft
     OR parent.status<>'paused' OR parent.error_code<>'renderer_unpublished'
     OR parent.progress_json::jsonb IS DISTINCT FROM jsonb_build_object(
         'stage','staged_unpublished','attempt',selected_attempt)
     OR parent.attempt<>selected_attempt OR parent.stored_bytes<1
     OR parent.manifest_json='{}'
     OR parent.version::text IS DISTINCT FROM att->>'runVersion'
     OR parent.binding_digest IS DISTINCT FROM att->>'bindingDigest'
     OR encode(sha256(convert_to(parent.manifest_json,'UTF8')),'hex')
        IS DISTINCT FROM att->>'compactJsonSha256'
  THEN RAISE EXCEPTION 'ai_budget_v11_staged_root_invalid'; END IF;
  PERFORM public.ai_business_promotion_budget_parent_requirements(
    parent.report_id,parent.owner_email,parent.scope_json);
  SELECT * INTO report FROM public.ai_report_runs item
    WHERE item.id=parent.report_id FOR SHARE;
  SELECT * INTO flow FROM public.ai_workflow_runs item
    WHERE item.id=report.workflow_id FOR SHARE;
  snapshot:=report.snapshot_json::jsonb;
  IF flow.status<>'completed' OR flow.completed_at IS NULL
     OR NOT EXISTS (SELECT 1 FROM public.access_control_users actor
       WHERE actor.email=parent.owner_email AND actor.role='admin'
         AND actor.status='active' AND actor.scope IS NULL
         AND actor.version::text=att->>'actorVersion')
     OR encode(sha256(convert_to(report.snapshot_json,'UTF8')),'hex')
        IS DISTINCT FROM att->>'reportSnapshotSha256'
     OR encode(sha256(convert_to(flow.input_json,'UTF8')),'hex')
        IS DISTINCT FROM att->>'workflowInputSha256'
  THEN RAISE EXCEPTION 'ai_budget_v11_review_not_final'; END IF;
  PERFORM public.ai_budget_v11_attestation_requirements(parent.id,parent.attempt,
    parent.binding_digest,parent.manifest_json);
  compact:=parent.manifest_json::jsonb;
  descriptors:=jsonb_build_object('files',compact->'files',
    'manifestFile',compact->'manifestFile');
  IF compact->>'rendererVersion' IS DISTINCT FROM '11'
     OR att->'files' IS DISTINCT FROM descriptors
  THEN RAISE EXCEPTION 'ai_budget_v11_file_descriptors_invalid'; END IF;
  SELECT string_agg(chunk.content,''::bytea ORDER BY chunk.sequence)
    INTO raw_manifest FROM public.ai_business_volume_chunks chunk
    WHERE chunk.run_id=parent.id AND chunk.attempt=parent.attempt
      AND chunk.volume_index=0 AND chunk.format='json';
  IF raw_manifest IS NULL OR octet_length(raw_manifest)>16777216
     OR encode(sha256(raw_manifest),'hex') IS DISTINCT FROM
       att->>'fullManifestSha256'
     OR encode(sha256(raw_manifest),'hex') IS DISTINCT FROM
       compact->'manifestFile'->>'sha256'
  THEN RAISE EXCEPTION 'ai_budget_v11_full_json_digest_mismatch'; END IF;
  full_manifest:=convert_from(raw_manifest,'UTF8')::jsonb;
  budget:=full_manifest->'promotionBudgetProof';
  slim:=full_manifest->'promotionSlimProof';
  present:=report.budget_plan_id IS NOT NULL;
  plan_digest:=snapshot#>>'{budgetRef,planDigest}';
  IF full_manifest->>'manifestDigest' IS DISTINCT FROM
       att->>'fullManifestDigest'
     OR full_manifest->>'reportId' IS DISTINCT FROM parent.report_id
     OR full_manifest->>'evidenceDigest' IS DISTINCT FROM
       snapshot->>'sealedDigest'
     OR full_manifest->'promotionFileProof'->>'contentDtoDigest'
        IS DISTINCT FROM att->>'approvedContentDigest'
     OR full_manifest->'promotionFileProof'->>'humanReviewDigest'
        IS DISTINCT FROM att->>'humanReviewDigest'
     OR budget->>'approvedContentDigest' IS DISTINCT FROM
       att->>'approvedContentDigest'
     OR budget->>'humanReviewDigest' IS DISTINCT FROM
       att->>'humanReviewDigest'
     OR budget->>'proofDigest' IS DISTINCT FROM
       att->>'budgetProofDigest'
     OR slim->>'proofDigest' IS DISTINCT FROM att->>'slimProofDigest'
     OR att->'budgetPresent' IS DISTINCT FROM to_jsonb(present)
     OR (present AND (att->>'budgetPlanDigest' IS DISTINCT FROM plan_digest
       OR full_manifest->>'budgetPlanDigest' IS DISTINCT FROM plan_digest
       OR budget->>'budgetPlanDigest' IS DISTINCT FROM plan_digest
       OR full_manifest->'volumes'->0->>'nativeBudgetSheets' IS DISTINCT FROM '3'
       OR full_manifest->'volumes'->0->>'offlineBudgetEnabled' IS DISTINCT FROM 'true'))
     OR (NOT present AND (att->'budgetPlanDigest' IS DISTINCT FROM 'null'::jsonb
       OR full_manifest ? 'budgetPlanDigest'
       OR budget->>'status' IS DISTINCT FROM 'missing_fixed_budget'
       OR full_manifest->'volumes'->0->>'nativeBudgetSheets' IS DISTINCT FROM '0'
       OR full_manifest->'volumes'->0->>'offlineBudgetEnabled' IS DISTINCT FROM 'false'))
  THEN RAISE EXCEPTION 'ai_budget_v11_proof_root_mismatch'; END IF;
  receipt_id:=encode(sha256(convert_to(parent.id||':'||parent.attempt::text,
    'UTF8')),'hex');
  att_sha:=encode(sha256(convert_to(attestation_text,'UTF8')),'hex');
  INSERT INTO public.ai_business_promotion_budget_v11_attestations (
    id,run_id,attempt,report_id,owner_email,binding_digest,
    compact_json_sha256,full_manifest_sha256,full_manifest_digest,
    file_descriptors_json,file_descriptors_digest,
    approved_content_digest,human_review_digest,budget_present,
    budget_plan_digest,budget_proof_digest,slim_proof_digest,
    file_byte_verification_digest,html_rows_digest,xlsx_opc_formula_digest,
    owning_verification_digest,attestation_sha256,attestation_json,recorded_at)
  VALUES (receipt_id,parent.id,parent.attempt,parent.report_id,parent.owner_email,
    parent.binding_digest,att->>'compactJsonSha256',att->>'fullManifestSha256',
    att->>'fullManifestDigest',descriptors::text,
    encode(sha256(convert_to(descriptors::text,'UTF8')),'hex'),
    att->>'approvedContentDigest',att->>'humanReviewDigest',present,
    plan_digest,att->>'budgetProofDigest',att->>'slimProofDigest',
    att->>'fileByteVerificationDigest',att->>'htmlRowsDigest',
    att->>'xlsxOpcFormulaDigest',att->>'owningVerificationDigest',
    att_sha,attestation_text,clock_timestamp())
  ON CONFLICT (run_id,attempt) DO NOTHING;
  SELECT * INTO existing FROM public.ai_business_promotion_budget_v11_attestations item
    WHERE item.run_id=parent.id AND item.attempt=parent.attempt;
  IF existing.id IS NULL OR existing.id IS DISTINCT FROM receipt_id
     OR existing.attestation_sha256 IS DISTINCT FROM att_sha
     OR existing.attestation_json IS DISTINCT FROM attestation_text
     OR existing.file_descriptors_json::jsonb IS DISTINCT FROM descriptors
  THEN RAISE EXCEPTION 'ai_budget_v11_attestation_conflicting_replay'; END IF;
  RETURN receipt_id;
END $$"""


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        frozen = _frozen(cursor)
        cursor.execute("SELECT to_regrole(%s)", [ROLE])
        if cursor.fetchone()[0] is None:
            cursor.execute("CREATE ROLE " + ROLE + " NOLOGIN NOINHERIT NOSUPERUSER "
                "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS")
        cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
            "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
            "WHERE rolname=%s", [ROLE])
        if cursor.fetchone() != (False,) * 7:
            raise RuntimeError("0067 attestor role must remain independent NOLOGIN")
        cursor.execute("SELECT count(*) FROM pg_catalog.pg_auth_members WHERE "
            "roleid=%s::regrole OR member=%s::regrole", [ROLE, ROLE])
        if cursor.fetchone() != (0,):
            raise RuntimeError("0067 attestor cannot have membership")
        cursor.execute("""CREATE TABLE public.ai_business_promotion_budget_v11_attestations (
          id varchar(64) PRIMARY KEY,
          run_id varchar(160) NOT NULL REFERENCES public.ai_business_file_runs(id)
            ON DELETE RESTRICT,
          attempt integer NOT NULL CHECK (attempt BETWEEN 1 AND 5),
          report_id varchar(160) NOT NULL REFERENCES public.ai_report_runs(id)
            ON DELETE RESTRICT,
          owner_email varchar(320) NOT NULL,
          binding_digest varchar(64) NOT NULL,
          compact_json_sha256 varchar(64) NOT NULL,
          full_manifest_sha256 varchar(64) NOT NULL,
          full_manifest_digest varchar(64) NOT NULL,
          file_descriptors_json text NOT NULL,
          file_descriptors_digest varchar(64) NOT NULL,
          approved_content_digest varchar(64) NOT NULL,
          human_review_digest varchar(64) NOT NULL,
          budget_present boolean NOT NULL,
          budget_plan_digest varchar(64),
          budget_proof_digest varchar(64) NOT NULL,
          slim_proof_digest varchar(64) NOT NULL,
          file_byte_verification_digest varchar(64) NOT NULL,
          html_rows_digest varchar(64) NOT NULL,
          xlsx_opc_formula_digest varchar(64) NOT NULL,
          owning_verification_digest varchar(64) NOT NULL,
          attestation_sha256 varchar(64) NOT NULL UNIQUE,
          attestation_json text NOT NULL,
          recorded_at timestamptz NOT NULL,
          CONSTRAINT ai_budget_v11_attest_attempt_uq UNIQUE (run_id,attempt)
        )""")
        cursor.execute("REVOKE ALL ON " + TABLE + " FROM PUBLIC")
        cursor.execute("GRANT USAGE ON SCHEMA public TO " + ROLE)
        for role in ("teruisi_ai_reader", "teruisi_ai_writer", ROLE):
            cursor.execute("SELECT to_regrole(%s)", [role])
            if cursor.fetchone()[0] is not None:
                cursor.execute("REVOKE ALL ON " + TABLE + " FROM " + role)
        cursor.execute(GUARD)
        cursor.execute(REQUIREMENTS)
        cursor.execute(ATTEST)
        cursor.execute("REVOKE ALL ON FUNCTION public.ai_budget_v11_attestation_guard() "
            "FROM PUBLIC")
        cursor.execute("REVOKE ALL ON FUNCTION " + SIGNATURE + " FROM PUBLIC")
        cursor.execute("REVOKE ALL ON FUNCTION " + REQUIREMENTS_SIGNATURE +
            " FROM PUBLIC")
        cursor.execute("GRANT EXECUTE ON FUNCTION " + SIGNATURE + " TO " + ROLE)
        cursor.execute("GRANT EXECUTE ON FUNCTION " + REQUIREMENTS_SIGNATURE +
            " TO " + ROLE)
        cursor.execute("CREATE TRIGGER ai_budget_v11_attestation_guard BEFORE "
            "INSERT OR UPDATE OR DELETE ON " + TABLE + " FOR EACH ROW "
            "EXECUTE FUNCTION public.ai_budget_v11_attestation_guard()")
        cursor.execute("CREATE TRIGGER ai_budget_v11_attestation_no_truncate "
            "BEFORE TRUNCATE ON " + TABLE + " FOR EACH STATEMENT EXECUTE FUNCTION "
            "public.ai_v4_seal_ticket_no_truncate()")
        if _frozen(cursor) != frozen:
            raise RuntimeError("0067 changed 0066 or older file guard")


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT EXISTS(SELECT 1 FROM " + TABLE + ")")
        if cursor.fetchone()[0]:
            raise RuntimeError("0067 cannot discard budget v11 attestations")
        cursor.execute("DROP TRIGGER ai_budget_v11_attestation_no_truncate ON " + TABLE)
        cursor.execute("DROP TRIGGER ai_budget_v11_attestation_guard ON " + TABLE)
        cursor.execute("DROP FUNCTION " + SIGNATURE)
        cursor.execute("DROP FUNCTION " + REQUIREMENTS_SIGNATURE)
        cursor.execute("DROP FUNCTION public.ai_budget_v11_attestation_guard()")
        cursor.execute("DROP TABLE " + TABLE)
        # Retain NOLOGIN role without grants for audit.


def verify_catalog(cursor):
    """Read-only pin for the closed sidecar and its two narrow SQL functions."""
    cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
        "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
        "WHERE rolname=%s", [ROLE])
    if cursor.fetchone() != (False,) * 7:
        raise RuntimeError("budget v11 attestor role drift")
    cursor.execute("SELECT count(*) FROM pg_catalog.pg_auth_members WHERE "
        "roleid=%s::regrole OR member=%s::regrole", [ROLE, ROLE])
    if cursor.fetchone() != (0,):
        raise RuntimeError("budget v11 attestor membership drift")
    cursor.execute("SELECT c.relkind,pg_catalog.pg_get_userbyid(c.relowner) "
        "FROM pg_catalog.pg_class c WHERE c.oid=%s::regclass", [TABLE])
    relation = cursor.fetchone()
    if relation is None or relation[0] != "r" or relation[1] in {
            ROLE, "teruisi_ai_reader", "teruisi_ai_writer"}:
        raise RuntimeError("budget v11 attestation ownership drift")
    cursor.execute("SELECT c.conname,c.contype,"
        "pg_catalog.pg_get_constraintdef(c.oid) FROM pg_catalog.pg_constraint c "
        "WHERE c.conrelid=%s::regclass", [TABLE])
    constraints = cursor.fetchall()
    if (len(constraints) != 6 or
            sorted(item[1] for item in constraints) != ["c", "f", "f", "p", "u", "u"] or
            not any(name == "ai_budget_v11_attest_attempt_uq" and
                definition == "UNIQUE (run_id, attempt)" for name, _, definition
                in constraints)):
        raise RuntimeError("budget v11 attestation constraints drift")
    for signature, definition, definer, attestor_allowed in (
            ("public.ai_budget_v11_attestation_guard()", GUARD, False, False),
            (REQUIREMENTS_SIGNATURE, REQUIREMENTS, True, True),
            (SIGNATURE, ATTEST, True, True)):
        cursor.execute("SELECT p.prosrc,p.prosecdef,p.proconfig,l.lanname,"
            "pg_catalog.pg_get_userbyid(p.proowner) FROM pg_catalog.pg_proc p "
            "JOIN pg_catalog.pg_language l ON l.oid=p.prolang "
            "WHERE p.oid=to_regprocedure(%s)", [signature])
        row = cursor.fetchone()
        if (row is None or row[0] != definition.split("$$", 2)[1]
                or row[1] is not definer or row[3] != "plpgsql"
                or {item.replace(" ", "") for item in (row[2] or [])}
                    != {"search_path=pg_catalog,public"}
                or row[4] in {ROLE, "teruisi_ai_writer", "teruisi_ai_reader"}):
            raise RuntimeError("budget v11 attestation function drift")
        for role, expected in ((ROLE, attestor_allowed),
                ("teruisi_ai_reader", False), ("teruisi_ai_writer", False)):
            cursor.execute("SELECT pg_catalog.has_function_privilege(%s,%s,'EXECUTE')",
                [role, signature])
            if cursor.fetchone() != (expected,):
                raise RuntimeError("budget v11 attestation function ACL drift")
    for role in (ROLE, "teruisi_ai_reader", "teruisi_ai_writer"):
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE",
                          "REFERENCES", "TRIGGER"):
            cursor.execute("SELECT pg_catalog.has_table_privilege(%s,%s,%s)",
                [role, TABLE, privilege])
            if cursor.fetchone() != (False,):
                raise RuntimeError("budget v11 attestation table ACL drift")
        for privilege in ("SELECT", "INSERT", "UPDATE", "REFERENCES"):
            cursor.execute("SELECT pg_catalog.has_any_column_privilege(%s,%s,%s)",
                [role, TABLE, privilege])
            if cursor.fetchone() != (False,):
                raise RuntimeError("budget v11 attestation column ACL drift")
    cursor.execute("SELECT t.tgname,t.tgenabled,t.tgdeferrable,"
        "t.tginitdeferred,t.tgfoid FROM pg_catalog.pg_trigger t "
        "WHERE t.tgrelid=%s::regclass AND NOT t.tgisinternal",
        [TABLE])
    rows = cursor.fetchall()
    expected = {"ai_budget_v11_attestation_guard":
        "public.ai_budget_v11_attestation_guard()",
        "ai_budget_v11_attestation_no_truncate":
        "public.ai_v4_seal_ticket_no_truncate()"}
    if len(rows) != 2 or {row[0] for row in rows} != set(expected):
        raise RuntimeError("budget v11 attestation trigger drift")
    for name, enabled, deferred, initially_deferred, oid in rows:
        cursor.execute("SELECT to_regprocedure(%s)::oid", [expected[name]])
        if (enabled, deferred, initially_deferred, oid) != (
                "O", False, False, cursor.fetchone()[0]):
            raise RuntimeError("budget v11 attestation trigger OID drift")


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0066_business_promotion_budget_v11_durable_stage")]
    operations = [
        migrations.SeparateDatabaseAndState(database_operations=[], state_operations=[
            migrations.CreateModel(name="AiBusinessPromotionBudgetV11Attestation", fields=[
                ("id", models.CharField(max_length=64, primary_key=True, serialize=False)),
                ("attempt", models.PositiveIntegerField()),
                ("owner_email", models.CharField(max_length=320)),
                ("binding_digest", models.CharField(max_length=64)),
                ("compact_json_sha256", models.CharField(max_length=64)),
                ("full_manifest_sha256", models.CharField(max_length=64)),
                ("full_manifest_digest", models.CharField(max_length=64)),
                ("file_descriptors_json", models.TextField()),
                ("file_descriptors_digest", models.CharField(max_length=64)),
                ("approved_content_digest", models.CharField(max_length=64)),
                ("human_review_digest", models.CharField(max_length=64)),
                ("budget_present", models.BooleanField()),
                ("budget_plan_digest", models.CharField(max_length=64, null=True)),
                ("budget_proof_digest", models.CharField(max_length=64)),
                ("slim_proof_digest", models.CharField(max_length=64)),
                ("file_byte_verification_digest", models.CharField(max_length=64)),
                ("html_rows_digest", models.CharField(max_length=64)),
                ("xlsx_opc_formula_digest", models.CharField(max_length=64)),
                ("owning_verification_digest", models.CharField(max_length=64)),
                ("attestation_sha256", models.CharField(max_length=64, unique=True)),
                ("attestation_json", models.TextField()),
                ("recorded_at", models.DateTimeField()),
                ("run", models.ForeignKey(on_delete=models.PROTECT,
                    to="ai_assistant.aibusinessfilerun")),
                ("report", models.ForeignKey(on_delete=models.PROTECT,
                    to="ai_assistant.aireportrun")),
            ], options={"db_table": "ai_business_promotion_budget_v11_attestations",
                "constraints": [models.UniqueConstraint(fields=["run", "attempt"],
                    name="ai_budget_v11_attest_attempt_uq")]}),
        ]),
        migrations.RunPython(install, uninstall),
    ]
