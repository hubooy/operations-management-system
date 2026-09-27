from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("market", "0004_projection_sync_fencing")]
    operations = [
        migrations.AddIndex(model_name="marketrankingentry", index=models.Index(fields=[field], name=name))
        for field, name in (
            ("scope", "mkt_facet_scope_idx"),
            ("ranking_dimension", "mkt_facet_dimension_idx"),
            ("operation_mode", "mkt_facet_operation_idx"),
            ("subcategory", "mkt_facet_subcategory_idx"),
        )
    ]
