from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("workflow", "0004_new_product_weekly_followup")]

    operations = [
        migrations.AddField(
            model_name="newproductline",
            name="product_image_url",
            field=models.URLField(default="", max_length=1000),
        ),
        migrations.RemoveField(
            model_name="newproductline",
            name="tracking_weeks",
        ),
    ]
