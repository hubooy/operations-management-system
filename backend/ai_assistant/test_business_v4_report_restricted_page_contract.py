"""Pure 0075 bounds and owning adapter negatives; PostgreSQL is separate."""
from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings

from . import (business_v4_report_restricted_page_owning as owning,
    business_v4_report_restricted_page_sql as sql,
    business_v4_report_link_sql as legacy)
from .policy import AiError


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _Reader:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def execute(self, query, params):
        self.calls.append((query, params))
        return _Result(self.rows[len(self.calls) - 1])


@override_settings(DJANGO_ENVIRONMENT="test", DJANGO_PROCESS_ROLE="development")
class RestrictedPagePureTests(SimpleTestCase):
    def test_default_and_non_test_environment_are_closed(self):
        with self.assertRaises(AiError):
            owning._closed(False)
        with override_settings(DJANGO_ENVIRONMENT="production"):
            with self.assertRaises(AiError):
                owning._closed(True)

    def test_caller_supplied_stub_cannot_claim_restricted_reader(self):
        with self.assertRaises(AiError):
            owning._reader_identity(_Reader([]))
        with self.assertRaises(AiError):
            owning.inspect_candidate("report", "v4", object(),
                _Reader([]), enabled=True)
        with override_settings(DJANGO_PROCESS_ROLE="ai_reader"):
            with self.assertRaises(AiError):
                owning._closed(True)

    def test_sql_contract_requires_exact_test_reader_and_0071_current_link(self):
        body = sql.PAGE_SQL
        for marker in ("test_teruisi_ai_rehearsal", "55440 AND 55999",
                "session_user IS DISTINCT FROM 'teruisi_ai_reader'",
                "current_setting('transaction_read_only') IS DISTINCT FROM 'on'",
                "public.ai_v4_read_report_source_link_ro(selected_report",
                "source.query_json::jsonb->>'window'",
                "receipt.response_digest IS DISTINCT FROM actual_digest",
                "audit.arguments_json IS DISTINCT FROM",
                "ai_v4_report_page_late_link_drift"):
            self.assertIn(marker, body)
        self.assertNotIn("UPDATE public.", body)
        self.assertNotIn("INSERT INTO public.", body)

    def test_read_only_0075_copy_preserves_0071_checks_except_row_locks(self):
        self.assertEqual(legacy.BINDINGS_SQL.count(" FOR SHARE"), 2)
        self.assertNotIn(" FOR SHARE", sql.RO_BINDINGS_SQL)
        self.assertEqual(sql.RO_BINDINGS_SQL.replace(
            "ai_v4_report_source_bindings_ro(",
            "ai_v4_report_source_bindings(").replace(
            "WHERE domain='netshop';",
            "WHERE domain='netshop' FOR SHARE;").replace(
            "WHERE domain='finance';",
            "WHERE domain='finance' FOR SHARE;"),
            legacy.BINDINGS_SQL)
        self.assertEqual(sql.RO_READ_SQL.replace(
            "ai_v4_read_report_source_link_ro(",
            "ai_v4_read_report_source_link(").replace(
            "ai_v4_report_source_bindings_ro(v4.id)",
            "ai_v4_report_source_bindings(v4.id)"),
            legacy.READ_SQL)

    def test_one_page_at_a_time_uses_exact_sql_identity_and_rejects_duplicates(self):
        parent = SimpleNamespace(id="v4-run")
        source = SimpleNamespace(id="physical-source", page_count=2,
            source_ref="a" * 64, source_revision="17:abc")
        actor = {"email": "admin@example.test", "version": 4}

        def row(sequence):
            return (sequence, "{}", "b" * 64, source.source_ref,
                source.source_revision, 0, "b" * 64, "b" * 64,
                "c" * 64, "get_business_source_page", "audit",
                "invocation")

        reader = _Reader([[row(1)], [row(2)]])
        pages = list(owning._pages(reader, "report", parent, source, actor))
        self.assertEqual([page["sequence"] for page in pages], [1, 2])
        self.assertEqual([params[3] for _, params in reader.calls], [1, 2])
        self.assertEqual(reader.calls[0][1][:3],
            ["report", "v4-run", "physical-source"])
        self.assertTrue(all(query == owning._PAGE_QUERY for query, _ in
            reader.calls))
        self.assertTrue(all(page["auditSucceeded"] for page in pages))
        for bad in ([], [row(1), row(1)], [row(2)]):
            with self.subTest(bad=bad), self.assertRaises(AiError):
                list(owning._pages(_Reader([bad, [row(2)]]), "report",
                    parent, source, actor))
