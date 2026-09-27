from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("inventory", "0003_replenishment_plan_details")]

    operations = [
        migrations.AddField(model_name="replenishmentplanitem", name="dingtalk_sync_status", field=models.CharField(default="not_synced", max_length=16)),
        migrations.AddField(model_name="replenishmentplanitem", name="dingtalk_record_id", field=models.CharField(default="", max_length=128)),
        migrations.AddField(model_name="replenishmentplanitem", name="dingtalk_payload_sha256", field=models.CharField(default="", max_length=64)),
        migrations.AddField(model_name="replenishmentplanitem", name="dingtalk_sync_owner_token", field=models.CharField(default="", max_length=64)),
        migrations.AddField(model_name="replenishmentplanitem", name="dingtalk_sync_started_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="replenishmentplanitem", name="dingtalk_synced_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="replenishmentplanitem", name="dingtalk_synced_by", field=models.CharField(default="", max_length=320)),
        migrations.AddField(model_name="replenishmentplanitem", name="dingtalk_sync_error", field=models.CharField(default="", max_length=500)),
    ]
