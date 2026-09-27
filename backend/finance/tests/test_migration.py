from __future__ import annotations

import hashlib
import io
import json
import shutil
import sqlite3
import tempfile
import uuid
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection as target_connection
from django.test import TestCase, TransactionTestCase, override_settings

from finance.import_service import _fingerprint, finance_scope_key
from finance.management.commands import migrate_finance_from_d1 as migration_command
from finance.models import (
    FinanceDataRevision,
    FinanceImportAttempt,
    FinanceImportBatch,
    FinanceImportFingerprint,
    FinanceLine,
    FinanceMigrationRun,
    FinanceMonth,
    FinanceWriteAuthority,
)


SOURCE_SCHEMA = """
CREATE TABLE finance_import_batches (
 id TEXT PRIMARY KEY, source TEXT, file_name TEXT, file_size_bytes INTEGER,
 file_hash TEXT, status TEXT, row_count INTEGER, inserted_count INTEGER,
 duplicate_count INTEGER, warning_count INTEGER, parsed_month_count INTEGER,
 imported_month_count INTEGER, skipped_month_count INTEGER, subject_count INTEGER,
 months_json TEXT, warnings_json TEXT, created_at TEXT, completed_at TEXT
);
CREATE TABLE finance_months (
 month TEXT PRIMARY KEY, batch_id TEXT, sheet_name TEXT, business_name TEXT,
 source_file_name TEXT, status TEXT, shop_count INTEGER, subject_count INTEGER,
 imported_at TEXT
);
CREATE TABLE finance_lines (
 id INTEGER PRIMARY KEY, month TEXT, section TEXT, metric_key TEXT,
 subject_name TEXT, scope_key TEXT, scope_type TEXT, scope_name TEXT,
 group_name TEXT, value_type TEXT, amount_cents INTEGER, rate_bps INTEGER,
 raw_value TEXT, source_row_count INTEGER, sort_order INTEGER, is_total INTEGER,
 created_at TEXT
);
CREATE TABLE finance_targets (id TEXT PRIMARY KEY);
CREATE TABLE finance_target_versions (
 target_id TEXT PRIMARY KEY, version INTEGER, updated_at TEXT
);
CREATE TABLE finance_targets_scoped (
 id TEXT PRIMARY KEY, period_type TEXT, period_key TEXT, platform TEXT,
 shop_name TEXT, category TEXT, manager TEXT, sales_target_cents INTEGER,
 profit_target_cents INTEGER, small_margin_bps INTEGER,
 inventory_cleanup_target_cents INTEGER, promotion_fee_ratio_bps INTEGER,
 stagnant_inventory_target_cents INTEGER, created_at TEXT, updated_at TEXT
);
CREATE TABLE finance_target_scoped_versions (
 target_id TEXT PRIMARY KEY, version INTEGER, updated_at TEXT
);
CREATE TABLE finance_target_deletion_audits (
 audit_id TEXT PRIMARY KEY, target_id TEXT, period_type TEXT, period_key TEXT,
 shop_name TEXT, category TEXT, actor TEXT, old_version INTEGER,
 expected_version INTEGER, reason TEXT, deleted_at TEXT
);
CREATE TABLE finance_target_scoped_deletion_audits (
 audit_id TEXT PRIMARY KEY, target_id TEXT, period_type TEXT, period_key TEXT,
 platform TEXT, shop_name TEXT, category TEXT, actor TEXT, old_version INTEGER,
 expected_version INTEGER, reason TEXT, deleted_at TEXT
);
CREATE TABLE finance_target_legacy_migrations (target_id TEXT PRIMARY KEY, migrated_at TEXT);
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
 current_batch_id TEXT, generation INTEGER, updated_at TEXT
);
CREATE TABLE finance_write_authority (
 id INTEGER PRIMARY KEY, owner TEXT, epoch INTEGER, cutover_id TEXT, updated_at TEXT
);
"""


def create_source(path: Path) -> None:
    batch_id = "a" * 64
    raw_hash = "c" * 64
    state = "d" * 64
    attempt = str(uuid.uuid4())
    normalized_lines = [
        {
            "month": "2026-08", "section": "summary", "metricKey": "net_sales",
            "subjectName": "净销售额", "scopeKey": "business", "scopeType": "business",
            "scopeName": "志高事业部", "groupName": "", "valueType": "amount",
            "amountCents": 100000, "rateBps": None, "rawValue": "1000",
            "sourceRowCount": 1, "sortOrder": 1, "isTotal": False,
        },
        {
            "month": "2026-08", "section": "summary",
            "metricKey": "selling_expense_total", "subjectName": "销售费用",
            "scopeKey": "business", "scopeType": "business",
            "scopeName": "志高事业部", "groupName": "", "valueType": "amount",
            "amountCents": 10000, "rateBps": None, "rawValue": "100",
            "sourceRowCount": 1, "sortOrder": 2, "isTotal": True,
        },
    ]
    _scope_key, content_hash, _row_count = _fingerprint([
        {"month": "2026-08", "businessName": "志高事业部", "lines": normalized_lines}
    ])
    connection = sqlite3.connect(path)
    try:
        connection.executescript(SOURCE_SCHEMA)
        connection.execute(
            "INSERT INTO finance_write_authority VALUES (1,'d1',1,'','2026-08-30 01:00:00')"
        )
        connection.execute(
            "INSERT INTO finance_import_batches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                batch_id, "月度财报 · 志高事业部", "财报.xlsx", 2048, batch_id,
                "completed", 2, 2, 0, 0, 1, 1, 0, 2,
                '["2026-08"]', "[]", "2026-08-30 01:00:00", "2026-08-30 01:01:00",
            ),
        )
        connection.execute(
            "INSERT INTO finance_months VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "2026-08", batch_id, "2026-08", "志高事业部", "财报.xlsx",
                "completed", 0, 2, "2026-08-30 01:01:00",
            ),
        )
        connection.executemany(
            "INSERT INTO finance_lines VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    1, "2026-08", "summary", "net_sales", "净销售额", "business",
                    "business", "志高事业部", "", "amount", 100000, None, "1000",
                    1, 1, 0, "2026-08-30 01:01:00",
                ),
                (
                    2, "2026-08", "summary", "selling_expense_total", "销售费用",
                    "business", "business", "志高事业部", "", "amount", 10000,
                    None, "100", 1, 2, 1, "2026-08-30 01:01:00",
                ),
            ],
        )
        connection.execute(
            "INSERT INTO import_content_fingerprints VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                1, "finance", batch_id, finance_scope_key(),
                '{"months":["2026-08"],"source":"monthly-finance-report"}',
                batch_id, raw_hash, content_hash, 2, "completed", 1,
                "2026-08-30 01:01:00",
            ),
        )
        connection.execute(
            "INSERT INTO import_content_attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                1, attempt, "finance", batch_id, finance_scope_key(),
                '{"months":["2026-08"],"source":"monthly-finance-report"}',
                batch_id, raw_hash, content_hash, 2, "财报.xlsx", 2048,
                "admin@example.test", "[]", "imported", "", "",
                "2026-08-30 01:00:00", "2026-08-30 01:01:00",
            ),
        )
        connection.execute(
            "INSERT INTO import_scope_heads VALUES (?,?,?,?,?,?,?,?)",
            (
                "finance", finance_scope_key(), state, "ready", "", batch_id, 1,
                "2026-08-30 01:01:00",
            ),
        )
        # Install the same authority transition and write guards used by the
        # operator-applied D1 migration.  The fixture tables above deliberately
        # model the historical schema that already exists when 0093 is applied.
        authority_sql = (
            Path(__file__).resolve().parents[3]
            / "drizzle"
            / "0093_finance_write_authority.sql"
        ).read_text(encoding="utf-8")
        connection.executescript(authority_sql)
        connection.commit()
    finally:
        connection.close()


def write_source_manifest(snapshot: Path, live_source: Path, manifest: Path) -> None:
    payload = {
        "authority": {"cutoverId": "", "epoch": 1, "owner": "d1"},
        "authoritySqlSha256": "e" * 64,
        "counts": {},
        "createdAt": "2026-08-30T01:00:00+00:00",
        "formatVersion": "finance-d1-rehearsal-snapshot-v1",
        "outputSha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        "sourceFinanceDigest": "f" * 64,
        "sourcePathSha256": hashlib.sha256(
            str(live_source.resolve()).encode("utf-8")
        ).hexdigest(),
    }
    manifest.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


class FinanceMigrationCommandTests(TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.source = Path(self.temporary.name) / "source.sqlite"
        create_source(self.source)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_command(self, **options):
        output = io.StringIO()
        call_command(
            "migrate_finance_from_d1",
            source=str(self.source),
            stdout=output,
            **options,
        )
        return json.loads(output.getvalue().strip().splitlines()[-1])

    def run_authority_command(self, *, source: Path | None = None, **options):
        output = io.StringIO()
        call_command(
            "finance_write_authority",
            source=str(source or self.source),
            stdout=output,
            **options,
        )
        return json.loads(output.getvalue().strip().splitlines()[-1])

    def test_dry_run_apply_verify_preserve_exact_snapshot(self) -> None:
        dry_run = self.run_command()
        self.assertEqual(dry_run["mode"], "dry-run")
        self.assertEqual(dry_run["counts"]["lines"], 2)
        self.assertEqual(FinanceLine.objects.count(), 0)

        applied = self.run_command(
            **{"apply": True}, approved_run_id=dry_run["runId"]
        )
        self.assertEqual(applied["mode"], "apply")
        self.assertEqual(FinanceImportBatch.objects.count(), 1)
        self.assertEqual(FinanceMonth.objects.count(), 1)
        self.assertEqual(FinanceLine.objects.count(), 2)
        self.assertEqual(FinanceImportFingerprint.objects.count(), 1)
        self.assertEqual(
            FinanceDataRevision.objects.get(domain="finance").source_digest,
            applied["targetProjectionDigest"],
        )

        verified = self.run_command(
            verify_only=True, approved_run_id=applied["runId"]
        )
        self.assertEqual(verified["mode"], "verify")
        self.assertEqual(verified["targetProjectionDigest"], applied["targetProjectionDigest"])
        self.assertEqual(
            list(FinanceMigrationRun.objects.order_by("created_at").values_list("mode", flat=True)),
            ["dry-run", "apply", "verify"],
        )

    def test_apply_rejects_source_changed_after_approval_without_target_write(self) -> None:
        dry_run = self.run_command()
        connection = sqlite3.connect(self.source)
        try:
            connection.execute("UPDATE finance_lines SET raw_value='changed' WHERE id=1")
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesMessage(CommandError, "已审批运行不一致"):
            self.run_command(**{"apply": True}, approved_run_id=dry_run["runId"])
        self.assertEqual(FinanceLine.objects.count(), 0)

    @override_settings(
        DJANGO_ENVIRONMENT="production", DJANGO_PROCESS_ROLE="migration_writer"
    )
    def test_production_migration_requires_controlled_source_manifest(self) -> None:
        with self.assertRaisesMessage(CommandError, "必须提供受控源清单"):
            self.run_command()

    def test_pre_fingerprint_batch_gets_deterministic_migration_audit(self) -> None:
        connection = sqlite3.connect(self.source)
        try:
            connection.execute(
                "DELETE FROM import_content_fingerprints WHERE domain='finance'"
            )
            connection.execute(
                "DELETE FROM import_content_attempts WHERE domain='finance'"
            )
            connection.commit()
        finally:
            connection.close()

        dry_run = self.run_command()
        self.assertEqual(dry_run["counts"]["fingerprints"], 1)
        self.assertEqual(dry_run["counts"]["attempts"], 1)
        applied = self.run_command(
            **{"apply": True}, approved_run_id=dry_run["runId"]
        )
        fingerprint = FinanceImportFingerprint.objects.get()
        self.assertEqual(fingerprint.batch_id, "a" * 64)
        self.assertEqual(len(fingerprint.content_hash), 64)
        attempt = FinanceImportAttempt.objects.get()
        self.assertEqual(
            attempt.metadata["migrationSynthesisVersion"],
            "finance-legacy-audit-synthesis-v1",
        )
        verified = self.run_command(
            verify_only=True, approved_run_id=applied["runId"]
        )
        self.assertEqual(verified["targetProjectionDigest"], applied["targetProjectionDigest"])

    def test_divergent_legacy_fingerprint_is_rebuilt_with_explicit_audit(self) -> None:
        stale_content_hash = "e" * 64
        connection = sqlite3.connect(self.source)
        try:
            connection.execute(
                "UPDATE import_content_fingerprints SET content_hash=? "
                "WHERE domain='finance'",
                (stale_content_hash,),
            )
            connection.commit()
        finally:
            connection.close()

        dry_run = self.run_command()
        self.assertEqual(dry_run["counts"]["fingerprints"], 1)
        self.assertEqual(dry_run["counts"]["attempts"], 2)
        self.run_command(**{"apply": True}, approved_run_id=dry_run["runId"])

        fingerprint = FinanceImportFingerprint.objects.get()
        self.assertNotEqual(fingerprint.content_hash, stale_content_hash)
        migration_attempt = FinanceImportAttempt.objects.get(
            metadata__migrationReason="source_fingerprint_diverged_from_current_facts"
        )
        self.assertEqual(
            migration_attempt.metadata["migrationSynthesisVersion"],
            "finance-legacy-audit-synthesis-v1",
        )
        self.assertEqual(
            migration_attempt.metadata["sourceContentHash"], stale_content_hash
        )
        self.assertEqual(FinanceImportAttempt.objects.count(), 2)

    def test_authority_prepare_abort_and_activate_are_single_writer_transitions(self) -> None:
        live_source = Path(self.temporary.name) / "live.sqlite"
        shutil.copy2(self.source, live_source)
        source_manifest = Path(self.temporary.name) / "finance-source-manifest.json"
        write_source_manifest(self.source, live_source, source_manifest)
        migration_options = {"source_manifest": str(source_manifest)}
        dry_run = self.run_command(**migration_options)
        applied = self.run_command(
            **{"apply": True},
            approved_run_id=dry_run["runId"],
            **migration_options,
        )
        verified = self.run_command(
            verify_only=True,
            approved_run_id=applied["runId"],
            **migration_options,
        )
        cutover_id = "finance-20260830-a"

        status = self.run_authority_command(source=live_source)
        self.assertEqual(status["d1"]["owner"], "d1")
        self.assertEqual(status["postgresql"]["status"], "d1")

        prepared = self.run_authority_command(
            source=live_source,
            prepare=True,
            verify_run_id=verified["runId"],
            cutover_id=cutover_id,
        )
        self.assertEqual(prepared["status"], "prepared")
        with closing(sqlite3.connect(live_source)) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT owner FROM finance_write_authority WHERE id=1"
                ).fetchone()[0],
                "pending",
            )
            with self.assertRaisesRegex(sqlite3.IntegrityError, "authority_not_d1"):
                connection.execute(
                    "UPDATE finance_lines SET raw_value='blocked' WHERE id=1"
                )

        aborted = self.run_authority_command(
            source=live_source,
            abort_pending=True,
            verify_run_id=verified["runId"],
            cutover_id=cutover_id,
        )
        self.assertEqual(aborted["status"], "aborted")
        with closing(sqlite3.connect(live_source)) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT owner FROM finance_write_authority WHERE id=1"
                ).fetchone()[0],
                "d1",
            )

        self.run_authority_command(
            source=live_source,
            prepare=True,
            verify_run_id=verified["runId"],
            cutover_id=cutover_id,
        )
        activated = self.run_authority_command(
            source=live_source,
            activate=True,
            verify_run_id=verified["runId"],
            cutover_id=cutover_id,
        )
        self.assertEqual(activated["status"], "activated")
        target = FinanceWriteAuthority.objects.get(id=1)
        self.assertEqual(target.status, "postgres")
        self.assertEqual(target.cutover_id, cutover_id)
        self.assertEqual(target.migration_verify_run_id, verified["runId"])
        self.assertEqual(str(target.authority_epoch), activated["authorityEpoch"])
        with closing(sqlite3.connect(live_source)) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT owner FROM finance_write_authority WHERE id=1"
                ).fetchone()[0],
                "postgresql",
            )
            with self.assertRaisesRegex(sqlite3.IntegrityError, "authority_not_d1"):
                connection.execute(
                    "DELETE FROM finance_lines WHERE id=1"
                )


class FinanceMigrationRevisionGuardTests(TransactionTestCase):
    """Real commits under finance.0003, including a second approved apply."""

    def setUp(self) -> None:
        if target_connection.vendor != "postgresql":
            self.skipTest("requires PostgreSQL finance.0003 deferred guard")
        FinanceWriteAuthority.objects.update_or_create(id=1,
            defaults={"status": "d1"})
        FinanceDataRevision.objects.get_or_create(domain="finance",
            defaults={"revision": 0, "source_digest": "0" * 64})
        self.temporary = tempfile.TemporaryDirectory()
        self.source = Path(self.temporary.name) / "source.sqlite"
        create_source(self.source)

    def tearDown(self) -> None:
        if hasattr(self, "temporary"):
            self.temporary.cleanup()

    def run_command(self, **options):
        output = io.StringIO()
        call_command("migrate_finance_from_d1", source=str(self.source),
            stdout=output, **options)
        return json.loads(output.getvalue().strip().splitlines()[-1])

    def target(self):
        return migration_command._target_snapshot()

    def marker_count(self):
        with target_connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM finance_source_revision_markers")
            return cursor.fetchone()[0]

    def first_line_xmin(self):
        with target_connection.cursor() as cursor:
            cursor.execute("SELECT xmin::text FROM finance_lines ORDER BY id LIMIT 1")
            return cursor.fetchone()[0]

    def change_source(self):
        with closing(sqlite3.connect(self.source)) as source:
            source.execute("UPDATE finance_lines SET raw_value='changed' WHERE id=1")
            source.commit()

    def test_first_identical_second_and_changed_second_keep_monotonic_revision(self):
        dry = self.run_command()
        first = self.run_command(apply=True, approved_run_id=dry["runId"])
        initial = FinanceDataRevision.objects.get(domain="finance")
        self.assertEqual(initial.revision, 1)
        self.assertEqual(initial.source_digest, first["targetProjectionDigest"])
        first_target, first_xmin = self.target(), self.first_line_xmin()
        self.assertEqual(self.marker_count(), 0)

        same = self.run_command(apply=True, approved_run_id=dry["runId"])
        unchanged = FinanceDataRevision.objects.get(domain="finance")
        self.assertEqual(unchanged.revision, 1)
        self.assertEqual(unchanged.source_digest, initial.source_digest)
        self.assertEqual(self.target(), first_target)
        self.assertEqual(self.first_line_xmin(), first_xmin)
        self.assertEqual(same["targetProjectionDigest"], first["targetProjectionDigest"])
        self.assertEqual(self.marker_count(), 0)

        self.change_source()
        new_dry = self.run_command()
        changed = self.run_command(apply=True, approved_run_id=new_dry["runId"])
        revised = FinanceDataRevision.objects.get(domain="finance")
        self.assertEqual(revised.revision, 2)
        self.assertEqual(revised.source_digest, changed["targetProjectionDigest"])
        self.assertNotEqual(changed["targetProjectionDigest"], first["targetProjectionDigest"])
        self.assertEqual(self.marker_count(), 0)

    def test_identical_target_rejects_late_source_artifact_change(self):
        dry = self.run_command()
        self.run_command(apply=True, approved_run_id=dry["runId"])
        approved = self.run_command()
        baseline = self.target(), FinanceDataRevision.objects.get(domain="finance").revision
        baseline_runs = FinanceMigrationRun.objects.count()
        original_digest = migration_command._file_digest
        calls = 0

        def late_change(path):
            nonlocal calls
            calls += 1
            return original_digest(path) if calls == 1 else "f" * 64

        with patch.object(migration_command, "_file_digest", side_effect=late_change):
            with self.assertRaisesMessage(CommandError, "提交前发生变化"):
                self.run_command(apply=True, approved_run_id=approved["runId"])
        self.assertEqual(FinanceMigrationRun.objects.count(), baseline_runs)
        self.assertEqual((self.target(), FinanceDataRevision.objects.get(
            domain="finance").revision), baseline)
        self.assertEqual(self.marker_count(), 0)

    def test_changed_snapshot_failure_rolls_back_fact_and_revision(self):
        dry = self.run_command()
        self.run_command(apply=True, approved_run_id=dry["runId"])
        before = self.target(), FinanceDataRevision.objects.get(domain="finance").revision
        before_xmin = self.first_line_xmin()
        self.change_source()
        approved = self.run_command()
        original_bulk = migration_command._bulk

        def fail_lines(model, records, generation, size):
            if model is FinanceLine:
                raise RuntimeError("synthetic rollback after source deletion")
            return original_bulk(model, records, generation, size)

        with patch.object(migration_command, "_bulk", side_effect=fail_lines):
            with self.assertRaisesRegex(RuntimeError, "synthetic rollback"):
                self.run_command(apply=True, approved_run_id=approved["runId"])
        self.assertEqual((self.target(), FinanceDataRevision.objects.get(
            domain="finance").revision), before)
        self.assertEqual(self.first_line_xmin(), before_xmin)
        self.assertEqual(self.marker_count(), 0)
