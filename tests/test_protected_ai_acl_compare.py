"""Independent-restore ACL comparison rejects real authority drift."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from protected_ai_acl_compare import restored_equal


def snapshot():
    return (
        [(101, "public.ai_old(text)", "ai_old", "BEGIN RETURN 1; END",
            "owner=X/owner", "owner")],
        [("protected_business_example", "r", "owner", None,
            (("owner", "owner", "SELECT", True),),
            ((1, "id", None),),
            ((1, "reader", "owner", "SELECT", False),),
            (("teruisi_ai_reader", True, False, False, False, False),),
            ((1, "teruisi_ai_reader", True, False, False),))],
        "f" * 64, 7)


class ProtectedAclCompareTests(unittest.TestCase):
    def test_restore_may_change_oid_and_raw_acl_representation_only(self):
        before = snapshot()
        after = deepcopy(before)
        after[0][0] = (999, *after[0][0][1:])
        table = list(after[1][0]); table[3] = "{owner=arwdDxt/owner}"
        table[5] = ((1, "id", "{owner=arwd/owner}"),)
        after[1][0] = tuple(table)
        self.assertTrue(restored_equal(before, after))

    def test_owner_function_body_acl_and_effective_privileges_cannot_drift(self):
        for field, value in ((2, "other_owner"),
                (4, (("PUBLIC", "owner", "INSERT", False),)),
                (6, ((1, "reader", "owner", "UPDATE", False),)),
                (7, (("teruisi_ai_reader", True, True, False, False, False),)),
                (8, ((1, "teruisi_ai_reader", True, True, False),))):
            changed = deepcopy(snapshot())
            row = list(changed[1][0]); row[field] = value
            changed[1][0] = tuple(row)
            with self.subTest(field=field):
                self.assertFalse(restored_equal(snapshot(), changed))
        changed = deepcopy(snapshot())
        changed[0][0] = (101, "public.ai_old(text)", "ai_old",
            "BEGIN RETURN 0; END", "owner=X/owner", "owner")
        self.assertFalse(restored_equal(snapshot(), changed))
        changed = deepcopy(snapshot()); changed = (*changed[:2], "a" * 64, 7)
        self.assertFalse(restored_equal(snapshot(), changed))


if __name__ == "__main__":
    unittest.main()
