from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("workflow", "0006_merge_20260904_1700")]

    operations = [
        migrations.AddField(
            model_name="newproductline",
            name="product_image_file_name",
            field=models.CharField(default="", max_length=255),
        ),
        migrations.AddField(
            model_name="newproductline",
            name="product_image_mime_type",
            field=models.CharField(default="", max_length=32),
        ),
        migrations.AddField(
            model_name="newproductline",
            name="product_image_size_bytes",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="newproductline",
            name="product_image_sha256",
            field=models.CharField(default="", max_length=64),
        ),
        migrations.AddField(
            model_name="newproductline",
            name="product_image_bytes",
            field=models.BinaryField(blank=True, editable=False, null=True),
        ),
    ]
