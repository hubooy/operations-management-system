from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("sales", "0005_postgres_write_authority")]

    # Intentionally preserve the inert legacy checkpoint until the complete
    # PostgreSQL write cutover and its rollback window have closed. Dropping it
    # during a pre-cutover ``migrate`` would make the still-authoritative read
    # deployment impossible to restore. Runtime code no longer consumes this
    # table; a separately approved post-cutover retirement may remove it later.
    operations = []
