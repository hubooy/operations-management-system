from unittest.mock import patch
from django.test import TestCase
from sales.tests.factories import TEST_SECRET, signed_headers


@patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET})
class FinanceTargetViewTests(TestCase):
    def get_view(self, query, scope=None):
        path = "/api/finance/targets"
        return self.client.get(f"{path}?{query}", headers=signed_headers(f"{path}?{query}", scope=scope))

    def test_items_skip_options_and_options_skip_items(self):
        with patch("finance.views.list_targets", return_value={"items": [], "pagination": {"total": 0}}) as items, patch("finance.views.target_options", side_effect=AssertionError("must not scan options")):
            response = self.get_view("view=items&page=1&pageSize=2")
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(set(response.json()), {"items", "pagination"})
            items.assert_called_once_with(1, 2)
        with patch("finance.views.list_targets", side_effect=AssertionError("must not scan items")), patch("finance.views.target_options", return_value={"shops": [], "projects": []}):
            response = self.get_view("view=options")
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(set(response.json()), {"financeOptions"})

    def test_default_full_remains_compatible(self):
        with patch("finance.views.list_targets", return_value={"items": [], "pagination": {}}), patch("finance.views.target_options", return_value={"shops": [], "projects": []}):
            self.assertEqual(set(self.get_view("").json()), {"items", "pagination", "financeOptions"})

    def test_invalid_view_scope_and_signature_are_rejected(self):
        for query in ("view=bad", "view=", "view=items&view=options"):
            self.assertEqual(self.get_view(query).status_code, 400)
        self.assertEqual(self.get_view("view=items", scope={"warehouses": [], "channels": [], "platforms": []}).status_code, 403)
        self.assertEqual(self.client.get("/api/finance/targets?view=items").status_code, 401)
