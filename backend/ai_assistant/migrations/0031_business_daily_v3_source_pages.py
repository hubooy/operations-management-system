"""Allow physical daily v3 source pages; mixed parent seal remains closed.

The 0030 finance branches and v1/v2 paths are inherited unchanged. No source
fetch or app append endpoint is registered by this schema migration.
"""
from importlib import import_module

from django.db import migrations


previous = import_module("ai_assistant.migrations.0030_business_finance_source_pages")


def replace_once(value, old, new):
    if value.count(old) != 1:
        raise RuntimeError("Frozen 0030 guard predecessor changed")
    return value.replace(old, new, 1)


SOURCE_GUARD = replace_once(previous.SOURCE_GUARD,
    "    IF TG_OP='UPDATE' THEN\n      IF OLD.domain<>'finance' OR OLD.finished",
    """    IF TG_OP='UPDATE' THEN
      IF OLD.domain IN ('sales','netshop','market') THEN
        IF OLD.finished OR NEW.version<>OLD.version+1
           OR NEW.checkpoint_run_version<>parent.version+1 OR NEW.page_count<>OLD.page_count+1
           OR NEW.page_count>1999 OR NEW.stored_bytes<=OLD.stored_bytes
           OR NEW.stored_bytes-OLD.stored_bytes>131072 OR NEW.row_count<OLD.row_count
           OR NEW.row_count>199900 OR NEW.updated_at<OLD.updated_at
        THEN RAISE EXCEPTION 'ai_business_v3_daily_checkpoint_invalid'; END IF;
        raw:=NEW.checkpoint_json::json;
        state:=public.ai_screen_fields(raw,ARRAY['pageCount','verifier','metadata']);
        q:=public.ai_screen_fields(raw->'verifier',ARRAY['source_ref','expected_cursor','last_id',
          'rows','totals','present','control','finished','evidence_digest']);
        IF public.ai_screen_uint(raw->'pageCount',1,1999)<>NEW.page_count
           OR public.ai_screen_uint(raw->'verifier'->'rows',0,199900)<>NEW.row_count
           OR q->>'source_ref' !~ '^[0-9a-f]{64}$'
           OR q->>'evidence_digest' !~ '^[0-9a-f]{64}$'
           OR json_typeof(raw->'verifier'->'finished') IS DISTINCT FROM 'boolean'
           OR (q->>'finished')::boolean IS DISTINCT FROM NEW.finished
           OR (NEW.finished AND q->'expected_cursor' IS DISTINCT FROM 'null'::jsonb)
           OR (NOT NEW.finished AND (json_typeof(raw->'verifier'->'expected_cursor') IS DISTINCT FROM 'string'
                OR length(q->>'expected_cursor') NOT BETWEEN 1 AND 1600))
           OR json_typeof(raw->'metadata') IS DISTINCT FROM 'object'
           OR json_typeof(raw->'metadata'->'sourceRevision') IS DISTINCT FROM 'string'
           OR length(state->'metadata'->>'sourceRevision') NOT BETWEEN 1 AND 128
        THEN RAISE EXCEPTION 'ai_business_v3_daily_checkpoint_shape'; END IF;
        SELECT * INTO chunk FROM public.ai_business_evidence_chunks
          WHERE run_id=NEW.run_id AND source_key=NEW.source_key AND sequence=NEW.page_count;
        IF NOT FOUND OR octet_length(chunk.payload_json)<>NEW.stored_bytes-OLD.stored_bytes
           OR chunk.payload_digest<>encode(sha256(convert_to(chunk.payload_json,'UTF8')),'hex')
           OR chunk.payload_json::jsonb->>'sourceRef' IS DISTINCT FROM q->>'source_ref'
           OR chunk.payload_json::jsonb->>'sourceRevision' IS DISTINCT FROM state->'metadata'->>'sourceRevision'
        THEN RAISE EXCEPTION 'ai_business_v3_daily_last_chunk_invalid'; END IF;
        RETURN NEW;
      END IF;
      IF OLD.domain<>'finance' OR OLD.finished""")

CHUNK_GUARD = replace_once(previous.CHUNK_GUARD,
    "    IF NOT FOUND OR source.domain<>'finance' OR source.finished",
    """    IF FOUND AND source.domain IN ('sales','netshop','market') THEN
      IF source.finished OR parent.status<>'collecting' OR parent.collection_status<>'manual'
         OR NEW.sequence<>source.page_count+1 OR NEW.sequence>1999
         OR octet_length(NEW.payload_json)>131072
         OR NEW.payload_digest<>encode(sha256(convert_to(NEW.payload_json,'UTF8')),'hex')
      THEN RAISE EXCEPTION 'ai_business_v3_daily_chunk_invalid'; END IF;
      page:=NEW.payload_json::jsonb;
      IF page->>'schemaVersion' IS DISTINCT FROM 'business-analysis-v1'
         OR page->>'sourceRef' !~ '^[0-9a-f]{64}$'
         OR jsonb_typeof(page->'items') IS DISTINCT FROM 'array'
         OR jsonb_typeof(page->'filters') IS DISTINCT FROM 'object'
         OR page->'filters'->>'platform' IS DISTINCT FROM source.query_json::jsonb->>'platform'
         OR page->'filters'->>'window' IS DISTINCT FROM source.query_json::jsonb->>'window'
         OR page->'pagination'->>'limit' IS DISTINCT FROM '100'
      THEN RAISE EXCEPTION 'ai_business_v3_daily_chunk_shape'; END IF;
      RETURN NEW;
    END IF;
    IF NOT FOUND OR source.domain<>'finance' OR source.finished""")

DIRECTORY_GUARD = replace_once(previous.DIRECTORY_GUARD,
    "    bool_or(domain<>'finance' AND (page_count<>0 OR stored_bytes<>0 OR row_count<>0 OR finished OR checkpoint_json<>'{}'))",
    "    bool_or(domain NOT IN ('sales','netshop','market','finance') AND (page_count<>0 OR stored_bytes<>0 OR row_count<>0 OR finished OR checkpoint_json<>'{}'))"
)
DIRECTORY_GUARD = replace_once(DIRECTORY_GUARD,
    "WHERE s.run_id=target AND s.source_key=c.source_key AND s.domain='finance'))",
    "WHERE s.run_id=target AND s.source_key=c.source_key AND s.domain IN ('finance','sales','netshop','market')))"
)


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for definition in (SOURCE_GUARD, DIRECTORY_GUARD, CHUNK_GUARD):
            cursor.execute(definition)


def uninstall(apps, schema_editor):
    source = apps.get_model("ai_assistant", "AiBusinessEvidenceSource")
    runs = apps.get_model("ai_assistant", "AiBusinessEvidenceRun")
    v3_runs = runs.objects.filter(plan_json__contains='"schemaVersion":"business-evidence-v3"').values("id")
    if source.objects.filter(run_id__in=v3_runs, domain__in=("sales", "netshop", "market"), page_count__gt=0).exists():
        raise RuntimeError("存在 v3 日来源事实，不能回退日来源物理门禁")
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for definition in (previous.SOURCE_GUARD, previous.DIRECTORY_GUARD, previous.CHUNK_GUARD):
            cursor.execute(definition)


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0030_business_finance_source_pages")]
    operations = [migrations.RunPython(install, uninstall)]
