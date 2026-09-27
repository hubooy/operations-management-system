from django.db import migrations


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("""CREATE FUNCTION ai_terminal_control_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
        BEGIN
          IF TG_OP='DELETE' THEN RAISE EXCEPTION 'ai_control_evidence_delete_denied'; END IF;
          IF TG_TABLE_NAME='ai_write_authority' THEN
            IF NEW.id<>1 OR NEW.status NOT IN ('d1','postgres') OR (NEW.status='postgres' AND (NEW.authority_epoch IS NULL OR NEW.cutover_id='' OR NEW.migration_verify_run_id='' OR NEW.activated_at IS NULL)) THEN RAISE EXCEPTION 'ai_invalid_authority'; END IF;
            IF TG_OP='UPDATE' AND OLD.status='postgres' AND to_jsonb(NEW)<>to_jsonb(OLD) THEN RAISE EXCEPTION 'ai_postgres_authority_is_terminal'; END IF;
          ELSIF TG_TABLE_NAME='ai_data_revisions' THEN
            IF TG_OP='UPDATE' AND (NEW.domain<>OLD.domain OR NEW.revision<OLD.revision OR (OLD.source_digest<>'' AND NEW.source_digest<>OLD.source_digest)) THEN RAISE EXCEPTION 'ai_revision_evidence_regression'; END IF;
          ELSIF TG_TABLE_NAME='ai_migration_runs' AND TG_OP='UPDATE' THEN
            IF (to_jsonb(NEW)-'consumed_by_run_id')<>(to_jsonb(OLD)-'consumed_by_run_id') OR OLD.mode<>'dry-run' OR OLD.consumed_by_run_id<>'' OR NEW.consumed_by_run_id='' THEN RAISE EXCEPTION 'ai_migration_evidence_immutable'; END IF;
          END IF;
          RETURN NEW;
        END $$""")
        for table in ("ai_write_authority", "ai_data_revisions", "ai_migration_runs"):
            cursor.execute(
                f'CREATE TRIGGER ai_terminal_control BEFORE INSERT OR UPDATE OR DELETE ON "{table}" FOR EACH ROW EXECUTE FUNCTION ai_terminal_control_guard()'
            )
        cursor.execute(
            "ALTER TABLE ai_space_asset_favorites ADD CONSTRAINT ai_favorite_asset_fk FOREIGN KEY (asset_id) REFERENCES ai_space_assets(id) DEFERRABLE INITIALLY DEFERRED"
        )


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0003_runtime_fencing")]
    operations = [migrations.RunPython(install)]
