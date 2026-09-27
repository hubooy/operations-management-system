from __future__ import annotations

from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from sales.authority_lock import (
    SALES_WRITE_AUTHORITY_LOCK_KEY,
    SalesWriteAuthorityLockError,
    acquire_sales_write_authority_exclusive_lock,
    acquire_sales_write_authority_shared_lock,
)


class _Cursor:
    def __init__(self) -> None:
        self.execute = Mock()

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _Connection:
    def __init__(self, *, vendor: str, in_atomic_block: bool) -> None:
        self.vendor = vendor
        self.in_atomic_block = in_atomic_block
        self.cursor_instance = _Cursor()

    def cursor(self) -> _Cursor:
        return self.cursor_instance


class SalesWriteAuthorityAdvisoryLockTests(SimpleTestCase):
    def test_postgresql_shared_and_exclusive_modes_use_the_same_stable_key(self) -> None:
        shared = _Connection(vendor="postgresql", in_atomic_block=True)
        with patch("sales.authority_lock.connection", shared):
            acquire_sales_write_authority_shared_lock()
        shared.cursor_instance.execute.assert_called_once_with(
            "SELECT pg_advisory_xact_lock_shared(%s)",
            [SALES_WRITE_AUTHORITY_LOCK_KEY],
        )

        exclusive = _Connection(vendor="postgresql", in_atomic_block=True)
        with patch("sales.authority_lock.connection", exclusive):
            acquire_sales_write_authority_exclusive_lock()
        exclusive.cursor_instance.execute.assert_called_once_with(
            "SELECT pg_advisory_xact_lock(%s)",
            [SALES_WRITE_AUTHORITY_LOCK_KEY],
        )
        self.assertEqual(SALES_WRITE_AUTHORITY_LOCK_KEY, -8847588757640662873)

    def test_lock_fails_closed_outside_atomic_transaction(self) -> None:
        for acquire in (
            acquire_sales_write_authority_shared_lock,
            acquire_sales_write_authority_exclusive_lock,
        ):
            candidate = _Connection(vendor="postgresql", in_atomic_block=False)
            with self.subTest(acquire=acquire.__name__), patch(
                "sales.authority_lock.connection", candidate
            ):
                with self.assertRaises(SalesWriteAuthorityLockError):
                    acquire()
                candidate.cursor_instance.execute.assert_not_called()

    def test_non_postgresql_backend_keeps_transaction_contract_without_sql(self) -> None:
        candidate = _Connection(vendor="sqlite", in_atomic_block=True)
        with patch("sales.authority_lock.connection", candidate):
            acquire_sales_write_authority_shared_lock()
            acquire_sales_write_authority_exclusive_lock()
        candidate.cursor_instance.execute.assert_not_called()
