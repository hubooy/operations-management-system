"""Allow only fully receipt-bound mixed-v3 parent seal; keep reports closed."""
from importlib import import_module

from django.db import migrations


finance = import_module("ai_assistant.migrations.0030_business_finance_source_pages")
daily = import_module("ai_assistant.migrations.0031_business_daily_v3_source_pages")


def replace_once(value, old, new):
    if value.count(old) != 1:
        raise RuntimeError("Frozen v3 seal predecessor changed")
    return value.replace(old, new, 1)


RUN_GUARD = replace_once(finance.RUN_GUARD,
    "DECLARE header jsonb;",
    """DECLARE header jsonb; state jsonb; proof jsonb;
  source public.ai_business_evidence_sources%ROWTYPE;
  source_count bigint; chunk_count bigint; receipt_count bigint; receipt_ref text;
  last_ref text; receipt_revision text; last_revision text;""")
RUN_GUARD = replace_once(RUN_GUARD,
    "  IF TG_OP='UPDATE' THEN",
    """  IF TG_OP='UPDATE' AND NEW.status='sealed' THEN
    IF OLD.status<>'collecting' OR OLD.collection_status<>'manual'
       OR NEW.collection_status<>'manual' OR OLD.state_json<>'{}'
       OR NEW.version<>OLD.version+1 OR NEW.stored_bytes<>OLD.stored_bytes
       OR NEW.stored_bytes<1 OR octet_length(NEW.state_json)>38000
       OR to_jsonb(NEW)-'status'-'version'-'state_json'
          IS DISTINCT FROM to_jsonb(OLD)-'status'-'version'-'state_json'
       OR NOT EXISTS (SELECT 1 FROM public.access_control_users u WHERE u.email=NEW.owner_email
         AND u.role='admin' AND u.status='active' AND u.scope IS NULL AND u.version>=1)
    THEN RAISE EXCEPTION 'ai_business_v3_seal_transition_invalid'; END IF;
    state:=public.ai_screen_fields(NEW.state_json::json,ARRAY['schemaVersion','runId',
      'evidenceVersion','planDigest','catalogDigest','sources','sourcesDigest','pageCount',
      'rowCount','storedBytes','snapshotMeaning','receiptBound','ownedReplayVerified',
      'sourceAuthorityVerified','persistentEvidenceVerified','reportGenerationSupported','sealedDigest']);
    IF state->>'schemaVersion' IS DISTINCT FROM 'business-evidence-seal-v3'
       OR state->>'runId' IS DISTINCT FROM NEW.id
       OR public.ai_screen_uint(NEW.state_json::json->'evidenceVersion',2,2000000000)<>NEW.version
       OR state->>'planDigest' IS DISTINCT FROM encode(sha256(convert_to(NEW.plan_json,'UTF8')),'hex')
       OR state->>'catalogDigest' IS DISTINCT FROM header->>'catalogDigest'
       OR coalesce(state->>'sourcesDigest','') !~ '^[0-9a-f]{64}$'
       OR coalesce(state->>'sealedDigest','') !~ '^[0-9a-f]{64}$'
       OR state->'receiptBound' IS DISTINCT FROM 'true'::jsonb
       OR state->'ownedReplayVerified' IS DISTINCT FROM 'true'::jsonb
       OR state->'sourceAuthorityVerified' IS DISTINCT FROM 'false'::jsonb
       OR state->'persistentEvidenceVerified' IS DISTINCT FROM 'false'::jsonb
       OR state->'reportGenerationSupported' IS DISTINCT FROM 'false'::jsonb
       OR jsonb_typeof(state->'snapshotMeaning') IS DISTINCT FROM 'string'
       OR jsonb_typeof(state->'sources') IS DISTINCT FROM 'array'
       OR jsonb_array_length(state->'sources')<>(header->>'sourceCount')::integer
       OR public.ai_screen_uint(NEW.state_json::json->'storedBytes',1,67108864)<>NEW.stored_bytes
    THEN RAISE EXCEPTION 'ai_business_v3_seal_shape_invalid'; END IF;
    SELECT count(*),coalesce(sum(page_count),0),coalesce(sum(row_count),0)
      INTO source_count,chunk_count,receipt_count
      FROM public.ai_business_evidence_sources WHERE run_id=NEW.id;
    IF source_count<>(header->>'sourceCount')::integer
       OR public.ai_screen_uint(NEW.state_json::json->'pageCount',1,1999)<>chunk_count
       OR public.ai_screen_uint(NEW.state_json::json->'rowCount',0,10000000)<>receipt_count
    THEN RAISE EXCEPTION 'ai_business_v3_seal_totals_invalid'; END IF;
    FOR source IN SELECT * FROM public.ai_business_evidence_sources
       WHERE run_id=NEW.id ORDER BY ordinal LOOP
      proof:=public.ai_screen_fields((state->'sources'->(source.ordinal-1))::json,
        ARRAY['sourceKey','ordinal','domain','queryDigest','sourceVersion','checkpointDigest',
          'pageCount','rowCount','storedBytes','sourceRef','sourceRevision',
          'receiptCount','receiptChainDigest','coverage']);
      IF NOT source.finished OR source.page_count<1 OR source.version<>source.page_count+1
         OR source.checkpoint_run_version>OLD.version
         OR proof->>'sourceKey' IS DISTINCT FROM source.source_key
         OR (proof->>'ordinal')::integer IS DISTINCT FROM source.ordinal
         OR proof->>'domain' IS DISTINCT FROM source.domain
         OR proof->>'queryDigest' IS DISTINCT FROM source.query_digest
         OR proof->>'checkpointDigest' IS DISTINCT FROM
            encode(sha256(convert_to(source.checkpoint_json,'UTF8')),'hex')
         OR (proof->>'sourceVersion')::integer IS DISTINCT FROM source.version
         OR (proof->>'pageCount')::integer IS DISTINCT FROM source.page_count
         OR (proof->>'rowCount')::bigint IS DISTINCT FROM source.row_count
         OR (proof->>'storedBytes')::bigint IS DISTINCT FROM source.stored_bytes
         OR (proof->>'receiptCount')::integer IS DISTINCT FROM source.page_count
         OR coalesce(proof->>'receiptChainDigest','') !~ '^[0-9a-f]{64}$'
         OR jsonb_typeof(proof->'coverage') IS DISTINCT FROM 'object'
      THEN RAISE EXCEPTION 'ai_business_v3_seal_source_invalid'; END IF;
      SELECT count(*),min(source_ref),max(source_ref),min(source_revision),max(source_revision)
        INTO receipt_count,receipt_ref,last_ref,receipt_revision,last_revision
        FROM public.ai_business_source_tool_receipts
        WHERE run_id=NEW.id AND source_id=source.id;
      IF receipt_count<>source.page_count OR receipt_ref IS DISTINCT FROM last_ref
         OR receipt_revision IS DISTINCT FROM last_revision
         OR proof->>'sourceRef' IS DISTINCT FROM receipt_ref
         OR proof->>'sourceRevision' IS DISTINCT FROM receipt_revision
         OR EXISTS (SELECT 1 FROM public.ai_business_evidence_chunks c
           LEFT JOIN public.ai_business_source_tool_receipts r ON r.chunk_id=c.id
           LEFT JOIN public.ai_tool_audit_logs a ON a.id=r.audit_id
           WHERE c.run_id=NEW.id AND c.source_key=source.source_key AND
             (r.chunk_id IS NULL OR r.run_id<>NEW.id OR r.source_id<>source.id
              OR r.sequence<>c.sequence OR r.response_digest<>c.payload_digest
              OR a.id IS NULL OR a.status<>'succeeded' OR a.actor_role<>'admin'
              OR a.actor_email<>NEW.owner_email OR a.surface<>'business_collection'
              OR a.response_digest IS DISTINCT FROM c.payload_digest))
         OR (SELECT count(*) FROM public.ai_business_evidence_chunks c
             WHERE c.run_id=NEW.id AND c.source_key=source.source_key)<>source.page_count
      THEN RAISE EXCEPTION 'ai_business_v3_seal_receipt_invalid'; END IF;
    END LOOP;
    RETURN NEW;
  END IF;
  IF TG_OP='UPDATE' THEN""")

DIRECTORY_GUARD = replace_once(daily.DIRECTORY_GUARD,
    "parent.status<>'collecting' OR parent.collection_status<>'manual'",
    "parent.status NOT IN ('collecting','sealed') OR parent.collection_status<>'manual'")
DIRECTORY_GUARD = replace_once(DIRECTORY_GUARD,
    "OR pages>1999 OR parent.state_json<>'{}'",
    """OR pages>1999 OR (parent.status='collecting' AND parent.state_json<>'{}')
     OR (parent.status='sealed' AND parent.state_json::jsonb->>'schemaVersion'
         IS DISTINCT FROM 'business-evidence-seal-v3')""")


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(RUN_GUARD)
        cursor.execute(DIRECTORY_GUARD)


def uninstall(apps, schema_editor):
    runs = apps.get_model("ai_assistant", "AiBusinessEvidenceRun")
    if runs.objects.filter(status="sealed", plan_json__contains='"schemaVersion":"business-evidence-v3"').exists():
        raise RuntimeError("存在已封存v3收据来源，不能逆迁移并抹除封存门禁")
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(finance.RUN_GUARD)
        cursor.execute(daily.DIRECTORY_GUARD)


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0032_business_source_tool_receipts")]
    operations = [migrations.RunPython(install, uninstall)]
