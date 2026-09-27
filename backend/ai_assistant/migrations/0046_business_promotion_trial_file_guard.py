"""Separate renderer-9 storage lane for the approved promotion trial.

The five 0029 trigger/function signatures remain fixed. Renderer 8 is reserved;
the compact volume protocol and the existing renderer-7 path are unchanged.
This migration checks structural approval, not the publisher's full-byte proof.
"""
from importlib import import_module

from django.db import migrations


previous = import_module("ai_assistant.migrations.0029_business_promotion_file_ready")
OLD_SQL = previous.NEW_SQL


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise RuntimeError("Frozen renderer-7 file SQL predecessor changed")
    return source.replace(old, new, 1)


PARENT_REQUIREMENTS = r"""CREATE FUNCTION public.ai_business_promotion_trial_parent_requirements(
  selected_report text, selected_owner text, selected_scope text)
RETURNS void LANGUAGE plpgsql VOLATILE SET search_path=pg_catalog,public AS $$
DECLARE report public.ai_report_runs%ROWTYPE; flow public.ai_workflow_runs%ROWTYPE;
  human public.ai_workflow_node_runs%ROWTYPE; decision jsonb;
  total integer; expected_output jsonb;
BEGIN
  SELECT * INTO report FROM public.ai_report_runs WHERE id=selected_report;
  SELECT * INTO flow FROM public.ai_workflow_runs WHERE id=report.workflow_id;
  IF report.id IS NULL OR flow.id IS NULL
     OR report.owner_email IS DISTINCT FROM selected_owner
     OR report.scope_json IS DISTINCT FROM selected_scope
     OR flow.owner_email IS DISTINCT FROM selected_owner
     OR flow.scope_json IS DISTINCT FROM selected_scope
     OR report.snapshot_json::jsonb->>'executionProfile' IS DISTINCT FROM
        'business-agent-screening-promotion-reference-v1'
     OR flow.status NOT IN ('queued','completed')
     OR flow.current_node_key IS NOT NULL
     OR flow.cancel_requested<>0 OR flow.dry_run<>0
  THEN RAISE EXCEPTION 'ai_promotion_trial_parent_unapproved'; END IF;
  SELECT * INTO human FROM public.ai_workflow_node_runs
    WHERE run_id=flow.id AND node_key='human_review';
  decision:=human.output_json::jsonb;
  IF human.id IS NULL OR human.status<>'completed' OR human.agent_job_id IS NOT NULL
     OR human.reviewer_email IS DISTINCT FROM selected_owner
     OR human.reviewed_at IS NULL OR human.completed_at IS NULL
     OR human.reviewed_at>human.completed_at
     OR (flow.status='completed' AND
         (flow.completed_at IS NULL OR flow.completed_at<human.completed_at))
     OR decision->>'decision' IS DISTINCT FROM 'approve'
     OR (decision-ARRAY['decision','comment'])<>'{}'::jsonb
  THEN RAISE EXCEPTION 'ai_promotion_trial_review_unapproved'; END IF;
  SELECT count(*) INTO total FROM public.ai_workflow_node_runs node
    JOIN public.ai_agent_jobs job ON job.id=node.agent_job_id
    WHERE node.run_id=flow.id AND node.node_key IN
      ('commerce','promotion','market_b2b','independent_review','report')
      AND node.status='completed' AND job.status='completed'
      AND job.workflow_run_id=flow.id AND job.workflow_node_key=node.node_key
      AND job.owner_email=selected_owner AND job.scope_json=selected_scope
      AND job.output_json=node.output_json;
  IF total<>5 THEN RAISE EXCEPTION 'ai_promotion_trial_agents_incomplete'; END IF;
  SELECT count(*),jsonb_object_agg(node_key,output_json::jsonb)
    INTO total,expected_output FROM public.ai_workflow_node_runs WHERE run_id=flow.id;
  IF total<>6 OR (flow.status='completed' AND
      (flow.output_json IS NULL OR
       flow.output_json::jsonb IS DISTINCT FROM expected_output))
  THEN RAISE EXCEPTION 'ai_promotion_trial_workflow_output_invalid'; END IF;
  SELECT count(*) INTO total FROM public.ai_workflow_events event
    WHERE event.run_id=flow.id AND event.node_key='human_review'
      AND event.event_type='review_approved'
      AND event.actor_email=selected_owner AND event.owner_email=selected_owner
      AND event.from_status='waiting_review' AND event.to_status='queued';
  IF total<>1 THEN RAISE EXCEPTION 'ai_promotion_trial_review_event_missing'; END IF;
END $$"""


READY_REQUIREMENTS = replace_once(previous.READY_REQUIREMENTS,
    "ai_business_promotion_ready_requirements(target text)",
    "ai_business_promotion_trial_ready_requirements(target text)")
READY_REQUIREMENTS = replace_once(READY_REQUIREMENTS,
    "parent.renderer_version<>7", "parent.renderer_version<>9")
READY_REQUIREMENTS = replace_once(READY_REQUIREMENTS,
    "  SELECT * INTO report FROM public.ai_report_runs WHERE id=parent.report_id;",
    "  PERFORM public.ai_business_promotion_trial_parent_requirements("
    "parent.report_id,parent.owner_email,parent.scope_json);\n"
    "  SELECT * INTO report FROM public.ai_report_runs WHERE id=parent.report_id;")

CHUNK_GUARD = OLD_SQL[0]
VOLUME_CHUNK_GUARD = replace_once(OLD_SQL[1],
    "parent.renderer_version NOT IN (4,6,7)",
    "parent.renderer_version NOT IN (4,6,7,9)")
MANIFEST_GUARD = replace_once(OLD_SQL[2],
    "parent_renderer NOT IN (4,6,7)", "parent_renderer NOT IN (4,6,7,9)")
MANIFEST_GUARD = replace_once(MANIFEST_GUARD,
    "public.ai_business_volume_uint(value->'rendererVersion',4,7)<>parent_renderer",
    "public.ai_business_volume_uint(value->'rendererVersion',4,9)<>parent_renderer")

RUN_GUARD = replace_once(OLD_SQL[3],
    "NEW.renderer_version IN (4,5,6,7) AND",
    "NEW.renderer_version IN (4,5,6,7,9) AND")
RUN_GUARD = replace_once(RUN_GUARD,
    """            END IF;
            RETURN NEW;
          END IF;
          IF TG_OP='DELETE' THEN RAISE EXCEPTION 'ai_file_delete_denied'; END IF;""",
    """            END IF;
            IF NEW.renderer_version=9 THEN
              IF NEW.draft THEN RAISE EXCEPTION 'ai_promotion_trial_draft_denied'; END IF;
              PERFORM public.ai_business_promotion_trial_parent_requirements(
                NEW.report_id,NEW.owner_email,NEW.scope_json);
              SELECT * INTO report FROM public.ai_report_runs WHERE id=NEW.report_id;
              selector:=report.snapshot_json::jsonb->'promotionSelector';
              IF jsonb_typeof(selector) IS DISTINCT FROM 'object'
                 OR NOT selector ? 'sourceKey'
                 OR selector->'views' IS DISTINCT FROM '["keyword_sku","keyword_sku_context"]'::jsonb
                 OR (selector - ARRAY['sourceKey','baselineKey','views']) <> '{}'::jsonb
                 OR NEW.binding_digest !~ '^[0-9a-f]{64}$'
              THEN RAISE EXCEPTION 'ai_promotion_trial_parent_invalid'; END IF;
            END IF;
            RETURN NEW;
          END IF;
          IF TG_OP='DELETE' THEN RAISE EXCEPTION 'ai_file_delete_denied'; END IF;""")
RUN_GUARD = replace_once(RUN_GUARD,
    """          IF TG_OP='DELETE' THEN RAISE EXCEPTION 'ai_file_delete_denied'; END IF;
          IF NEW.renderer_version IN (4,6,7) THEN""",
    """          IF TG_OP='DELETE' THEN RAISE EXCEPTION 'ai_file_delete_denied'; END IF;
          IF OLD.renderer_version=9 OR NEW.renderer_version=9 THEN
            IF NEW.renderer_version IS DISTINCT FROM OLD.renderer_version
               OR NEW.report_id IS DISTINCT FROM OLD.report_id
               OR NEW.owner_email IS DISTINCT FROM OLD.owner_email
               OR NEW.scope_json IS DISTINCT FROM OLD.scope_json
               OR NEW.binding_digest IS DISTINCT FROM OLD.binding_digest
               OR NEW.draft OR OLD.draft
            THEN RAISE EXCEPTION 'ai_promotion_trial_binding_immutable'; END IF;
            BEGIN
              PERFORM public.ai_business_promotion_trial_parent_requirements(
                NEW.report_id,NEW.owner_email,NEW.scope_json);
            EXCEPTION WHEN raise_exception THEN
              IF SQLERRM NOT IN ('ai_promotion_trial_parent_unapproved',
                  'ai_promotion_trial_review_unapproved',
                  'ai_promotion_trial_agents_incomplete',
                  'ai_promotion_trial_workflow_output_invalid',
                  'ai_promotion_trial_review_event_missing')
              THEN RAISE; END IF;
              IF NEW.status NOT IN ('paused','cancelled')
                 OR NEW.attempt IS DISTINCT FROM OLD.attempt
                 OR NEW.stored_bytes IS DISTINCT FROM OLD.stored_bytes
                 OR NEW.manifest_json IS DISTINCT FROM OLD.manifest_json
              THEN RAISE EXCEPTION 'ai_promotion_trial_parent_changed_no_progress'; END IF;
            END;
          END IF;
          IF NEW.renderer_version IN (4,6,7) THEN""")
RUN_GUARD = replace_once(RUN_GUARD,
    "IF NEW.renderer_version IN (4,6,7) THEN",
    "IF NEW.renderer_version IN (4,6,7,9) THEN")
RUN_GUARD = replace_once(RUN_GUARD,
    """              IF NEW.renderer_version=7 THEN
                IF OLD.status<>'paused' OR OLD.error_code<>'renderer_unpublished'
                   OR OLD.progress_json::jsonb->>'stage' IS DISTINCT FROM 'staged_unpublished'
                   OR NEW.attempt<>OLD.attempt OR NEW.stored_bytes<>OLD.stored_bytes
                   OR NEW.manifest_json IS DISTINCT FROM OLD.manifest_json
                THEN RAISE EXCEPTION 'ai_promotion_ready_requires_staged_attempt'; END IF;
              ELSIF OLD.status<>'building' THEN RAISE EXCEPTION 'ai_volume_manifest_invalid'; END IF;""",
    """              IF NEW.renderer_version IN (7,9) THEN
                IF OLD.status<>'paused' OR OLD.error_code<>'renderer_unpublished'
                   OR OLD.progress_json::jsonb->>'stage' IS DISTINCT FROM 'staged_unpublished'
                   OR NEW.attempt<>OLD.attempt OR NEW.stored_bytes<>OLD.stored_bytes
                   OR NEW.manifest_json IS DISTINCT FROM OLD.manifest_json
                THEN RAISE EXCEPTION 'ai_promotion_ready_requires_staged_attempt'; END IF;
              ELSIF OLD.status<>'building' THEN RAISE EXCEPTION 'ai_volume_manifest_invalid'; END IF;""")

COMPLETE_GUARD = replace_once(OLD_SQL[4],
    "IF parent.renderer_version NOT IN (4,6,7) THEN RETURN NULL; END IF;",
    "IF parent.renderer_version NOT IN (4,6,7,9) THEN RETURN NULL; END IF;")
COMPLETE_GUARD = replace_once(COMPLETE_GUARD,
    """            IF parent.renderer_version=7 THEN
              PERFORM public.ai_business_promotion_ready_requirements(parent.id);
            END IF;""",
    """            IF parent.renderer_version=7 THEN
              PERFORM public.ai_business_promotion_ready_requirements(parent.id);
            ELSIF parent.renderer_version=9 THEN
              PERFORM public.ai_business_promotion_trial_ready_requirements(parent.id);
            END IF;""")

NEW_SQL = (CHUNK_GUARD, VOLUME_CHUNK_GUARD, MANIFEST_GUARD,
           RUN_GUARD, COMPLETE_GUARD)


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    import_module("ai_assistant.migrations.0017_business_file_renderer").change_constraint(
        schema_editor, "1,2,3,4,5,6,7,9")
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(PARENT_REQUIREMENTS)
        cursor.execute(READY_REQUIREMENTS)
        for signature in (
            "public.ai_business_promotion_trial_parent_requirements(text,text,text)",
            "public.ai_business_promotion_trial_ready_requirements(text)",
        ):
            cursor.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
            cursor.execute("DO $$ BEGIN IF to_regrole('teruisi_ai_writer') IS NOT NULL THEN "
                f"GRANT EXECUTE ON FUNCTION {signature} TO teruisi_ai_writer; "
                "END IF; END $$")
        for definition in NEW_SQL:
            cursor.execute(definition)


def uninstall(apps, schema_editor):
    if apps.get_model("ai_assistant", "AiBusinessFileRun").objects.filter(renderer_version=9).exists():
        raise RuntimeError("存在 renderer 9 文件任务（含未完成、取消或终态），禁止逆迁移")
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for definition in OLD_SQL:
            cursor.execute(definition)
        cursor.execute("DROP FUNCTION public.ai_business_promotion_trial_ready_requirements(text)")
        cursor.execute("DROP FUNCTION public.ai_business_promotion_trial_parent_requirements(text,text,text)")
    import_module("ai_assistant.migrations.0017_business_file_renderer").change_constraint(
        schema_editor, "1,2,3,4,5,6,7")


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0045_business_market_v2_material_attestation")]
    operations = [migrations.RunPython(install, uninstall)]
