from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("finance", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="financetarget",
            name="gross_margin_bps",
            field=models.BigIntegerField(default=0),
        ),
    ]
