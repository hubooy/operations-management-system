"""Version the 0047 receipt writer for bounded finance segment candidates.

This changes one function body. It grants no new role, table, reader or seal
authority; the existing claim, append-only receipt and direct-seal denial stay.
"""
from importlib import import_module

from django.db import migrations


previous = import_module("ai_assistant.migrations.0047_business_v4_sealer_replay_progress")


def _replace_once(body, old, new):
    if body.count(old) != 1:
        raise RuntimeError("0048 requires the frozen 0047 writer body")
    return body.replace(old, new)


RECORD = _replace_once(previous.RECORD,
    """     OR source.domain<>'netshop' OR NOT source.finished
     OR source.query_json::jsonb->>'platform' IS DISTINCT FROM '京东'
     OR source.query_json::jsonb->>'dataset' IS DISTINCT FROM 'promotion'
""",
    """     OR source.domain NOT IN ('netshop','finance') OR NOT source.finished
     OR (source.domain='netshop' AND (
       source.query_json::jsonb->>'platform' IS DISTINCT FROM '京东'
       OR source.query_json::jsonb->>'dataset' IS DISTINCT FROM 'promotion'))
     OR (source.domain='finance' AND (
       source.temporal_role IS DISTINCT FROM 'monthly_context'
       OR source.query_json IS DISTINCT FROM
         public.ai_v4_replay_canonical(source.query_json::jsonb)
       OR source.query_digest IS DISTINCT FROM
         encode(sha256(convert_to(source.query_json,'UTF8')),'hex')
       OR jsonb_typeof(source.query_json::jsonb) IS DISTINCT FROM 'object'
       OR NOT (source.query_json::jsonb ?&
         ARRAY['months','scope','analysisPeriod'])
       OR (SELECT count(*) FROM jsonb_object_keys(source.query_json::jsonb))<>3))
""")

_candidate_start = RECORD.index("  IF NOT candidate ?& ARRAY[")
_candidate_end = RECORD.index("  digest_text:=", _candidate_start)
RECORD = RECORD[:_candidate_start] + """  IF candidate->>'schemaVersion' =
      'business-v4-sealer-promotion-segment-candidate-v2' THEN
    IF source.domain IS DISTINCT FROM 'netshop'
       OR NOT candidate ?& ARRAY['schemaVersion','runId','attemptId','sourceId',
         'sourceRoot','sourceKey','sourceRef','sourceRevision','sourceVersion',
         'keyId','segmentIndex','endSequence','segmentProofDigest','progress',
         'candidateOnly','authorityVerified','financeReplayed',
         'sourceMetadataVerified','upstreamSignatureVerified','sealCommitted',
         'previousCandidateDigest','candidateDigest']
       OR (SELECT count(*) FROM jsonb_object_keys(candidate))<>22
       OR candidate->'financeReplayed' IS DISTINCT FROM 'false'::jsonb
       OR candidate->'sourceMetadataVerified' IS DISTINCT FROM 'false'::jsonb
    THEN RAISE EXCEPTION 'ai_v4_replay_progress_candidate_invalid'; END IF;
  ELSIF candidate->>'schemaVersion' =
      'business-v4-sealer-finance-segment-candidate-v2' THEN
    IF source.domain IS DISTINCT FROM 'finance'
       OR NOT candidate ?& ARRAY['schemaVersion','runId','attemptId','sourceId',
         'sourceRoot','sourceKey','sourceRef','sourceRevision','sourceVersion',
         'keyId','segmentIndex','endSequence','segmentProofDigest','progress',
         'candidateOnly','authorityVerified','financeReplayed',
         'upstreamSignatureVerified','sealCommitted','previousCandidateDigest',
         'candidateDigest']
       OR (SELECT count(*) FROM jsonb_object_keys(candidate))<>21
       OR candidate->'financeReplayed' IS DISTINCT FROM 'true'::jsonb
    THEN RAISE EXCEPTION 'ai_v4_replay_progress_candidate_invalid'; END IF;
  ELSE RAISE EXCEPTION 'ai_v4_replay_progress_candidate_invalid'; END IF;
  IF candidate->'candidateOnly' IS DISTINCT FROM 'true'::jsonb
     OR candidate->'authorityVerified' IS DISTINCT FROM 'false'::jsonb
     OR candidate->'upstreamSignatureVerified' IS DISTINCT FROM 'false'::jsonb
     OR candidate->'sealCommitted' IS DISTINCT FROM 'false'::jsonb
     OR candidate->>'runId' IS DISTINCT FROM selected_run
     OR candidate->>'attemptId' IS DISTINCT FROM selected_attempt
     OR candidate->>'sourceId' IS DISTINCT FROM selected_source
     OR candidate->>'sourceRoot' IS DISTINCT FROM ticket.source_root
     OR candidate->>'sourceKey' IS DISTINCT FROM source.source_key
     OR candidate->>'sourceRef' IS DISTINCT FROM source.source_ref
     OR candidate->>'sourceRevision' IS DISTINCT FROM source.source_revision
     OR candidate->'sourceVersion' IS DISTINCT FROM to_jsonb(source.version)
     OR candidate->>'keyId' IS DISTINCT FROM attempt.key_id
     OR candidate->'segmentIndex' IS DISTINCT FROM to_jsonb(selected_index)
     OR candidate->'endSequence' IS DISTINCT FROM to_jsonb(segment.end_sequence)
     OR candidate->>'segmentProofDigest' IS DISTINCT FROM segment.proof_digest
     OR coalesce(candidate->>'candidateDigest','') !~ '^[0-9a-f]{64}$'
     OR coalesce(candidate->>'previousCandidateDigest','') !~ '^[0-9a-f]{64}$'
  THEN RAISE EXCEPTION 'ai_v4_replay_progress_candidate_invalid'; END IF;
""" + RECORD[_candidate_end:]

_progress_start = RECORD.index("  IF jsonb_typeof(finite)<>'object' OR NOT finite ?& ARRAY[")
_progress_end = RECORD.index("  IF selected_index=1 THEN", _progress_start)
RECORD = RECORD[:_progress_start] + """  IF jsonb_typeof(finite) IS DISTINCT FROM 'object'
     OR progress->>'schemaVersion' IS DISTINCT FROM
       'business-v4-validation-progress-candidate-v1'
     OR progress->>'sourceKey' IS DISTINCT FROM source.source_key
     OR progress->>'domain' IS DISTINCT FROM source.domain
     OR progress->>'sourceRef' IS DISTINCT FROM source.source_ref
     OR progress->>'sourceRevision' IS DISTINCT FROM source.source_revision
     OR finite->'pageCount' IS DISTINCT FROM progress->'pageCount'
     OR finite->'rowCount' IS DISTINCT FROM progress->'rowCount'
     OR finite->'storedBytes' IS DISTINCT FROM progress->'storedBytes'
     OR finite->'lastChunkDigest' IS DISTINCT FROM progress->'lastChunkDigest'
     OR finite->'receiptChainDigest' IS DISTINCT FROM progress->'receiptChainDigest'
     OR finite->'pageCount' IS DISTINCT FROM to_jsonb(segment.end_sequence)
     OR segment.start_sequence<>(selected_index-1)*16+1
     OR segment.end_sequence<>least(selected_index*16,source.page_count)
  THEN RAISE EXCEPTION 'ai_v4_replay_progress_mismatch'; END IF;
  IF source.domain='netshop' THEN
    IF NOT finite ?& ARRAY['pageCount','rowCount','storedBytes',
         'lastChunkDigest','receiptChainDigest','verifier','observedDates']
       OR (SELECT count(*) FROM jsonb_object_keys(finite))<>7
       OR finite->'verifier' IS DISTINCT FROM progress->'domainState'->'verifier'
       OR finite->'observedDates' IS DISTINCT FROM
         progress->'domainState'->'observedDates'
    THEN RAISE EXCEPTION 'ai_v4_replay_progress_mismatch'; END IF;
  ELSE
    IF NOT finite ?& ARRAY['pageCount','rowCount','storedBytes',
         'lastChunkDigest','receiptChainDigest','financeState']
       OR (SELECT count(*) FROM jsonb_object_keys(finite))<>6
       OR jsonb_typeof(finite->'financeState') IS DISTINCT FROM 'object'
       OR finite->'financeState' IS DISTINCT FROM progress->'domainState'
       OR finite->'financeState'->>'schemaVersion' IS DISTINCT FROM
         'business-v4-finance-collection-checkpoint-v1'
       OR finite->'financeState'->>'queryDigest' IS DISTINCT FROM source.query_digest
       OR finite->'financeState'->>'sourceRef' IS DISTINCT FROM source.source_ref
       OR finite->'financeState'->>'sourceRevision' IS DISTINCT FROM
         source.source_revision
       OR finite->'financeState'->'pageCount' IS DISTINCT FROM
         to_jsonb(segment.end_sequence)
       OR finite->'financeState'->'rowsRead' IS DISTINCT FROM finite->'rowCount'
       OR finite->'financeState'->'storedBytes' IS DISTINCT FROM finite->'storedBytes'
       OR finite->'financeState'->'persistentEvidenceVerified' IS DISTINCT FROM
         'false'::jsonb
       OR finite->'financeState'->'finished' IS DISTINCT FROM
         to_jsonb(segment.end_sequence=source.page_count)
    THEN RAISE EXCEPTION 'ai_v4_replay_progress_mismatch'; END IF;
  END IF;
""" + RECORD[_progress_end:]


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,"
            "rolcreaterole,rolreplication,rolbypassrls FROM pg_catalog.pg_roles "
            "WHERE rolname=%s", [previous.SEALER])
        if cursor.fetchone() != (False,) * 7:
            raise RuntimeError("0048 requires unchanged NOLOGIN seal writer")
        cursor.execute("SELECT has_function_privilege(%s,%s,'EXECUTE')",
            [previous.SEALER,
             "public.ai_v4_commit_seal(text,text,bigint,text,text,text,text)"])
        if cursor.fetchone() != (False,):
            raise RuntimeError("0048 direct seal commit must remain revoked")
        cursor.execute("SELECT p.prosrc,p.prosecdef,p.proconfig,"
            "pg_catalog.pg_get_userbyid(p.proowner) FROM pg_catalog.pg_proc p "
            "WHERE p.oid=to_regprocedure(%s)", [previous.WRITE])
        current = cursor.fetchone()
        if (current is None or current[0] != previous.RECORD.split("$$")[1]
                or current[1] is not True
                or {item.replace(" ", "") for item in (current[2] or [])}
                    != {"search_path=pg_catalog,public"}
                or current[3] in {"teruisi_ai_reader", "teruisi_ai_writer",
                    previous.SEALER}):
            raise RuntimeError("0048 requires the frozen 0047 writer")
        for role, allowed in ((previous.SEALER, True),
                              ("teruisi_ai_reader", False),
                              ("teruisi_ai_writer", False)):
            cursor.execute("SELECT to_regrole(%s)", [role])
            if cursor.fetchone()[0] is None:
                if role == previous.SEALER:
                    raise RuntimeError("0048 requires the independent sealer role")
                continue
            cursor.execute("SELECT has_function_privilege(%s,%s,'EXECUTE'),"
                "has_table_privilege(%s,%s,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')",
                [role, previous.WRITE, role, previous.TABLE])
            if cursor.fetchone() != (allowed, False):
                raise RuntimeError("0048 requires unchanged 0047 ACL")
        cursor.execute(RECORD.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT EXISTS(SELECT 1 FROM " + previous.TABLE +
            " WHERE candidate_json::jsonb->>'schemaVersion'=%s)",
            ["business-v4-sealer-finance-segment-candidate-v2"])
        if cursor.fetchone()[0]:
            raise RuntimeError("0048 cannot discard finance replay progress")
        cursor.execute(previous.RECORD.replace("CREATE FUNCTION",
            "CREATE OR REPLACE FUNCTION", 1))


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0047_business_v4_sealer_replay_progress")]
    operations = [migrations.RunPython(install, uninstall)]
