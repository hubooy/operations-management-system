from django.db import migrations, models


def require_empty_raw_chunk_table(apps, schema_editor):
    raw_chunk = apps.get_model("sales", "SalesRawUploadChunk")
    if raw_chunk.objects.exists():
        raise RuntimeError(
            "sales_raw_upload_chunks must be empty before PostgreSQL payload storage is enabled"
        )


class Migration(migrations.Migration):
    dependencies = [("sales", "0008_sales_cutover_attestation")]

    operations = [
        migrations.RunPython(require_empty_raw_chunk_table, migrations.RunPython.noop),
        migrations.AddField(
            model_name="salesrawuploadchunk",
            name="payload",
            field=models.BinaryField(),
        ),
    ]
