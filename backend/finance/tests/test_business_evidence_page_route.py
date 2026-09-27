"""Signed reader-only HTTP boundary for the internal monthly page source."""
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from access_control.models import AccessRole, AppUser
from business_analysis.contracts import canonical
from finance import urls
from finance.import_service import import_finance_payload
from finance.models import FinanceWriteAuthority
from finance.tests.factories import body_bytes, prepared_payload
from sales.tests.factories import TEST_SECRET, signed_headers


PATH = "/api/finance/business-evidence/page"


class FinanceBusinessEvidencePageRouteTests(TestCase):
    def setUp(self):
        FinanceWriteAuthority.objects.filter(id=1).update(status="postgres")
        role, _ = AccessRole.objects.get_or_create(code="admin", defaults={"rank": 40, "label": "Admin"})
        now = timezone.now()
        AppUser.objects.create(email="finance-page-route@example.test", display_name="Synthetic", role=role,
            status="active", scope=None, version=1, created_at=now, updated_at=now)
        import_finance_payload(prepared_payload("2026-08"), "finance-page-route@example.test")
        self.payload = {"query": {"months": ["2026-08"], "scope": {
            "scope_key": "business", "scope_type": "business", "scope_name": "志高事业部", "group_name": ""},
            "analysisPeriod": {"startDate": "2026-08-01", "endDate": "2026-08-31"}},
            "offset": 0, "afterId": 0}

    def post(self, payload, *, role="admin", scope=None, body=None, headers=None):
        body = body or body_bytes(payload)
        headers = headers or signed_headers(PATH, method="POST", body=body, role=role,
            scope=scope, email="finance-page-route@example.test", request_id="finance-page-route")
        with patch.dict("os.environ", {"TERUISI_DJANGO_INTERNAL_SECRET": TEST_SECRET}):
            return self.client.post(PATH, data=body, content_type="application/json; charset=utf-8", headers=headers)

    def test_signed_admin_gets_exact_canonical_bounded_page(self):
        response = self.post(self.payload)
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual(response.content, canonical(data).encode("utf-8"))
        self.assertLessEqual(len(response.content), 38_000)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(data["schemaVersion"], "business-finance-owned-page-v1")
        self.assertTrue(data["rows"])
        self.assertEqual(response["X-Finance-Data-Revision"],
                         data["sourceRevision"].split(":")[0] + ":" + data["sourceRevision"].split(":")[1][:12])
        self.assertFalse(data["persistentEvidenceVerified"])
        self.assertFalse(any(str(item.pattern) == "business-evidence/page" for item in urls.write_patterns))

    def test_tampered_signature_body_and_restricted_role_are_rejected(self):
        original = body_bytes(self.payload)
        headers = signed_headers(PATH, method="POST", body=original, role="admin",
            email="finance-page-route@example.test", request_id="finance-page-route")
        changed = {**self.payload, "offset": 1}
        self.assertEqual(self.post(changed, body=body_bytes(changed), headers=headers).status_code, 401)
        self.assertEqual(self.post(self.payload, role="viewer").status_code, 403)
        self.assertEqual(self.post(self.payload, scope={"shops": ["测试店"]}).status_code, 403)
        self.assertEqual(self.client.get(PATH).status_code, 405)

    def test_unknown_fields_and_wrong_continuation_fail_closed(self):
        for body in ({**self.payload, "unknown": True},
                     {**self.payload, "offset": True},
                     {**self.payload, "offset": 1, "afterId": 1},
                     {**self.payload, "query": {**self.payload["query"], "analysisPeriod": None}}):
            with self.subTest(body=body):
                response = self.post(body)
                self.assertIn(response.status_code, {400, 409, 422})
                self.assertEqual(response["Cache-Control"], "no-store")
