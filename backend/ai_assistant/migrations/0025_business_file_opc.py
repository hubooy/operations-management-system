"""OPC-compatible renderers 5/6; existing rows, bytes and attempts stay fixed.

The five version-sensitive functions retain signatures, triggers and grants.
The exact predecessor bodies below make reversal independent of current code.
"""
from importlib import import_module
from django.db import migrations


def _updated(sql):
    sql = sql.replace("renderer_version IN (1,2,3)", "renderer_version IN (1,2,3,5)")
    sql = sql.replace("parent.renderer_version<>4", "parent.renderer_version NOT IN (4,6)")
    sql = sql.replace("NEW.renderer_version=4 AND", "NEW.renderer_version IN (4,5,6) AND")
    sql = sql.replace("IF NEW.renderer_version=4 THEN", "IF NEW.renderer_version IN (4,6) THEN")
    if "FUNCTION ai_business_volume_manifest_check(" in sql:
        sql = sql.replace("groups_count bigint;", "groups_count bigint; parent_renderer integer;")
        sql = sql.replace("value:=raw::json;", """SELECT renderer_version INTO parent_renderer FROM public.ai_business_file_runs WHERE id=target;
          IF NOT FOUND OR parent_renderer NOT IN (4,6) THEN RAISE EXCEPTION 'ai_volume_parent_renderer'; END IF;
          value:=raw::json;""")
        sql = sql.replace("public.ai_business_volume_uint(value->'rendererVersion',4,4)<>4",
            "public.ai_business_volume_uint(value->'rendererVersion',4,6)<>parent_renderer")
    if "FUNCTION ai_business_files_guard(" in sql:
        marker = "          IF NEW.status='ready' THEN\n            value := NEW.manifest_json::jsonb;"
        assert sql.count(marker) == 1
        sql = sql.replace(marker, """          IF NEW.renderer_version=5 AND NEW.status='ready' THEN
            IF public.ai_business_volume_uint(NEW.manifest_json::json->'rendererVersion',5,5)<>5
              OR json_typeof(NEW.manifest_json::json->'bindingDigest') IS DISTINCT FROM 'string'
              OR NEW.manifest_json::json->>'bindingDigest' IS DISTINCT FROM NEW.binding_digest
              OR json_typeof(NEW.manifest_json::json->'draft') IS DISTINCT FROM 'boolean'
              OR (NEW.manifest_json::json->>'draft')::boolean IS DISTINCT FROM NEW.draft THEN
              RAISE EXCEPTION 'ai_file_opc_manifest_binding';
            END IF;
          END IF;
""" + marker)
    return sql

OLD_SQL = (
    r"""CREATE OR REPLACE FUNCTION ai_business_file_chunk_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM ai_business_file_runs WHERE id=NEW.run_id AND renderer_version IN (1,2,3) AND status='building' AND attempt=NEW.attempt) THEN
            RAISE EXCEPTION 'ai_file_chunk_not_building';
          END IF;
          RETURN NEW;
        END $$""",
    r"""CREATE OR REPLACE FUNCTION ai_business_volume_chunk_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
        DECLARE parent public.ai_business_file_runs%ROWTYPE;
        BEGIN
          SELECT * INTO parent FROM public.ai_business_file_runs WHERE id=NEW.run_id FOR UPDATE;
          IF NOT FOUND OR parent.renderer_version<>4 OR parent.status<>'building' OR parent.attempt<>NEW.attempt THEN
            RAISE EXCEPTION 'ai_volume_chunk_not_building';
          END IF;
          RETURN NEW;
        END $$""",
    r"""CREATE OR REPLACE FUNCTION ai_business_volume_manifest_check(target text, target_attempt integer, target_binding text, target_draft boolean, raw text)
        RETURNS void LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
        DECLARE value json; volumes integer; position integer; groups_count bigint;
        BEGIN
          value:=raw::json;
          IF json_typeof(value) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'ai_volume_manifest_invalid'; END IF;
          IF (SELECT count(*) FROM json_object_keys(value))<>8
             OR (value::jsonb - ARRAY['schemaVersion','rendererVersion','bindingDigest','attempt','draft','volumeCount','files','manifestFile'])<>'{}'::jsonb
             OR value->>'schemaVersion' IS DISTINCT FROM 'business-file-delivery-v2'
             OR json_typeof(value->'bindingDigest') IS DISTINCT FROM 'string'
             OR value->>'bindingDigest' IS DISTINCT FROM target_binding
             OR coalesce(value->>'bindingDigest','') !~ '^[0-9a-f]{64}$'
             OR json_typeof(value->'draft') IS DISTINCT FROM 'boolean'
             OR (value->>'draft')::boolean IS DISTINCT FROM target_draft
             OR json_typeof(value->'files') IS DISTINCT FROM 'array'
          THEN RAISE EXCEPTION 'ai_volume_manifest_invalid'; END IF;
          IF public.ai_business_volume_uint(value->'rendererVersion',4,4)<>4
             OR public.ai_business_volume_uint(value->'attempt',1,5)<>target_attempt THEN RAISE EXCEPTION 'ai_volume_manifest_binding'; END IF;
          volumes:=public.ai_business_volume_uint(value->'volumeCount',1,100);
          IF json_array_length(value->'files')<>2*volumes THEN RAISE EXCEPTION 'ai_volume_files_incomplete'; END IF;
          FOR position IN 1..volumes LOOP
            PERFORM public.ai_business_volume_file_check(target,target_attempt,value->'files'->((position-1)*2),position,'html',268435456);
            PERFORM public.ai_business_volume_file_check(target,target_attempt,value->'files'->((position-1)*2+1),position,'xlsx',268435456);
          END LOOP;
          PERFORM public.ai_business_volume_file_check(target,target_attempt,value->'manifestFile',0,'json',16777216);
          SELECT count(*) INTO groups_count FROM (SELECT volume_index,format FROM public.ai_business_volume_chunks
            WHERE run_id=target AND attempt=target_attempt GROUP BY volume_index,format) AS files;
          IF groups_count<>2*volumes+1 THEN RAISE EXCEPTION 'ai_volume_unlisted_files'; END IF;
        END $$""",
    r"""CREATE OR REPLACE FUNCTION ai_business_files_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
        DECLARE value jsonb; kind text; n bigint; lo bigint; hi bigint; bytes bigint;
        BEGIN
          IF TG_OP='INSERT' THEN
            IF NEW.renderer_version=4 AND (NEW.status<>'queued' OR NEW.version<>1 OR NEW.attempt<>0 OR NEW.stored_bytes<>0 OR NEW.manifest_json<>'{}') THEN
              RAISE EXCEPTION 'ai_volume_initial_state';
            END IF;
            RETURN NEW;
          END IF;
          IF TG_OP='DELETE' THEN RAISE EXCEPTION 'ai_file_delete_denied'; END IF;
          IF NEW.renderer_version=4 THEN
            IF OLD.status IN ('ready','cancelled') OR NEW.version<>OLD.version+1 OR NEW.attempt<OLD.attempt OR NEW.attempt>OLD.attempt+1 THEN
              RAISE EXCEPTION 'ai_file_terminal_or_version';
            END IF;
            SELECT coalesce(sum(size),0) INTO bytes FROM (
              SELECT octet_length(content) AS size FROM public.ai_business_file_chunks WHERE run_id=NEW.id
              UNION ALL SELECT octet_length(content) FROM public.ai_business_volume_chunks WHERE run_id=NEW.id
            ) AS all_attempts;
            IF bytes<>NEW.stored_bytes THEN RAISE EXCEPTION 'ai_file_storage_mismatch'; END IF;
            IF NEW.status='ready' THEN
              IF OLD.status<>'building' THEN RAISE EXCEPTION 'ai_volume_manifest_invalid'; END IF;
              PERFORM public.ai_business_volume_manifest_check(NEW.id,NEW.attempt,NEW.binding_digest,NEW.draft,NEW.manifest_json);
            END IF;
            RETURN NEW;
          END IF;
        
          IF TG_OP='DELETE' THEN RAISE EXCEPTION 'ai_file_delete_denied'; END IF;
          IF OLD.status IN ('ready','cancelled') OR NEW.version<>OLD.version+1 OR NEW.attempt<OLD.attempt OR NEW.attempt>OLD.attempt+1 THEN
            RAISE EXCEPTION 'ai_file_terminal_or_version';
          END IF;
          IF NEW.stored_bytes<>(SELECT coalesce(sum(octet_length(content)),0) FROM ai_business_file_chunks WHERE run_id=NEW.id) THEN
            RAISE EXCEPTION 'ai_file_storage_mismatch';
          END IF;
          IF NEW.status='ready' THEN
            value := NEW.manifest_json::jsonb;
            IF OLD.status<>'building' OR (value->>'schemaVersion') IS DISTINCT FROM 'business-file-delivery-v1' OR (value->>'attempt')::int IS DISTINCT FROM NEW.attempt THEN
              RAISE EXCEPTION 'ai_file_manifest_invalid';
            END IF;
            FOREACH kind IN ARRAY ARRAY['html','xlsx'] LOOP
              SELECT count(*),min(sequence),max(sequence),coalesce(sum(octet_length(content)),0) INTO n,lo,hi,bytes
                FROM ai_business_file_chunks WHERE run_id=NEW.id AND attempt=NEW.attempt AND format=kind;
              IF n=0 OR lo<>1 OR hi<>n OR n IS DISTINCT FROM (value->'files'->kind->>'chunkCount')::bigint
                OR bytes IS DISTINCT FROM (value->'files'->kind->>'bytes')::bigint OR bytes>268435456
                OR coalesce(value->'files'->kind->>'sha256','') !~ '^[0-9a-f]{64}$' THEN
                RAISE EXCEPTION 'ai_file_incomplete_manifest';
              END IF;
            END LOOP;
          END IF;
          RETURN NEW;
 END $$""",
    r"""CREATE OR REPLACE FUNCTION ai_business_volume_complete_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
        DECLARE target text; parent public.ai_business_file_runs%ROWTYPE; bytes bigint;
        BEGIN
          IF TG_TABLE_NAME='ai_business_file_runs' THEN target:=NEW.id; ELSE target:=NEW.run_id; END IF;
          SELECT * INTO parent FROM public.ai_business_file_runs WHERE id=target FOR UPDATE;
          IF NOT FOUND THEN RAISE EXCEPTION 'ai_volume_parent_missing'; END IF;
          IF parent.renderer_version<>4 THEN RETURN NULL; END IF;
          IF EXISTS(SELECT 1 FROM public.ai_business_file_chunks WHERE run_id=target) THEN RAISE EXCEPTION 'ai_volume_legacy_chunks'; END IF;
          SELECT coalesce(sum(octet_length(content)),0) INTO bytes FROM public.ai_business_volume_chunks WHERE run_id=target;
          IF bytes<>parent.stored_bytes OR bytes>1073741824 THEN RAISE EXCEPTION 'ai_volume_storage_mismatch'; END IF;
          IF EXISTS (SELECT 1 FROM public.ai_business_volume_chunks WHERE run_id=target GROUP BY attempt,volume_index,format
            HAVING min(sequence)<>1 OR max(sequence)<>count(*) OR max(attempt)>parent.attempt
              OR sum(octet_length(content))>CASE WHEN volume_index=0 THEN 16777216 ELSE 268435456 END)
          THEN RAISE EXCEPTION 'ai_volume_chunk_chain_invalid'; END IF;
          IF parent.status='ready' THEN
            PERFORM public.ai_business_volume_manifest_check(parent.id,parent.attempt,parent.binding_digest,parent.draft,parent.manifest_json);
          END IF;
          RETURN NULL;
        END $$""",
)

NEW_SQL = tuple(_updated(sql) for sql in OLD_SQL)


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    import_module("ai_assistant.migrations.0017_business_file_renderer").change_constraint(schema_editor, "1,2,3,4,5,6")
    with schema_editor.connection.cursor() as cursor:
        for sql in NEW_SQL:
            cursor.execute(sql)


def uninstall(apps, schema_editor):
    if apps.get_model("ai_assistant", "AiBusinessFileRun").objects.filter(renderer_version__in=(5,6)).exists():
        raise RuntimeError("存在 renderer 5/6 文件任务（含未完成或取消），禁止逆迁移；旧文件不得改版")
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for sql in OLD_SQL:
            cursor.execute(sql)
    import_module("ai_assistant.migrations.0017_business_file_renderer").change_constraint(schema_editor, "1,2,3,4")


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0024_business_screening_runtime")]
    operations = [migrations.RunPython(install, uninstall)]
