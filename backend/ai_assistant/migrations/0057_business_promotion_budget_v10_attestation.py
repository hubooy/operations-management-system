"""Closed renderer-10 file prepublication attestation; ready remains denied.

Only the independent, default-NOLOGIN role may record a candidate through a
narrow SECURITY DEFINER function. The database checks current report/budget
identity, immutable chunk layout, JSON bytes and every compact file descriptor.
An opaque owning-verification digest is not itself source authority. 0054's
renderer-10 ready denial is unchanged.
"""
from django.db import migrations, models


ROLE = "teruisi_ai_budget_v10_attestor"
TABLE = "public.ai_business_promotion_budget_v10_attestations"
SIGNATURE = "public.ai_budget_v10_attest_staged(text,integer,text)"

GUARD = r"""CREATE FUNCTION public.ai_budget_v10_attestation_guard()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
  IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'ai_budget_v10_attestation_immutable'; END IF;
  IF session_user<>'teruisi_ai_budget_v10_attestor'
     OR current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT c.relowner FROM pg_catalog.pg_class c WHERE c.oid=TG_RELID))
  THEN RAISE EXCEPTION 'ai_budget_v10_attestation_direct_write_denied'; END IF;
  RETURN NEW;
END $$"""

ATTEST = r"""CREATE FUNCTION public.ai_budget_v10_attest_staged(
  selected_run text, selected_attempt integer, attestation_text text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE parent public.ai_business_file_runs%ROWTYPE;
  report public.ai_report_runs%ROWTYPE;
  flow public.ai_workflow_runs%ROWTYPE;
  snapshot jsonb; compact jsonb; full_manifest jsonb; proof jsonb; att jsonb;
  descriptors jsonb; item jsonb; volume jsonb; file_item jsonb;
  payload bytea; payload_sha text; att_sha text; position integer;
  receipt_id text; existing public.ai_business_promotion_budget_v10_attestations%ROWTYPE;
  present boolean; plan_digest text;
BEGIN
  IF session_user<>'teruisi_ai_budget_v10_attestor'
     OR selected_run IS NULL OR selected_run !~ '^[A-Za-z0-9_-]{1,160}$'
     OR selected_attempt NOT BETWEEN 1 AND 5
     OR attestation_text IS NULL
     OR octet_length(attestation_text) NOT BETWEEN 1 AND 131072
     OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r
       WHERE r.rolname='teruisi_ai_budget_v10_attestor'
         AND NOT r.rolcanlogin AND NOT r.rolinherit
         AND NOT r.rolsuper AND NOT r.rolcreatedb
         AND NOT r.rolcreaterole AND NOT r.rolreplication
         AND NOT r.rolbypassrls)
     OR EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members membership
       WHERE membership.roleid='teruisi_ai_budget_v10_attestor'::regrole
          OR membership.member='teruisi_ai_budget_v10_attestor'::regrole)
     OR current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT c.relowner FROM pg_catalog.pg_class c
        WHERE c.oid='public.ai_business_promotion_budget_v10_attestations'::regclass))
  THEN RAISE EXCEPTION 'ai_budget_v10_attestor_unavailable'; END IF;
  att:=attestation_text::jsonb;
  IF jsonb_typeof(att) IS DISTINCT FROM 'object'
     OR NOT att ?& ARRAY['schemaVersion','runId','attempt','bindingDigest',
       'compactJsonSha256','fullManifestSha256','fullManifestDigest',
       'files','approvedContentDigest','humanReviewDigest','budgetPresent',
       'budgetPlanDigest','budgetProofDigest','owningVerificationDigest',
       'publicationFenceDigest','verifierVersion']
     OR (att - ARRAY['schemaVersion','runId','attempt','bindingDigest',
       'compactJsonSha256','fullManifestSha256','fullManifestDigest',
       'files','approvedContentDigest','humanReviewDigest','budgetPresent',
       'budgetPlanDigest','budgetProofDigest','owningVerificationDigest',
       'publicationFenceDigest','verifierVersion'])<>'{}'::jsonb
     OR att->>'schemaVersion' IS DISTINCT FROM
       'business-promotion-budget-v10-staged-attestation-v1'
     OR att->>'verifierVersion' IS DISTINCT FROM
       'business-promotion-budget-v10-owning-verifier-v1'
     OR att->>'runId' IS DISTINCT FROM selected_run
     OR att->>'attempt' IS DISTINCT FROM selected_attempt::text
     OR jsonb_typeof(att->'attempt') IS DISTINCT FROM 'number'
     OR jsonb_typeof(att->'budgetPresent') IS DISTINCT FROM 'boolean'
     OR jsonb_typeof(att->'files') IS DISTINCT FROM 'object'
     OR EXISTS (SELECT 1 FROM unnest(ARRAY[
       att->>'bindingDigest',att->>'compactJsonSha256',
       att->>'fullManifestSha256',att->>'fullManifestDigest',
       att->>'approvedContentDigest',att->>'humanReviewDigest',
       att->>'budgetProofDigest',att->>'owningVerificationDigest',
       att->>'publicationFenceDigest']) AS item(value)
       WHERE item.value IS NULL OR item.value !~ '^[0-9a-f]{64}$')
  THEN RAISE EXCEPTION 'ai_budget_v10_attestation_shape_invalid'; END IF;
  SELECT * INTO parent FROM public.ai_business_file_runs item
    WHERE item.id=selected_run FOR UPDATE;
  IF parent.id IS NULL OR parent.renderer_version<>10 OR parent.draft
     OR parent.status<>'paused' OR parent.error_code<>'renderer_unpublished'
     OR parent.progress_json::jsonb->>'stage' IS DISTINCT FROM 'staged_unpublished'
     OR parent.attempt<>selected_attempt OR parent.stored_bytes<1
     OR parent.manifest_json='{}'
     OR parent.binding_digest IS DISTINCT FROM att->>'bindingDigest'
     OR encode(sha256(convert_to(parent.manifest_json,'UTF8')),'hex')
        IS DISTINCT FROM att->>'compactJsonSha256'
  THEN RAISE EXCEPTION 'ai_budget_v10_staged_root_invalid'; END IF;
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
         AND actor.status='active' AND actor.scope IS NULL)
  THEN RAISE EXCEPTION 'ai_budget_v10_review_not_final'; END IF;
  compact:=parent.manifest_json::jsonb;
  PERFORM public.ai_business_volume_manifest_check(
    parent.id,parent.attempt,parent.binding_digest,parent.draft,
    parent.manifest_json);
  descriptors:=jsonb_build_object('files',compact->'files',
    'manifestFile',compact->'manifestFile');
  IF compact->>'rendererVersion' IS DISTINCT FROM '10'
     OR compact->>'attempt' IS DISTINCT FROM selected_attempt::text
     OR att->'files' IS DISTINCT FROM descriptors
  THEN RAISE EXCEPTION 'ai_budget_v10_file_descriptors_invalid'; END IF;
  SELECT string_agg(chunk.content,''::bytea ORDER BY chunk.sequence)
    INTO payload FROM public.ai_business_volume_chunks chunk
    WHERE chunk.run_id=parent.id AND chunk.attempt=parent.attempt
      AND chunk.volume_index=0 AND chunk.format='json';
  IF payload IS NULL OR octet_length(payload)>16777216
  THEN RAISE EXCEPTION 'ai_budget_v10_full_json_missing'; END IF;
  payload_sha:=encode(sha256(payload),'hex');
  IF payload_sha IS DISTINCT FROM compact->'manifestFile'->>'sha256'
     OR payload_sha IS DISTINCT FROM att->>'fullManifestSha256'
  THEN RAISE EXCEPTION 'ai_budget_v10_full_json_digest_mismatch'; END IF;
  full_manifest:=convert_from(payload,'UTF8')::jsonb;
  proof:=full_manifest->'promotionBudgetProof';
  IF jsonb_typeof(full_manifest->'volumes') IS DISTINCT FROM 'array'
     OR jsonb_array_length(full_manifest->'volumes') IS DISTINCT FROM
        (compact->>'volumeCount')::integer
  THEN RAISE EXCEPTION 'ai_budget_v10_full_volume_list_invalid'; END IF;
  FOR position IN 0..jsonb_array_length(compact->'files')-1 LOOP
    item:=compact->'files'->position;
    volume:=full_manifest->'volumes'->((item->>'volumeIndex')::integer-1);
    file_item:=volume->'files'->(item->>'format');
    IF file_item->>'sha256' IS DISTINCT FROM item->>'sha256'
       OR file_item->>'bytes' IS DISTINCT FROM item->>'bytes'
    THEN RAISE EXCEPTION 'ai_budget_v10_full_file_list_invalid'; END IF;
  END LOOP;
  present:=report.budget_plan_id IS NOT NULL;
  plan_digest:=snapshot#>>'{budgetRef,planDigest}';
  IF full_manifest->>'schemaVersion' IS DISTINCT FROM 'business-volume-files-v1'
     OR full_manifest->>'status' IS DISTINCT FROM 'complete'
     OR full_manifest->>'rendererVersion' IS DISTINCT FROM '10'
     OR full_manifest->>'reportId' IS DISTINCT FROM parent.report_id
     OR full_manifest->>'evidenceDigest' IS DISTINCT FROM snapshot->>'sealedDigest'
     OR full_manifest->>'manifestDigest' IS DISTINCT FROM att->>'fullManifestDigest'
     OR full_manifest->>'manifestDigest' !~ '^[0-9a-f]{64}$'
     OR proof->>'schemaVersion' IS DISTINCT FROM
       'business-promotion-budget-renderer10-candidate-v1'
     OR proof->'candidateOnly' IS DISTINCT FROM 'true'::jsonb
     OR proof->>'proofDigest' IS DISTINCT FROM att->>'budgetProofDigest'
     OR proof->>'approvedContentDigest' IS DISTINCT FROM
       att->>'approvedContentDigest'
     OR proof->>'humanReviewDigest' IS DISTINCT FROM
       att->>'humanReviewDigest'
     OR full_manifest->'promotionFileProof'->>'contentDtoDigest' IS DISTINCT FROM
       att->>'approvedContentDigest'
     OR full_manifest->'promotionFileProof'->>'humanReviewDigest' IS DISTINCT FROM
       att->>'humanReviewDigest'
     OR att->'budgetPresent' IS DISTINCT FROM to_jsonb(present)
     OR (present AND (att->>'budgetPlanDigest' IS DISTINCT FROM plan_digest
       OR full_manifest->>'budgetPlanDigest' IS DISTINCT FROM plan_digest
       OR proof->>'budgetPlanDigest' IS DISTINCT FROM plan_digest
       OR proof->>'status' IS DISTINCT FROM 'reconciled_fixed_budget_candidate'
       OR full_manifest->'volumes'->0->>'nativeBudgetSheets' IS DISTINCT FROM '3'
       OR full_manifest->'volumes'->0->>'offlineBudgetEnabled' IS DISTINCT FROM 'true'))
     OR (NOT present AND (att->'budgetPlanDigest' IS DISTINCT FROM 'null'::jsonb
       OR full_manifest ? 'budgetPlanDigest'
       OR proof->'budgetPlanDigest' IS DISTINCT FROM 'null'::jsonb
       OR proof->>'status' IS DISTINCT FROM 'missing_fixed_budget'
       OR full_manifest->'volumes'->0->>'nativeBudgetSheets' IS DISTINCT FROM '0'
       OR full_manifest->'volumes'->0->>'offlineBudgetEnabled' IS DISTINCT FROM 'false'))
  THEN RAISE EXCEPTION 'ai_budget_v10_proof_root_mismatch'; END IF;
  receipt_id:=encode(sha256(convert_to(
    parent.id||':'||parent.attempt::text,'UTF8')),'hex');
  att_sha:=encode(sha256(convert_to(attestation_text,'UTF8')),'hex');
  INSERT INTO public.ai_business_promotion_budget_v10_attestations (
    id,run_id,attempt,report_id,owner_email,binding_digest,
    compact_json_sha256,full_manifest_sha256,full_manifest_digest,
    file_descriptors_json,file_descriptors_digest,approved_content_digest,
    human_review_digest,budget_present,budget_plan_digest,
    budget_proof_digest,owning_verification_digest,
    publication_fence_digest,attestation_sha256,attestation_json,recorded_at)
  VALUES (receipt_id,parent.id,parent.attempt,parent.report_id,parent.owner_email,
    parent.binding_digest,att->>'compactJsonSha256',payload_sha,
    att->>'fullManifestDigest',descriptors::text,
    encode(sha256(convert_to(descriptors::text,'UTF8')),'hex'),
    att->>'approvedContentDigest',att->>'humanReviewDigest',present,
    plan_digest,att->>'budgetProofDigest',
    att->>'owningVerificationDigest',att->>'publicationFenceDigest',
    att_sha,attestation_text,clock_timestamp())
  ON CONFLICT (run_id,attempt) DO NOTHING;
  SELECT * INTO existing FROM public.ai_business_promotion_budget_v10_attestations item
    WHERE item.run_id=parent.id AND item.attempt=parent.attempt;
  IF existing.id IS NULL OR existing.id IS DISTINCT FROM receipt_id
     OR existing.attestation_sha256 IS DISTINCT FROM att_sha
     OR existing.attestation_json IS DISTINCT FROM attestation_text
     OR existing.file_descriptors_json::jsonb IS DISTINCT FROM descriptors
     OR existing.compact_json_sha256 IS DISTINCT FROM
       att->>'compactJsonSha256'
     OR existing.full_manifest_sha256 IS DISTINCT FROM payload_sha
  THEN RAISE EXCEPTION 'ai_budget_v10_attestation_conflicting_replay'; END IF;
  RETURN receipt_id;
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
            raise RuntimeError("0057 verifier role must remain independent NOLOGIN")
        cursor.execute("SELECT 1 FROM pg_catalog.pg_auth_members membership "
            "JOIN pg_catalog.pg_roles member ON member.oid=membership.member "
            "JOIN pg_catalog.pg_roles parent ON parent.oid=membership.roleid "
            "WHERE member.rolname=%s OR parent.rolname=%s", [ROLE, ROLE])
        if cursor.fetchone():
            raise RuntimeError("0057 verifier role cannot inherit another role")
        cursor.execute("""CREATE TABLE public.ai_business_promotion_budget_v10_attestations (
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
          owning_verification_digest varchar(64) NOT NULL,
          publication_fence_digest varchar(64) NOT NULL,
          attestation_sha256 varchar(64) NOT NULL UNIQUE,
          attestation_json text NOT NULL,
          recorded_at timestamptz NOT NULL,
          CONSTRAINT ai_budget_v10_attest_attempt_uq UNIQUE (run_id,attempt)
        )""")
        cursor.execute("REVOKE ALL ON " + TABLE + " FROM PUBLIC")
        cursor.execute("GRANT USAGE ON SCHEMA public TO " + ROLE)
        for role in ("teruisi_ai_reader", "teruisi_ai_writer", ROLE):
            cursor.execute("SELECT to_regrole(%s)", [role])
            if cursor.fetchone()[0] is not None:
                cursor.execute("REVOKE ALL ON " + TABLE + " FROM " + role)
        cursor.execute(GUARD)
        cursor.execute(ATTEST)
        cursor.execute("REVOKE ALL ON FUNCTION public.ai_budget_v10_attestation_guard() "
            "FROM PUBLIC")
        cursor.execute("REVOKE ALL ON FUNCTION " + SIGNATURE + " FROM PUBLIC")
        cursor.execute("GRANT EXECUTE ON FUNCTION " + SIGNATURE + " TO " + ROLE)
        cursor.execute("CREATE TRIGGER ai_budget_v10_attestation_guard BEFORE "
            "INSERT OR UPDATE OR DELETE ON " + TABLE + " FOR EACH ROW "
            "EXECUTE FUNCTION public.ai_budget_v10_attestation_guard()")
        cursor.execute("CREATE TRIGGER ai_budget_v10_attestation_no_truncate "
            "BEFORE TRUNCATE ON " + TABLE + " FOR EACH STATEMENT EXECUTE FUNCTION "
            "public.ai_v4_seal_ticket_no_truncate()")


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT EXISTS(SELECT 1 FROM " + TABLE + ")")
        if cursor.fetchone()[0]:
            raise RuntimeError("0057 cannot discard budget attestations")
        for trigger in ("ai_budget_v10_attestation_no_truncate",
                "ai_budget_v10_attestation_guard"):
            cursor.execute("DROP TRIGGER " + trigger + " ON " + TABLE)
        cursor.execute("DROP FUNCTION " + SIGNATURE)
        cursor.execute("DROP FUNCTION public.ai_budget_v10_attestation_guard()")
        cursor.execute("DROP TABLE " + TABLE)
        # Retain the independent NOLOGIN role for audit, with no usable grant.


def verify_catalog(cursor):
    """Read-only catalog pin for the closed attestation role and sidecar."""
    cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
        "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
        "WHERE rolname=%s", [ROLE])
    if cursor.fetchone() != (False,) * 7:
        raise RuntimeError("budget v10 attestor role no longer closed")
    cursor.execute("SELECT count(*) FROM pg_catalog.pg_auth_members WHERE "
        "roleid=%s::regrole OR member=%s::regrole", [ROLE, ROLE])
    if cursor.fetchone() != (0,):
        raise RuntimeError("budget v10 attestor gained membership")
    for signature, definition, security in (
            ("public.ai_budget_v10_attestation_guard()", GUARD, False),
            (SIGNATURE, ATTEST, True)):
        cursor.execute("SELECT p.prosrc,p.prosecdef,p.proconfig,l.lanname,"
            "pg_catalog.pg_get_userbyid(p.proowner) FROM pg_catalog.pg_proc p "
            "JOIN pg_catalog.pg_language l ON l.oid=p.prolang "
            "WHERE p.oid=to_regprocedure(%s)", [signature])
        row = cursor.fetchone()
        if (row is None or row[0] != definition.split("$$", 2)[1]
                or row[1] is not security or row[3] != "plpgsql"
                or {item.replace(" ", "") for item in (row[2] or [])}
                    != {"search_path=pg_catalog,public"}
                or row[4] == ROLE):
            raise RuntimeError("budget v10 attestation function drift")
        cursor.execute("SELECT CASE WHEN acl.grantee=p.proowner THEN 'OWNER' "
            "WHEN acl.grantee=0 THEN 'PUBLIC' ELSE grantee.rolname END,"
            "acl.privilege_type,acl.is_grantable FROM pg_catalog.pg_proc p "
            "CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(p.proacl,"
            "pg_catalog.acldefault('f',p.proowner))) acl "
            "LEFT JOIN pg_catalog.pg_roles grantee ON grantee.oid=acl.grantee "
            "WHERE p.oid=to_regprocedure(%s)", [signature])
        expected = {("OWNER", "EXECUTE", False)}
        if security:
            expected.add((ROLE, "EXECUTE", False))
        if set(cursor.fetchall()) != expected:
            raise RuntimeError("budget v10 attestation function ACL drift")
    cursor.execute("SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_class c "
        "CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(c.relacl,"
        "pg_catalog.acldefault('r',c.relowner))) acl "
        "LEFT JOIN pg_catalog.pg_roles r ON r.oid=acl.grantee "
        "WHERE c.oid=%s::regclass AND (acl.grantee=0 OR "
        "r.rolname IN (%s,'teruisi_ai_writer','teruisi_ai_reader')))",
        [TABLE, ROLE])
    if cursor.fetchone() != (False,):
        raise RuntimeError("budget v10 attestation direct table access reopened")
    cursor.execute("SELECT t.tgname,t.tgenabled,t.tgdeferrable,t.tginitdeferred,"
        "t.tgfoid FROM pg_catalog.pg_trigger t "
        "WHERE t.tgrelid=%s::regclass AND NOT t.tgisinternal ORDER BY t.tgname",
        [TABLE])
    rows = cursor.fetchall()
    expected = {"ai_budget_v10_attestation_guard":
        "public.ai_budget_v10_attestation_guard()",
        "ai_budget_v10_attestation_no_truncate":
        "public.ai_v4_seal_ticket_no_truncate()"}
    if len(rows) != 2 or {row[0] for row in rows} != set(expected):
        raise RuntimeError("budget v10 attestation trigger drift")
    for name, enabled, deferred, initially_deferred, oid in rows:
        cursor.execute("SELECT to_regprocedure(%s)::oid", [expected[name]])
        if (enabled, deferred, initially_deferred, oid) != (
                "O", False, False, cursor.fetchone()[0]):
            raise RuntimeError("budget v10 attestation trigger binding drift")


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0056_business_market_v2_material_role_bridge")]
    operations = [
        migrations.SeparateDatabaseAndState(database_operations=[], state_operations=[
            migrations.CreateModel(name="AiBusinessPromotionBudgetV10Attestation", fields=[
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
                ("owning_verification_digest", models.CharField(max_length=64)),
                ("publication_fence_digest", models.CharField(max_length=64)),
                ("attestation_sha256", models.CharField(max_length=64, unique=True)),
                ("attestation_json", models.TextField()),
                ("recorded_at", models.DateTimeField()),
                ("run", models.ForeignKey(on_delete=models.PROTECT, to="ai_assistant.aibusinessfilerun")),
                ("report", models.ForeignKey(on_delete=models.PROTECT, to="ai_assistant.aireportrun")),
            ], options={"db_table": "ai_business_promotion_budget_v10_attestations",
                "constraints": [models.UniqueConstraint(fields=["run", "attempt"],
                    name="ai_budget_v10_attest_attempt_uq")]}),
        ]),
        migrations.RunPython(install, uninstall),
    ]
