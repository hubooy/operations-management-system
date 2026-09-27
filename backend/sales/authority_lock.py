"""Transaction-scoped PostgreSQL fencing for sales write authority."""

from __future__ import annotations

from django.db import connection


# First signed int64 from sha256("teruisi:sales-write-authority:v1").  Keep this
# literal stable: every writer and authority transition must rendezvous on the
# same PostgreSQL heavyweight-lock identity.
SALES_WRITE_AUTHORITY_LOCK_KEY = -8847588757640662873


class SalesWriteAuthorityLockError(RuntimeError):
    pass


def _require_atomic_transaction() -> None:
    if not connection.in_atomic_block:
        raise SalesWriteAuthorityLockError(
            "销售写入权威锁必须在 transaction.atomic 事务内获取"
        )


def acquire_sales_write_authority_shared_lock() -> None:
    """Fence a normal writer/read-check without serializing peer writers."""

    _require_atomic_transaction()
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock_shared(%s)",
            [SALES_WRITE_AUTHORITY_LOCK_KEY],
        )


def acquire_sales_write_authority_exclusive_lock() -> None:
    """Fence an authority/cutover mutation against every in-flight writer."""

    _require_atomic_transaction()
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            [SALES_WRITE_AUTHORITY_LOCK_KEY],
        )
