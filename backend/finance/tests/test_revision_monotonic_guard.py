"""PostgreSQL negative probes for finance revision-pair rollback (ABA)."""
from uuid import uuid4

from django.db import DatabaseError, connection, transaction
from django.test import TransactionTestCase

from business_analysis.contracts import digest
from finance.models import FinanceDataRevision


class FinanceRevisionMonotonicTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("requires PostgreSQL finance.0004 guard")
        FinanceDataRevision.objects.get_or_create(domain="finance",
            defaults={"revision": 0, "source_digest": "0" * 64})

    def value(self):
        row = FinanceDataRevision.objects.get(domain="finance")
        return row.revision, row.source_digest

    def forward(self, label):
        before = self.value()
        after = (before[0] + 1, digest([before[1], label]))
        FinanceDataRevision.objects.filter(domain="finance").update(
            revision=after[0], source_digest=after[1])
        return before, after

    def test_revision_pair_cannot_return_to_old_source_identity(self):
        old, first = self.forward("first")
        _, latest = self.forward("second")
        self.assertNotEqual(old, first)
        self.assertNotEqual(first, latest)
        for rollback in (old, first):
            with self.subTest(rollback=rollback), self.assertRaises(DatabaseError):
                with transaction.atomic():
                    FinanceDataRevision.objects.filter(domain="finance").update(
                        revision=rollback[0], source_digest=rollback[1])
            self.assertEqual(self.value(), latest)

    def test_partial_invalid_and_domain_mutations_reject(self):
        _, current = self.forward("baseline")
        cases = (
            {"revision": current[0] + 1},
            {"source_digest": digest("digest-only")},
            {"revision": current[0], "source_digest": digest("same-version")},
            {"revision": 9_007_199_254_740_992,
                "source_digest": digest("out-of-range")},
            {"revision": current[0] + 1, "source_digest": "bad"},
            {"domain": "other", "revision": current[0] + 1,
                "source_digest": digest("domain-move")},
        )
        for changed in cases:
            with self.subTest(changed=changed), self.assertRaises(DatabaseError):
                with transaction.atomic():
                    FinanceDataRevision.objects.filter(domain="finance").update(**changed)
            self.assertEqual(self.value(), current)
        with self.assertRaises(DatabaseError), transaction.atomic():
            FinanceDataRevision.objects.filter(domain="finance").delete()
        self.assertEqual(self.value(), current)

    def test_only_zero_digest_can_seed_an_absent_finance_revision(self):
        # Explicit isolated-owner simulation of a pre-0004 database only.
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE finance_data_revisions "
                "DISABLE TRIGGER finance_revision_monotonic")
        try:
            FinanceDataRevision.objects.filter(domain="finance").delete()
        finally:
            with connection.cursor() as cursor:
                cursor.execute("ALTER TABLE finance_data_revisions "
                    "ENABLE TRIGGER finance_revision_monotonic")
        with self.assertRaises(DatabaseError), transaction.atomic():
            FinanceDataRevision.objects.create(domain="finance", revision=8,
                source_digest=digest("forged-seed"))
        self.assertFalse(FinanceDataRevision.objects.filter(domain="finance").exists())
        FinanceDataRevision.objects.create(domain="finance", revision=0,
            source_digest="0" * 64)
        self.assertEqual(self.value(), (0, "0" * 64))

    def test_writer_role_cannot_delete_truncate_or_disable_guard(self):
        role = "fin_rev_aba_" + uuid4().hex[:12]
        with connection.cursor() as cursor:
            cursor.execute(f'CREATE ROLE "{role}" NOLOGIN')
            cursor.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
            cursor.execute(f'GRANT SELECT, INSERT, UPDATE '
                f'ON finance_data_revisions TO "{role}"')
            cursor.execute("SELECT tgenabled FROM pg_trigger WHERE tgrelid="
                "'public.finance_data_revisions'::regclass AND tgname="
                "'finance_revision_monotonic'")
            self.assertEqual(cursor.fetchone()[0], "O")
        try:
            _, older = self.forward("older")
            _, latest = self.forward("latest")
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(f'SET LOCAL ROLE "{role}"')
                for statement in (
                    "DELETE FROM public.finance_data_revisions WHERE domain='finance'",
                    "TRUNCATE public.finance_data_revisions",
                    "ALTER TABLE public.finance_data_revisions DISABLE TRIGGER finance_revision_monotonic",
                    "UPDATE public.finance_data_revisions SET revision=%s,source_digest=%s "
                        "WHERE domain='finance'",
                ):
                    params = [older[0], older[1]] if statement.startswith("UPDATE") else []
                    with self.subTest(statement=statement), self.assertRaises(DatabaseError):
                        with transaction.atomic(), connection.cursor() as cursor:
                            cursor.execute(statement, params)
            self.assertEqual(self.value(), latest)
        finally:
            with connection.cursor() as cursor:
                cursor.execute(f'DROP OWNED BY "{role}"')
                cursor.execute(f'DROP ROLE "{role}"')
