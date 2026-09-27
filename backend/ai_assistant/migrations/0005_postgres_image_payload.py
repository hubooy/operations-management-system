from django.db import migrations, models
import django.db.models.deletion


def install(apps, schema_editor):
    # This cutover is specifically for a proven empty R2 image watermark.
    # Refuse existing assets instead of silently publishing metadata without bytes.
    if apps.get_model("ai_assistant", "AiSpaceAssets").objects.exists():
        raise RuntimeError("AI image payload migration requires an empty asset watermark")
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("""CREATE TRIGGER ai_write_fence BEFORE INSERT OR UPDATE OR DELETE
            ON ai_space_asset_payloads FOR EACH ROW EXECUTE FUNCTION ai_runtime_write_fence()""")
        cursor.execute("""CREATE TRIGGER ai_immutable_evidence BEFORE UPDATE OR DELETE
            ON ai_space_asset_payloads FOR EACH ROW EXECUTE FUNCTION ai_immutable_record_guard()""")
        cursor.execute("""ALTER TABLE ai_space_asset_payloads ADD CONSTRAINT ai_payload_size
            CHECK (octet_length(content) BETWEEN 1 AND 6291456)""")
        cursor.execute("""CREATE FUNCTION ai_asset_payload_check() RETURNS trigger
            LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
            DECLARE asset_key text;
            BEGIN
              IF TG_TABLE_NAME='ai_space_assets' THEN asset_key=NEW.id;
              ELSE asset_key=NEW.asset_id; END IF;
              IF NOT EXISTS (
                SELECT 1 FROM public.ai_space_assets a
                JOIN public.ai_space_asset_payloads p ON p.asset_id=a.id
                WHERE a.id=asset_key AND a.mime_type='image/png'
                  AND octet_length(p.content)=a.byte_size
                  AND encode(sha256(p.content),'hex')=a.content_sha256
              ) THEN RAISE EXCEPTION 'ai_asset_payload_mismatch'; END IF;
              RETURN NULL;
            END $$""")
        for table in ("ai_space_assets", "ai_space_asset_payloads"):
            cursor.execute(f"""CREATE CONSTRAINT TRIGGER ai_asset_payload_complete
                AFTER INSERT OR UPDATE ON {table} DEFERRABLE INITIALLY DEFERRED
                FOR EACH ROW EXECUTE FUNCTION ai_asset_payload_check()""")


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0004_terminal_control_guards")]
    operations = [
        migrations.CreateModel(
            name="AiSpaceAssetPayload",
            fields=[
                ("asset", models.OneToOneField(
                    primary_key=True, serialize=False, db_column="asset_id",
                    on_delete=django.db.models.deletion.PROTECT,
                    to="ai_assistant.aispaceassets",
                )),
                ("content", models.BinaryField()),
            ],
            options={"db_table": "ai_space_asset_payloads"},
        ),
        migrations.RunPython(install),
    ]
