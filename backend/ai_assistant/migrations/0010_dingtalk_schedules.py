"""Bounded, fenced DingTalk schedule definitions and at-most-once slots."""
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for table in ("ai_dingtalk_schedules", "ai_dingtalk_schedule_runs"):
            cursor.execute(f"CREATE TRIGGER ai_write_fence BEFORE INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION ai_runtime_write_fence()")
        cursor.execute("""CREATE TRIGGER ai_immutable_identity BEFORE UPDATE ON ai_dingtalk_schedules
            FOR EACH ROW EXECUTE FUNCTION ai_immutable_record_guard('id','owner_email')""")
        cursor.execute("""CREATE TRIGGER ai_immutable_identity BEFORE UPDATE ON ai_dingtalk_schedule_runs
            FOR EACH ROW EXECUTE FUNCTION ai_immutable_record_guard('id','schedule_id','schedule_version','scheduled_at','created_at')""")
        cursor.execute("""ALTER TABLE ai_dingtalk_schedules ADD CONSTRAINT ai_ding_schedule_bound CHECK
            (octet_length(prompt)<=16000 AND cadence IN ('daily','weekly','monthly') AND
             target_type IN ('group','person') AND hour<=23 AND minute<=59 AND day BETWEEN 1 AND 28 AND version>=1)""")
        cursor.execute("""ALTER TABLE ai_dingtalk_schedule_runs ADD CONSTRAINT ai_ding_run_status CHECK
            (status IN ('queued','running','ready','sending','sent','unknown','denied','failed'))""")


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0009_model_generation_capabilities")]
    operations = [
        migrations.CreateModel(name="AiDingTalkSchedule", fields=[
            ("id", models.CharField(primary_key=True, serialize=False, max_length=64)),
            ("name", models.CharField(max_length=100)), ("prompt", models.TextField()),
            ("cadence", models.CharField(max_length=8)), ("hour", models.PositiveSmallIntegerField()),
            ("minute", models.PositiveSmallIntegerField()), ("day", models.PositiveSmallIntegerField(default=1)),
            ("target_type", models.CharField(max_length=8)), ("target_id", models.CharField(max_length=256)),
            ("sender_id", models.CharField(max_length=160)), ("owner_email", models.CharField(max_length=320)),
            ("enabled", models.BooleanField(default=False)), ("version", models.PositiveIntegerField(default=1)),
            ("next_run_at", models.DateTimeField(null=True)), ("last_run_at", models.DateTimeField(null=True)),
            ("updated_at", models.DateTimeField(default=django.utils.timezone.now)),
        ], options={"db_table": "ai_dingtalk_schedules", "indexes": [models.Index(fields=["enabled", "next_run_at"], name="ai_ding_schedule_due")]}),
        migrations.CreateModel(name="AiDingTalkScheduleRun", fields=[
            ("id", models.CharField(primary_key=True, serialize=False, max_length=64)),
            ("schedule", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="ai_assistant.aidingtalkschedule")),
            ("schedule_version", models.PositiveIntegerField()), ("scheduled_at", models.DateTimeField()),
            ("status", models.CharField(max_length=16, default="running")),
            ("error_code", models.CharField(max_length=64, default="")),
            ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
            ("completed_at", models.DateTimeField(null=True)),
        ], options={"db_table": "ai_dingtalk_schedule_runs", "indexes": [models.Index(fields=["schedule", "created_at"], name="ai_ding_schedule_history")],
            "constraints": [models.UniqueConstraint(fields=["schedule", "scheduled_at"], name="ai_ding_schedule_slot_uq")]}),
        migrations.RunPython(install),
    ]
