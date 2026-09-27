"""Fail-closed renderer 7 storage lane; publication remains unregistered.

The five 0025 predecessor functions and their public signatures stay frozen.
Renderer 7 may stage bounded multi-volume chunks only for an immutable 0026
promotion report. No renderer 7 run can become ready in this migration.
"""
from importlib import import_module

from django.db import migrations


previous = import_module("ai_assistant.migrations.0025_business_file_opc")
profile = import_module("ai_assistant.migrations.0026_business_promotion_profile")
OLD_SQL = previous.NEW_SQL


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise RuntimeError("Frozen renderer-6 SQL predecessor changed")
    return source.replace(old, new, 1)


# Legacy single-file chunks and the full compact manifest validator remain
# byte-identical. The latter itself rejects 7, independent of the ready gate.
CHUNK_GUARD = OLD_SQL[0]
MANIFEST_GUARD = OLD_SQL[2]
VOLUME_CHUNK_GUARD = replace_once(OLD_SQL[1],
    "parent.renderer_version NOT IN (4,6)", "parent.renderer_version NOT IN (4,6,7)")

RUN_GUARD = replace_once(OLD_SQL[3],
    "NEW.renderer_version IN (4,5,6) AND", "NEW.renderer_version IN (4,5,6,7) AND")
RUN_GUARD = replace_once(RUN_GUARD,
    "DECLARE value jsonb; kind text; n bigint; lo bigint; hi bigint; bytes bigint;",
    "DECLARE value jsonb; kind text; n bigint; lo bigint; hi bigint; bytes bigint; report public.ai_report_runs%ROWTYPE; selector jsonb;")
RUN_GUARD = replace_once(RUN_GUARD,
    """            RETURN NEW;
          END IF;
          IF TG_OP='DELETE' THEN RAISE EXCEPTION 'ai_file_delete_denied'; END IF;""",
    """            IF NEW.renderer_version=7 THEN
              SELECT * INTO report FROM public.ai_report_runs WHERE id=NEW.report_id;
              selector:=report.snapshot_json::jsonb->'promotionSelector';
              IF NOT FOUND OR report.owner_email IS DISTINCT FROM NEW.owner_email
                 OR report.scope_json IS DISTINCT FROM NEW.scope_json
                 OR report.snapshot_json::jsonb->>'executionProfile' IS DISTINCT FROM
                    'business-agent-screening-promotion-reference-v1'
                 OR jsonb_typeof(selector) IS DISTINCT FROM 'object'
                 OR NOT selector ? 'sourceKey'
                 OR selector->'views' IS DISTINCT FROM '["keyword_sku","keyword_sku_context"]'::jsonb
                 OR (selector - ARRAY['sourceKey','baselineKey','views']) <> '{}'::jsonb
                 OR NEW.binding_digest !~ '^[0-9a-f]{64}$'
              THEN RAISE EXCEPTION 'ai_promotion_file_parent_invalid'; END IF;
            END IF;
            RETURN NEW;
          END IF;
          IF TG_OP='DELETE' THEN RAISE EXCEPTION 'ai_file_delete_denied'; END IF;""")
RUN_GUARD = replace_once(RUN_GUARD,
    "IF NEW.renderer_version IN (4,6) THEN",
    "IF NEW.renderer_version IN (4,6,7) THEN")
RUN_GUARD = replace_once(RUN_GUARD,
    """            IF NEW.status='ready' THEN
              IF OLD.status<>'building' THEN RAISE EXCEPTION 'ai_volume_manifest_invalid'; END IF;""",
    """            IF NEW.renderer_version=7 AND NEW.status='ready' THEN
              RAISE EXCEPTION 'ai_promotion_renderer_unpublished';
            END IF;
            IF NEW.status='ready' THEN
              IF OLD.status<>'building' THEN RAISE EXCEPTION 'ai_volume_manifest_invalid'; END IF;""")

COMPLETE_GUARD = replace_once(OLD_SQL[4],
    "IF parent.renderer_version NOT IN (4,6) THEN RETURN NULL; END IF;",
    "IF parent.renderer_version NOT IN (4,6,7) THEN RETURN NULL; END IF;")
COMPLETE_GUARD = replace_once(COMPLETE_GUARD,
    """          IF parent.status='ready' THEN
            PERFORM public.ai_business_volume_manifest_check(parent.id,parent.attempt,parent.binding_digest,parent.draft,parent.manifest_json);
          END IF;""",
    """          IF parent.renderer_version=7 AND parent.status='ready' THEN
            RAISE EXCEPTION 'ai_promotion_renderer_unpublished';
          END IF;
          IF parent.status='ready' THEN
            PERFORM public.ai_business_volume_manifest_check(parent.id,parent.attempt,parent.binding_digest,parent.draft,parent.manifest_json);
          END IF;""")

NEW_SQL = (CHUNK_GUARD, VOLUME_CHUNK_GUARD, MANIFEST_GUARD, RUN_GUARD, COMPLETE_GUARD)


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    import_module("ai_assistant.migrations.0017_business_file_renderer").change_constraint(
        schema_editor, "1,2,3,4,5,6,7")
    with schema_editor.connection.cursor() as cursor:
        for definition in NEW_SQL:
            cursor.execute(definition)


def uninstall(apps, schema_editor):
    if apps.get_model("ai_assistant", "AiBusinessFileRun").objects.filter(renderer_version=7).exists():
        raise RuntimeError("存在 renderer 7 文件任务（含排队、暂停或取消），禁止逆迁移")
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for definition in OLD_SQL:
            cursor.execute(definition)
    import_module("ai_assistant.migrations.0017_business_file_renderer").change_constraint(
        schema_editor, "1,2,3,4,5,6")


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0026_business_promotion_profile")]
    operations = [migrations.RunPython(install, uninstall)]
