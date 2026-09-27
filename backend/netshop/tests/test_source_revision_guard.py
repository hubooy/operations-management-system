"""PostgreSQL transaction tests for netshop owning-page revision fencing."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from unittest.mock import patch

from django.db import DatabaseError, close_old_connections, connection, connections, transaction
from django.test import TransactionTestCase
from django.utils import timezone

from business_analysis.contracts import digest
from netshop.import_service import import_netshop_payload
from netshop.models import (NetshopDataRevision, NetshopImportBatch, NetshopRow,
    NetshopWriteAuthority)
from .factories import netshop_row, prepared_payload


class NetshopSourceRevisionGuardTests(TransactionTestCase):
    reset_sequences = False

    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("requires PostgreSQL deferred triggers and roles")
        NetshopWriteAuthority.objects.filter(id=1).update(status="postgres",
            authority_epoch=uuid4(), cutover_id="netshop-guard-test",
            migration_verify_run_id="netshop-guard-test", activated_at=timezone.now())
        NetshopDataRevision.objects.get_or_create(domain="netshop",
            defaults={"revision": 0, "source_digest": "0" * 64})

    def revision(self):
        return NetshopDataRevision.objects.get(domain="netshop")

    def bump(self, label):
        item = NetshopDataRevision.objects.select_for_update().get(domain="netshop")
        item.revision += 1
        item.source_digest = digest([item.source_digest, label, item.revision])
        item.save(update_fields=["revision", "source_digest"])

    def create_row(self, subject="synthetic"):
        return NetshopRow.objects.create(source_row_key=subject,
            source_row_hash="a" * 64, first_import_batch_id="synthetic",
            last_import_batch_id="synthetic", source_row_number=1,
            source="jd_promotion", dataset="ad", platform="京东",
            shop_name="测试店", business_date="2026-08-20",
            metrics_json={"spendCents": 100}, raw_json={"搜索词": "开水器"},
            spend_cents=100, created_at="synthetic", updated_at="synthetic")

    def create_batch(self, identifier="synthetic"):
        return NetshopImportBatch.objects.create(id=identifier,
            source="jd_promotion", dataset="ad", platform="京东",
            shop_name="测试店", file_name="synthetic.xlsx", file_size_bytes=1,
            file_hash=digest(identifier), raw_file_hash=digest([identifier, "raw"]),
            content_hash=digest([identifier, "content"]), scope_key=digest(identifier),
            status="completed", created_at="synthetic", completed_at="synthetic")

    def seed(self):
        with transaction.atomic():
            row = self.create_row()
            batch = self.create_batch()
            self.bump("seed")
        return row, batch

    def marker_count(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM public.netshop_source_revision_markers")
            return cursor.fetchone()[0]

    def test_unversioned_row_and_batch_writes_rollback_at_commit(self):
        row, batch = self.seed()
        baseline = (self.revision().revision, self.revision().source_digest)
        operations = (
            lambda: NetshopRow.objects.filter(pk=row.pk).update(spend_cents=200),
            lambda: NetshopRow.objects.filter(pk=row.pk).delete(),
            lambda: self.create_row("new-row"),
            lambda: NetshopImportBatch.objects.filter(pk=batch.pk).update(note="changed"),
            lambda: NetshopImportBatch.objects.filter(pk=batch.pk).delete(),
            lambda: self.create_batch("new-batch"),
        )
        for change in operations:
            with self.subTest(operation=str(change)), self.assertRaises(DatabaseError):
                with transaction.atomic(): change()
            self.assertEqual((self.revision().revision,
                self.revision().source_digest), baseline)
            self.assertEqual(self.marker_count(), 0)
        row.refresh_from_db(); batch.refresh_from_db()
        self.assertEqual(row.spend_cents, 100)
        self.assertEqual(batch.note, "")
        self.assertEqual(NetshopRow.objects.count(), 1)
        self.assertEqual(NetshopImportBatch.objects.count(), 1)

    def test_revision_pair_cannot_aba_or_delete_after_fact_commit(self):
        self.seed()
        before = self.revision()
        with transaction.atomic():
            self.bump("later")
        after = self.revision()
        self.assertGreater(after.revision, before.revision)
        for change in (
            lambda: NetshopDataRevision.objects.filter(domain="netshop").update(
                revision=before.revision, source_digest=before.source_digest),
            lambda: NetshopDataRevision.objects.filter(domain="netshop").update(
                source_digest="b" * 64),
            lambda: NetshopDataRevision.objects.filter(domain="netshop").delete(),
        ):
            with self.subTest(change=str(change)), self.assertRaises(DatabaseError):
                with transaction.atomic(): change()
            self.assertEqual((self.revision().revision, self.revision().source_digest),
                (after.revision, after.source_digest))

    def test_normal_multi_shop_import_duplicate_and_failed_publish_keep_contract(self):
        baseline = self.revision().revision
        for index, shop in enumerate(("京东一店", "京东二店"), 1):
            payload = prepared_payload(netshop_row(source="jd_promotion", dataset="ad",
                shop_name=shop, business_date="2026-08-20",
                sku_id=f"SKU-{index}",
                metrics={"spendCents": 100, "impressions": 20, "clicks": 2}),
                raw_seed=shop)
            first = import_netshop_payload(payload, "synthetic@example.invalid")
            self.assertEqual(first["status"], "imported")
            self.assertEqual(self.revision().revision, baseline + index)
            duplicate = import_netshop_payload(payload, "synthetic@example.invalid")
            self.assertEqual(duplicate["status"], "duplicate")
            self.assertEqual(self.revision().revision, baseline + index)
        self.assertEqual(NetshopRow.objects.count(), 2)
        self.assertEqual(NetshopImportBatch.objects.count(), 2)
        replacement = prepared_payload(netshop_row(source="jd_promotion", dataset="ad",
            shop_name="京东一店", business_date="2026-08-20", sku_id="SKU-1",
            metrics={"spendCents": 101, "impressions": 20, "clicks": 2}),
            raw_seed="replacement")
        before = (self.revision().revision,
            list(NetshopRow.objects.values_list("source_row_key", "spend_cents")))
        with patch.object(NetshopRow.objects, "bulk_create", side_effect=RuntimeError("rollback")):
            with self.assertRaises(RuntimeError):
                import_netshop_payload(replacement, "synthetic@example.invalid")
        self.assertEqual((self.revision().revision,
            list(NetshopRow.objects.values_list("source_row_key", "spend_cents"))), before)
        self.assertEqual(self.marker_count(), 0)

    def test_writer_role_cannot_forge_marker_or_disable_trigger(self):
        row, _ = self.seed()
        role = "net_rev_guard_" + uuid4().hex[:12]
        with connection.cursor() as cursor:
            cursor.execute(f'CREATE ROLE "{role}" NOLOGIN')
            cursor.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
            cursor.execute(f'GRANT SELECT, UPDATE ON netshop_rows,netshop_data_revisions TO "{role}"')
            cursor.execute("SELECT prosecdef,proconfig FROM pg_proc WHERE oid="
                "'public.netshop_source_mark_revision_required()'::regprocedure")
            security_definer, settings = cursor.fetchone()
            self.assertTrue(security_definer)
            self.assertIn("search_path=pg_catalog,public",
                [setting.replace(" ", "") for setting in settings])
            cursor.execute("SELECT has_table_privilege(%s,"
                "'public.netshop_source_revision_markers','INSERT')", [role])
            self.assertFalse(cursor.fetchone()[0])
            cursor.execute("SELECT has_function_privilege(%s,"
                "'public.netshop_source_mark_revision_required()','EXECUTE')", [role])
            self.assertFalse(cursor.fetchone()[0])
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(f'SET LOCAL ROLE "{role}"')
                for statement in (
                    "INSERT INTO public.netshop_source_revision_markers VALUES (1,0,'" + "0"*64 + "')",
                    "ALTER TABLE public.netshop_rows DISABLE TRIGGER netshop_row_revision_required",
                ):
                    with self.assertRaises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
                        cursor.execute(statement)
                with connection.cursor() as cursor:
                    cursor.execute("UPDATE public.netshop_rows SET spend_cents=201 WHERE id=%s",
                        [row.id])
                    cursor.execute("UPDATE public.netshop_data_revisions SET "
                        "revision=revision+1,source_digest=%s WHERE domain='netshop'",
                        ["b" * 64])
            self.assertEqual(NetshopRow.objects.get(pk=row.pk).spend_cents, 201)
            self.assertEqual(self.revision().source_digest, "b" * 64)
            self.assertEqual(self.marker_count(), 0)
        finally:
            with connection.cursor() as cursor:
                cursor.execute(f'DROP OWNED BY "{role}"')
                cursor.execute(f'DROP ROLE "{role}"')

    def test_two_connections_serialize_on_revision_row(self):
        first, _ = self.seed()
        with transaction.atomic():
            second = self.create_row("second")
            self.bump("second")
        baseline = self.revision().revision

        def competing_update():
            close_old_connections()
            try:
                with transaction.atomic(using="default"):
                    with connections["default"].cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '250ms'")
                    NetshopRow.objects.filter(pk=second.pk).update(spend_cents=202)
                    self.bump("competing")
            finally:
                connections["default"].close()

        with transaction.atomic():
            NetshopRow.objects.filter(pk=first.pk).update(spend_cents=101)
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(competing_update)
                with self.assertRaises(DatabaseError):
                    future.result(timeout=10)
            self.bump("first")
        self.assertEqual(self.revision().revision, baseline + 1)
        self.assertEqual(NetshopRow.objects.get(pk=second.pk).spend_cents, 100)
        self.assertEqual(self.marker_count(), 0)
