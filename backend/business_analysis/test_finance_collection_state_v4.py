"""v4 monthly finance capacity and unchanged v3 boundary probes."""
from unittest import TestCase

from . import finance_collection_state as old
from . import finance_collection_state_v4 as new
from .contracts import AnalysisContractError, digest
from .test_finance_collection_state import owned_page, sources
from .test_finance_source import row


class FinanceCollectionStateV4Tests(TestCase):
    def test_versioned_checkpoint_preserves_monthly_meaning(self):
        _, query, publication, rows = sources([row(1)])
        page = owned_page(query, publication, rows, offset=0, total=1)
        current = new.consume(None, page, trusted_query=query)
        result = new.result(current, trusted_query=query)
        self.assertEqual(current["schemaVersion"], new.SCHEMA)
        self.assertNotEqual(current["schemaVersion"], old.SCHEMA)
        self.assertEqual(result["coverage"][0]["metrics"]["net_sales"]["status"], "present")
        self.assertFalse(result["dailyProrationAllowed"])
        self.assertFalse(result["inferSkuProfit"])
        with self.assertRaises(AnalysisContractError):
            old.result(current, trusted_query=query)

    def test_v4_large_capacity_does_not_reinterpret_v3(self):
        _, query, publication, rows = sources([row(1), row(2, subject_name="次页")])
        page = owned_page(query, publication, rows[:1], offset=0, total=2)
        v4 = new.consume(None, page, trusted_query=query)
        v3 = old.consume(None, page, trusted_query=query)
        for profile, state in ((new, v4), (old, v3)):
            state["pageCount"] = 2000
            state["checkpointDigest"] = digest({key: value for key, value in state.items()
                if key != "checkpointDigest"})
            if profile is old:
                with self.assertRaises(AnalysisContractError):
                    profile.next_arguments(state, trusted_query=query)
            else:
                self.assertEqual(profile.next_arguments(state,
                    trusted_query=query)["offset"], 1)
        self.assertEqual(new.MAX_DATA_PAGES, 16_384)
        self.assertEqual(new.TOTAL_BYTES, 2 * 1024 * 1024 * 1024)
