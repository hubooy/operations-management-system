"""Renderer-7 ready transition after owning verification; old 1-6 unchanged.

The database independently checks parent/version/attempt/chunk layout and the
persisted approval/job structure. File SHA and numeric proof semantics remain
the owning writer's responsibility, as in the prior multi-volume protocol.
"""
from importlib import import_module

from django.db import migrations


previous = import_module("ai_assistant.migrations.0027_business_promotion_file_guard")
OLD_SQL = previous.NEW_SQL


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise RuntimeError("Frozen renderer-7 staging SQL predecessor changed")
    return source.replace(old, new, 1)


CHUNK_GUARD = OLD_SQL[0]
VOLUME_CHUNK_GUARD = OLD_SQL[1]
MANIFEST_GUARD = replace_once(OLD_SQL[2],
    "parent_renderer NOT IN (4,6)", "parent_renderer NOT IN (4,6,7)")
MANIFEST_GUARD = replace_once(MANIFEST_GUARD,
    "public.ai_business_volume_uint(value->'rendererVersion',4,6)<>parent_renderer",
    "public.ai_business_volume_uint(value->'rendererVersion',4,7)<>parent_renderer")

READY_REQUIREMENTS = r"""CREATE FUNCTION ai_business_promotion_ready_requirements(target text)
RETURNS void LANGUAGE plpgsql VOLATILE SET search_path=pg_catalog,public AS $$
DECLARE parent public.ai_business_file_runs%ROWTYPE; report public.ai_report_runs%ROWTYPE;
  flow public.ai_workflow_runs%ROWTYPE; human public.ai_workflow_node_runs%ROWTYPE;
  total integer; decision jsonb; expected_output jsonb;
BEGIN
  SELECT * INTO parent FROM public.ai_business_file_runs WHERE id=target;
  IF NOT FOUND OR parent.renderer_version<>7 OR parent.status<>'ready' OR parent.draft
     OR parent.error_code<>'' OR parent.progress_json::jsonb->>'stage' IS DISTINCT FROM 'ready'
  THEN RAISE EXCEPTION 'ai_promotion_ready_state_invalid'; END IF;
  SELECT * INTO report FROM public.ai_report_runs WHERE id=parent.report_id;
  SELECT * INTO flow FROM public.ai_workflow_runs WHERE id=report.workflow_id;
  IF report.id IS NULL OR flow.id IS NULL OR report.owner_email<>parent.owner_email
     OR report.scope_json<>parent.scope_json OR flow.owner_email<>parent.owner_email
     OR flow.scope_json<>parent.scope_json
     OR report.snapshot_json::jsonb->>'executionProfile' IS DISTINCT FROM
        'business-agent-screening-promotion-reference-v1'
     OR flow.status<>'completed' OR flow.completed_at IS NULL
     OR flow.output_json IS NULL
     OR flow.current_node_key IS NOT NULL
     OR flow.cancel_requested<>0 OR flow.dry_run<>0
  THEN RAISE EXCEPTION 'ai_promotion_ready_parent_invalid'; END IF;
  SELECT * INTO human FROM public.ai_workflow_node_runs
    WHERE run_id=flow.id AND node_key='human_review';
  decision:=human.output_json::jsonb;
  IF human.id IS NULL OR human.status<>'completed' OR human.agent_job_id IS NOT NULL
     OR human.reviewer_email IS DISTINCT FROM parent.owner_email
     OR human.reviewed_at IS NULL OR human.completed_at IS NULL
     OR human.reviewed_at>human.completed_at
     OR flow.completed_at<human.completed_at
     OR decision->>'decision' IS DISTINCT FROM 'approve'
     OR (decision-ARRAY['decision','comment'])<>'{}'::jsonb
  THEN RAISE EXCEPTION 'ai_promotion_ready_review_invalid'; END IF;
  SELECT count(*) INTO total FROM public.ai_workflow_node_runs node
    JOIN public.ai_agent_jobs job ON job.id=node.agent_job_id
    WHERE node.run_id=flow.id AND node.node_key IN
      ('commerce','promotion','market_b2b','independent_review','report')
      AND node.status='completed' AND job.status='completed'
      AND job.workflow_run_id=flow.id AND job.workflow_node_key=node.node_key
      AND job.owner_email=parent.owner_email AND job.scope_json=parent.scope_json
      AND job.output_json=node.output_json;
  IF total<>5 THEN RAISE EXCEPTION 'ai_promotion_ready_agents_incomplete'; END IF;
  SELECT count(*),jsonb_object_agg(node_key,output_json::jsonb)
    INTO total,expected_output FROM public.ai_workflow_node_runs
    WHERE run_id=flow.id;
  IF total<>6 OR flow.output_json::jsonb IS DISTINCT FROM expected_output
  THEN RAISE EXCEPTION 'ai_promotion_ready_workflow_output_invalid'; END IF;
  SELECT count(*) INTO total FROM public.ai_workflow_events event
    WHERE event.run_id=flow.id AND event.node_key='human_review'
      AND event.event_type='review_approved'
      AND event.actor_email=parent.owner_email
      AND event.owner_email=parent.owner_email
      AND event.from_status='waiting_review' AND event.to_status='queued';
  IF total<>1 THEN RAISE EXCEPTION 'ai_promotion_ready_review_event_missing'; END IF;
END $$"""

RUN_GUARD = replace_once(OLD_SQL[3],
    """            IF NEW.renderer_version=7 AND NEW.status='ready' THEN
              RAISE EXCEPTION 'ai_promotion_renderer_unpublished';
            END IF;
            IF NEW.status='ready' THEN
              IF OLD.status<>'building' THEN RAISE EXCEPTION 'ai_volume_manifest_invalid'; END IF;
              PERFORM public.ai_business_volume_manifest_check(NEW.id,NEW.attempt,NEW.binding_digest,NEW.draft,NEW.manifest_json);
            END IF;""",
    """            IF NEW.status='ready' THEN
              IF NEW.renderer_version=7 THEN
                IF OLD.status<>'paused' OR OLD.error_code<>'renderer_unpublished'
                   OR OLD.progress_json::jsonb->>'stage' IS DISTINCT FROM 'staged_unpublished'
                   OR NEW.attempt<>OLD.attempt OR NEW.stored_bytes<>OLD.stored_bytes
                   OR NEW.manifest_json IS DISTINCT FROM OLD.manifest_json
                THEN RAISE EXCEPTION 'ai_promotion_ready_requires_staged_attempt'; END IF;
              ELSIF OLD.status<>'building' THEN RAISE EXCEPTION 'ai_volume_manifest_invalid'; END IF;
              PERFORM public.ai_business_volume_manifest_check(NEW.id,NEW.attempt,NEW.binding_digest,NEW.draft,NEW.manifest_json);
            END IF;""")

COMPLETE_GUARD = replace_once(OLD_SQL[4],
    """          IF parent.renderer_version=7 AND parent.status='ready' THEN
            RAISE EXCEPTION 'ai_promotion_renderer_unpublished';
          END IF;
          IF parent.status='ready' THEN""",
    """          IF parent.status='ready' THEN""")
COMPLETE_GUARD = replace_once(COMPLETE_GUARD,
    """          IF parent.status='ready' THEN
            PERFORM public.ai_business_volume_manifest_check""",
    """          IF parent.status='ready' THEN
            IF parent.renderer_version=7 THEN
              PERFORM public.ai_business_promotion_ready_requirements(parent.id);
            END IF;
            PERFORM public.ai_business_volume_manifest_check""")

NEW_SQL = (CHUNK_GUARD, VOLUME_CHUNK_GUARD, MANIFEST_GUARD, RUN_GUARD, COMPLETE_GUARD)


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(READY_REQUIREMENTS)
        for definition in NEW_SQL:
            cursor.execute(definition)


def uninstall(apps, schema_editor):
    if apps.get_model("ai_assistant", "AiBusinessFileRun").objects.filter(renderer_version=7).exists():
        raise RuntimeError("存在 renderer 7 文件任务（含未完成或终态），禁止逆迁移")
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for definition in OLD_SQL:
            cursor.execute(definition)
        cursor.execute("DROP FUNCTION ai_business_promotion_ready_requirements(text)")


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0028_business_finance_v3_gate")]
    operations = [migrations.RunPython(install, uninstall)]
