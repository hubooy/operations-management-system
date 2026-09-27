"""Isolated PostgreSQL tests for an unregistered monthly page reader."""
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from access_control.models import AccessRole, AppUser
from business_analysis.contracts import canonical, digest
from finance import business_evidence_page as service
from finance.errors import FinanceApiError
from finance.import_service import import_finance_payload
from finance.models import FinanceDataRevision, FinanceLine, FinanceMonth, FinanceWriteAuthority
from finance.tests.factories import prepared_payload
from sales.auth import Principal


class FinanceEvidencePageTests(TestCase):
    def setUp(self):
        FinanceWriteAuthority.objects.filter(id=1).update(status="postgres")
        self.principal = Principal("finance-pages@example.test", "Synthetic", "admin", None)
        role, _ = AccessRole.objects.get_or_create(code="admin", defaults={"rank": 40, "label": "Admin"})
        now = timezone.now()
        AppUser.objects.create(email=self.principal.email, role=role, status="active", scope=None,
            display_name="Synthetic", version=1, created_at=now, updated_at=now)
        import_finance_payload(prepared_payload("2026-08"), self.principal.email)
        import_finance_payload(prepared_payload("2026-09"), self.principal.email)
        self.query = {"months": ["2026-08", "2026-09"], "scope": {
            "scope_key": "business", "scope_type": "business", "scope_name": "志高事业部", "group_name": ""},
            "analysisPeriod": {"startDate": "2026-08-20", "endDate": "2026-09-18"}}

    def advance_revision(self, next_digest=None):
        current = FinanceDataRevision.objects.get(domain="finance")
        FinanceDataRevision.objects.filter(domain="finance").update(
            revision=current.revision + 1,
            source_digest=next_digest or digest([current.source_digest, "synthetic-change"]))

    def extend_rows(self, amount=105):
        base = FinanceLine.objects.filter(month="2026-08", scope_key="business", section="summary").first()
        self.assertIsNotNone(base)
        data = {field.attname: getattr(base, field.attname) for field in base._meta.concrete_fields if field.attname != "id"}
        for index in range(amount):
            FinanceLine.objects.create(**{**data, "subject_name": f"额外核验科目 {index}"})

    def test_multiple_months_complete_prefix_real_ids_null_zero_and_sections(self):
        self.extend_rows()
        FinanceLine.objects.filter(month="2026-08", scope_key="business", metric_key="net_sales").update(amount_cents=0)
        FinanceLine.objects.filter(month="2026-09", scope_key="business", metric_key="net_sales").update(amount_cents=None)
        first = service.read_page(self.principal, self.query)
        self.assertLessEqual(len(canonical(first).encode("utf-8")), service.MAX_PAGE_BYTES)
        self.assertEqual(first["schemaVersion"], service.SCHEMA)
        self.assertFalse(first["persistentEvidenceVerified"])
        self.assertEqual(first["pageEvidence"], {"rowCount": len(first["rows"]), "sha256": digest(first["rows"])})
        self.assertEqual(first["pageDigest"], digest({k: v for k, v in first.items() if k != "pageDigest"}))
        self.assertIsNotNone(first["pagination"]["nextOffset"])
        self.assertEqual(first["publication"]["missingMonths"], [])
        self.assertEqual(len(first["publication"]["months"]), 2)
        self.assertEqual(len(first["publication"]["batches"]), 2)
        self.assertEqual(first["periodAlignment"]["alignment"], "different_or_partial_months")
        rows, page = list(first["rows"]), first
        while page["pagination"]["nextOffset"] is not None:
            page = service.read_page(self.principal, self.query,
                offset=page["pagination"]["nextOffset"], after_id=page["pagination"]["nextLastId"],
                expected_source_ref=first["sourceRef"], expected_revision=first["sourceRevision"])
            rows.extend(page["rows"])
        self.assertEqual(len(rows), page["pagination"]["total"])
        self.assertEqual([row["id"] for row in rows], sorted(row["id"] for row in rows))
        self.assertEqual(len({row["id"] for row in rows}), len(rows))
        self.assertTrue(all(len(row["rowId"]) == 64 for row in rows))
        self.assertIn(0, [row["amount_cents"] for row in rows])
        self.assertIn(None, [row["amount_cents"] for row in rows])
        self.assertEqual({row["month"] for row in rows}, {"2026-08", "2026-09"})
        self.assertEqual({row["section"] for row in rows}, {"summary", "kingdee"})

    def test_missing_month_and_other_group_are_explicit_gaps(self):
        query = {**self.query, "months": [*self.query["months"], "2026-10"]}
        page = service.read_page(self.principal, query)
        self.assertEqual(page["publication"]["missingMonths"], ["2026-10"])
        self.assertEqual(page["pagination"]["total"], service._facts(query).count())
        other = {**query, "scope": {**query["scope"], "group_name": "其他组"}}
        empty = service.read_page(self.principal, other)
        self.assertEqual(empty["rows"], [])
        self.assertEqual(empty["pagination"]["total"], 0)
        self.assertNotEqual(empty["sourceRef"], page["sourceRef"])

    def test_pagination_checkpoint_wrong_binding_or_offset_rejects(self):
        self.extend_rows()
        first = service.read_page(self.principal, self.query)
        offset, last = first["pagination"]["nextOffset"], first["pagination"]["nextLastId"]
        for change in ({"offset": offset, "after_id": last},
                       {"offset": offset + 1, "after_id": last, "expected_source_ref": first["sourceRef"],
                        "expected_revision": first["sourceRevision"]},
                       {"offset": offset, "after_id": last, "expected_source_ref": "0"*64,
                        "expected_revision": first["sourceRevision"]},
                       {"offset": offset, "after_id": last + 1, "expected_source_ref": first["sourceRef"],
                        "expected_revision": first["sourceRevision"]}):
            with self.subTest(change=change), self.assertRaises(FinanceApiError):
                service.read_page(self.principal, self.query, **change)

    def test_daily_context_must_be_exact_and_months_cover_it(self):
        for changed in ({"analysisPeriod": None},
                        {"months": ["2026-08"]},
                        {"analysisPeriod": {"startDate": "2026-08-20", "endDate": "2026-12-20"}},
                        {"scope": {**self.query["scope"], "unknown": "x"}}):
            with self.subTest(changed=changed), self.assertRaises(FinanceApiError):
                service.read_page(self.principal, {**self.query, **changed})

    def test_live_actor_revision_batch_and_count_changes_stop_continuation(self):
        self.extend_rows()
        first = service.read_page(self.principal, self.query)
        args = {"offset": first["pagination"]["nextOffset"], "after_id": first["pagination"]["nextLastId"],
                "expected_source_ref": first["sourceRef"], "expected_revision": first["sourceRevision"]}
        for change in (lambda: AppUser.objects.filter(email=self.principal.email).update(version=2),
                       lambda: self.advance_revision(),
                       lambda: FinanceMonth.objects.filter(month="2026-09").update(status="processing"),
                       lambda: FinanceLine.objects.create(**{**{field.attname: getattr(
                           FinanceLine.objects.filter(month="2026-08", scope_key="business").first(), field.attname)
                           for field in FinanceLine._meta.concrete_fields if field.attname != "id"},
                           "subject_name": "新增账本行"})):
            with self.subTest(change=change), self.assertRaises(FinanceApiError):
                with self.captureOnRollback(change):
                    service.read_page(self.principal, self.query, **args)

    def captureOnRollback(self, callback):
        from django.db import transaction
        class Rollback:
            def __enter__(self_inner):
                self_inner.atomic = transaction.atomic()
                self_inner.atomic.__enter__()
                callback()
            def __exit__(self_inner, *_):
                transaction.set_rollback(True)
                self_inner.atomic.__exit__(None, None, None)
        return Rollback()

    def test_late_actor_revision_and_batch_switch_return_no_page(self):
        for mode in ("actor", "revision", "batch"):
            with self.subTest(mode=mode):
                original = service._snapshot
                calls = []
                def changed(*args):
                    calls.append(1)
                    if len(calls) == 2:
                        if mode == "actor": AppUser.objects.filter(email=self.principal.email).update(status="disabled")
                        elif mode == "revision": self.advance_revision("f"*64)
                        else: FinanceMonth.objects.filter(month="2026-08").update(batch_id="missing")
                    return original(*args)
                with self.captureOnRollback(lambda: None), patch.object(service, "_snapshot", side_effect=changed):
                    with self.assertRaises(FinanceApiError): service.read_page(self.principal, self.query)
                self.assertEqual(len(calls), 2)

    def test_wrong_actor_and_bounded_selects(self):
        for principal in (Principal("missing@example.test", "Missing", "admin", None),
                          Principal(self.principal.email, "Scoped", "admin", {"shops": ["测试店"]}),
                          Principal(self.principal.email, "Operator", "operator", None)):
            with self.assertRaises(FinanceApiError): service.read_page(principal, self.query)
        with CaptureQueriesContext(connection) as captured:
            service.read_page(self.principal, self.query)
        facts = [item["sql"] for item in captured if "finance_lines" in item["sql"]]
        self.assertTrue(any("LIMIT 101" in query for query in facts))
        self.assertTrue(all(query.lstrip().upper().startswith("SELECT") for query in facts))
