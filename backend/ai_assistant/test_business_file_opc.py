"""Synthetic renderer 5/6 compatibility and immutable version fences."""
from copy import deepcopy
import hashlib
from importlib import import_module
from types import SimpleNamespace
from unittest import TestCase as PureTestCase, skipUnless
from unittest.mock import MagicMock, patch

from django.apps import apps
from django.db import connection, DatabaseError, transaction
from django.test import TestCase, override_settings

from . import models as m
from .policy import canonical, uid
from . import test_business_volume_database as legacy_fixtures


class OpcMigrationPureTests(PureTestCase):
    def test_inverse_keeps_exact_five_predecessor_functions(self):
        current = import_module("ai_assistant.migrations.0025_business_file_opc")
        predecessor = import_module("ai_assistant.migrations.0020_business_volume_files")
        cursor = MagicMock()
        editor = SimpleNamespace(connection=SimpleNamespace(vendor="postgresql", cursor=MagicMock()))
        editor.connection.cursor.return_value.__enter__.return_value = cursor
        with patch.object(import_module("ai_assistant.migrations.0017_business_file_renderer"), "change_constraint"):
            predecessor.install(None, editor)
        old = {call.args[0].replace("CREATE FUNCTION ", "CREATE OR REPLACE FUNCTION ", 1)
               for call in cursor.execute.call_args_list if call.args[0].startswith("CREATE ")}
        self.assertTrue(set(current.OLD_SQL) <= old)
        self.assertEqual(len(current.OLD_SQL), 5)
        self.assertEqual(len(current.NEW_SQL), 5)


@skipUnless(connection.vendor == "postgresql", "OPC database fences require isolated PostgreSQL")
@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class OpcDatabaseTests(TestCase):
    user, call, execute, collect, create = legacy_fixtures.BusinessVolumeDatabaseTests.user, legacy_fixtures.BusinessVolumeDatabaseTests.call, legacy_fixtures.BusinessVolumeDatabaseTests.execute, legacy_fixtures.BusinessVolumeDatabaseTests.collect, legacy_fixtures.BusinessVolumeDatabaseTests.create
    setUp, drain, chunk, account, ready = legacy_fixtures.BusinessVolumeDatabaseTests.setUp, legacy_fixtures.BusinessVolumeDatabaseTests.drain, legacy_fixtures.BusinessVolumeDatabaseTests.chunk, legacy_fixtures.BusinessVolumeDatabaseTests.account, legacy_fixtures.BusinessVolumeDatabaseTests.ready

    def building(self, version=6):
        row = m.AiBusinessFileRun.objects.create(id=uid("opc-run"), report=self.report,
            owner_email=self.admin.email, binding_digest=hashlib.sha256(uid("binding").encode()).hexdigest(), renderer_version=version)
        m.AiBusinessFileRun.objects.filter(pk=row.pk).update(status="building", version=2, attempt=1)
        row.refresh_from_db()
        self.drain()
        return row

    def staged(self):
        row, manifest = legacy_fixtures.BusinessVolumeDatabaseTests.staged(self)
        manifest["rendererVersion"] = row.renderer_version
        return row, manifest

    def test_v6_complete_and_cross_version_manifest_is_rejected(self):
        row, manifest = self.staged()
        for version in (4, 5, 7, True, 6.0, "6", None):
            changed = deepcopy(manifest)
            changed["rendererVersion"] = version
            with self.subTest(version=version), self.assertRaises(DatabaseError), transaction.atomic():
                self.ready(row, changed)
        self.ready(row, manifest)
        row.refresh_from_db()
        self.assertEqual(row.status, "ready")
        self.assertEqual(row.renderer_version, 6)
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessFileRun.objects.filter(pk=row.pk).update(renderer_version=4, version=row.version+1)

    def test_v5_uses_only_single_chunks_and_matching_manifest(self):
        row = self.building(5)
        descriptors = {}
        for kind in ("html", "xlsx"):
            content = ("synthetic-"+kind).encode()
            m.AiBusinessFileChunk.objects.create(id=uid("opc-chunk"), run=row, attempt=1, format=kind,
                sequence=1, content=content, content_digest=hashlib.sha256(content).hexdigest())
            descriptors[kind] = {"bytes":len(content),"chunkCount":1,"sha256":hashlib.sha256(content).hexdigest()}
        m.AiBusinessFileRun.objects.filter(pk=row.pk).update(version=3, stored_bytes=sum(v["bytes"] for v in descriptors.values()))
        row.refresh_from_db()
        with self.assertRaises(DatabaseError), transaction.atomic():
            self.chunk(row, 1, "html")
        manifest = {"schemaVersion":"business-file-delivery-v1", "rendererVersion":5,
            "bindingDigest":row.binding_digest,"draft":False,"attempt":1,"files":descriptors}
        for key, value in (("rendererVersion",3),("rendererVersion",5.0),("rendererVersion",True),
                           ("bindingDigest","f"*64),("draft",True),("draft",None)):
            changed = {**manifest,key:value}
            with self.subTest(key=key,value=value), self.assertRaises(DatabaseError), transaction.atomic():
                self.ready(row, changed)
        self.ready(row, manifest)
        row.refresh_from_db()
        self.assertEqual(row.status, "ready")

    def test_reverse_refuses_new_versions_even_queued_and_preserves_old_identity(self):
        migration = import_module("ai_assistant.migrations.0025_business_file_opc")
        for version in (5,6):
            with self.subTest(version=version), transaction.atomic():
                row = m.AiBusinessFileRun.objects.create(id=uid("opc-new"), report=self.report,
                    owner_email=self.admin.email, binding_digest="a"*64, renderer_version=version)
                with self.assertRaises(RuntimeError):
                    migration.uninstall(apps, SimpleNamespace(connection=connection))
                self.assertEqual(m.AiBusinessFileRun.objects.get(pk=row.pk).renderer_version, version)
                transaction.set_rollback(True)

    def test_new_initial_state_and_chunk_kind_guards(self):
        for version in (5,6):
            with self.subTest(version=version), self.assertRaises(DatabaseError), transaction.atomic():
                m.AiBusinessFileRun.objects.create(id=uid("opc-bad"), report=self.report,
                    owner_email=self.admin.email,binding_digest="b"*64,renderer_version=version,status="ready")
        row = self.building(6)
        with self.assertRaises(DatabaseError), transaction.atomic():
            m.AiBusinessFileChunk.objects.create(id=uid("opc-wrong"),run=row,attempt=1,format="html",
                sequence=1,content=b"x",content_digest=hashlib.sha256(b"x").hexdigest())
