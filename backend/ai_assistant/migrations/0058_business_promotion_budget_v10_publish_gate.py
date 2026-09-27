"""Narrow renderer-10 atomic publish gate; verifier role remains NOLOGIN.

0057 stores a role-bound immutable candidate. A protected external verifier
must actually check source/XLSX semantics before using that role; this database
cannot authenticate an arbitrary owningVerificationDigest. No route, credential,
download dispatch or automatic tick publication is added here.
"""
from importlib import import_module
from django.db import migrations

stage = import_module("ai_assistant.migrations.0054_business_promotion_budget_file_staging")
attestation = import_module("ai_assistant.migrations.0057_business_promotion_budget_v10_attestation")
ROLE = attestation.ROLE
OLD_SQL = stage.NEW_SQL
replace_once = stage.replace_once
READY_SIGNATURE = "public.ai_budget_v10_ready_requirements(text,jsonb,text)"
PUBLISH_SIGNATURE = "public.ai_budget_v10_publish(text,integer,bigint,text,text,text)"
OUTCOME_SIGNATURE = "public.ai_budget_v10_publish_outcome(text,integer,text,text,text)"

READY_REQUIREMENTS = r"""CREATE FUNCTION public.ai_budget_v10_ready_requirements(
  selected_run text, proposed_progress jsonb, expected_parent_status text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE parent public.ai_business_file_runs%ROWTYPE;
  report public.ai_report_runs%ROWTYPE;
  flow public.ai_workflow_runs%ROWTYPE;
  evidence public.ai_business_evidence_runs%ROWTYPE;
  receipt public.ai_business_promotion_budget_v10_attestations%ROWTYPE;
  snapshot jsonb; compact jsonb; descriptors jsonb; request_body jsonb;
  original_version bigint; expected_request text;
BEGIN
  IF session_user<>'teruisi_ai_budget_v10_attestor'
     OR expected_parent_status NOT IN ('paused','ready')
     OR current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT c.relowner FROM pg_catalog.pg_class c
        WHERE c.oid='public.ai_business_promotion_budget_v10_attestations'::regclass))
     OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r
       WHERE r.rolname='teruisi_ai_budget_v10_attestor'
         AND NOT r.rolcanlogin AND NOT r.rolinherit
         AND NOT r.rolsuper AND NOT r.rolcreatedb
         AND NOT r.rolcreaterole AND NOT r.rolreplication
         AND NOT r.rolbypassrls)
     OR EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members membership
       WHERE membership.roleid='teruisi_ai_budget_v10_attestor'::regrole
          OR membership.member='teruisi_ai_budget_v10_attestor'::regrole)
  THEN RAISE EXCEPTION 'ai_budget_v10_publisher_unavailable'; END IF;
  SELECT * INTO parent FROM public.ai_business_file_runs item
    WHERE item.id=selected_run FOR SHARE;
  SELECT * INTO receipt FROM public.ai_business_promotion_budget_v10_attestations item
    WHERE item.run_id=selected_run AND item.attempt=parent.attempt FOR SHARE;
  IF parent.id IS NULL OR receipt.id IS NULL
     OR parent.renderer_version<>10 OR parent.draft
     OR parent.status IS DISTINCT FROM expected_parent_status
     OR (expected_parent_status='paused' AND
       (parent.error_code<>'renderer_unpublished'
        OR parent.progress_json::jsonb->>'stage' IS DISTINCT FROM 'staged_unpublished'))
     OR (expected_parent_status='ready' AND
       (parent.error_code<>'' OR parent.progress_json::jsonb IS DISTINCT FROM proposed_progress))
     OR receipt.report_id IS DISTINCT FROM parent.report_id
     OR receipt.owner_email IS DISTINCT FROM parent.owner_email
     OR receipt.binding_digest IS DISTINCT FROM parent.binding_digest
     OR receipt.attempt IS DISTINCT FROM parent.attempt
     OR receipt.compact_json_sha256 IS DISTINCT FROM
       encode(sha256(convert_to(parent.manifest_json,'UTF8')),'hex')
  THEN RAISE EXCEPTION 'ai_budget_v10_publication_root_invalid'; END IF;
  IF jsonb_typeof(proposed_progress) IS DISTINCT FROM 'object'
     OR NOT proposed_progress ?& ARRAY['stage','attempt','bindingDigest',
       'attestationId','attestationSha256','fullManifestDigest',
       'manifestFileSha256','owningVerificationDigest',
       'publicationFenceDigest','publishRequestDigest']
     OR (proposed_progress - ARRAY['stage','attempt','bindingDigest',
       'attestationId','attestationSha256','fullManifestDigest',
       'manifestFileSha256','owningVerificationDigest',
       'publicationFenceDigest','publishRequestDigest'])<>'{}'::jsonb
     OR proposed_progress->>'stage' IS DISTINCT FROM 'ready'
     OR proposed_progress->>'attempt' IS DISTINCT FROM parent.attempt::text
     OR jsonb_typeof(proposed_progress->'attempt') IS DISTINCT FROM 'number'
     OR proposed_progress->>'bindingDigest' IS DISTINCT FROM parent.binding_digest
     OR proposed_progress->>'attestationId' IS DISTINCT FROM receipt.id
     OR proposed_progress->>'attestationSha256' IS DISTINCT FROM
       receipt.attestation_sha256
     OR proposed_progress->>'fullManifestDigest' IS DISTINCT FROM
       receipt.full_manifest_digest
     OR proposed_progress->>'manifestFileSha256' IS DISTINCT FROM
       receipt.full_manifest_sha256
     OR proposed_progress->>'owningVerificationDigest' IS DISTINCT FROM
       receipt.owning_verification_digest
     OR proposed_progress->>'publicationFenceDigest' IS DISTINCT FROM
       receipt.publication_fence_digest
     OR proposed_progress->>'publishRequestDigest' IS NULL
     OR proposed_progress->>'publishRequestDigest' !~ '^[0-9a-f]{64}$'
  THEN RAISE EXCEPTION 'ai_budget_v10_publication_receipt_invalid'; END IF;
  original_version:=CASE expected_parent_status WHEN 'paused' THEN parent.version
    ELSE parent.version-1 END;
  IF original_version<1 THEN RAISE EXCEPTION 'ai_budget_v10_request_version_invalid'; END IF;
  request_body:=jsonb_build_object(
    'schemaVersion','business-budget-v10-publish-request-v1',
    'runId',parent.id,'attempt',parent.attempt,
    'expectedVersion',original_version,'attestationId',receipt.id,
    'attestationSha256',receipt.attestation_sha256,
    'bindingDigest',receipt.binding_digest,
    'fullManifestDigest',receipt.full_manifest_digest,
    'fullManifestSha256',receipt.full_manifest_sha256);
  expected_request:=encode(sha256(convert_to(
    public.ai_v4_replay_canonical(request_body),'UTF8')),'hex');
  IF proposed_progress->>'publishRequestDigest' IS DISTINCT FROM expected_request
  THEN RAISE EXCEPTION 'ai_budget_v10_request_digest_invalid'; END IF;
  PERFORM public.ai_business_promotion_budget_parent_requirements(
    parent.report_id,parent.owner_email,parent.scope_json);
  SELECT * INTO report FROM public.ai_report_runs item
    WHERE item.id=parent.report_id FOR SHARE;
  SELECT * INTO flow FROM public.ai_workflow_runs item
    WHERE item.id=report.workflow_id FOR SHARE;
  snapshot:=report.snapshot_json::jsonb;
  SELECT * INTO evidence FROM public.ai_business_evidence_runs item
    WHERE item.id=snapshot->>'evidenceRunId' FOR SHARE;
  IF flow.status<>'completed' OR flow.completed_at IS NULL
     OR evidence.id IS NULL OR evidence.status<>'sealed'
     OR evidence.version::text IS DISTINCT FROM snapshot->>'evidenceVersion'
     OR encode(sha256(convert_to(evidence.plan_json,'UTF8')),'hex')
        IS DISTINCT FROM snapshot->>'evidencePlanDigest'
     OR evidence.state_json::jsonb->>'sealedDigest' IS DISTINCT FROM
        snapshot->>'sealedDigest'
     OR NOT EXISTS (SELECT 1 FROM public.access_control_users actor
       WHERE actor.email=parent.owner_email AND actor.role='admin'
         AND actor.status='active' AND actor.scope IS NULL)
     OR receipt.budget_present IS DISTINCT FROM
        (report.budget_plan_id IS NOT NULL)
     OR receipt.budget_plan_digest IS DISTINCT FROM
        snapshot#>>'{budgetRef,planDigest}'
  THEN RAISE EXCEPTION 'ai_budget_v10_publication_authority_changed'; END IF;
  compact:=parent.manifest_json::jsonb;
  descriptors:=jsonb_build_object('files',compact->'files',
    'manifestFile',compact->'manifestFile');
  IF compact->>'rendererVersion' IS DISTINCT FROM '10'
     OR compact->>'attempt' IS DISTINCT FROM parent.attempt::text
     OR compact->'manifestFile'->>'sha256' IS DISTINCT FROM
       receipt.full_manifest_sha256
     OR receipt.file_descriptors_json::jsonb IS DISTINCT FROM descriptors
     OR receipt.file_descriptors_digest IS DISTINCT FROM
       encode(sha256(convert_to(receipt.file_descriptors_json,'UTF8')),'hex')
     OR receipt.attestation_json::jsonb->>'budgetProofDigest' IS DISTINCT FROM
       receipt.budget_proof_digest
     OR receipt.attestation_json::jsonb->>'approvedContentDigest' IS DISTINCT FROM
       receipt.approved_content_digest
     OR receipt.attestation_json::jsonb->>'humanReviewDigest' IS DISTINCT FROM
       receipt.human_review_digest
     OR receipt.attestation_sha256 IS DISTINCT FROM
       encode(sha256(convert_to(receipt.attestation_json,'UTF8')),'hex')
  THEN RAISE EXCEPTION 'ai_budget_v10_publication_manifest_changed'; END IF;
  PERFORM public.ai_business_volume_manifest_check(parent.id,parent.attempt,
    parent.binding_digest,parent.draft,parent.manifest_json);
END $$"""

PUBLISH = r"""CREATE FUNCTION public.ai_budget_v10_publish(
  selected_run text,selected_attempt integer,expected_version bigint,
  selected_attestation_id text,selected_attestation_sha text,
  selected_request_digest text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE parent public.ai_business_file_runs%ROWTYPE;
  receipt public.ai_business_promotion_budget_v10_attestations%ROWTYPE;
  compact jsonb; progress jsonb; updated public.ai_business_file_runs%ROWTYPE;
  request_body jsonb; expected_request text;
BEGIN
  IF session_user<>'teruisi_ai_budget_v10_attestor'
     OR selected_run IS NULL OR selected_run !~ '^[A-Za-z0-9_-]{1,160}$'
     OR selected_attempt NOT BETWEEN 1 AND 5
     OR expected_version IS NULL OR expected_version<1
     OR selected_attestation_id !~ '^[0-9a-f]{64}$'
     OR selected_attestation_sha !~ '^[0-9a-f]{64}$'
     OR selected_request_digest !~ '^[0-9a-f]{64}$'
  THEN RAISE EXCEPTION 'ai_budget_v10_publish_input_invalid'; END IF;
  SELECT * INTO parent FROM public.ai_business_file_runs item
    WHERE item.id=selected_run FOR UPDATE;
  SELECT * INTO receipt FROM public.ai_business_promotion_budget_v10_attestations item
    WHERE item.run_id=selected_run AND item.attempt=selected_attempt FOR SHARE;
  IF parent.id IS NULL OR receipt.id IS NULL
     OR parent.renderer_version<>10 OR parent.draft
     OR parent.attempt<>selected_attempt
     OR receipt.id IS DISTINCT FROM selected_attestation_id
     OR receipt.attestation_sha256 IS DISTINCT FROM selected_attestation_sha
  THEN RAISE EXCEPTION 'ai_budget_v10_publish_attestation_missing'; END IF;
  IF parent.status='ready' AND expected_version<>parent.version-1
  THEN RAISE EXCEPTION 'ai_budget_v10_publish_conflicting_replay'; END IF;
  request_body:=jsonb_build_object(
    'schemaVersion','business-budget-v10-publish-request-v1',
    'runId',selected_run,'attempt',selected_attempt,
    'expectedVersion',expected_version,
    'attestationId',receipt.id,
    'attestationSha256',receipt.attestation_sha256,
    'bindingDigest',receipt.binding_digest,
    'fullManifestDigest',receipt.full_manifest_digest,
    'fullManifestSha256',receipt.full_manifest_sha256);
  expected_request:=encode(sha256(convert_to(
    public.ai_v4_replay_canonical(request_body),'UTF8')),'hex');
  IF selected_request_digest IS DISTINCT FROM expected_request
  THEN RAISE EXCEPTION 'ai_budget_v10_publish_request_digest_invalid'; END IF;
  IF parent.status='ready' THEN
    progress:=parent.progress_json::jsonb;
    IF progress->>'publishRequestDigest' IS DISTINCT FROM selected_request_digest
       OR progress->>'attestationId' IS DISTINCT FROM selected_attestation_id
       OR progress->>'attestationSha256' IS DISTINCT FROM selected_attestation_sha
    THEN RAISE EXCEPTION 'ai_budget_v10_publish_conflicting_replay'; END IF;
    PERFORM public.ai_budget_v10_ready_requirements(parent.id,progress,'ready');
    RETURN jsonb_build_object('schemaVersion','business-budget-v10-publication-v1',
      'status','committed','runId',parent.id,'attempt',parent.attempt,
      'version',parent.version,'requestDigest',selected_request_digest,
      'attestationId',receipt.id,
      'manifestFileSha256',receipt.full_manifest_sha256);
  END IF;
  IF parent.status<>'paused' OR parent.error_code<>'renderer_unpublished'
     OR parent.progress_json::jsonb->>'stage' IS DISTINCT FROM 'staged_unpublished'
     OR parent.version<>expected_version
  THEN RAISE EXCEPTION 'ai_budget_v10_publish_cas_failed'; END IF;
  compact:=parent.manifest_json::jsonb;
  progress:=jsonb_build_object(
    'stage','ready','attempt',parent.attempt,'bindingDigest',parent.binding_digest,
    'attestationId',receipt.id,'attestationSha256',receipt.attestation_sha256,
    'fullManifestDigest',receipt.full_manifest_digest,
    'manifestFileSha256',receipt.full_manifest_sha256,
    'owningVerificationDigest',receipt.owning_verification_digest,
    'publicationFenceDigest',receipt.publication_fence_digest,
    'publishRequestDigest',selected_request_digest);
  PERFORM public.ai_budget_v10_ready_requirements(parent.id,progress,'paused');
  UPDATE public.ai_business_file_runs item
    SET status='ready',error_code='',progress_json=progress::text,
        version=item.version+1
    WHERE item.id=selected_run AND item.renderer_version=10
      AND item.status='paused' AND item.error_code='renderer_unpublished'
      AND item.version=expected_version AND item.attempt=selected_attempt
      AND item.binding_digest=receipt.binding_digest
      AND item.manifest_json=parent.manifest_json
      AND item.stored_bytes=parent.stored_bytes
    RETURNING item.* INTO updated;
  IF updated.id IS NULL THEN RAISE EXCEPTION 'ai_budget_v10_publish_cas_failed'; END IF;
  PERFORM public.ai_budget_v10_ready_requirements(updated.id,progress,'ready');
  RETURN jsonb_build_object('schemaVersion','business-budget-v10-publication-v1',
    'status','committed','runId',updated.id,'attempt',updated.attempt,
    'version',updated.version,'requestDigest',selected_request_digest,
    'attestationId',receipt.id,
    'manifestFileSha256',receipt.full_manifest_sha256);
END $$"""

OUTCOME = r"""CREATE FUNCTION public.ai_budget_v10_publish_outcome(
  selected_run text,selected_attempt integer,selected_request_digest text,
  selected_attestation_id text,selected_attestation_sha text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE parent public.ai_business_file_runs%ROWTYPE;
  receipt public.ai_business_promotion_budget_v10_attestations%ROWTYPE;
  progress jsonb; request_body jsonb; expected_request text;
  original_version bigint;
BEGIN
  IF session_user<>'teruisi_ai_budget_v10_attestor'
     OR selected_run IS NULL OR selected_run !~ '^[A-Za-z0-9_-]{1,160}$'
     OR selected_attempt NOT BETWEEN 1 AND 5
     OR selected_request_digest !~ '^[0-9a-f]{64}$'
     OR selected_attestation_id !~ '^[0-9a-f]{64}$'
     OR selected_attestation_sha !~ '^[0-9a-f]{64}$'
  THEN RAISE EXCEPTION 'ai_budget_v10_outcome_input_invalid'; END IF;
  SELECT * INTO parent FROM public.ai_business_file_runs item
    WHERE item.id=selected_run FOR SHARE;
  SELECT * INTO receipt FROM public.ai_business_promotion_budget_v10_attestations item
    WHERE item.run_id=selected_run AND item.attempt=selected_attempt FOR SHARE;
  IF parent.id IS NULL OR receipt.id IS NULL OR parent.renderer_version<>10
     OR parent.attempt<>selected_attempt
     OR receipt.id IS DISTINCT FROM selected_attestation_id
     OR receipt.attestation_sha256 IS DISTINCT FROM selected_attestation_sha
  THEN RETURN jsonb_build_object('status','unknown'); END IF;
  IF parent.status IN ('ready','paused') THEN
    original_version:=CASE parent.status WHEN 'ready' THEN parent.version-1
      ELSE parent.version END;
    IF original_version<1 THEN RAISE EXCEPTION 'ai_budget_v10_outcome_version_invalid'; END IF;
    request_body:=jsonb_build_object(
      'schemaVersion','business-budget-v10-publish-request-v1',
      'runId',selected_run,'attempt',selected_attempt,
      'expectedVersion',original_version,
      'attestationId',receipt.id,
      'attestationSha256',receipt.attestation_sha256,
      'bindingDigest',receipt.binding_digest,
      'fullManifestDigest',receipt.full_manifest_digest,
      'fullManifestSha256',receipt.full_manifest_sha256);
    expected_request:=encode(sha256(convert_to(
      public.ai_v4_replay_canonical(request_body),'UTF8')),'hex');
    IF selected_request_digest IS DISTINCT FROM expected_request
    THEN RAISE EXCEPTION 'ai_budget_v10_outcome_request_digest_invalid'; END IF;
  END IF;
  IF parent.status='ready' THEN
    progress:=parent.progress_json::jsonb;
    IF progress->>'publishRequestDigest' IS DISTINCT FROM selected_request_digest
       OR progress->>'attestationId' IS DISTINCT FROM selected_attestation_id
       OR progress->>'attestationSha256' IS DISTINCT FROM selected_attestation_sha
    THEN RETURN jsonb_build_object('status','conflict'); END IF;
    PERFORM public.ai_budget_v10_ready_requirements(parent.id,progress,'ready');
    RETURN jsonb_build_object('schemaVersion','business-budget-v10-publication-v1',
      'status','committed','runId',parent.id,
      'attempt',parent.attempt,'version',parent.version,
      'requestDigest',selected_request_digest,
      'attestationId',receipt.id,
      'manifestFileSha256',receipt.full_manifest_sha256);
  END IF;
  IF parent.status='paused' AND parent.error_code='renderer_unpublished'
     AND parent.progress_json::jsonb->>'stage'='staged_unpublished'
     AND receipt.binding_digest=parent.binding_digest
  THEN RETURN jsonb_build_object('status','not_committed','runId',parent.id,
      'attempt',parent.attempt,'version',parent.version,
      'requestDigest',selected_request_digest,'attestationId',receipt.id); END IF;
  RETURN jsonb_build_object('status','unknown');
END $$"""

RUN_GUARD = replace_once(OLD_SQL[3],
    """              IF NEW.renderer_version=10 THEN
                RAISE EXCEPTION 'ai_promotion_budget_renderer_unpublished';
              END IF;""",
    """              IF NEW.renderer_version=10 THEN
                IF session_user<>'teruisi_ai_budget_v10_attestor'
                   OR OLD.status<>'paused' OR OLD.error_code<>'renderer_unpublished'
                   OR OLD.progress_json::jsonb->>'stage' IS DISTINCT FROM
                      'staged_unpublished'
                   OR NEW.attempt<>OLD.attempt OR NEW.stored_bytes<>OLD.stored_bytes
                   OR NEW.manifest_json IS DISTINCT FROM OLD.manifest_json
                THEN RAISE EXCEPTION 'ai_budget_v10_ready_direct_write_denied'; END IF;
                PERFORM public.ai_budget_v10_ready_requirements(
                  NEW.id,NEW.progress_json::jsonb,'paused');
              END IF;""")
RUN_GUARD = replace_once(RUN_GUARD,
    "IF NEW.renderer_version IN (7,9) THEN",
    "IF NEW.renderer_version IN (7,9,10) THEN")
COMPLETE_GUARD = replace_once(OLD_SQL[4],
    """            IF parent.renderer_version=10 THEN
              RAISE EXCEPTION 'ai_promotion_budget_renderer_unpublished';
            END IF;""",
    """            IF parent.renderer_version=10 THEN
              PERFORM public.ai_budget_v10_ready_requirements(
                parent.id,parent.progress_json::jsonb,'ready');
            END IF;""")
# The deferred trigger fires after the SECURITY DEFINER publisher returns.
# Its invoker is then the NOLOGIN attestor, which has no table SELECT. Keep
# this single fixed-target, read-only trigger body unchanged and execute it as
# the protected database owner; do not grant the attestor broad table access.
COMPLETE_GUARD = replace_once(COMPLETE_GUARD,
    "RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$",
    "RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$")
NEW_SQL = (*OLD_SQL[:3], RUN_GUARD, COMPLETE_GUARD)


def _verify_predecessor(cursor):
    attestation.verify_catalog(cursor)
    canonical_migration = import_module(
        "ai_assistant.migrations.0047_business_v4_sealer_replay_progress")
    cursor.execute("SELECT p.prosrc,p.prosecdef,p.proconfig,p.proowner,"
        "(SELECT c.relowner FROM pg_catalog.pg_class c WHERE "
        "c.oid='public.ai_business_promotion_budget_v10_attestations'::regclass) "
        "FROM pg_catalog.pg_proc p "
        "WHERE p.oid=to_regprocedure(%s)", [canonical_migration.CANONICAL])
    canonical_row = cursor.fetchone()
    if (canonical_row is None or canonical_row[0] !=
            canonical_migration.CANONICAL_SQL.split("$$", 2)[1]
            or canonical_row[1] is not False
            or {part.replace(" ", "") for part in (canonical_row[2] or [])}
                != {"search_path=pg_catalog"}
            or canonical_row[3] != canonical_row[4]):
        raise RuntimeError("0058 requires frozen canonical JSON serializer")
    for definition in OLD_SQL:
        name = definition.split("FUNCTION ", 1)[1].split("(", 1)[0].removeprefix("public.")
        cursor.execute("SELECT p.prosrc FROM pg_catalog.pg_proc p "
            "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='public' AND p.proname=%s", [name])
        rows = cursor.fetchall()
        if len(rows) != 1 or rows[0][0] != definition.split("$$", 2)[1]:
            raise RuntimeError("0058 requires exact closed 0054 file guards")


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        _verify_predecessor(cursor)
        file_signatures = (
            "public.ai_business_file_chunk_guard()",
            "public.ai_business_volume_chunk_guard()",
            "public.ai_business_volume_manifest_check(text,integer,text,boolean,text)",
            "public.ai_business_files_guard()",
            "public.ai_business_volume_complete_guard()",
        )
        original_identity = []
        for signature in file_signatures:
            cursor.execute("SELECT p.oid,p.proacl::text,p.proowner FROM "
                "pg_catalog.pg_proc p WHERE p.oid=to_regprocedure(%s)",
                [signature])
            original_identity.append(cursor.fetchone())
        for definition in (READY_REQUIREMENTS, PUBLISH, OUTCOME):
            cursor.execute(definition)
        for signature in (READY_SIGNATURE, PUBLISH_SIGNATURE, OUTCOME_SIGNATURE):
            cursor.execute("REVOKE ALL ON FUNCTION " + signature + " FROM PUBLIC")
            cursor.execute("GRANT EXECUTE ON FUNCTION " + signature + " TO " + ROLE)
        for definition in (RUN_GUARD, COMPLETE_GUARD):
            cursor.execute(definition)
        for signature, frozen in zip(file_signatures, original_identity):
            cursor.execute("SELECT p.oid,p.proacl::text,p.proowner FROM "
                "pg_catalog.pg_proc p WHERE p.oid=to_regprocedure(%s)",
                [signature])
            if cursor.fetchone() != frozen:
                raise RuntimeError("0058 changed old file guard OID, ACL or owner")


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT EXISTS(SELECT 1 FROM public.ai_business_file_runs "
            "WHERE renderer_version=10 AND status='ready')")
        if cursor.fetchone()[0]:
            raise RuntimeError("0058 cannot reverse a published renderer-10 file")
        for definition in (OLD_SQL[3], OLD_SQL[4]):
            cursor.execute(definition)
        for signature in (OUTCOME_SIGNATURE, PUBLISH_SIGNATURE, READY_SIGNATURE):
            cursor.execute("DROP FUNCTION " + signature)


def verify_catalog(cursor):
    """Pin the dormant publisher and old file-guard identities."""
    attestation.verify_catalog(cursor)
    for index, (signature, definition) in enumerate(zip((
            "public.ai_business_file_chunk_guard()",
            "public.ai_business_volume_chunk_guard()",
            "public.ai_business_volume_manifest_check(text,integer,text,boolean,text)",
            "public.ai_business_files_guard()",
            "public.ai_business_volume_complete_guard()"), NEW_SQL)):
        cursor.execute("SELECT p.prosrc,p.prosecdef,p.proconfig,l.lanname,"
            "p.proowner=(SELECT c.relowner FROM pg_catalog.pg_class c WHERE "
            "c.oid='public.ai_business_promotion_budget_v10_attestations'::regclass) "
            "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_language l "
            "ON l.oid=p.prolang WHERE p.oid=to_regprocedure(%s)", [signature])
        row = cursor.fetchone()
        if (row is None or row[0] != definition.split("$$", 2)[1]
                or row[1] is not (index == 4) or row[3] != "plpgsql"
                or {part.replace(" ", "") for part in (row[2] or [])}
                    != {"search_path=pg_catalog,public"}
                or index == 4 and row[4] is not True):
            raise RuntimeError("budget v10 publish file guard drift")
    for signature, definition in ((READY_SIGNATURE, READY_REQUIREMENTS),
                                  (PUBLISH_SIGNATURE, PUBLISH),
                                  (OUTCOME_SIGNATURE, OUTCOME)):
        cursor.execute("SELECT p.prosrc,p.prosecdef,p.proconfig,l.lanname,"
            "pg_catalog.pg_get_userbyid(p.proowner) FROM pg_catalog.pg_proc p "
            "JOIN pg_catalog.pg_language l ON l.oid=p.prolang "
            "WHERE p.oid=to_regprocedure(%s)", [signature])
        row = cursor.fetchone()
        if (row is None or row[0] != definition.split("$$", 2)[1]
                or row[1] is not True or row[3] != "plpgsql"
                or {part.replace(" ", "") for part in (row[2] or [])}
                    != {"search_path=pg_catalog,public"}
                or row[4] == ROLE):
            raise RuntimeError("budget v10 publish function drift")
        cursor.execute("SELECT CASE WHEN acl.grantee=p.proowner THEN 'OWNER' "
            "WHEN acl.grantee=0 THEN 'PUBLIC' ELSE grantee.rolname END,"
            "acl.privilege_type,acl.is_grantable FROM pg_catalog.pg_proc p "
            "CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(p.proacl,"
            "pg_catalog.acldefault('f',p.proowner))) acl "
            "LEFT JOIN pg_catalog.pg_roles grantee ON grantee.oid=acl.grantee "
            "WHERE p.oid=to_regprocedure(%s)", [signature])
        if set(cursor.fetchall()) != {("OWNER", "EXECUTE", False),
                (ROLE, "EXECUTE", False)}:
            raise RuntimeError("budget v10 publish function ACL drift")


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0057_business_promotion_budget_v10_attestation")]
    operations = [migrations.RunPython(install, uninstall)]
