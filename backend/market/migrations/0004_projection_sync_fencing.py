from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        (
            "market",
            "0003_remove_marketannotationvalidationsample_mkt_validation_sample_uq_and_more",
        )
    ]

    operations = [
        migrations.AddField(
            model_name="marketnetshopprojectioncontrol",
            name="syncing_offset",
            field=models.BigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="marketnetshopprojectioncontrol",
            name="syncing_owner",
            field=models.CharField(default="", max_length=320),
        ),
        migrations.AddField(
            model_name="marketnetshopprojectioncontrol",
            name="syncing_total",
            field=models.BigIntegerField(default=0),
        ),
    ]
