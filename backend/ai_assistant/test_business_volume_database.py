"""Renderer 4 PostgreSQL invariants, independently of the file service."""
from copy import deepcopy
import hashlib
from importlib import import_module
from types import SimpleNamespace
from unittest import skipUnless
from unittest import TestCase as PureTestCase
from unittest.mock import MagicMock

from django.apps import apps
from django.db import connection, DatabaseError, transaction
from django.test import TestCase, override_settings

from . import models as m, test_business_reports as fixtures
from .database_contract import MODELS, READ_TABLES, WRITER_PRIVILEGES
from .policy import canonical, uid
from .table_manifest import AI_TABLES


@skipUnless(connection.vendor == "postgresql", "multi-volume guards require PostgreSQL")
@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class BusinessVolumeDatabaseTests(TestCase):
    user = fixtures.BusinessReportTests.user
    call = fixtures.BusinessReportTests.call
    execute = fixtures.BusinessReportTests.execute
    collect = fixtures.BusinessReportTests.collect
    create = fixtures.BusinessReportTests.create

    def setUp(self):
        fixtures.BusinessReportTests.setUp(self)
        self.report = m.AiReportRun.objects.get(pk=self.create(True)["id"])

    def drain(self):
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ai_business_volume_complete IMMEDIATE")
            cursor.execute("SET CONSTRAINTS ai_business_volume_complete DEFERRED")

    def building(self):
        row = m.AiBusinessFileRun.objects.create(id=uid("volume-run"), report=self.report,
            owner_email=self.admin.email, binding_digest=hashlib.sha256(uid("binding").encode()).hexdigest(), renderer_version=4)
        m.AiBusinessFileRun.objects.filter(pk=row.pk).update(status="building", version=2, attempt=1)
        row.refresh_from_db()
        self.drain()
        return row

    def chunk(self, row, index, kind, *, sequence=1, content=b"synthetic", **values):
        fields = {"id": uid("volume-chunk"), "run": row, "attempt": row.attempt, "volume_index": index,
            "format": kind, "sequence": sequence, "content": content, "content_digest": hashlib.sha256(content).hexdigest()}
        fields.update(values)
        return m.AiBusinessVolumeChunk.objects.create(**fields)

    def account(self, row):
        size = sum(len(bytes(chunk.content)) for chunk in m.AiBusinessVolumeChunk.objects.filter(run=row))
        m.AiBusinessFileRun.objects.filter(pk=row.pk).update(version=row.version+1, stored_bytes=size)
        row.refresh_from_db()
        self.drain()

    def staged(self, count=2):
        row = self.building()
        files = []
        for index in range(1, count+1):
            for kind in ("html", "xlsx"):
                content = f"synthetic-{index}-{kind}".encode()
                self.chunk(row, index, kind, content=content)
                files.append({"volumeIndex": index, "format": kind, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(), "chunkCount": 1})
        self.chunk(row, 0, "json", content=b"{}")
        self.account(row)
        manifest = {"schemaVersion": "business-file-delivery-v2", "rendererVersion": 4, "bindingDigest": row.binding_digest,
            "attempt": row.attempt, "draft": False, "volumeCount": count, "files": files,
            "manifestFile": {"volumeIndex": 0, "format": "json", "bytes": 2, "sha256": hashlib.sha256(b"{}").hexdigest(), "chunkCount": 1}}
        return row, manifest

    def ready(self, row, manifest):
        m.AiBusinessFileRun.objects.filter(pk=row.pk).update(version=row.version+1, status="ready", manifest_json=manifest if isinstance(manifest, str) else canonical(manifest))
        self.drain()

    def test_two_complete_volumes_commit_and_manifest_matches_all_files(self):
        row, manifest = self.staged()
        self.ready(row, manifest)
        row.refresh_from_db()
        self.assertEqual(row.status, "ready")
        self.assertEqual(m.AiBusinessVolumeChunk.objects.filter(run=row).count(), 5)
        self.assertEqual(row.manifest_json, canonical(manifest))
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessFileRun.objects.filter(pk=row.pk).update(version=row.version+1, status="building")

    def test_exact_manifest_shape_binding_order_and_types_reject_atomically(self):
        row, manifest = self.staged()
        changes = [lambda v: v.update(volumeCount=1), lambda v: v.update(attempt=2), lambda v: v.update(draft=True),
            lambda v: v.update(bindingDigest="0"*64), lambda v: v.update(rendererVersion=True), lambda v: v.update(files=v["files"][:-1]),
            lambda v: v["files"].reverse(), lambda v: v["files"][0].update(bytes=None),
            lambda v: v["files"][0].update(chunkCount="1"), lambda v: v["files"][0].update(sha256=None),
            lambda v: v["manifestFile"].update(volumeIndex=1), lambda v: v.update(extra=True)]
        for change in changes:
            value = deepcopy(manifest); change(value)
            with self.subTest(value=value), self.assertRaises(DatabaseError), transaction.atomic(): self.ready(row, value)
        row.refresh_from_db()
        self.assertEqual(row.status, "building")
        self.assertEqual(row.manifest_json, "{}")

    def test_manifest_integer_fraction_exponent_and_duplicate_fields_rejected(self):
        row, manifest = self.staged()
        raw = canonical(manifest)
        for field, value in (("rendererVersion", 4), ("volumeCount", 2), ("attempt", 1), ("chunkCount", 1), ("volumeIndex", 1)):
            for suffix in (".0", "e0"):
                changed = raw.replace(f'"{field}":{value}', f'"{field}":{value}{suffix}', 1)
                with self.subTest(field=field, suffix=suffix), self.assertRaises(DatabaseError), transaction.atomic(): self.ready(row, changed)
        duplicate = raw.replace('"volumeCount":2', '"volumeCount":2,"volumeCount":2')
        with self.assertRaises(DatabaseError), transaction.atomic(): self.ready(row, duplicate)

    def test_chunks_require_parent_accounting_and_continuity_before_ready(self):
        row = self.building()
        with self.assertRaises(DatabaseError), transaction.atomic():
            self.chunk(row, 1, "html")
            self.drain()
        with self.assertRaises(DatabaseError), transaction.atomic():
            self.chunk(row, 1, "html", sequence=2)
            self.account(row)
        row.refresh_from_db()
        self.assertEqual(row.stored_bytes, 0)
        self.assertEqual(m.AiBusinessVolumeChunk.objects.filter(run=row).count(), 0)
        with transaction.atomic():
            self.chunk(row, 1, "html")
            self.account(row)

    def test_chunk_coordinates_bytes_digest_and_immutability(self):
        row = self.building()
        for index, kind, values in ((0,"html",{}), (1,"json",{}), (101,"xlsx",{}), (0,"json",{"sequence":33}),
            (1,"html",{"content":b""}), (1,"html",{"content":b"x"*524289}), (1,"html",{"content_digest":"0"*64})):
            with self.subTest(index=index, kind=kind, keys=list(values)), self.assertRaises(DatabaseError), transaction.atomic():
                self.chunk(row, index, kind, **values)
        chunk = self.chunk(row, 1, "html")
        self.account(row)
        for action in (lambda: m.AiBusinessVolumeChunk.objects.filter(pk=chunk.pk).update(content=b"changed"),
                       lambda: m.AiBusinessVolumeChunk.objects.filter(pk=chunk.pk).delete()):
            with self.assertRaises(DatabaseError), transaction.atomic(): action()

    def test_renderer_tables_are_separate_and_paused_rejects_late_chunks(self):
        row = self.building()
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessFileChunk.objects.create(id=uid("legacy-chunk"),run=row,attempt=1,format="html",sequence=1,content=b"old",content_digest=hashlib.sha256(b"old").hexdigest())
        old = m.AiBusinessFileRun.objects.create(id=uid("legacy-run"),report=self.report,owner_email=self.admin.email,
            binding_digest="a"*64,renderer_version=3,status="building",attempt=1)
        with self.assertRaises(DatabaseError), transaction.atomic(): self.chunk(old,1,"html")
        m.AiBusinessFileRun.objects.filter(pk=row.pk).update(version=row.version+1,status="paused")
        self.drain()
        with self.assertRaises(DatabaseError), transaction.atomic(): self.chunk(row,1,"html")

    def test_unlisted_volume_blocks_ready_and_previous_attempts_keep_bytes(self):
        row, manifest = self.staged(1)
        self.chunk(row,2,"html")
        self.account(row)
        with self.assertRaises(DatabaseError), transaction.atomic(): self.ready(row,manifest)
        previous_bytes = row.stored_bytes
        m.AiBusinessFileRun.objects.filter(pk=row.pk).update(version=row.version+1,attempt=2)
        row.refresh_from_db()
        self.chunk(row,1,"html",content=b"new")
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessFileRun.objects.filter(pk=row.pk).update(version=row.version+1,stored_bytes=3)
        self.account(row)
        self.assertEqual(row.stored_bytes,previous_bytes+3)

    def test_new_initial_state_grants_and_reverse_migration(self):
        for changes in ({"status":"ready"},{"attempt":1},{"stored_bytes":1},{"manifest_json":'{"bad":true}'}):
            with self.subTest(changes=changes), self.assertRaises(DatabaseError), transaction.atomic():
                m.AiBusinessFileRun.objects.create(id=uid("bad-initial"),report=self.report,owner_email=self.admin.email,
                    renderer_version=4,binding_digest=uid("binding"),**changes)
        self.building()
        reverse = import_module("ai_assistant.migrations.0020_business_volume_files").uninstall
        with self.assertRaisesMessage(RuntimeError,"renderer 4"): reverse(apps,None)
        self.assertIn("ai_business_volume_chunks",AI_TABLES)
        self.assertIn("ai_business_volume_chunks",READ_TABLES)
        self.assertIs(MODELS["ai_business_volume_chunks"],m.AiBusinessVolumeChunk)
        self.assertEqual(WRITER_PRIVILEGES["ai_business_volume_chunks"],("SELECT","INSERT"))


class BusinessVolumeMigrationPureTests(PureTestCase):
    def test_preserved_legacy_function_body_matches_0016(self):
        cursor = MagicMock()
        schema = SimpleNamespace(connection=SimpleNamespace(vendor="postgresql",cursor=MagicMock()))
        schema.connection.cursor.return_value.__enter__.return_value = cursor
        import_module("ai_assistant.migrations.0016_business_files").install(None,schema)
        original = next(call.args[0] for call in cursor.execute.call_args_list if "CREATE FUNCTION ai_business_files_guard()" in call.args[0])
        body = original.split("        BEGIN",1)[1].rsplit("        END $$",1)[0]
        preserved = import_module("ai_assistant.migrations.0020_business_volume_files").LEGACY_RUN_GUARD
        self.assertEqual(" ".join(body.split())," ".join(preserved.split()))
