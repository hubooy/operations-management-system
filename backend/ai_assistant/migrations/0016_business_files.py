from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for table in ("ai_business_file_runs", "ai_business_file_chunks"):
            cursor.execute(f"CREATE TRIGGER ai_write_fence BEFORE INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION ai_runtime_write_fence()")
        cursor.execute("CREATE TRIGGER ai_immutable_evidence BEFORE UPDATE OR DELETE ON ai_business_file_chunks FOR EACH ROW EXECUTE FUNCTION ai_immutable_record_guard()")
        cursor.execute("CREATE TRIGGER ai_immutable_identity BEFORE UPDATE ON ai_business_file_runs FOR EACH ROW EXECUTE FUNCTION ai_immutable_record_guard('id','owner_email','scope_json','report_id','draft','renderer_version','binding_digest','created_at')")
        cursor.execute("ALTER TABLE ai_business_file_runs ADD CONSTRAINT ai_business_file_bound CHECK (scope_json='null' AND version>=1 AND attempt<=5 AND renderer_version=1 AND stored_bytes<=1073741824 AND octet_length(progress_json)<=4096 AND octet_length(manifest_json)<=131072 AND status IN ('queued','building','paused','ready','cancelled'))")
        cursor.execute("ALTER TABLE ai_business_file_chunks ADD CONSTRAINT ai_business_file_chunk_bound CHECK (attempt BETWEEN 1 AND 5 AND format IN ('html','xlsx') AND sequence BETWEEN 1 AND 512 AND octet_length(content) BETWEEN 1 AND 524288 AND content_digest ~ '^[0-9a-f]{64}$')")
        cursor.execute("""CREATE FUNCTION ai_business_file_chunk_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM ai_business_file_runs WHERE id=NEW.run_id AND status='building' AND attempt=NEW.attempt) THEN
            RAISE EXCEPTION 'ai_file_chunk_not_building';
          END IF;
          RETURN NEW;
        END $$""")
        cursor.execute("CREATE TRIGGER ai_business_file_chunk_state BEFORE INSERT ON ai_business_file_chunks FOR EACH ROW EXECUTE FUNCTION ai_business_file_chunk_guard()")
        cursor.execute("""CREATE FUNCTION ai_business_files_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
        DECLARE value jsonb; kind text; n bigint; lo bigint; hi bigint; bytes bigint;
        BEGIN
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
        END $$""")
        cursor.execute("CREATE TRIGGER ai_business_file_state BEFORE UPDATE OR DELETE ON ai_business_file_runs FOR EACH ROW EXECUTE FUNCTION ai_business_files_guard()")


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        with schema_editor.connection.cursor() as cursor:
            cursor.execute("DROP FUNCTION ai_business_files_guard() CASCADE")
            cursor.execute("DROP FUNCTION ai_business_file_chunk_guard() CASCADE")


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0015_business_collection")]
    operations = [
        migrations.CreateModel(name="AiBusinessFileRun", fields=[
            ("id", models.CharField(primary_key=True, max_length=160, serialize=False)),
            ("owner_email", models.CharField(max_length=320)), ("scope_json", models.TextField(default="null")),
            ("draft", models.BooleanField(default=False)), ("renderer_version", models.PositiveIntegerField(default=1)),
            ("binding_digest", models.CharField(max_length=64)), ("status", models.CharField(max_length=20, default="queued")),
            ("version", models.PositiveIntegerField(default=1)), ("attempt", models.PositiveIntegerField(default=0)),
            ("lease_until", models.DateTimeField(default=django.utils.timezone.now)), ("stored_bytes", models.PositiveBigIntegerField(default=0)),
            ("progress_json", models.TextField(default="{}")), ("manifest_json", models.TextField(default="{}")),
            ("error_code", models.CharField(max_length=64, default="")), ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
            ("report", models.ForeignKey(to="ai_assistant.aireportrun", on_delete=django.db.models.deletion.PROTECT)),
        ], options={"db_table": "ai_business_file_runs", "constraints": [models.UniqueConstraint(fields=["report", "draft", "renderer_version", "binding_digest"], name="ai_business_file_binding_uq")],
            "indexes": [models.Index(fields=["owner_email", "-created_at"], name="ai_business_file_owner_idx"), models.Index(fields=["status", "lease_until"], name="ai_business_file_queue_idx")]}),
        migrations.CreateModel(name="AiBusinessFileChunk", fields=[
            ("id", models.CharField(primary_key=True, max_length=160, serialize=False)), ("attempt", models.PositiveIntegerField()),
            ("format", models.CharField(max_length=4)), ("sequence", models.PositiveIntegerField()), ("content", models.BinaryField()),
            ("content_digest", models.CharField(max_length=64)), ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
            ("run", models.ForeignKey(to="ai_assistant.aibusinessfilerun", on_delete=django.db.models.deletion.PROTECT)),
        ], options={"db_table": "ai_business_file_chunks", "constraints": [models.UniqueConstraint(fields=["run", "attempt", "format", "sequence"], name="ai_business_file_chunk_uq")]}),
        migrations.RunPython(install, uninstall),
    ]
