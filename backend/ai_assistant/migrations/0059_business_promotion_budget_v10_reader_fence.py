"""Narrow, current-row renderer-10 download fence; no download route.

The attestor can obtain the versioned fence body before 0057 attestation. A
reader may obtain only a small ready receipt after the same body is recomputed
from current owned rows. Arbitrary old 0057/0058 fence digests fail closed.
"""
from importlib import import_module
from django.db import migrations

attestation = import_module(
    "ai_assistant.migrations.0057_business_promotion_budget_v10_attestation")
publication = import_module(
    "ai_assistant.migrations.0058_business_promotion_budget_v10_publish_gate")
READER = "teruisi_ai_reader"
ATTESTOR = attestation.ROLE
BODY_SIGNATURE = "public.ai_budget_v10_download_fence_body(text,text,text,text)"
READ_SIGNATURE = "public.ai_budget_v10_download_receipt(text,text,integer,text)"

BODY = r"""CREATE FUNCTION public.ai_budget_v10_download_fence_body(
  selected_run text, selected_full_digest text,
  selected_budget_proof text, selected_approved_digest text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE parent public.ai_business_file_runs%ROWTYPE;
  report public.ai_report_runs%ROWTYPE;
  flow public.ai_workflow_runs%ROWTYPE;
  evidence public.ai_business_evidence_runs%ROWTYPE;
  budget public.ai_business_budget_plans%ROWTYPE;
  human public.ai_workflow_node_runs%ROWTYPE;
  snapshot jsonb; compact jsonb; node_rows jsonb; job_rows jsonb;
  event_rows jsonb; budget_body jsonb; node_count bigint;
  job_count bigint; event_count bigint; attestation_id text;
BEGIN
  IF session_user NOT IN ('teruisi_ai_budget_v10_attestor','teruisi_ai_reader')
     OR selected_run IS NULL OR selected_run !~ '^[A-Za-z0-9_-]{1,160}$'
     OR selected_full_digest !~ '^[0-9a-f]{64}$'
     OR selected_budget_proof !~ '^[0-9a-f]{64}$'
     OR selected_approved_digest !~ '^[0-9a-f]{64}$'
     OR current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT c.relowner FROM pg_catalog.pg_class c WHERE
        c.oid='public.ai_business_promotion_budget_v10_attestations'::regclass))
  THEN RAISE EXCEPTION 'ai_budget_v10_download_fence_unavailable'; END IF;
  SELECT * INTO parent FROM public.ai_business_file_runs item
    WHERE item.id=selected_run FOR SHARE;
  IF parent.id IS NULL OR parent.renderer_version<>10 OR parent.draft
     OR parent.attempt NOT BETWEEN 1 AND 5
     OR parent.stored_bytes<1
     OR NOT ((parent.status='paused' AND
       parent.error_code='renderer_unpublished' AND
       parent.progress_json::jsonb->>'stage'='staged_unpublished')
       OR (parent.status='ready' AND parent.error_code='' AND
       parent.progress_json::jsonb->>'stage'='ready'))
  THEN RAISE EXCEPTION 'ai_budget_v10_download_file_state_invalid'; END IF;
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
  THEN RAISE EXCEPTION 'ai_budget_v10_download_source_changed'; END IF;
  SELECT * INTO human FROM public.ai_workflow_node_runs item
    WHERE item.run_id=flow.id AND item.node_key='human_review' FOR SHARE;
  SELECT count(*),jsonb_agg(jsonb_build_object(
    'id',item.id,'position',item.position,'nodeKey',item.node_key,
    'status',item.status,'version',item.version,
    'agentJobId',item.agent_job_id,
    'outputSha256',encode(sha256(convert_to(item.output_json,'UTF8')),'hex'))
    ORDER BY item.position) INTO node_count,node_rows
    FROM public.ai_workflow_node_runs item WHERE item.run_id=flow.id;
  SELECT count(*),jsonb_agg(jsonb_build_object(
    'id',item.id,'role',item.workflow_node_key,'status',item.status,
    'version',item.version,'outputSha256',
    encode(sha256(convert_to(item.output_json,'UTF8')),'hex'),
    'providerRoundCount',item.provider_round_count,
    'toolCallCount',item.tool_call_count)
    ORDER BY item.workflow_node_key COLLATE "C")
    INTO job_count,job_rows FROM public.ai_agent_jobs item
    WHERE item.workflow_run_id=flow.id;
  SELECT count(*),jsonb_agg(jsonb_build_object(
    'id',item.id,'actorEmail',item.actor_email,
    'ownerEmail',item.owner_email,'runVersion',item.run_version,
    'fromStatus',item.from_status,'toStatus',item.to_status,
    'eventType',item.event_type,'createdAtUtc',
    to_char(item.created_at AT TIME ZONE 'UTC',
      'YYYY-MM-DD"T"HH24:MI:SS.US')) ORDER BY item.id)
    INTO event_count,event_rows FROM public.ai_workflow_events item
    WHERE item.run_id=flow.id AND item.node_key='human_review'
      AND item.event_type='review_approved';
  IF node_count<>6 OR job_count<>5 OR event_count<>1
     OR human.id IS NULL OR human.status<>'completed'
     OR human.reviewer_email IS DISTINCT FROM parent.owner_email
     OR human.reviewed_at IS NULL OR human.completed_at IS NULL
  THEN RAISE EXCEPTION 'ai_budget_v10_download_approval_changed'; END IF;
  IF report.budget_plan_id IS NOT NULL THEN
    SELECT * INTO budget FROM public.ai_business_budget_plans item
      WHERE item.id=report.budget_plan_id FOR SHARE;
    IF budget.id IS NULL OR budget.owner_email IS DISTINCT FROM parent.owner_email
       OR budget.scope_json IS DISTINCT FROM parent.scope_json
       OR budget.evidence_id IS DISTINCT FROM evidence.id
       OR budget.evidence_version IS DISTINCT FROM evidence.version
       OR budget.plan_digest IS DISTINCT FROM snapshot#>>'{budgetRef,planDigest}'
       OR budget.binding_digest IS DISTINCT FROM snapshot#>>'{budgetRef,bindingDigest}'
    THEN RAISE EXCEPTION 'ai_budget_v10_download_budget_changed'; END IF;
    budget_body:=jsonb_build_object(
      'id',budget.id,'evidenceId',budget.evidence_id,
      'evidenceVersion',budget.evidence_version,
      'planDigest',budget.plan_digest,
      'bindingDigest',budget.binding_digest,
      'planJsonSha256',encode(sha256(convert_to(budget.plan_json,'UTF8')),'hex'),
      'bindingJsonSha256',encode(sha256(convert_to(budget.binding_json,'UTF8')),'hex'));
  ELSE
    budget_body:=NULL;
  END IF;
  compact:=parent.manifest_json::jsonb;
  IF compact->>'rendererVersion' IS DISTINCT FROM '10'
     OR compact->>'attempt' IS DISTINCT FROM parent.attempt::text
     OR compact->>'bindingDigest' IS DISTINCT FROM parent.binding_digest
     OR compact->'manifestFile'->>'sha256' IS NULL
  THEN RAISE EXCEPTION 'ai_budget_v10_download_manifest_changed'; END IF;
  attestation_id:=encode(sha256(convert_to(
    parent.id||':'||parent.attempt::text,'UTF8')),'hex');
  RETURN jsonb_build_object(
    'schemaVersion','business-promotion-budget-v10-download-fence-v1',
    'report',jsonb_build_object(
      'id',report.id,'ownerEmail',report.owner_email,
      'scopeJson',report.scope_json,
      'snapshotSha256',encode(sha256(convert_to(report.snapshot_json,'UTF8')),'hex')),
    'workflow',jsonb_build_object(
      'id',flow.id,'version',flow.version,'status',flow.status,
      'inputSha256',encode(sha256(convert_to(flow.input_json,'UTF8')),'hex'),
      'outputSha256',encode(sha256(convert_to(flow.output_json,'UTF8')),'hex'),
      'graphDigest',flow.graph_digest,'toolPolicyDigest',flow.tool_policy_digest,
      'modelId',flow.model_id,'modelVersion',flow.model_version,
      'nodeRootDigest',encode(sha256(convert_to(
        public.ai_v4_replay_canonical(node_rows),'UTF8')),'hex'),
      'jobRootDigest',encode(sha256(convert_to(
        public.ai_v4_replay_canonical(job_rows),'UTF8')),'hex'),
      'reviewEventDigest',encode(sha256(convert_to(
        public.ai_v4_replay_canonical(event_rows),'UTF8')),'hex')),
    'humanReview',jsonb_build_object(
      'nodeId',human.id,'version',human.version,'status',human.status,
      'reviewerEmail',human.reviewer_email,
      'outputSha256',encode(sha256(convert_to(human.output_json,'UTF8')),'hex'),
      'reviewedAtUtc',to_char(human.reviewed_at AT TIME ZONE 'UTC',
        'YYYY-MM-DD"T"HH24:MI:SS.US'),
      'completedAtUtc',to_char(human.completed_at AT TIME ZONE 'UTC',
        'YYYY-MM-DD"T"HH24:MI:SS.US')),
    'evidence',jsonb_build_object(
      'id',evidence.id,'version',evidence.version,'status',evidence.status,
      'planSha256',encode(sha256(convert_to(evidence.plan_json,'UTF8')),'hex'),
      'stateSha256',encode(sha256(convert_to(evidence.state_json,'UTF8')),'hex'),
      'sealedDigest',snapshot->>'sealedDigest'),
    'budget',budget_body,
    'file',jsonb_build_object(
      'runId',parent.id,'attempt',parent.attempt,
      'bindingDigest',parent.binding_digest,
      'compactSha256',encode(sha256(convert_to(parent.manifest_json,'UTF8')),'hex'),
      'storedBytes',parent.stored_bytes,'attestationId',attestation_id,
      'fullManifestSha256',compact->'manifestFile'->>'sha256',
      'fullManifestDigest',selected_full_digest,
      'budgetProofDigest',selected_budget_proof),
    'approvedContentDigest',selected_approved_digest);
END $$"""

READ = r"""CREATE FUNCTION public.ai_budget_v10_download_receipt(
  selected_run text, selected_owner text, selected_attempt integer,
  selected_binding text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE parent public.ai_business_file_runs%ROWTYPE;
  report public.ai_report_runs%ROWTYPE;
  receipt public.ai_business_promotion_budget_v10_attestations%ROWTYPE;
  progress jsonb; compact jsonb; body jsonb; request_body jsonb;
  fence text; expected_request text;
BEGIN
  IF session_user<>'teruisi_ai_reader'
     OR selected_run IS NULL OR selected_run !~ '^[A-Za-z0-9_-]{1,160}$'
     OR selected_owner IS NULL OR selected_owner<>lower(selected_owner)
     OR octet_length(selected_owner) NOT BETWEEN 3 AND 320
     OR selected_attempt NOT BETWEEN 1 AND 5
     OR selected_binding !~ '^[0-9a-f]{64}$'
     OR current_user IS DISTINCT FROM pg_catalog.pg_get_userbyid(
       (SELECT c.relowner FROM pg_catalog.pg_class c WHERE
        c.oid='public.ai_business_promotion_budget_v10_attestations'::regclass))
  THEN RAISE EXCEPTION 'ai_budget_v10_download_reader_unavailable'; END IF;
  SELECT * INTO parent FROM public.ai_business_file_runs item
    WHERE item.id=selected_run FOR SHARE;
  SELECT * INTO report FROM public.ai_report_runs item
    WHERE item.id=parent.report_id FOR SHARE;
  SELECT * INTO receipt FROM public.ai_business_promotion_budget_v10_attestations item
    WHERE item.run_id=selected_run AND item.attempt=selected_attempt FOR SHARE;
  IF parent.id IS NULL OR report.id IS NULL OR receipt.id IS NULL
     OR parent.renderer_version<>10 OR parent.draft OR parent.status<>'ready'
     OR parent.error_code<>'' OR parent.attempt<>selected_attempt
     OR parent.binding_digest IS DISTINCT FROM selected_binding
     OR parent.owner_email IS DISTINCT FROM selected_owner
     OR report.owner_email IS DISTINCT FROM selected_owner
     OR parent.scope_json<>'null' OR report.scope_json<>'null'
     OR receipt.binding_digest IS DISTINCT FROM parent.binding_digest
     OR receipt.report_id IS DISTINCT FROM report.id
     OR receipt.owner_email IS DISTINCT FROM selected_owner
     OR NOT EXISTS (SELECT 1 FROM public.access_control_users actor
       WHERE actor.email=selected_owner AND actor.role='admin'
         AND actor.status='active' AND actor.scope IS NULL)
  THEN RAISE EXCEPTION 'ai_budget_v10_download_owner_or_ready_invalid'; END IF;
  progress:=parent.progress_json::jsonb;
  compact:=parent.manifest_json::jsonb;
  IF jsonb_typeof(progress) IS DISTINCT FROM 'object'
     OR progress->>'stage' IS DISTINCT FROM 'ready'
     OR progress->>'attempt' IS DISTINCT FROM selected_attempt::text
     OR progress->>'bindingDigest' IS DISTINCT FROM selected_binding
     OR progress->>'attestationId' IS DISTINCT FROM receipt.id
     OR progress->>'attestationSha256' IS DISTINCT FROM receipt.attestation_sha256
     OR progress->>'fullManifestDigest' IS DISTINCT FROM receipt.full_manifest_digest
     OR progress->>'manifestFileSha256' IS DISTINCT FROM receipt.full_manifest_sha256
     OR progress->>'owningVerificationDigest' IS DISTINCT FROM
       receipt.owning_verification_digest
     OR progress->>'publicationFenceDigest' IS DISTINCT FROM
       receipt.publication_fence_digest
     OR progress->>'publishRequestDigest' !~ '^[0-9a-f]{64}$'
     OR compact->>'rendererVersion' IS DISTINCT FROM '10'
     OR compact->>'attempt' IS DISTINCT FROM selected_attempt::text
     OR compact->>'bindingDigest' IS DISTINCT FROM selected_binding
     OR compact->'manifestFile'->>'sha256' IS DISTINCT FROM receipt.full_manifest_sha256
     OR receipt.compact_json_sha256 IS DISTINCT FROM
       encode(sha256(convert_to(parent.manifest_json,'UTF8')),'hex')
     OR receipt.file_descriptors_json::jsonb IS DISTINCT FROM
       jsonb_build_object('files',compact->'files',
         'manifestFile',compact->'manifestFile')
     OR receipt.budget_present IS DISTINCT FROM
       (report.budget_plan_id IS NOT NULL)
  THEN RAISE EXCEPTION 'ai_budget_v10_download_receipt_changed'; END IF;
  IF parent.version<2 THEN RAISE EXCEPTION 'ai_budget_v10_download_version_invalid'; END IF;
  request_body:=jsonb_build_object(
    'schemaVersion','business-budget-v10-publish-request-v1',
    'runId',parent.id,'attempt',parent.attempt,
    'expectedVersion',parent.version-1,
    'attestationId',receipt.id,
    'attestationSha256',receipt.attestation_sha256,
    'bindingDigest',receipt.binding_digest,
    'fullManifestDigest',receipt.full_manifest_digest,
    'fullManifestSha256',receipt.full_manifest_sha256);
  expected_request:=encode(sha256(convert_to(
    public.ai_v4_replay_canonical(request_body),'UTF8')),'hex');
  IF progress->>'publishRequestDigest' IS DISTINCT FROM expected_request
  THEN RAISE EXCEPTION 'ai_budget_v10_download_publish_request_changed'; END IF;
  body:=public.ai_budget_v10_download_fence_body(parent.id,
    receipt.full_manifest_digest,receipt.budget_proof_digest,
    receipt.approved_content_digest);
  fence:=encode(sha256(convert_to(
    public.ai_v4_replay_canonical(body),'UTF8')),'hex');
  IF fence IS DISTINCT FROM receipt.publication_fence_digest
     OR fence IS DISTINCT FROM progress->>'publicationFenceDigest'
  THEN RAISE EXCEPTION 'ai_budget_v10_download_fence_mismatch'; END IF;
  RETURN jsonb_build_object(
    'schemaVersion','business-promotion-budget-v10-download-receipt-v1',
    'runId',parent.id,'reportId',report.id,'ownerEmail',selected_owner,
    'readyVersion',parent.version,'attempt',parent.attempt,
    'bindingDigest',parent.binding_digest,
    'attestationId',receipt.id,
    'attestationSha256',receipt.attestation_sha256,
    'publishRequestDigest',progress->>'publishRequestDigest',
    'publicationFenceDigest',fence,
    'manifestFileSha256',receipt.full_manifest_sha256,
    'fullManifestDigest',receipt.full_manifest_digest,
    'budgetProofDigest',receipt.budget_proof_digest,
    'budgetPresent',receipt.budget_present,
    'budgetPlanDigest',receipt.budget_plan_digest);
END $$"""


def _verify_predecessor(cursor):
    publication.verify_catalog(cursor)
    canonical = import_module(
        "ai_assistant.migrations.0047_business_v4_sealer_replay_progress")
    cursor.execute("SELECT p.prosrc,p.proowner,(SELECT c.relowner FROM pg_catalog.pg_class c "
        "WHERE c.oid='public.ai_business_promotion_budget_v10_attestations'::regclass) "
        "FROM pg_catalog.pg_proc p WHERE p.oid=to_regprocedure(%s)",
        [canonical.CANONICAL])
    row = cursor.fetchone()
    if row is None or row[0] != canonical.CANONICAL_SQL.split("$$", 2)[1] or row[1] != row[2]:
        raise RuntimeError("0059 requires unchanged canonical JSON serializer")


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        _verify_predecessor(cursor)
        cursor.execute("SELECT to_regrole(%s)", [READER])
        if cursor.fetchone()[0] is None:
            raise RuntimeError("0059 requires the exact AI reader role")
        for definition in (BODY, READ):
            cursor.execute(definition)
        for signature in (BODY_SIGNATURE, READ_SIGNATURE):
            cursor.execute("REVOKE ALL ON FUNCTION " + signature + " FROM PUBLIC")
        cursor.execute("GRANT EXECUTE ON FUNCTION " + BODY_SIGNATURE + " TO " + ATTESTOR)
        cursor.execute("GRANT EXECUTE ON FUNCTION " + READ_SIGNATURE + " TO " + READER)


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("DROP FUNCTION " + READ_SIGNATURE)
        cursor.execute("DROP FUNCTION " + BODY_SIGNATURE)


def verify_catalog(cursor):
    publication.verify_catalog(cursor)
    for signature, definition, grantee in (
            (BODY_SIGNATURE, BODY, ATTESTOR),
            (READ_SIGNATURE, READ, READER)):
        cursor.execute("SELECT p.prosrc,p.prosecdef,p.proconfig,l.lanname,"
            "p.proowner=(SELECT c.relowner FROM pg_catalog.pg_class c WHERE "
            "c.oid='public.ai_business_promotion_budget_v10_attestations'::regclass) "
            "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_language l ON l.oid=p.prolang "
            "WHERE p.oid=to_regprocedure(%s)", [signature])
        row = cursor.fetchone()
        if (row is None or row[0] != definition.split("$$", 2)[1]
                or row[1] is not True or row[3] != "plpgsql" or row[4] is not True
                or {part.replace(" ", "") for part in (row[2] or [])}
                    != {"search_path=pg_catalog,public"}):
            raise RuntimeError("budget v10 reader fence function drift")
        cursor.execute("SELECT CASE WHEN acl.grantee=p.proowner THEN 'OWNER' "
            "WHEN acl.grantee=0 THEN 'PUBLIC' ELSE grantee_role.rolname END,"
            "acl.privilege_type,acl.is_grantable FROM pg_catalog.pg_proc p "
            "CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(p.proacl,"
            "pg_catalog.acldefault('f',p.proowner))) acl "
            "LEFT JOIN pg_catalog.pg_roles grantee_role ON "
            "grantee_role.oid=acl.grantee "
            "WHERE p.oid=to_regprocedure(%s)", [signature])
        if set(cursor.fetchall()) != {("OWNER", "EXECUTE", False),
                (grantee, "EXECUTE", False)}:
            raise RuntimeError("budget v10 reader fence ACL drift")
    cursor.execute("SELECT has_table_privilege(%s,%s,'SELECT')",
        [READER, attestation.TABLE])
    if cursor.fetchone() != (False,):
        raise RuntimeError("budget v10 reader obtained attestation table SELECT")


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0058_business_promotion_budget_v10_publish_gate")]
    operations = [migrations.RunPython(install, uninstall)]
