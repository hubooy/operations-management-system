import io
import json
from pathlib import Path
import sqlite3
import tempfile
from contextlib import contextmanager
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError
from django.test import TestCase, override_settings
from . import models as m
from .control_models import AiDataRevision, AiMigrationRun, AiWriteAuthority
from .migration_service import migrate, source_snapshot, target_snapshot
from .management.commands.ai_cutover_check import _source_rejects, _preserved


@contextmanager
def sqlite_connection(path):
    connection = sqlite3.connect(path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


@override_settings(DJANGO_PROCESS_ROLE="development", DJANGO_ENVIRONMENT="test")
class AiMigrationTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="teruisi-ai-isolated-")
        self.addCleanup(self.directory.cleanup)
        self.source = Path(self.directory.name) / "source.sqlite"
        with sqlite_connection(self.source) as source:
            source.executescript(
                (Path(__file__).parent / "fixtures/d1-schema.sql").read_text(
                    encoding="utf-8"
                )
            )
            source.execute(
                "INSERT INTO ai_models(id,name,protocol,model_type,model_name,base_url,api_key_encrypted,api_key_suffix,status) VALUES ('fixture-model','测试模型','openai_compatible','text','fixture','https://api.openai.com/v1','preserved-ciphertext','text','enabled')"
            )
            source.execute(
                "CREATE TABLE other_domain_fact(id INTEGER PRIMARY KEY,value TEXT)"
            )
            source.execute("INSERT INTO other_domain_fact VALUES (1,'unchanged')")
        AiDataRevision.objects.get_or_create(domain="ai-assistant")
        AiWriteAuthority.objects.get_or_create(id=1)

    def command(self, name, **options):
        output = io.StringIO()
        call_command(name, stdout=output, **options)
        return json.loads(output.getvalue())

    def adopt(self):
        dry = migrate(self.source, "dry-run")
        applied = migrate(self.source, "apply", dry["runId"])
        self.assertEqual(applied["sourceDigest"], applied["targetDigest"])
        return applied

    def test_exact_apply_preserves_ciphertext_and_is_consumed_once(self):
        dry = migrate(self.source, "dry-run")
        self.assertEqual(m.AiModels.objects.count(), 0)
        applied = migrate(self.source, "apply", dry["runId"])
        self.assertEqual(
            m.AiModels.objects.get().api_key_encrypted, "preserved-ciphertext"
        )
        self.assertEqual(
            migrate(self.source, "verify-only")["targetDigest"], applied["sourceDigest"]
        )
        self.assertEqual(
            AiMigrationRun.objects.get(id=dry["runId"]).consumed_by_run_id,
            applied["runId"],
        )
        with self.assertRaises(CommandError):
            migrate(self.source, "apply", dry["runId"])
        self.assertEqual(m.AiModels.objects.count(), 1)
        with sqlite_connection(self.source) as source:
            self.assertEqual(
                source.execute("SELECT value FROM other_domain_fact").fetchone(),
                ("unchanged",),
            )

    def test_changed_source_and_changed_path_reject_without_target_writes(self):
        dry = migrate(self.source, "dry-run")
        other = self.source.with_name("other.sqlite")
        other.write_bytes(self.source.read_bytes())
        with self.assertRaises(CommandError):
            migrate(other, "apply", dry["runId"])
        with sqlite_connection(self.source) as source:
            source.execute("UPDATE ai_models SET name='changed'")
        with self.assertRaises(CommandError):
            migrate(self.source, "apply", dry["runId"])
        self.assertFalse(m.AiModels.objects.exists())
        self.assertEqual(AiDataRevision.objects.get(domain="ai-assistant").revision, 0)

    def test_unknown_source_objects_and_invalid_scope_fail_closed(self):
        with sqlite_connection(self.source) as source:
            source.execute("CREATE TABLE ai_unknown(value TEXT)")
        with self.assertRaises(CommandError):
            source_snapshot(self.source)
        with sqlite_connection(self.source) as source:
            source.execute("DROP TABLE ai_unknown")
            source.execute(
                "INSERT INTO ai_conversation_scopes(conversation_id,scope_json) VALUES ('missing','{\"warehouses\":[]}')"
            )
        with self.assertRaises(CommandError):
            migrate(self.source, "dry-run")

    def test_target_constraint_failure_rolls_back_the_whole_adoption(self):
        with sqlite_connection(self.source) as source:
            source.execute(
                "INSERT INTO ai_conversation_scopes(conversation_id,scope_json) VALUES ('missing','null')"
            )
        dry = migrate(self.source, "dry-run")
        with self.assertRaises(IntegrityError):
            migrate(self.source, "apply", dry["runId"])
        self.assertFalse(m.AiModels.objects.exists())
        self.assertFalse(m.AiConversationScopes.objects.exists())
        self.assertEqual(
            AiMigrationRun.objects.get(id=dry["runId"]).consumed_by_run_id, ""
        )

    def test_wrong_process_role_cannot_migrate(self):
        with override_settings(
            DJANGO_ENVIRONMENT="production", DJANGO_PROCESS_ROLE="ai_writer"
        ):
            with self.assertRaises(CommandError):
                migrate(self.source, "dry-run")

    def test_authority_and_terminal_retirement_are_one_way_and_preserve_other_domain(
        self,
    ):
        applied = self.adopt()
        output = Path(self.directory.name) / "install.json"
        self.command(
            "ai_cutover_check",
            action="install-authority",
            source=str(self.source),
            output=str(output),
        )
        with sqlite_connection(self.source) as source:
            preserved = _preserved(source)
        self.command(
            "ai_write_authority",
            source=str(self.source),
            approved_run_id=applied["runId"],
            cutover_id="ai-isolated-cutover",
            prepare=True,
        )
        with sqlite_connection(self.source) as source:
            _source_rejects(source)
        self.command(
            "ai_write_authority",
            source=str(self.source),
            approved_run_id=applied["runId"],
            cutover_id="ai-isolated-cutover",
            activate=True,
        )
        authority = AiWriteAuthority.objects.get(id=1)
        epoch = authority.authority_epoch
        self.command(
            "ai_write_authority",
            source=str(self.source),
            approved_run_id=applied["runId"],
            cutover_id="ai-isolated-cutover",
            activate=True,
        )
        self.assertEqual(AiWriteAuthority.objects.get(id=1).authority_epoch, epoch)
        with self.assertRaises(CommandError):
            self.command(
                "ai_write_authority",
                source=str(self.source),
                approved_run_id=applied["runId"],
                cutover_id="ai-isolated-cutover",
                prepare=True,
            )
        from .management.commands.ai_cutover_check import _sql

        _, statements = _sql("0114_ai_domain_retirement.sql")
        with sqlite_connection(self.source) as source:
            source.execute("BEGIN IMMEDIATE")
            source.execute(statements[0])
            source.execute(
                "INSERT INTO domain_retirement_receipts(domain,version,status,cutover_id,plan_id,attestation_sha256,smoke_receipt_sha256,preflight_evidence_sha256,migration_sha256,audit_id,preserved_evidence_sha256,created_at,completed_at) VALUES ('ai-assistant','ai-assistant-domain-retirement-receipt-v1','approved','ai-isolated-cutover',?,?,?,?,?,?,?,'2026-09-05T00:00:00Z',NULL)",
                ["a" * 64] * 7,
            )
            for statement in statements[1:]:
                source.execute(statement)
            self.assertEqual(_preserved(source), preserved)
            _source_rejects(source)
            self.assertEqual(
                source.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='view' AND name LIKE 'ai_%'"
                ).fetchone()[0],
                40,
            )
        with self.assertRaises(CommandError):
            source_snapshot(self.source)
        self.assertEqual(target_snapshot()["digest"], applied["targetDigest"])

    def test_unapproved_retirement_cannot_remove_facts(self):
        from .management.commands.ai_cutover_check import _sql

        _, statements = _sql("0114_ai_domain_retirement.sql")
        before = self.source.read_bytes()
        with self.assertRaises(sqlite3.DatabaseError):
            with sqlite_connection(self.source) as source:
                source.execute("BEGIN IMMEDIATE")
                for statement in statements:
                    source.execute(statement)
        self.assertEqual(self.source.read_bytes(), before)
