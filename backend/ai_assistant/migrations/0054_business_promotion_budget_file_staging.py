"""Renderer-10 durable staging lane; ready is deliberately impossible.

The existing PostgreSQL volume guard checks immutable chunks and compact
layout, but does not independently verify all v10 XLSX/HTML semantic bytes.
This migration therefore permits only queued/building/paused/cancelled v10
rows. A later, separately reviewed migration must establish an independent
publication gate before any v10 ready transition is possible.
"""
from importlib import import_module

from django.db import migrations


previous = import_module("ai_assistant.migrations.0046_business_promotion_trial_file_guard")
OLD_SQL = previous.NEW_SQL
replace_once = previous.replace_once


BUDGET_PARENT_REQUIREMENTS = r"""CREATE FUNCTION public.ai_business_promotion_budget_parent_requirements(
  selected_report text, selected_owner text, selected_scope text)
RETURNS void LANGUAGE plpgsql VOLATILE SET search_path=pg_catalog,public AS $$
DECLARE report public.ai_report_runs%ROWTYPE;
  budget public.ai_business_budget_plans%ROWTYPE; snapshot jsonb;
BEGIN
  PERFORM public.ai_business_promotion_trial_parent_requirements(
    selected_report,selected_owner,selected_scope);
  SELECT * INTO report FROM public.ai_report_runs WHERE id=selected_report;
  snapshot:=report.snapshot_json::jsonb;
  IF (snapshot ? 'budgetRef') IS DISTINCT FROM (report.budget_plan_id IS NOT NULL)
  THEN RAISE EXCEPTION 'ai_promotion_budget_presence_mismatch'; END IF;
  IF report.budget_plan_id IS NOT NULL THEN
    SELECT * INTO budget FROM public.ai_business_budget_plans
      WHERE id=report.budget_plan_id;
    IF budget.id IS NULL OR budget.owner_email IS DISTINCT FROM selected_owner
       OR budget.scope_json IS DISTINCT FROM selected_scope
       OR budget.evidence_id IS DISTINCT FROM snapshot->>'evidenceRunId'
       OR budget.evidence_version IS DISTINCT FROM
          (snapshot->>'evidenceVersion')::integer
       OR jsonb_typeof(snapshot->'budgetRef') IS DISTINCT FROM 'object'
       OR ((snapshot->'budgetRef') - ARRAY['schemaVersion','id','planDigest','bindingDigest'])<>'{}'::jsonb
       OR snapshot#>>'{budgetRef,schemaVersion}' IS DISTINCT FROM
          'business-budget-reference-v1'
       OR snapshot#>>'{budgetRef,id}' IS DISTINCT FROM budget.id
       OR snapshot#>>'{budgetRef,planDigest}' IS DISTINCT FROM budget.plan_digest
       OR snapshot#>>'{budgetRef,bindingDigest}' IS DISTINCT FROM budget.binding_digest
    THEN RAISE EXCEPTION 'ai_promotion_budget_root_mismatch'; END IF;
  END IF;
END $$"""


CHUNK_GUARD = OLD_SQL[0]
VOLUME_CHUNK_GUARD = replace_once(OLD_SQL[1],
    "parent.renderer_version NOT IN (4,6,7,9)",
    "parent.renderer_version NOT IN (4,6,7,9,10)")
MANIFEST_GUARD = replace_once(OLD_SQL[2],
    "parent_renderer NOT IN (4,6,7,9)",
    "parent_renderer NOT IN (4,6,7,9,10)")
MANIFEST_GUARD = replace_once(MANIFEST_GUARD,
    "public.ai_business_volume_uint(value->'rendererVersion',4,9)<>parent_renderer",
    "public.ai_business_volume_uint(value->'rendererVersion',4,10)<>parent_renderer")

RUN_GUARD = replace_once(OLD_SQL[3],
    "NEW.renderer_version IN (4,5,6,7,9) AND",
    "NEW.renderer_version IN (4,5,6,7,9,10) AND")
RUN_GUARD = replace_once(RUN_GUARD,
    """            END IF;
            RETURN NEW;
          END IF;
          IF TG_OP='DELETE' THEN RAISE EXCEPTION 'ai_file_delete_denied'; END IF;""",
    """            END IF;
            IF NEW.renderer_version=10 THEN
              IF NEW.draft OR NEW.binding_digest !~ '^[0-9a-f]{64}$'
              THEN RAISE EXCEPTION 'ai_promotion_budget_initial_invalid'; END IF;
              PERFORM public.ai_business_promotion_budget_parent_requirements(
                NEW.report_id,NEW.owner_email,NEW.scope_json);
            END IF;
            RETURN NEW;
          END IF;
          IF TG_OP='DELETE' THEN RAISE EXCEPTION 'ai_file_delete_denied'; END IF;""")
RUN_GUARD = replace_once(RUN_GUARD,
    """          END IF;
          IF NEW.renderer_version IN (4,6,7,9) THEN""",
    """          END IF;
          IF OLD.renderer_version=10 OR NEW.renderer_version=10 THEN
            IF NEW.renderer_version IS DISTINCT FROM OLD.renderer_version
               OR NEW.report_id IS DISTINCT FROM OLD.report_id
               OR NEW.owner_email IS DISTINCT FROM OLD.owner_email
               OR NEW.scope_json IS DISTINCT FROM OLD.scope_json
               OR NEW.binding_digest IS DISTINCT FROM OLD.binding_digest
               OR NEW.draft OR OLD.draft
            THEN RAISE EXCEPTION 'ai_promotion_budget_binding_immutable'; END IF;
            BEGIN
              PERFORM public.ai_business_promotion_budget_parent_requirements(
                NEW.report_id,NEW.owner_email,NEW.scope_json);
            EXCEPTION WHEN raise_exception THEN
              IF SQLERRM NOT IN ('ai_promotion_trial_parent_unapproved',
                  'ai_promotion_trial_review_unapproved',
                  'ai_promotion_trial_agents_incomplete',
                  'ai_promotion_trial_workflow_output_invalid',
                  'ai_promotion_trial_review_event_missing',
                  'ai_promotion_budget_presence_mismatch',
                  'ai_promotion_budget_root_mismatch')
              THEN RAISE; END IF;
              IF NEW.status NOT IN ('paused','cancelled')
                 OR NEW.attempt IS DISTINCT FROM OLD.attempt
                 OR NEW.stored_bytes IS DISTINCT FROM OLD.stored_bytes
                 OR NEW.manifest_json IS DISTINCT FROM OLD.manifest_json
              THEN RAISE EXCEPTION 'ai_promotion_budget_parent_changed_no_progress'; END IF;
            END;
          END IF;
          IF NEW.renderer_version IN (4,6,7,9) THEN""")
RUN_GUARD = replace_once(RUN_GUARD,
    "IF NEW.renderer_version IN (4,6,7,9) THEN",
    "IF NEW.renderer_version IN (4,6,7,9,10) THEN")
RUN_GUARD = replace_once(RUN_GUARD,
    """            IF NEW.status='ready' THEN
              IF NEW.renderer_version IN (7,9) THEN""",
    """            IF NEW.status='ready' THEN
              IF NEW.renderer_version=10 THEN
                RAISE EXCEPTION 'ai_promotion_budget_renderer_unpublished';
              END IF;
              IF NEW.renderer_version IN (7,9) THEN""")

COMPLETE_GUARD = replace_once(OLD_SQL[4],
    "IF parent.renderer_version NOT IN (4,6,7,9) THEN RETURN NULL; END IF;",
    "IF parent.renderer_version NOT IN (4,6,7,9,10) THEN RETURN NULL; END IF;")
COMPLETE_GUARD = replace_once(COMPLETE_GUARD,
    """          IF parent.status='ready' THEN
            IF parent.renderer_version=7 THEN""",
    """          IF parent.status='ready' THEN
            IF parent.renderer_version=10 THEN
              RAISE EXCEPTION 'ai_promotion_budget_renderer_unpublished';
            END IF;
            IF parent.renderer_version=7 THEN""")

NEW_SQL = (CHUNK_GUARD, VOLUME_CHUNK_GUARD, MANIFEST_GUARD,
           RUN_GUARD, COMPLETE_GUARD)


def _verify_predecessor(cursor):
    for definition in (*OLD_SQL, previous.PARENT_REQUIREMENTS,
            previous.READY_REQUIREMENTS):
        name = definition.split("FUNCTION ", 1)[1].split("(", 1)[0].removeprefix("public.")
        cursor.execute("SELECT p.prosrc FROM pg_proc p JOIN pg_namespace n "
            "ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname=%s", [name])
        rows = cursor.fetchall()
        if len(rows) != 1 or rows[0][0] != definition.split("$$", 2)[1]:
            raise RuntimeError("Frozen renderer-9 PostgreSQL function changed")


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        _verify_predecessor(cursor)
    import_module("ai_assistant.migrations.0017_business_file_renderer").change_constraint(
        schema_editor, "1,2,3,4,5,6,7,9,10")
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(BUDGET_PARENT_REQUIREMENTS)
        signature = "public.ai_business_promotion_budget_parent_requirements(text,text,text)"
        cursor.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        cursor.execute("DO $$ BEGIN IF to_regrole('teruisi_ai_writer') IS NOT NULL THEN "
            f"GRANT EXECUTE ON FUNCTION {signature} TO teruisi_ai_writer; "
            "END IF; END $$")
        for definition in NEW_SQL:
            cursor.execute(definition)


def uninstall(apps, schema_editor):
    if apps.get_model("ai_assistant", "AiBusinessFileRun").objects.filter(
            renderer_version=10).exists():
        raise RuntimeError("存在 renderer 10 文件任务，禁止逆迁移")
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for definition in OLD_SQL:
            cursor.execute(definition)
        cursor.execute("DROP FUNCTION public.ai_business_promotion_budget_parent_requirements(text,text,text)")
    import_module("ai_assistant.migrations.0017_business_file_renderer").change_constraint(
        schema_editor, "1,2,3,4,5,6,7,9")


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0053_business_market_v2_admitted_paused")]
    operations = [migrations.RunPython(install, uninstall)]
