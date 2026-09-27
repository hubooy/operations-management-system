"""Renderer 4 immutable multi-volume ledger; legacy file rows are untouched."""
from importlib import import_module

from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


# This body is the 0016 guard verbatim. Keeping its branch and inverse explicit
# avoids silently changing the renderer 1/2/3 delivery contract.
LEGACY_RUN_GUARD = """
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
"""


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    import_module("ai_assistant.migrations.0017_business_file_renderer").change_constraint(schema_editor, "1,2,3,4")
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("""ALTER TABLE ai_business_volume_chunks ADD CONSTRAINT ai_business_volume_chunk_bound CHECK (
          attempt BETWEEN 1 AND 5 AND sequence BETWEEN 1 AND 512
          AND ((volume_index=0 AND format='json' AND sequence<=32) OR (volume_index BETWEEN 1 AND 100 AND format IN ('html','xlsx')))
          AND octet_length(content) BETWEEN 1 AND 524288 AND content_digest ~ '^[0-9a-f]{64}$'
          AND content_digest=encode(sha256(content),'hex')
        )""")
        cursor.execute("CREATE TRIGGER ai_write_fence BEFORE INSERT OR UPDATE OR DELETE ON ai_business_volume_chunks FOR EACH ROW EXECUTE FUNCTION ai_runtime_write_fence()")
        cursor.execute("CREATE TRIGGER ai_immutable_evidence BEFORE UPDATE OR DELETE ON ai_business_volume_chunks FOR EACH ROW EXECUTE FUNCTION ai_immutable_record_guard()")
        cursor.execute("""CREATE OR REPLACE FUNCTION ai_business_file_chunk_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM ai_business_file_runs WHERE id=NEW.run_id AND renderer_version IN (1,2,3) AND status='building' AND attempt=NEW.attempt) THEN
            RAISE EXCEPTION 'ai_file_chunk_not_building';
          END IF;
          RETURN NEW;
        END $$""")
        cursor.execute("""CREATE FUNCTION ai_business_volume_chunk_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
        DECLARE parent public.ai_business_file_runs%ROWTYPE;
        BEGIN
          SELECT * INTO parent FROM public.ai_business_file_runs WHERE id=NEW.run_id FOR UPDATE;
          IF NOT FOUND OR parent.renderer_version<>4 OR parent.status<>'building' OR parent.attempt<>NEW.attempt THEN
            RAISE EXCEPTION 'ai_volume_chunk_not_building';
          END IF;
          RETURN NEW;
        END $$""")
        cursor.execute("CREATE TRIGGER ai_business_volume_chunk_state BEFORE INSERT ON ai_business_volume_chunks FOR EACH ROW EXECUTE FUNCTION ai_business_volume_chunk_guard()")
        cursor.execute("""CREATE FUNCTION ai_business_volume_uint(value json, minimum bigint, maximum bigint) RETURNS bigint
        LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,public AS $$
        DECLARE raw text; result bigint;
        BEGIN
          -- json preserves exponent/fraction lexemes that jsonb would erase.
          raw:=value#>>'{}';
          IF json_typeof(value) IS DISTINCT FROM 'number' OR raw IS NULL OR raw !~ '^(0|[1-9][0-9]{0,9})$' THEN
            RAISE EXCEPTION 'ai_volume_integer_invalid';
          END IF;
          result:=raw::bigint;
          IF result<minimum OR result>maximum THEN RAISE EXCEPTION 'ai_volume_integer_range'; END IF;
          RETURN result;
        END $$""")
        cursor.execute("""CREATE FUNCTION ai_business_volume_file_check(target text, target_attempt integer, descriptor json, expected_volume integer, expected_format text, maximum bigint)
        RETURNS void LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
        DECLARE count_value bigint; byte_value bigint; n bigint; lo bigint; hi bigint; actual_bytes bigint;
        BEGIN
          IF json_typeof(descriptor) IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'ai_volume_file_invalid'; END IF;
          IF (SELECT count(*) FROM json_object_keys(descriptor))<>5
             OR (descriptor::jsonb - ARRAY['volumeIndex','format','bytes','sha256','chunkCount'])<>'{}'::jsonb
             OR json_typeof(descriptor->'format') IS DISTINCT FROM 'string' OR descriptor->>'format' IS DISTINCT FROM expected_format
             OR json_typeof(descriptor->'sha256') IS DISTINCT FROM 'string' OR coalesce(descriptor->>'sha256','') !~ '^[0-9a-f]{64}$'
          THEN RAISE EXCEPTION 'ai_volume_file_invalid'; END IF;
          IF public.ai_business_volume_uint(descriptor->'volumeIndex',0,100)<>expected_volume THEN RAISE EXCEPTION 'ai_volume_order_invalid'; END IF;
          byte_value:=public.ai_business_volume_uint(descriptor->'bytes',1,maximum);
          count_value:=public.ai_business_volume_uint(descriptor->'chunkCount',1,512);
          IF count_value<>(byte_value+524287)/524288 THEN RAISE EXCEPTION 'ai_volume_chunk_count_invalid'; END IF;
          SELECT count(*),min(sequence),max(sequence),coalesce(sum(octet_length(content)),0) INTO n,lo,hi,actual_bytes
            FROM public.ai_business_volume_chunks WHERE run_id=target AND attempt=target_attempt AND volume_index=expected_volume AND format=expected_format;
          IF n<>count_value OR lo IS DISTINCT FROM 1::bigint OR hi IS DISTINCT FROM n OR actual_bytes<>byte_value THEN
            RAISE EXCEPTION 'ai_volume_incomplete_manifest';
          END IF;
        END $$""")
        cursor.execute("""CREATE FUNCTION ai_business_volume_manifest_check(target text, target_attempt integer, target_binding text, target_draft boolean, raw text)
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
        END $$""")
        cursor.execute("""CREATE OR REPLACE FUNCTION ai_business_files_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
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
        """ + LEGACY_RUN_GUARD + " END $$")
        cursor.execute("CREATE TRIGGER ai_business_volume_initial BEFORE INSERT ON ai_business_file_runs FOR EACH ROW EXECUTE FUNCTION ai_business_files_guard()")
        cursor.execute("""CREATE FUNCTION ai_business_volume_complete_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
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
        END $$""")
        cursor.execute("CREATE CONSTRAINT TRIGGER ai_business_volume_complete AFTER INSERT ON ai_business_volume_chunks DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ai_business_volume_complete_guard()")
        cursor.execute("CREATE CONSTRAINT TRIGGER ai_business_volume_complete AFTER INSERT OR UPDATE ON ai_business_file_runs DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ai_business_volume_complete_guard()")


def uninstall(apps, schema_editor):
    if (apps.get_model("ai_assistant", "AiBusinessFileRun").objects.filter(renderer_version=4).exists()
            or apps.get_model("ai_assistant", "AiBusinessVolumeChunk").objects.exists()):
        raise RuntimeError("存在 v4 文件任务或不可变多卷分块，不能回退 renderer 4 结构")
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for table, trigger in (("ai_business_file_runs", "ai_business_volume_complete"),
            ("ai_business_file_runs", "ai_business_volume_initial"), ("ai_business_volume_chunks", "ai_business_volume_complete"),
            ("ai_business_volume_chunks", "ai_business_volume_chunk_state"), ("ai_business_volume_chunks", "ai_immutable_evidence"),
            ("ai_business_volume_chunks", "ai_write_fence")):
            cursor.execute(f"DROP TRIGGER {trigger} ON {table}")
        cursor.execute("""CREATE OR REPLACE FUNCTION ai_business_files_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
        DECLARE value jsonb; kind text; n bigint; lo bigint; hi bigint; bytes bigint;
        BEGIN """ + LEGACY_RUN_GUARD + " END $$")
        cursor.execute("""CREATE OR REPLACE FUNCTION ai_business_file_chunk_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM ai_business_file_runs WHERE id=NEW.run_id AND status='building' AND attempt=NEW.attempt) THEN
            RAISE EXCEPTION 'ai_file_chunk_not_building';
          END IF;
          RETURN NEW;
        END $$""")
        for signature in ("ai_business_volume_complete_guard()", "ai_business_volume_chunk_guard()",
            "ai_business_volume_manifest_check(text,integer,text,boolean,text)",
            "ai_business_volume_file_check(text,integer,json,integer,text,bigint)", "ai_business_volume_uint(json,bigint,bigint)"):
            cursor.execute(f"DROP FUNCTION {signature}")
    import_module("ai_assistant.migrations.0017_business_file_renderer").change_constraint(schema_editor, "1,2,3")


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0019_business_source_directory")]
    operations = [
        migrations.CreateModel(name="AiBusinessVolumeChunk", fields=[
            ("id", models.CharField(primary_key=True, max_length=160, serialize=False)),
            ("attempt", models.PositiveIntegerField()), ("volume_index", models.PositiveIntegerField()),
            ("format", models.CharField(max_length=4)), ("sequence", models.PositiveIntegerField()),
            ("content", models.BinaryField()), ("content_digest", models.CharField(max_length=64)),
            ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
            ("run", models.ForeignKey(to="ai_assistant.aibusinessfilerun", on_delete=django.db.models.deletion.PROTECT)),
        ], options={"db_table": "ai_business_volume_chunks", "constraints": [models.UniqueConstraint(
            fields=["run", "attempt", "volume_index", "format", "sequence"], name="ai_business_volume_chunk_uq")]}),
        migrations.RunPython(install, uninstall),
    ]
