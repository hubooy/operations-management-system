from django.test import SimpleTestCase

from access_control.policy import PolicyError, normalize_scope, scope_covers


class AccessControlPolicyTests(SimpleTestCase):
    def test_scope_is_canonical_and_exact(self) -> None:
        self.assertEqual(
            normalize_scope({"warehouses": [" 二仓 ", "主仓", "主仓"], "channels": [], "platforms": ["京东"]}),
            {"warehouses": ["主仓", "二仓"], "channels": [], "platforms": ["京东"]},
        )
        with self.assertRaises(PolicyError):
            normalize_scope({"warehouses": [], "channels": []})

    def test_scope_coverage_never_broadens_snapshot(self) -> None:
        current = {"warehouses": ["主仓"], "channels": ["京东-店铺"], "platforms": ["京东"]}
        self.assertTrue(scope_covers(current, {"warehouses": ["主仓"], "channels": [], "platforms": []}))
        self.assertFalse(scope_covers(current, None))
        self.assertTrue(scope_covers(None, None))
