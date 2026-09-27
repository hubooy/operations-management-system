from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("workflow", "0007_new_product_line_uploaded_image")]

    operations = [
        migrations.AddField(
            model_name="newproductproject",
            name="shop_plan",
            field=models.TextField(blank=True, default=None, null=True),
        ),
    ]
