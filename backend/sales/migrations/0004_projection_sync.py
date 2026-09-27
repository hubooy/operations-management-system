from django.db import migrations


CREATE_CHECKPOINT_SQL = """
CREATE TABLE sales_projection_sync_checkpoint (
    id integer PRIMARY KEY CHECK (id = 1),
    source_epoch varchar(128) NOT NULL,
    source_path_digest varchar(64) NOT NULL,
    last_event_sequence bigint NOT NULL CHECK (last_event_sequence >= 0),
    last_event_id text NOT NULL,
    sales_revision bigint NOT NULL CHECK (sales_revision >= 1),
    erp_revision bigint NOT NULL CHECK (erp_revision >= 1),
    created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_checked_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


class Migration(migrations.Migration):
    dependencies = [("sales", "0003_query_ready_projection")]

    operations = [
        migrations.RunSQL(
            sql=CREATE_CHECKPOINT_SQL,
            reverse_sql="DROP TABLE sales_projection_sync_checkpoint",
        )
    ]
