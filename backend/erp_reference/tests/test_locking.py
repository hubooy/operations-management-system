from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from erp_reference.locking import (
    ERP_REFERENCE_ADVISORY_LOCK_KEY,
    lock_erp_reference_for_replace,
    lock_erp_reference_for_sales_read,
)


class ErpReferenceLockingTests(SimpleTestCase):
    def test_sales_shared_and_erp_exclusive_locks_use_the_same_transaction_key(self) -> None:
        fake_connection = MagicMock()
        fake_connection.vendor = "postgresql"
        cursor = fake_connection.cursor.return_value.__enter__.return_value

        with patch("erp_reference.locking.connection", fake_connection):
            lock_erp_reference_for_sales_read()
            lock_erp_reference_for_replace()

        self.assertEqual(cursor.execute.call_count, 2)
        shared_call, exclusive_call = cursor.execute.call_args_list
        self.assertIn("pg_advisory_xact_lock_shared", shared_call.args[0])
        self.assertIn("pg_advisory_xact_lock", exclusive_call.args[0])
        self.assertEqual(shared_call.args[1], [ERP_REFERENCE_ADVISORY_LOCK_KEY])
        self.assertEqual(exclusive_call.args[1], [ERP_REFERENCE_ADVISORY_LOCK_KEY])
