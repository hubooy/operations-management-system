from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [("ai_assistant", "0014_business_evidence")]
    operations = [
        migrations.AddField(model_name="aibusinessevidencerun", name="collection_status", field=models.CharField(default="manual", max_length=20)),
        migrations.AddField(model_name="aibusinessevidencerun", name="next_collect_at", field=models.DateTimeField(default=django.utils.timezone.now)),
        migrations.AddField(model_name="aibusinessevidencerun", name="collection_failures", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="aibusinessevidencerun", name="collection_error_code", field=models.CharField(default="", max_length=64)),
    ]
