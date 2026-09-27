from django.db import connection


# A sales publish transaction holds the shared form while resolving ERP
# categories. The ERP bridge holds the exclusive form while replacing the
# reference and recalculating derived categories. PostgreSQL releases both at
# transaction end, including rollback.
ERP_REFERENCE_ADVISORY_LOCK_KEY = 401_831_776_500_219_443


def lock_erp_reference_for_sales_read() -> None:
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock_shared(%s)",
            [ERP_REFERENCE_ADVISORY_LOCK_KEY],
        )


def lock_erp_reference_for_replace() -> None:
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            [ERP_REFERENCE_ADVISORY_LOCK_KEY],
        )
