from __future__ import annotations

import json
import sqlite3
from io import StringIO
from pathlib import Path
import tempfile

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from customer_service.models import (
    CustomerServiceConversation,
    CustomerServiceDataRevision,
    CustomerServiceImportAttempt,
    CustomerServiceImportBatch,
    CustomerServiceImportFingerprint,
    CustomerServiceImportScopeHead,
    CustomerServiceMigrationRun,
    CustomerServiceWriteAuthority,
)


SOURCE_SCHEMA = """
CREATE TABLE customer_service_import_batches (
  id TEXT PRIMARY KEY, shop_name TEXT, session_file_name TEXT, chat_file_name TEXT,
  file_hash TEXT, status TEXT, conversation_count INTEGER, matched_count INTEGER,
  session_only_count INTEGER, chat_only_count INTEGER, ambiguous_count INTEGER,
  warnings_json TEXT, created_at TEXT, completed_at TEXT
);
CREATE TABLE customer_service_conversations (
  id INTEGER PRIMARY KEY, conversation_key TEXT, first_import_batch_id TEXT,
  last_import_batch_id TEXT, shop_name TEXT, consulted_at TEXT, customer_id TEXT,
  customer_alias TEXT, consultation_type TEXT, agent TEXT, transferred_agent TEXT,
  skill_group TEXT, product_sku TEXT, product_name TEXT, first_response_at TEXT,
  response_seconds REAL, duration_minutes REAL, customer_message_count INTEGER,
  agent_message_count INTEGER, satisfaction TEXT, resolved TEXT, conversation_id TEXT,
  match_status TEXT, match_confidence TEXT, chat_started_at TEXT, chat_ended_at TEXT,
  chat_customer_alias TEXT, messages_json TEXT, robot_scope TEXT, problem_type TEXT,
  conversion_status TEXT, service_issues TEXT, summary_text TEXT, analysis_source TEXT,
  analyzed_at TEXT, annotated_at TEXT, created_at TEXT, updated_at TEXT
);
CREATE TABLE customer_service_conversation_versions (
  conversation_id INTEGER PRIMARY KEY, version INTEGER, updated_at TEXT
);
CREATE TABLE customer_service_deletion_audits (
  audit_id TEXT PRIMARY KEY, conversation_id INTEGER, conversation_key TEXT,
  actor TEXT, old_version INTEGER, expected_version INTEGER, reason TEXT, deleted_at TEXT
);
CREATE TABLE import_content_fingerprints (
  sequence INTEGER PRIMARY KEY, domain TEXT, batch_id TEXT, scope_key TEXT,
  scope_json TEXT, import_hash TEXT, raw_file_hash TEXT, content_hash TEXT,
  row_count INTEGER, status TEXT, publication_sequence INTEGER, created_at TEXT
);
CREATE TABLE import_content_attempts (
  sequence INTEGER PRIMARY KEY, attempt_id TEXT, domain TEXT, batch_id TEXT,
  scope_key TEXT, scope_json TEXT, import_hash TEXT, raw_file_hash TEXT,
  content_hash TEXT, row_count INTEGER, file_name TEXT, file_size_bytes INTEGER,
  actor TEXT, warnings_json TEXT, outcome TEXT, error_code TEXT,
  recovered_from_attempt_id TEXT, created_at TEXT, updated_at TEXT
);
CREATE TABLE import_scope_heads (
  domain TEXT, scope_key TEXT, state_token TEXT, status TEXT, owner_token TEXT,
  current_batch_id TEXT, generation INTEGER, updated_at TEXT,
  PRIMARY KEY(domain, scope_key)
);
CREATE TABLE customer_service_write_authority (
  id INTEGER PRIMARY KEY, owner TEXT, epoch INTEGER, cutover_id TEXT, updated_at TEXT
);
CREATE TABLE inventory_import_uploads (
  id TEXT PRIMARY KEY, fingerprint TEXT UNIQUE, file_name TEXT,
  file_size_bytes INTEGER, chunk_size_bytes INTEGER, chunk_count INTEGER,
  received_chunk_count INTEGER DEFAULT 0, received_bytes INTEGER DEFAULT 0,
  status TEXT DEFAULT 'uploading', created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP, expires_at TEXT
);
CREATE TABLE inventory_import_upload_chunks (
  upload_id TEXT, chunk_index INTEGER, object_key TEXT, size_bytes INTEGER,
  sha256 TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(upload_id, chunk_index)
);
CREATE TABLE inventory_import_upload_results (
  upload_id TEXT PRIMARY KEY, result_json TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def create_source(path: Path) -> None:
    scope_key = "a" * 64
    import_hash = "b" * 64
    raw_hash = "c" * 64
    content_hash = "d" * 64
    state_token = "e" * 64
    connection = sqlite3.connect(path)
    try:
        connection.executescript(SOURCE_SCHEMA)
        connection.execute("INSERT INTO customer_service_write_authority VALUES (1,'legacy',1,'','2026-09-01 10:00:00')")
        connection.execute(
            "INSERT INTO customer_service_import_batches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("batch-1", "志高商用设备", "session.xlsx", "chat.log", raw_hash, "completed", 1, 1, 0, 0, 0, '{"items":["warning"]}', "2026-09-01 10:00:00", "2026-09-01 10:01:00"),
        )
        connection.execute(
            "INSERT INTO customer_service_conversations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (7, "志高商用设备:conversation-7", "batch-1", "batch-1", "志高商用设备", "2026-09-01 10:00:00", "customer-7", "顾客7", "商品咨询", "客服A", "", "在线客服", "SKU-7", "饮水机", "2026-09-01 10:00:02", 2.0, 3.5, 2, 2, "满意", "已解决", "chat-7", "matched", "exact", "2026-09-01 10:00:00", "2026-09-01 10:03:30", "顾客7", '[{"sender":"顾客","sentAt":"2026-09-01 10:00:00","content":"请问价格？"}]', "", "", "", "", "", "", None, None, "2026-09-01 10:00:00", "2026-09-01 10:01:00"),
        )
        connection.execute("INSERT INTO customer_service_conversation_versions VALUES (7,3,'2026-09-01 10:01:00')")
        connection.execute(
            "INSERT INTO customer_service_deletion_audits VALUES (?,?,?,?,?,?,?,?)",
            ("00000000-0000-0000-0000-000000000007", 99, "removed-99", "admin@example.test", 2, 2, "历史清理", "2026-09-01 11:00:00"),
        )
        connection.execute(
            "INSERT INTO import_content_fingerprints VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (1, "customer-service", "batch-1", scope_key, '{"shopName":"志高商用设备"}', import_hash, raw_hash, content_hash, 1, "completed", 1, "2026-09-01 10:01:00"),
        )
        connection.execute(
            "INSERT INTO import_content_attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (1, "00000000-0000-0000-0000-000000000017", "customer-service", "batch-1", scope_key, '{"shopName":"志高商用设备"}', import_hash, raw_hash, content_hash, 1, "session.xlsx / chat.log", 2048, "admin@example.test", '{"items":[]}', "imported", "", "", "2026-09-01 10:01:00", "2026-09-01 10:01:00"),
        )
        connection.execute(
            "INSERT INTO import_scope_heads VALUES (?,?,?,?,?,?,?,?)",
            ("customer-service", scope_key, state_token, "ready", "", "batch-1", 1, "2026-09-01 10:01:00"),
        )
        connection.commit()
    finally:
        connection.close()


class CustomerServiceMigrationTests(TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.source = Path(self.temporary.name) / "customer-service.sqlite3"
        create_source(self.source)

    def test_plan_apply_verify_preserves_facts_and_versions(self) -> None:
        planned = StringIO()
        call_command("migrate_customer_service_from_d1", source=str(self.source), plan=True, stdout=planned)
        plan = json.loads(planned.getvalue())
        self.assertTrue(plan["planId"].startswith("customer-service-plan-"))
        self.assertEqual(plan["counts"], {"batches": 1, "conversations": 1, "audits": 1, "attempts": 1, "fingerprints": 1, "heads": 1})

        applied = StringIO()
        call_command("migrate_customer_service_from_d1", source=str(self.source), apply=True, approved_plan_id=plan["planId"], stdout=applied)
        result = json.loads(applied.getvalue())
        run_id = result["runId"]
        self.assertTrue(run_id.startswith("customer-service-"))
        self.assertEqual(CustomerServiceConversation.objects.get(id=7).version, 3)
        self.assertEqual(CustomerServiceConversation.objects.get(id=7).messages[0]["content"], "请问价格？")
        self.assertEqual(CustomerServiceImportBatch.objects.get(id="batch-1").raw_file_hash, "c" * 64)
        self.assertEqual(CustomerServiceImportAttempt.objects.count(), 1)
        self.assertEqual(CustomerServiceImportFingerprint.objects.count(), 1)
        self.assertEqual(CustomerServiceImportScopeHead.objects.get(shop_name="志高商用设备").current_batch_id, "batch-1")
        self.assertEqual(CustomerServiceDataRevision.objects.get(domain="customer-service").revision, 1)
        self.assertEqual(CustomerServiceWriteAuthority.objects.get(id=1).migration_verify_run_id, run_id)

        verified = StringIO()
        call_command("migrate_customer_service_from_d1", source=str(self.source), verify=True, approved_run_id=run_id, stdout=verified)
        self.assertEqual(json.loads(verified.getvalue())["runId"], run_id)

    def test_apply_rejects_changed_frozen_source(self) -> None:
        planned = StringIO()
        call_command("migrate_customer_service_from_d1", source=str(self.source), plan=True, stdout=planned)
        plan_id = json.loads(planned.getvalue())["planId"]
        connection = sqlite3.connect(self.source)
        connection.execute("UPDATE customer_service_conversations SET customer_alias='changed' WHERE id=7")
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(CommandError, "approved-plan-id"):
            call_command("migrate_customer_service_from_d1", source=str(self.source), apply=True, approved_plan_id=plan_id)

    def test_plan_preserves_historical_batch_count_after_conversation_reimport(self) -> None:
        connection = sqlite3.connect(self.source)
        try:
            connection.execute(
                "UPDATE customer_service_import_batches SET conversation_count=2 WHERE id='batch-1'"
            )
            connection.commit()
        finally:
            connection.close()

        planned = StringIO()
        call_command(
            "migrate_customer_service_from_d1",
            source=str(self.source),
            plan=True,
            stdout=planned,
        )
        self.assertEqual(json.loads(planned.getvalue())["counts"]["conversations"], 1)

    def test_migration_preserves_legacy_messages_above_online_import_limit(self) -> None:
        messages = [
            {"sender": "顾客", "sentAt": "2026-09-01 10:00:00", "content": f"消息 {index}"}
            for index in range(201)
        ]
        connection = sqlite3.connect(self.source)
        try:
            connection.execute(
                "UPDATE customer_service_conversations SET messages_json=? WHERE id=7",
                (json.dumps(messages, ensure_ascii=False, separators=(",", ":")),),
            )
            connection.commit()
        finally:
            connection.close()

        planned = StringIO()
        call_command(
            "migrate_customer_service_from_d1",
            source=str(self.source),
            plan=True,
            stdout=planned,
        )
        plan_id = json.loads(planned.getvalue())["planId"]
        applied = StringIO()
        call_command(
            "migrate_customer_service_from_d1",
            source=str(self.source),
            apply=True,
            approved_plan_id=plan_id,
            stdout=applied,
        )
        self.assertEqual(len(CustomerServiceConversation.objects.get(id=7).messages), 201)

    def test_authority_prepare_blocks_d1_and_activate_is_terminal(self) -> None:
        planned = StringIO()
        call_command("migrate_customer_service_from_d1", source=str(self.source), plan=True, stdout=planned)
        plan_id = json.loads(planned.getvalue())["planId"]
        applied = StringIO()
        call_command("migrate_customer_service_from_d1", source=str(self.source), apply=True, approved_plan_id=plan_id, stdout=applied)
        run_id = json.loads(applied.getvalue())["runId"]
        sql_path = Path(__file__).resolve().parents[3] / "drizzle" / "0107_customer_service_write_authority.sql"
        source = sqlite3.connect(self.source)
        try:
            for statement in sql_path.read_text(encoding="utf-8").split("--> statement-breakpoint"):
                if statement.strip():
                    source.execute(statement.strip())
            source.commit()
        finally:
            source.close()

        cutover_id = "customer-service-test-cutover"
        call_command("customer_service_write_authority", source=str(self.source), prepare=True, approved_run_id=run_id, cutover_id=cutover_id, stdout=StringIO())
        source = sqlite3.connect(self.source)
        try:
            self.assertEqual(source.execute("SELECT owner FROM customer_service_write_authority WHERE id=1").fetchone()[0], "pending")
            with self.assertRaisesRegex(sqlite3.DatabaseError, "customer_service_authority_not_legacy"):
                source.execute("INSERT INTO customer_service_import_batches(id,shop_name,session_file_name,chat_file_name,file_hash,status) VALUES ('blocked','shop','s','c','f','completed')")
            with self.assertRaisesRegex(sqlite3.DatabaseError, "customer_service_authority_not_legacy"):
                source.execute("INSERT INTO inventory_import_uploads(id,fingerprint,file_name,file_size_bytes,chunk_size_bytes,chunk_count,expires_at) VALUES ('blocked-upload','customer-service:session:blocked','s.xlsx',1,1,1,'2999-01-01T00:00:00Z')")
        finally:
            source.close()

        call_command("customer_service_write_authority", source=str(self.source), activate=True, approved_run_id=run_id, cutover_id=cutover_id, stdout=StringIO())
        authority = CustomerServiceWriteAuthority.objects.get(id=1)
        self.assertEqual(authority.status, "postgres")
        self.assertEqual(authority.cutover_id, cutover_id)
        self.assertIsNotNone(authority.authority_epoch)
        source = sqlite3.connect(self.source)
        try:
            self.assertEqual(source.execute("SELECT owner FROM customer_service_write_authority WHERE id=1").fetchone()[0], "postgresql")
        finally:
            source.close()
