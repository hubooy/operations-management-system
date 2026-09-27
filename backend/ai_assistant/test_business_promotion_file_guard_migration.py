"""Isolated PostgreSQL storage boundary for unpublished renderer 7."""
from hashlib import sha256
from importlib import import_module
from types import SimpleNamespace
import unittest

from django.apps import apps
from django.db import DatabaseError, connection, transaction
from django.test import TransactionTestCase, override_settings

from . import models as m, test_business_promotion_profile_migration as fixtures
from .policy import mutation


def migration():
    return import_module("ai_assistant.migrations.0027_business_promotion_file_guard")


class Renderer7SqlContractTests(unittest.TestCase):
    def test_frozen_functions_and_ready_guard(self):
        sql = migration()
        self.assertEqual(len(sql.OLD_SQL), len(sql.NEW_SQL))
        self.assertIs(sql.CHUNK_GUARD, sql.OLD_SQL[0])
        self.assertIs(sql.MANIFEST_GUARD, sql.OLD_SQL[2])
        self.assertEqual(sql.NEW_SQL[0], sql.OLD_SQL[0])
        self.assertEqual(sql.NEW_SQL[2], sql.OLD_SQL[2])
        self.assertIn("renderer_version NOT IN (4,6,7)", sql.VOLUME_CHUNK_GUARD)
        self.assertIn("renderer_version IN (4,5,6,7)", sql.RUN_GUARD)
        self.assertIn("renderer_version IN (4,6,7)", sql.RUN_GUARD)
        self.assertIn("business-agent-screening-promotion-reference-v1", sql.RUN_GUARD)
        self.assertIn("NEW.renderer_version=7 AND NEW.status='ready'", sql.RUN_GUARD)
        self.assertIn("parent.renderer_version=7 AND parent.status='ready'", sql.COMPLETE_GUARD)
        self.assertNotIn("renderer_version=7", sql.MANIFEST_GUARD)
        for old, new in zip(sql.OLD_SQL, sql.NEW_SQL):
            self.assertEqual(old.split("FUNCTION ", 1)[1].split("(", 1)[0],
                             new.split("FUNCTION ", 1)[1].split("(", 1)[0])


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class Renderer7GuardTests(TransactionTestCase):
    user = fixtures.PromotionProfileMigrationTests.user
    call = fixtures.PromotionProfileMigrationTests.call
    collect_body = fixtures.PromotionProfileMigrationTests.collect_body
    bundle = fixtures.PromotionProfileMigrationTests.bundle
    input_for = fixtures.PromotionProfileMigrationTests.input_for
    insert = fixtures.PromotionProfileMigrationTests.insert
    seed = fixtures.PromotionProfileMigrationTests.seed
    setUp = fixtures.PromotionProfileMigrationTests.setUp
    prepared = fixtures.PromotionProfileMigrationTests.prepared
    insert_shape = fixtures.PromotionProfileMigrationTests.insert_shape

    def new_report(self, tag):
        candidate = self.prepared(tag)
        with mutation(self.admin):
            return self.insert_shape(candidate)

    def create(self, report, *, tag="good", owner=None, binding=None, version=7):
        return m.AiBusinessFileRun.objects.create(id="promotion-file-"+tag,
            report=report, owner_email=owner or self.admin.email.lower(),
            draft=True, renderer_version=version, binding_digest=binding or "a"*64)

    def test_exact_new_parent_stages_chunks_but_cannot_publish(self):
        report = self.new_report("stage")
        with mutation(self.admin):
            row = self.create(report)
            row.status, row.version, row.attempt = "building", 2, 1
            row.save(update_fields=["status", "version", "attempt"])
            raw = b"synthetic-unpublished-fragment"
            m.AiBusinessVolumeChunk.objects.create(id="promotion-volume-fragment", run=row,
                attempt=1, volume_index=1, format="html", sequence=1, content=raw,
                content_digest=sha256(raw).hexdigest())
            row.stored_bytes, row.version = len(raw), 3
            row.save(update_fields=["stored_bytes", "version"])
        row.refresh_from_db()
        self.assertEqual((row.renderer_version, row.status, row.stored_bytes), (7, "building", len(raw)))
        with self.assertRaises(DatabaseError), mutation(self.admin):
            m.AiBusinessFileChunk.objects.create(id="promotion-legacy-fragment", run=row,
                attempt=1, format="html", sequence=1, content=b"x", content_digest=sha256(b"x").hexdigest())
        with self.assertRaises(DatabaseError), mutation(self.admin):
            m.AiBusinessVolumeChunk.objects.create(id="promotion-wrong-attempt", run=row,
                attempt=2, volume_index=1, format="xlsx", sequence=1, content=b"x",
                content_digest=sha256(b"x").hexdigest())
        with self.assertRaisesRegex(DatabaseError, "ai_promotion_renderer_unpublished"), mutation(self.admin):
            row.status, row.version = "ready", 4
            row.save(update_fields=["status", "version"])
        row.refresh_from_db()
        self.assertEqual((row.status, row.version, row.stored_bytes), ("building", 3, len(raw)))

    def test_wrong_parent_owner_or_binding_and_old_queued_versions(self):
        report = self.new_report("scope")
        for tag, target, owner, binding in (
            ("old", self.report, None, None),
            ("owner", report, "different@example.invalid", None),
            ("binding", report, None, "x"*64),
        ):
            with self.subTest(tag=tag), self.assertRaises(DatabaseError), mutation(self.admin):
                self.create(target, tag=tag, owner=owner, binding=binding)
        with mutation(self.admin):
            for version in range(1, 7):
                row = self.create(self.report, tag="old-"+str(version), version=version)
                self.assertEqual(row.renderer_version, version)
        self.assertEqual(m.AiBusinessFileRun.objects.filter(renderer_version__lte=6).count(), 6)

    def test_empty_reverse_restores_exact_old_functions_and_any_seven_row_blocks(self):
        sql = migration(); editor = SimpleNamespace(connection=connection)
        with transaction.atomic():
            sql.uninstall(apps, editor)
            for original in sql.OLD_SQL:
                name = original.split("FUNCTION ", 1)[1].split("(", 1)[0]
                with connection.cursor() as cursor:
                    cursor.execute("SELECT prosrc FROM pg_proc WHERE proname=%s", [name])
                    self.assertEqual(cursor.fetchone()[0], original.split("$$")[1])
            sql.install(apps, editor)
        report = self.new_report("reverse")
        with mutation(self.admin):
            row = self.create(report, tag="reverse")
            row.status, row.version = "cancelled", 2
            row.save(update_fields=["status", "version"])
        with self.assertRaisesRegex(RuntimeError, "renderer 7"):
            sql.uninstall(apps, editor)
        self.assertEqual(m.AiBusinessFileRun.objects.get(pk=row.id).status, "cancelled")
