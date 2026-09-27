"""0066 SQL candidate, dependent on the exact market 0065 migration.

Version 11 is admitted to the same bounded volume storage only for an approved
budget report and a complete staged_unpublished proof. Ready is always denied.
This SQL checks immutable bytes and JSON bindings, not native XLSX semantics.
"""
from importlib import import_module


predecessor = import_module(
    "ai_assistant.migrations.0058_business_promotion_budget_v10_publish_gate")
OLD_SQL = predecessor.NEW_SQL
replace_once = predecessor.replace_once
STAGE_SIGNATURE = "public.ai_budget_v11_stage_requirements(text,integer,text,text)"


STAGE_REQUIREMENTS = r"""CREATE FUNCTION public.ai_budget_v11_stage_requirements(
  selected_run text, selected_attempt integer, selected_binding text,
  selected_compact text)
RETURNS void LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE parent public.ai_business_file_runs%ROWTYPE;
  report public.ai_report_runs%ROWTYPE; snapshot jsonb;
  compact jsonb; full_manifest jsonb; slim jsonb; budget jsonb;
  volume jsonb; payload jsonb; part jsonb; compressed jsonb;
  html_file jsonb; xlsx_file jsonb; payloads jsonb:='[]'::jsonb;
  file_bindings jsonb:='[]'::jsonb; raw_manifest bytea;
  count integer; table_count integer; i integer; j integer;
  present boolean; plan_digest text; actual_digest text;
BEGIN
  IF session_user<>'teruisi_ai_writer' OR selected_run IS NULL
     OR selected_run !~ '^[A-Za-z0-9_-]{1,160}$'
     OR selected_attempt NOT BETWEEN 1 AND 5
     OR selected_binding !~ '^[0-9a-f]{64}$'
  THEN RAISE EXCEPTION 'ai_budget_v11_stage_writer_unavailable'; END IF;
  SELECT * INTO parent FROM public.ai_business_file_runs item
    WHERE item.id=selected_run FOR SHARE;
  IF parent.id IS NULL OR parent.renderer_version<>11 OR parent.draft
     OR parent.binding_digest IS DISTINCT FROM selected_binding
     OR parent.attempt IS DISTINCT FROM selected_attempt
     OR parent.status NOT IN ('building','paused')
     OR parent.stored_bytes<1 OR selected_compact='{}'
  THEN RAISE EXCEPTION 'ai_budget_v11_stage_parent_invalid'; END IF;
  PERFORM public.ai_business_promotion_budget_parent_requirements(
    parent.report_id,parent.owner_email,parent.scope_json);
  SELECT * INTO report FROM public.ai_report_runs item
    WHERE item.id=parent.report_id FOR SHARE;
  IF report.id IS NULL OR report.owner_email IS DISTINCT FROM parent.owner_email
     OR report.scope_json IS DISTINCT FROM parent.scope_json
     OR NOT EXISTS (SELECT 1 FROM public.access_control_users actor
       WHERE actor.email=parent.owner_email AND actor.role='admin'
         AND actor.status='active' AND actor.scope IS NULL)
  THEN RAISE EXCEPTION 'ai_budget_v11_stage_owner_invalid'; END IF;
  snapshot:=report.snapshot_json::jsonb;
  PERFORM public.ai_business_volume_manifest_check(parent.id,selected_attempt,
    selected_binding,false,selected_compact);
  compact:=selected_compact::jsonb;
  IF compact->>'rendererVersion' IS DISTINCT FROM '11'
     OR compact->>'attempt' IS DISTINCT FROM selected_attempt::text
     OR compact->>'bindingDigest' IS DISTINCT FROM selected_binding
  THEN RAISE EXCEPTION 'ai_budget_v11_stage_compact_invalid'; END IF;
  SELECT string_agg(chunk.content,''::bytea ORDER BY chunk.sequence)
    INTO raw_manifest FROM public.ai_business_volume_chunks chunk
    WHERE chunk.run_id=parent.id AND chunk.attempt=selected_attempt
      AND chunk.volume_index=0 AND chunk.format='json';
  IF raw_manifest IS NULL OR octet_length(raw_manifest)>16777216
     OR encode(sha256(raw_manifest),'hex') IS DISTINCT FROM
        compact->'manifestFile'->>'sha256'
  THEN RAISE EXCEPTION 'ai_budget_v11_stage_full_json_invalid'; END IF;
  full_manifest:=convert_from(raw_manifest,'UTF8')::jsonb;
  count:=public.ai_business_volume_uint(
    (compact->'volumeCount')::json,1,100);
  IF full_manifest->>'schemaVersion' IS DISTINCT FROM 'business-volume-files-v1'
     OR full_manifest->>'status' IS DISTINCT FROM 'complete'
     OR full_manifest->>'rendererVersion' IS DISTINCT FROM '11'
     OR full_manifest->>'reportId' IS DISTINCT FROM report.id
     OR full_manifest->>'evidenceDigest' IS DISTINCT FROM snapshot->>'sealedDigest'
     OR jsonb_typeof(full_manifest->'volumes') IS DISTINCT FROM 'array'
     OR jsonb_array_length(full_manifest->'volumes')<>count
     OR full_manifest->>'manifestDigest' IS DISTINCT FROM encode(sha256(
       convert_to(public.ai_v4_replay_canonical(
         full_manifest-'manifestDigest'),'UTF8')),'hex')
  THEN RAISE EXCEPTION 'ai_budget_v11_stage_full_binding_invalid'; END IF;
  slim:=full_manifest->'promotionSlimProof';
  budget:=full_manifest->'promotionBudgetProof';
  present:=report.budget_plan_id IS NOT NULL;
  plan_digest:=snapshot#>>'{budgetRef,planDigest}';
  IF jsonb_typeof(slim) IS DISTINCT FROM 'object'
     OR (slim-ARRAY['schemaVersion','rendererVersion',
       'sourceBudgetProofDigest','htmlPayloadVersion','browserRequirements',
       'volumePayloadDigest','fileBindingsDigest','volumeCount',
       'candidateOnly','publicationStatus','proofDigest'])<>'{}'::jsonb
     OR slim->>'schemaVersion' IS DISTINCT FROM
       'business-promotion-budget-v11-slim-proof-v1'
     OR slim->>'rendererVersion' IS DISTINCT FROM '11'
     OR slim->>'htmlPayloadVersion' IS DISTINCT FROM '2'
     OR slim->'browserRequirements' IS DISTINCT FROM
       '["DecompressionStream:gzip","SubtleCrypto:SHA-256"]'::jsonb
     OR slim->'candidateOnly' IS DISTINCT FROM 'true'::jsonb
     OR slim->>'publicationStatus' IS DISTINCT FROM 'unpublished'
     OR slim->>'volumeCount' IS DISTINCT FROM count::text
     OR slim->>'sourceBudgetProofDigest' IS DISTINCT FROM
       budget->>'proofDigest'
     OR slim->>'proofDigest' IS DISTINCT FROM encode(sha256(
       convert_to(public.ai_v4_replay_canonical(slim-'proofDigest'),
         'UTF8')),'hex')
     OR budget->>'schemaVersion' IS DISTINCT FROM
       'business-promotion-budget-renderer10-candidate-v1'
     OR budget->>'rendererVersion' IS DISTINCT FROM '10'
     OR budget->'candidateOnly' IS DISTINCT FROM 'true'::jsonb
     OR budget->>'proofDigest' IS DISTINCT FROM encode(sha256(
       convert_to(public.ai_v4_replay_canonical(budget-'proofDigest'),
         'UTF8')),'hex')
     OR full_manifest->'promotionTrialProof'->>'proofDigest' IS DISTINCT FROM
       budget->>'promotionTrialProofDigest'
     OR full_manifest->'promotionFileProof'->>'contentDtoDigest' IS DISTINCT FROM
       budget->>'approvedContentDigest'
     OR full_manifest->'promotionFileProof'->>'humanReviewDigest' IS DISTINCT FROM
       budget->>'humanReviewDigest'
     OR (present AND (full_manifest->>'budgetPlanDigest' IS DISTINCT FROM plan_digest
       OR budget->>'budgetPlanDigest' IS DISTINCT FROM plan_digest
       OR budget->>'status' IS DISTINCT FROM 'reconciled_fixed_budget_candidate'))
     OR (NOT present AND (full_manifest ? 'budgetPlanDigest'
       OR budget->>'status' IS DISTINCT FROM 'missing_fixed_budget'))
  THEN RAISE EXCEPTION 'ai_budget_v11_stage_proof_invalid'; END IF;
  FOR i IN 0..count-1 LOOP
    volume:=full_manifest->'volumes'->i;
    payload:=volume->'htmlPayload';
    html_file:=volume->'files'->'html';
    xlsx_file:=volume->'files'->'xlsx';
    IF volume->>'volumeIndex' IS DISTINCT FROM (i+1)::text
       OR html_file->>'sha256' IS DISTINCT FROM
         compact->'files'->(i*2)->>'sha256'
       OR html_file->>'bytes' IS DISTINCT FROM
         compact->'files'->(i*2)->>'bytes'
       OR xlsx_file->>'sha256' IS DISTINCT FROM
         compact->'files'->(i*2+1)->>'sha256'
       OR xlsx_file->>'bytes' IS DISTINCT FROM
         compact->'files'->(i*2+1)->>'bytes'
       OR jsonb_typeof(payload) IS DISTINCT FROM 'object'
       OR (payload-ARRAY['schemaVersion','htmlPayloadVersion',
         'browserRequirements','tables','proofDigest'])<>'{}'::jsonb
       OR payload->>'schemaVersion' IS DISTINCT FROM
         'business-html-compressed-rows-v1'
       OR payload->>'htmlPayloadVersion' IS DISTINCT FROM '2'
       OR payload->'browserRequirements' IS DISTINCT FROM
         '["DecompressionStream:gzip","SubtleCrypto:SHA-256"]'::jsonb
       OR payload->>'proofDigest' IS DISTINCT FROM encode(sha256(
         convert_to(public.ai_v4_replay_canonical(payload-'proofDigest'),
           'UTF8')),'hex')
       OR jsonb_typeof(payload->'tables') IS DISTINCT FROM 'array'
       OR jsonb_typeof(volume->'tables') IS DISTINCT FROM 'array'
       OR jsonb_array_length(payload->'tables') IS DISTINCT FROM
         jsonb_array_length(volume->'tables')
    THEN RAISE EXCEPTION 'ai_budget_v11_stage_volume_invalid'; END IF;
    table_count:=jsonb_array_length(payload->'tables');
    IF table_count>0 THEN
      FOR j IN 0..table_count-1 LOOP
        compressed:=payload->'tables'->j;
        part:=volume->'tables'->j;
        IF jsonb_typeof(compressed) IS DISTINCT FROM 'object'
           OR (compressed-ARRAY['key','rowCount','rowDigest',
             'rowsNdjsonBytes','rowsGzipBytes','rowsGzipSha256'])<>'{}'::jsonb
           OR compressed->>'key' IS DISTINCT FROM part->>'fragmentKey'
           OR compressed->>'rowCount' IS DISTINCT FROM part->>'rowLimit'
           OR compressed->>'rowDigest' IS DISTINCT FROM part->>'rowDigest'
           OR compressed->>'rowsGzipSha256' !~ '^[0-9a-f]{64}$'
           OR jsonb_typeof(compressed->'rowsNdjsonBytes') IS DISTINCT FROM 'number'
           OR jsonb_typeof(compressed->'rowsGzipBytes') IS DISTINCT FROM 'number'
           OR (compressed->>'rowsNdjsonBytes')::bigint NOT BETWEEN 0 AND 268435456
           OR (compressed->>'rowsGzipBytes')::bigint NOT BETWEEN 1 AND 268435456
           OR (compressed->>'rowsGzipBytes')::bigint>
             (html_file->>'bytes')::bigint
        THEN RAISE EXCEPTION 'ai_budget_v11_stage_table_invalid'; END IF;
        PERFORM public.ai_business_volume_uint(
          (compressed->'rowsNdjsonBytes')::json,0,268435456);
        PERFORM public.ai_business_volume_uint(
          (compressed->'rowsGzipBytes')::json,1,268435456);
      END LOOP;
    END IF;
    payloads:=payloads||jsonb_build_array(payload);
    file_bindings:=file_bindings||jsonb_build_array(jsonb_build_object(
      'volumeIndex',i+1,'htmlSha256',html_file->>'sha256',
      'htmlBytes',(html_file->>'bytes')::bigint,
      'xlsxSha256',xlsx_file->>'sha256',
      'xlsxBytes',(xlsx_file->>'bytes')::bigint));
  END LOOP;
  IF slim->>'volumePayloadDigest' IS DISTINCT FROM encode(sha256(
       convert_to(public.ai_v4_replay_canonical(payloads),'UTF8')),'hex')
     OR slim->>'fileBindingsDigest' IS DISTINCT FROM encode(sha256(
       convert_to(public.ai_v4_replay_canonical(file_bindings),'UTF8')),'hex')
  THEN RAISE EXCEPTION 'ai_budget_v11_stage_payload_digest_invalid'; END IF;
END $$"""


VOLUME_CHUNK_GUARD = replace_once(OLD_SQL[1],
    "parent.renderer_version NOT IN (4,6,7,9,10)",
    "parent.renderer_version NOT IN (4,6,7,9,10,11)")
MANIFEST_GUARD = replace_once(OLD_SQL[2],
    "parent_renderer NOT IN (4,6,7,9,10)",
    "parent_renderer NOT IN (4,6,7,9,10,11)")
MANIFEST_GUARD = replace_once(MANIFEST_GUARD,
    "public.ai_business_volume_uint(value->'rendererVersion',4,10)",
    "public.ai_business_volume_uint(value->'rendererVersion',4,11)")

RUN_GUARD = replace_once(OLD_SQL[3],
    "NEW.renderer_version IN (4,5,6,7,9,10) AND",
    "NEW.renderer_version IN (4,5,6,7,9,10,11) AND")
RUN_GUARD = replace_once(RUN_GUARD,
    """            IF NEW.renderer_version=10 THEN
              IF NEW.draft OR NEW.binding_digest !~ '^[0-9a-f]{64}$'
              THEN RAISE EXCEPTION 'ai_promotion_budget_initial_invalid'; END IF;
              PERFORM public.ai_business_promotion_budget_parent_requirements(
                NEW.report_id,NEW.owner_email,NEW.scope_json);
            END IF;""",
    """            IF NEW.renderer_version=10 THEN
              IF NEW.draft OR NEW.binding_digest !~ '^[0-9a-f]{64}$'
              THEN RAISE EXCEPTION 'ai_promotion_budget_initial_invalid'; END IF;
              PERFORM public.ai_business_promotion_budget_parent_requirements(
                NEW.report_id,NEW.owner_email,NEW.scope_json);
            END IF;
            IF NEW.renderer_version=11 THEN
              IF NEW.draft OR NEW.binding_digest !~ '^[0-9a-f]{64}$'
                 OR NEW.error_code<>'' OR NEW.progress_json<>'{}'
              THEN RAISE EXCEPTION 'ai_budget_v11_initial_invalid'; END IF;
              PERFORM public.ai_business_promotion_budget_parent_requirements(
                NEW.report_id,NEW.owner_email,NEW.scope_json);
            END IF;""")
RUN_GUARD = replace_once(RUN_GUARD,
    "IF OLD.renderer_version=10 OR NEW.renderer_version=10 THEN",
    "IF OLD.renderer_version IN (10,11) OR NEW.renderer_version IN (10,11) THEN")
RUN_GUARD = replace_once(RUN_GUARD,
    "IF NEW.renderer_version IN (4,6,7,9,10) THEN",
    "IF NEW.renderer_version IN (4,6,7,9,10,11) THEN")
RUN_GUARD = replace_once(RUN_GUARD,
    """            IF NEW.status='ready' THEN
              IF NEW.renderer_version=10 THEN""",
    """            IF NEW.renderer_version=11 AND
               (NEW.error_code='renderer_unpublished' OR
                NEW.progress_json::jsonb->>'stage'='staged_unpublished') THEN
              IF NEW.status<>'paused' OR NEW.error_code<>'renderer_unpublished'
                 OR NEW.progress_json::jsonb->>'stage' IS DISTINCT FROM
                    'staged_unpublished' OR OLD.status NOT IN ('building','paused')
              THEN RAISE EXCEPTION 'ai_budget_v11_stage_state_invalid'; END IF;
              PERFORM public.ai_budget_v11_stage_requirements(
                NEW.id,NEW.attempt,NEW.binding_digest,NEW.manifest_json);
            END IF;
            IF NEW.status='ready' THEN
              IF NEW.renderer_version=11 THEN
                RAISE EXCEPTION 'ai_budget_v11_ready_unpublished';
              END IF;
              IF NEW.renderer_version=10 THEN""")

COMPLETE_GUARD = replace_once(OLD_SQL[4],
    "parent.renderer_version NOT IN (4,6,7,9,10)",
    "parent.renderer_version NOT IN (4,6,7,9,10,11)")
COMPLETE_GUARD = replace_once(COMPLETE_GUARD,
    """          IF parent.status='ready' THEN
            IF parent.renderer_version=10 THEN""",
    """          IF parent.renderer_version=11 AND parent.status='paused' AND
             (parent.error_code='renderer_unpublished' OR
              parent.progress_json::jsonb->>'stage'='staged_unpublished') THEN
            IF parent.error_code<>'renderer_unpublished' OR
               parent.progress_json::jsonb->>'stage' IS DISTINCT FROM
                 'staged_unpublished'
            THEN RAISE EXCEPTION 'ai_budget_v11_stage_state_invalid'; END IF;
            PERFORM public.ai_budget_v11_stage_requirements(
              parent.id,parent.attempt,parent.binding_digest,
              parent.manifest_json);
          END IF;
          IF parent.status='ready' THEN
            IF parent.renderer_version=11 THEN
              RAISE EXCEPTION 'ai_budget_v11_ready_unpublished';
            END IF;
            IF parent.renderer_version=10 THEN""")

NEW_SQL = (OLD_SQL[0], VOLUME_CHUNK_GUARD, MANIFEST_GUARD,
    RUN_GUARD, COMPLETE_GUARD)

FILE_SIGNATURES = (
    "public.ai_business_file_chunk_guard()",
    "public.ai_business_volume_chunk_guard()",
    "public.ai_business_volume_manifest_check(text,integer,text,boolean,text)",
    "public.ai_business_files_guard()",
    "public.ai_business_volume_complete_guard()",
)
OLD_VERSIONS = "1,2,3,4,5,6,7,9,10"
NEW_VERSIONS = OLD_VERSIONS + ",11"


def _catalog(cursor, signature):
    cursor.execute("SELECT p.oid,p.proacl::text,p.proowner,p.prosecdef,p.prosrc "
        "FROM pg_catalog.pg_proc p WHERE p.oid=to_regprocedure(%s)",
        [signature])
    return cursor.fetchone()


def _predecessor(cursor):
    frozen = []
    for signature, definition in zip(FILE_SIGNATURES, OLD_SQL):
        item = _catalog(cursor, signature)
        if item is None or item[4] != definition.split("$$", 2)[1]:
            raise RuntimeError("0066 requires exact frozen 0058 file guard")
        frozen.append(item[:4])
    canonical = import_module(
        "ai_assistant.migrations.0047_business_v4_sealer_replay_progress")
    item = _catalog(cursor, canonical.CANONICAL)
    if item is None or item[4] != canonical.CANONICAL_SQL.split("$$", 2)[1]:
        raise RuntimeError("0066 requires frozen canonical JSON serializer")
    return frozen


def install(apps, schema_editor):
    """Called by the 0066 migration after its exact 0065 dependency."""
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        frozen = _predecessor(cursor)
    import_module("ai_assistant.migrations.0017_business_file_renderer").change_constraint(
        schema_editor, NEW_VERSIONS)
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(STAGE_REQUIREMENTS)
        cursor.execute("REVOKE ALL ON FUNCTION " + STAGE_SIGNATURE + " FROM PUBLIC")
        cursor.execute("DO $$ BEGIN IF to_regrole('teruisi_ai_writer') IS NOT NULL "
            "THEN GRANT EXECUTE ON FUNCTION " + STAGE_SIGNATURE +
            " TO teruisi_ai_writer; END IF; END $$")
        for definition in NEW_SQL[1:]:
            cursor.execute(definition)
        for signature, expected in zip(FILE_SIGNATURES, frozen):
            current = _catalog(cursor, signature)
            if current is None or current[:4] != expected:
                raise RuntimeError("0066 changed old file guard OID, ACL or owner")


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    if apps.get_model("ai_assistant", "AiBusinessFileRun").objects.filter(
            renderer_version=11).exists():
        raise RuntimeError("存在 renderer 11 暂存任务，禁止逆迁移")
    with schema_editor.connection.cursor() as cursor:
        for definition in OLD_SQL[1:]:
            cursor.execute(definition)
        cursor.execute("DROP FUNCTION " + STAGE_SIGNATURE)
    import_module("ai_assistant.migrations.0017_business_file_renderer").change_constraint(
        schema_editor, OLD_VERSIONS)


def verify_catalog(cursor):
    """Pin the new narrow function; older guards are checked by file health."""
    cursor.execute("SELECT p.prosrc,p.prosecdef,p.proconfig,l.lanname,"
        "pg_catalog.pg_get_userbyid(p.proowner),"
        "ARRAY(SELECT (CASE WHEN a.grantee=p.proowner THEN 'OWNER' "
        "WHEN a.grantee=0 THEN 'PUBLIC' ELSE r.rolname END)||':'||"
        "a.privilege_type FROM pg_catalog.aclexplode(COALESCE(p.proacl,"
        "pg_catalog.acldefault('f',p.proowner))) a LEFT JOIN pg_catalog.pg_roles r "
        "ON r.oid=a.grantee ORDER BY a.grantee,a.privilege_type) "
        "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_language l ON l.oid=p.prolang "
        "WHERE p.oid=to_regprocedure(%s)", [STAGE_SIGNATURE])
    row = cursor.fetchone()
    if (row is None or row[0] != STAGE_REQUIREMENTS.split('$$', 2)[1]
            or row[1] is not True or row[3] != 'plpgsql'
            or {part.replace(' ', '') for part in (row[2] or [])}
                != {'search_path=pg_catalog,public'}
            or row[4] in {'teruisi_ai_writer', 'teruisi_ai_reader'}
            or set(row[5]) != {'OWNER:EXECUTE', 'teruisi_ai_writer:EXECUTE'}):
        raise RuntimeError('AI budget v11 stage function drift')
