"""Bounded page and reviewed-report sources for bot-authored scheduled media."""
from django.db import migrations, models


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("""ALTER TABLE ai_dingtalk_schedules ADD CONSTRAINT ai_ding_schedule_media_bound CHECK
            (content_type IN ('text','screenshot','report_file') AND octet_length(source_ref)<=160
             AND ((content_type='text' AND source_ref='') OR (content_type<>'text' AND source_ref<>'')))""")


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0012_report_library")]
    operations = [
        migrations.AddField(model_name="aidingtalkschedule", name="content_type",
                            field=models.CharField(max_length=16, default="text")),
        migrations.AddField(model_name="aidingtalkschedule", name="source_ref",
                            field=models.CharField(max_length=160, default="")),
        migrations.RunPython(install),
    ]
