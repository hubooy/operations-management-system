"""Candidate v2 directory only; no old rows or fact limits are rewritten."""
import json
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("""CREATE FUNCTION ai_business_v2_header(raw text) RETURNS jsonb
        LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,public AS $$
        DECLARE header jsonb; original json;
        BEGIN
          BEGIN header:=raw::jsonb;
          EXCEPTION WHEN invalid_text_representation THEN RETURN NULL; END;
          IF header->>'schemaVersion' IS DISTINCT FROM 'business-evidence-v2' THEN RETURN NULL; END IF;
          original:=raw::json;
          -- jsonb numeric equality erases exponent notation; retain the raw integer contract.
          IF (original->>'sourceCount') !~ '^[1-9][0-9]?$'
             OR original->'collector'->>'version' IS DISTINCT FROM '1'
             OR original->'collector'->>'pageSize' IS DISTINCT FROM '100'
             OR original->'limits'->>'factBytes' IS DISTINCT FROM '67108864'
             OR original->'limits'->>'factPages' IS DISTINCT FROM '2000'
          THEN RAISE EXCEPTION 'ai_business_v2_header_invalid'; END IF;
          IF jsonb_typeof(header) IS DISTINCT FROM 'object'
             OR (header - ARRAY['schemaVersion','sourceCount','catalogDigest','capacityProfile','collector','limits','analysisRequest']) <> '{}'::jsonb
             OR jsonb_typeof(header->'sourceCount') IS DISTINCT FROM 'number'
             OR (header->>'sourceCount') !~ '^[1-9][0-9]?$'
             OR (header->>'sourceCount')::integer > 48
             OR jsonb_typeof(header->'catalogDigest') IS DISTINCT FROM 'string'
             OR (header->>'catalogDigest') !~ '^[0-9a-f]{64}$'
             OR header->>'capacityProfile' IS DISTINCT FROM 'catalog-48-facts-v1'
             OR header->'collector' IS DISTINCT FROM '{"version":1,"surface":"business_collection","pageSize":100}'::jsonb
             OR header->'limits' IS DISTINCT FROM '{"factBytes":67108864,"factPages":2000}'::jsonb
             OR (header ? 'analysisRequest' AND jsonb_typeof(header->'analysisRequest') IS DISTINCT FROM 'object')
          THEN RAISE EXCEPTION 'ai_business_v2_header_invalid'; END IF;
          RETURN header;
        END $$""")
        cursor.execute("""ALTER TABLE ai_business_evidence_sources ADD CONSTRAINT ai_business_source_bound CHECK (
          source_key ~ '^[A-Za-z0-9_-]{1,160}$' AND ordinal BETWEEN 1 AND 48
          AND domain IN ('sales','netshop','market') AND query_digest ~ '^[0-9a-f]{64}$'
          AND octet_length(query_json)<=4096 AND jsonb_typeof(query_json::jsonb)='object'
          AND octet_length(checkpoint_json)<=32768 AND jsonb_typeof(checkpoint_json::jsonb)='object'
          AND version>=1 AND checkpoint_run_version>=1 AND page_count<=2000
          AND stored_bytes<=67108864 AND row_count<=9007199254740991 AND updated_at>=created_at
        )""")
        cursor.execute("CREATE TRIGGER ai_write_fence BEFORE INSERT OR UPDATE OR DELETE ON ai_business_evidence_sources FOR EACH ROW EXECUTE FUNCTION ai_runtime_write_fence()")
        cursor.execute("CREATE TRIGGER ai_immutable_identity BEFORE UPDATE ON ai_business_evidence_sources FOR EACH ROW EXECUTE FUNCTION ai_immutable_record_guard('id','run_id','source_key','ordinal','domain','query_json','query_digest','created_at')")
        cursor.execute("""CREATE FUNCTION ai_business_source_guard() RETURNS trigger LANGUAGE plpgsql
        SET search_path=pg_catalog,public AS $$
        DECLARE parent public.ai_business_evidence_runs%ROWTYPE; header jsonb;
        BEGIN
          IF TG_OP='DELETE' THEN RAISE EXCEPTION 'ai_business_source_delete_denied'; END IF;
          SELECT * INTO parent FROM public.ai_business_evidence_runs WHERE id=NEW.run_id FOR UPDATE;
          IF NOT FOUND THEN RAISE EXCEPTION 'ai_business_source_parent_missing'; END IF;
          header:=public.ai_business_v2_header(parent.plan_json);
          IF header IS NULL OR parent.status<>'collecting' THEN RAISE EXCEPTION 'ai_business_source_parent_unavailable'; END IF;
          IF TG_OP='INSERT' THEN
            IF parent.version<>1 OR EXISTS(SELECT 1 FROM public.ai_business_evidence_chunks WHERE run_id=parent.id)
               OR NEW.version<>1 OR NEW.checkpoint_run_version<>1 OR NEW.checkpoint_json<>'{}'
               OR NEW.page_count<>0 OR NEW.stored_bytes<>0 OR NEW.row_count<>0 OR NEW.finished
            THEN RAISE EXCEPTION 'ai_business_source_initial_state'; END IF;
          ELSE
            IF OLD.finished OR NEW.version<>OLD.version+1 OR NEW.checkpoint_run_version<>parent.version+1
               OR NEW.page_count<OLD.page_count OR NEW.stored_bytes<OLD.stored_bytes OR NEW.row_count<OLD.row_count
               OR NEW.updated_at<OLD.updated_at
            THEN RAISE EXCEPTION 'ai_business_source_checkpoint_fence'; END IF;
          END IF;
          RETURN NEW;
        END $$""")
        cursor.execute("CREATE TRIGGER ai_business_source_state BEFORE INSERT OR UPDATE OR DELETE ON ai_business_evidence_sources FOR EACH ROW EXECUTE FUNCTION ai_business_source_guard()")
        cursor.execute("""CREATE FUNCTION ai_business_directory_guard() RETURNS trigger LANGUAGE plpgsql
        SET search_path=pg_catalog,public AS $$
        DECLARE parent public.ai_business_evidence_runs%ROWTYPE; target text; header jsonb;
          found_count bigint; first_ordinal integer; last_ordinal integer; query_bytes bigint;
          pages bigint; facts numeric; rows_count numeric; unfinished bigint; checkpoint_version integer;
        BEGIN
          IF TG_TABLE_NAME='ai_business_evidence_runs' THEN target:=NEW.id; ELSE target:=NEW.run_id; END IF;
          SELECT * INTO parent FROM public.ai_business_evidence_runs WHERE id=target FOR UPDATE;
          IF NOT FOUND THEN RAISE EXCEPTION 'ai_business_directory_parent_missing'; END IF;
          header:=public.ai_business_v2_header(parent.plan_json);
          IF header IS NULL THEN RETURN NULL; END IF;
          SELECT count(*),min(ordinal),max(ordinal),COALESCE(sum(octet_length(query_json)),0),
            COALESCE(sum(page_count),0),COALESCE(sum(stored_bytes),0),COALESCE(sum(row_count),0),
            count(*) FILTER (WHERE NOT finished),COALESCE(max(checkpoint_run_version),1)
          INTO found_count,first_ordinal,last_ordinal,query_bytes,pages,facts,rows_count,unfinished,checkpoint_version
          FROM public.ai_business_evidence_sources WHERE run_id=target;
          IF found_count<>(header->>'sourceCount')::integer OR first_ordinal<>1 OR last_ordinal<>found_count
             OR query_bytes>131072 OR pages>2000 OR facts>67108864 OR facts<>parent.stored_bytes
             OR rows_count>9007199254740991 OR checkpoint_version>parent.version
             OR (parent.status='sealed' AND unfinished<>0)
          THEN RAISE EXCEPTION 'ai_business_directory_incomplete_or_inconsistent'; END IF;
          -- Counters are a checkpoint, never authority for the immutable fact ledger.
          IF EXISTS (
            SELECT 1 FROM public.ai_business_evidence_sources s LEFT JOIN (
              SELECT source_key,count(*) AS actual_pages,sum(octet_length(payload_json)) AS actual_bytes,
                min(sequence) AS first_sequence,max(sequence) AS last_sequence
              FROM public.ai_business_evidence_chunks WHERE run_id=target GROUP BY source_key
            ) c ON c.source_key=s.source_key
            WHERE s.run_id=target AND (
              s.page_count<>COALESCE(c.actual_pages,0) OR s.stored_bytes<>COALESCE(c.actual_bytes,0)
              OR (c.actual_pages>0 AND (c.first_sequence<>1 OR c.last_sequence<>c.actual_pages))
            )
          ) THEN RAISE EXCEPTION 'ai_business_directory_chunk_mismatch'; END IF;
          RETURN NULL;
        END $$""")
        for table in ("ai_business_evidence_runs", "ai_business_evidence_sources"):
            cursor.execute(f"CREATE CONSTRAINT TRIGGER ai_business_directory_complete AFTER INSERT OR UPDATE ON {table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ai_business_directory_guard()")
        cursor.execute("CREATE CONSTRAINT TRIGGER ai_business_directory_complete AFTER INSERT ON ai_business_evidence_chunks DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ai_business_directory_guard()")
        cursor.execute("""CREATE FUNCTION ai_business_v2_chunk_guard() RETURNS trigger LANGUAGE plpgsql
        SET search_path=pg_catalog,public AS $$
        DECLARE parent public.ai_business_evidence_runs%ROWTYPE;
        BEGIN
          SELECT * INTO parent FROM public.ai_business_evidence_runs WHERE id=NEW.run_id FOR UPDATE;
          IF public.ai_business_v2_header(parent.plan_json) IS NOT NULL THEN
            IF parent.status<>'collecting' OR NOT EXISTS (
              SELECT 1 FROM public.ai_business_evidence_sources WHERE run_id=NEW.run_id AND source_key=NEW.source_key AND NOT finished
            ) THEN RAISE EXCEPTION 'ai_business_v2_chunk_source_missing_or_finished'; END IF;
          END IF;
          RETURN NEW;
        END $$""")
        cursor.execute("CREATE TRIGGER ai_business_v2_chunk_source BEFORE INSERT ON ai_business_evidence_chunks FOR EACH ROW EXECUTE FUNCTION ai_business_v2_chunk_guard()")


def uninstall(apps, schema_editor):
    if apps.get_model("ai_assistant", "AiBusinessEvidenceSource").objects.exists():
        raise RuntimeError("存在 v2 来源目录，不能回退证据目录结构")
    for raw in apps.get_model("ai_assistant", "AiBusinessEvidenceRun").objects.values_list("plan_json", flat=True).iterator():
        try:
            value = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict) and value.get("schemaVersion") == "business-evidence-v2":
            raise RuntimeError("存在 v2 证据任务，不能回退证据目录结构")
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for table, trigger in (
            ("ai_business_evidence_chunks", "ai_business_v2_chunk_source"),
            ("ai_business_evidence_chunks", "ai_business_directory_complete"),
            ("ai_business_evidence_runs", "ai_business_directory_complete"),
            ("ai_business_evidence_sources", "ai_business_directory_complete"),
            ("ai_business_evidence_sources", "ai_business_source_state"),
            ("ai_business_evidence_sources", "ai_immutable_identity"),
            ("ai_business_evidence_sources", "ai_write_fence"),
        ):
            cursor.execute(f"DROP TRIGGER {trigger} ON {table}")
        for function in ("ai_business_v2_chunk_guard()", "ai_business_directory_guard()", "ai_business_source_guard()", "ai_business_v2_header(text)"):
            cursor.execute(f"DROP FUNCTION {function}")


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0018_business_excel_renderer")]
    operations = [
        migrations.CreateModel(
            name="AiBusinessEvidenceSource",
            fields=[
                ("id", models.CharField(max_length=160, primary_key=True, serialize=False)),
                ("source_key", models.CharField(max_length=160)),
                ("ordinal", models.PositiveIntegerField()),
                ("domain", models.CharField(max_length=20)),
                ("query_json", models.TextField()),
                ("query_digest", models.CharField(max_length=64)),
                ("checkpoint_json", models.TextField(default="{}")),
                ("version", models.PositiveIntegerField(default=1)),
                ("checkpoint_run_version", models.PositiveIntegerField(default=1)),
                ("page_count", models.PositiveIntegerField(default=0)),
                ("stored_bytes", models.PositiveBigIntegerField(default=0)),
                ("row_count", models.PositiveBigIntegerField(default=0)),
                ("finished", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("run", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="ai_assistant.aibusinessevidencerun")),
            ],
            options={
                "db_table": "ai_business_evidence_sources",
                "indexes": [models.Index(fields=["run", "finished", "ordinal"], name="ai_business_source_next_idx")],
                "constraints": [
                    models.UniqueConstraint(fields=("run", "source_key"), name="ai_business_source_key_uq"),
                    models.UniqueConstraint(fields=("run", "ordinal"), name="ai_business_source_ord_uq"),
                    models.UniqueConstraint(fields=("run", "domain", "query_digest"), name="ai_business_source_query_uq"),
                ],
            },
        ),
        migrations.RunPython(install, uninstall),
    ]
