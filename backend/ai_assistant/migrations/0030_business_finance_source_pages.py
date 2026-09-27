"""Finance-only v3 source page transitions; parent seal remains forbidden.

No application append endpoint is registered by this migration. The SQL guards
only fence physical ledger transitions; provenance requires a future owning
reader call and full immutable replay before any consumer may trust the rows.
"""
from importlib import import_module

from django.db import migrations


previous = import_module("ai_assistant.migrations.0028_business_finance_v3_gate")


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise RuntimeError("Frozen v3 predecessor SQL changed")
    return source.replace(old, new, 1)


RUN_GUARD = replace_once(previous.V3_RUN_GUARD,
    "  IF TG_OP<>'INSERT' OR NEW.status<>'collecting' OR NEW.version<>1 OR NEW.scope_json<>'null'",
    """  IF TG_OP='UPDATE' THEN
    IF OLD.status<>'collecting' OR NEW.status<>'collecting'
       OR OLD.collection_status<>'manual' OR NEW.collection_status<>'manual'
       OR NEW.version<>OLD.version+1 OR NEW.state_json<>'{}'
       OR NEW.stored_bytes<=OLD.stored_bytes OR NEW.stored_bytes>67108864-38000
       OR lower(NEW.owner_email)<>NEW.owner_email
       OR NOT EXISTS (SELECT 1 FROM public.access_control_users u WHERE u.email=NEW.owner_email
         AND u.role='admin' AND u.status='active' AND u.scope IS NULL AND u.version>=1)
    THEN RAISE EXCEPTION 'ai_business_v3_finance_parent_advance_invalid'; END IF;
    RETURN NEW;
  END IF;
  IF TG_OP<>'INSERT' OR NEW.status<>'collecting' OR NEW.version<>1 OR NEW.scope_json<>'null'""")
RUN_GUARD = replace_once(RUN_GUARD, "CREATE FUNCTION ai_business_v3_run_guard()",
                         "CREATE OR REPLACE FUNCTION ai_business_v3_run_guard()")

SOURCE_GUARD = replace_once(previous.V3_SOURCE_GUARD,
    "DECLARE parent public.ai_business_evidence_runs%ROWTYPE; header jsonb; q jsonb; raw json;",
    "DECLARE parent public.ai_business_evidence_runs%ROWTYPE; header jsonb; q jsonb; raw json; state jsonb; chunk public.ai_business_evidence_chunks%ROWTYPE;")
SOURCE_GUARD = replace_once(SOURCE_GUARD,
    "    IF header IS NULL OR TG_OP<>'INSERT' OR parent.status<>'collecting' OR parent.version<>1",
    """    IF header IS NULL OR parent.status<>'collecting' OR parent.collection_status<>'manual'
       OR parent.state_json<>'{}' OR parent.stored_bytes>67108864-38000
       OR NOT EXISTS (SELECT 1 FROM public.access_control_users u WHERE u.email=parent.owner_email
         AND u.role='admin' AND u.status='active' AND u.scope IS NULL AND u.version>=1)
    THEN RAISE EXCEPTION 'ai_business_v3_source_parent_invalid'; END IF;
    IF TG_OP='UPDATE' THEN
      IF OLD.domain<>'finance' OR OLD.finished OR NEW.version<>OLD.version+1
         OR NEW.checkpoint_run_version<>parent.version+1 OR NEW.page_count<>OLD.page_count+1
         OR NEW.page_count>1999 OR NEW.stored_bytes<=OLD.stored_bytes
         OR NEW.stored_bytes-OLD.stored_bytes>38000 OR NEW.row_count<OLD.row_count
         OR NEW.row_count>100000 OR NEW.updated_at<OLD.updated_at
      THEN RAISE EXCEPTION 'ai_business_v3_finance_checkpoint_invalid'; END IF;
      raw:=NEW.checkpoint_json::json;
      state:=public.ai_screen_fields(raw,ARRAY['schemaVersion','queryDigest','sourceRef','sourceRevision',
        'publication','periodAlignment','totalRows','rowsRead','lastId','nextOffset','pageCount','storedBytes',
        'lastPageDigest','pageChainDigest','rowChainDigest','monthCounts','metricStates','finished',
        'persistentEvidenceVerified','checkpointDigest']);
      IF state->>'schemaVersion' IS DISTINCT FROM 'business-finance-collection-checkpoint-v1'
         OR state->>'queryDigest' IS DISTINCT FROM NEW.query_digest
         OR state->>'sourceRef' !~ '^[0-9a-f]{64}$'
         OR state->>'sourceRevision' !~ '^(0|[1-9][0-9]*):[0-9a-f]{64}$'
         OR state->>'checkpointDigest' !~ '^[0-9a-f]{64}$'
         OR state->>'lastPageDigest' !~ '^[0-9a-f]{64}$'
         OR state->>'pageChainDigest' !~ '^[0-9a-f]{64}$'
         OR state->>'rowChainDigest' !~ '^[0-9a-f]{64}$'
         OR state->'persistentEvidenceVerified' IS DISTINCT FROM 'false'::jsonb
         OR public.ai_screen_uint(raw->'pageCount',1,1999)<>NEW.page_count
         OR public.ai_screen_uint(raw->'rowsRead',0,100000)<>NEW.row_count
         OR public.ai_screen_uint(raw->'storedBytes',1,67108864-38000)<>NEW.stored_bytes
         OR json_typeof(raw->'finished') IS DISTINCT FROM 'boolean'
         OR (state->>'finished')::boolean IS DISTINCT FROM NEW.finished
         OR (NEW.finished AND state->'nextOffset' IS DISTINCT FROM 'null'::jsonb)
         OR (NOT NEW.finished AND state->'nextOffset' IS DISTINCT FROM to_jsonb(NEW.row_count))
      THEN RAISE EXCEPTION 'ai_business_v3_finance_checkpoint_shape'; END IF;
      SELECT * INTO chunk FROM public.ai_business_evidence_chunks
        WHERE run_id=NEW.run_id AND source_key=NEW.source_key AND sequence=NEW.page_count;
      IF NOT FOUND OR octet_length(chunk.payload_json)<>NEW.stored_bytes-OLD.stored_bytes
         OR chunk.payload_digest<>encode(sha256(convert_to(chunk.payload_json,'UTF8')),'hex')
         OR chunk.payload_json::jsonb->>'pageDigest' IS DISTINCT FROM state->>'lastPageDigest'
      THEN RAISE EXCEPTION 'ai_business_v3_finance_last_chunk_invalid'; END IF;
      RETURN NEW;
    END IF;
    IF TG_OP<>'INSERT' OR parent.version<>1""")
SOURCE_GUARD = replace_once(SOURCE_GUARD, "CREATE OR REPLACE FUNCTION ai_business_source_guard()",
                            "CREATE OR REPLACE FUNCTION ai_business_source_guard()")

DIRECTORY_GUARD = replace_once(previous.V3_DIRECTORY_GUARD,
    "  query_bytes bigint; date_pair text; one_pair text; mismatched boolean;",
    "  query_bytes bigint; date_pair text; one_pair text; mismatched boolean; pages bigint; bytes bigint;")
DIRECTORY_GUARD = replace_once(DIRECTORY_GUARD,
    "    bool_or(page_count<>0 OR stored_bytes<>0 OR row_count<>0 OR finished OR checkpoint_json<>'{}')\n  INTO total,first_ordinal,last_ordinal,finance_count,daily_count,query_bytes,mismatched",
    """    bool_or(domain<>'finance' AND (page_count<>0 OR stored_bytes<>0 OR row_count<>0 OR finished OR checkpoint_json<>'{}')),
    COALESCE(sum(page_count),0),COALESCE(sum(stored_bytes),0)
  INTO total,first_ordinal,last_ordinal,finance_count,daily_count,query_bytes,mismatched,pages,bytes""")
DIRECTORY_GUARD = replace_once(DIRECTORY_GUARD,
    "     OR parent.version<>1 OR parent.status<>'collecting' OR parent.stored_bytes<>0 OR parent.state_json<>'{}'\n     OR EXISTS(SELECT 1 FROM public.ai_business_evidence_chunks WHERE run_id=target)",
    """     OR parent.version<1 OR parent.status<>'collecting' OR parent.collection_status<>'manual'
     OR parent.stored_bytes<>bytes OR parent.stored_bytes>67108864-38000
     OR pages>1999 OR parent.state_json<>'{}'
     OR EXISTS(SELECT 1 FROM public.ai_business_evidence_sources s WHERE s.run_id=target
       AND (s.version<>s.page_count+1 OR s.checkpoint_run_version>parent.version))
     OR EXISTS(SELECT 1 FROM public.ai_business_evidence_chunks c
       WHERE c.run_id=target AND NOT EXISTS (SELECT 1 FROM public.ai_business_evidence_sources s
         WHERE s.run_id=target AND s.source_key=c.source_key AND s.domain='finance'))
     OR EXISTS(SELECT 1 FROM public.ai_business_evidence_sources s
       LEFT JOIN (SELECT source_key,count(*) AS n,sum(octet_length(payload_json)) AS size,
           min(sequence) AS first_seq,max(sequence) AS last_seq
         FROM public.ai_business_evidence_chunks WHERE run_id=target GROUP BY source_key) c
       ON c.source_key=s.source_key WHERE s.run_id=target AND
         (s.page_count<>COALESCE(c.n,0) OR s.stored_bytes<>COALESCE(c.size,0)
          OR (c.n>0 AND (c.first_seq<>1 OR c.last_seq<>c.n))))""")
DIRECTORY_GUARD = replace_once(DIRECTORY_GUARD,
    "CREATE FUNCTION ai_business_v3_directory_guard()",
    "CREATE OR REPLACE FUNCTION ai_business_v3_directory_guard()")

CHUNK_GUARD = replace_once(previous.V3_CHUNK_GUARD,
    "DECLARE parent public.ai_business_evidence_runs%ROWTYPE;",
    "DECLARE parent public.ai_business_evidence_runs%ROWTYPE; source public.ai_business_evidence_sources%ROWTYPE; page jsonb;")
CHUNK_GUARD = replace_once(CHUNK_GUARD,
    "  THEN RAISE EXCEPTION 'ai_business_v3_collection_disabled'; END IF;",
    """  THEN
    SELECT * INTO source FROM public.ai_business_evidence_sources
      WHERE run_id=NEW.run_id AND source_key=NEW.source_key FOR UPDATE;
    IF NOT FOUND OR source.domain<>'finance' OR source.finished
       OR parent.status<>'collecting' OR parent.collection_status<>'manual'
       OR NEW.sequence<>source.page_count+1 OR NEW.sequence>1999
       OR octet_length(NEW.payload_json)>38000
       OR NEW.payload_digest<>encode(sha256(convert_to(NEW.payload_json,'UTF8')),'hex')
    THEN RAISE EXCEPTION 'ai_business_v3_finance_chunk_invalid'; END IF;
    page:=NEW.payload_json::jsonb;
    IF page->>'schemaVersion' IS DISTINCT FROM 'business-finance-owned-page-v1'
       OR page->>'sourceRef' !~ '^[0-9a-f]{64}$'
       OR page->>'sourceRevision' !~ '^(0|[1-9][0-9]*):[0-9a-f]{64}$'
       OR page->>'pageDigest' !~ '^[0-9a-f]{64}$'
       OR page->'persistentEvidenceVerified' IS DISTINCT FROM 'false'::jsonb
       OR page->'sourceAuthorityVerified' IS DISTINCT FROM 'false'::jsonb
       OR page->'query' IS DISTINCT FROM source.query_json::jsonb
       OR page->'pagination'->>'offset' IS DISTINCT FROM source.row_count::text
    THEN RAISE EXCEPTION 'ai_business_v3_finance_chunk_shape'; END IF;
  END IF;""")
CHUNK_GUARD = replace_once(CHUNK_GUARD, "CREATE FUNCTION ai_business_v3_chunk_guard()",
                           "CREATE OR REPLACE FUNCTION ai_business_v3_chunk_guard()")


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for definition in (RUN_GUARD, SOURCE_GUARD, DIRECTORY_GUARD, CHUNK_GUARD):
            cursor.execute(definition)


def uninstall(apps, schema_editor):
    source = apps.get_model("ai_assistant", "AiBusinessEvidenceSource")
    chunk = apps.get_model("ai_assistant", "AiBusinessEvidenceChunk")
    if source.objects.filter(domain="finance", page_count__gt=0).exists() or chunk.objects.filter(
            run_id__in=source.objects.filter(domain="finance").values("run_id")).exists():
        raise RuntimeError("存在 v3 财务来源事实或检查点，不能回退其存储守卫")
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for definition in (previous.V3_RUN_GUARD, previous.V3_SOURCE_GUARD,
                           previous.V3_DIRECTORY_GUARD, previous.V3_CHUNK_GUARD):
            cursor.execute(definition.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0029_business_promotion_file_ready")]
    operations = [migrations.RunPython(install, uninstall)]
