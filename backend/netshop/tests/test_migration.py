from __future__ import annotations

import io
import json
import sqlite3
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from netshop.management.commands.migrate_netshop_from_d1 import _snapshot
from netshop.models import (
    NetshopImportBatch,
    NetshopDataRevision,
    NetshopImportFingerprint,
    NetshopMigrationRun,
    NetshopRow,
    NetshopWriteAuthority,
)


DDL = """
CREATE TABLE netshop_import_batches (
 id TEXT PRIMARY KEY,source TEXT,dataset TEXT,platform TEXT,shop_name TEXT,file_name TEXT,
 file_size_bytes INTEGER,file_hash TEXT,sheet_name TEXT,status TEXT,row_count INTEGER,
 inserted_count INTEGER,duplicate_count INTEGER,warning_count INTEGER,date_min TEXT,date_max TEXT,
 snapshot_date TEXT,warnings_json TEXT,totals_json TEXT,note TEXT,created_at TEXT,completed_at TEXT
);
CREATE TABLE netshop_rows (
 id INTEGER PRIMARY KEY,source_row_key TEXT,source_row_hash TEXT,first_import_batch_id TEXT,
 last_import_batch_id TEXT,source_row_number INTEGER,source TEXT,dataset TEXT,platform TEXT,
 shop_name TEXT,business_date TEXT,snapshot_date TEXT,product_code TEXT,product_name TEXT,
 sku_id TEXT,spu_id TEXT,warehouse_type TEXT,metrics_json TEXT,raw_json TEXT,created_at TEXT,updated_at TEXT
);
CREATE TABLE netshop_product_daily_revisions (platform TEXT PRIMARY KEY,data_version INTEGER,updated_at TEXT);
CREATE TABLE netshop_product_daily_scope_revisions (platform TEXT,shop_name TEXT,data_version INTEGER,updated_at TEXT);
CREATE TABLE netshop_promotion_product_daily (
 platform TEXT,shop_name TEXT,business_date TEXT,product_id TEXT,source TEXT,product_name TEXT,
 product_line TEXT,spend_cents INTEGER,net_transaction_amount_cents INTEGER,
 gross_transaction_amount_cents INTEGER,impressions INTEGER,clicks INTEGER,net_orders INTEGER,
 favorites INTEGER,cart_quantity INTEGER,source_row_count INTEGER,source_batch_id TEXT,
 source_batch_count INTEGER,rebuilt_at TEXT
);
CREATE TABLE netshop_promotion_shop_daily (
 platform TEXT,shop_name TEXT,business_date TEXT,source TEXT,product_count INTEGER,spend_cents INTEGER,
 net_transaction_amount_cents INTEGER,gross_transaction_amount_cents INTEGER,impressions INTEGER,
 clicks INTEGER,net_orders INTEGER,favorites INTEGER,cart_quantity INTEGER,source_row_count INTEGER,
 source_batch_id TEXT,source_batch_count INTEGER,rebuilt_at TEXT
);
CREATE TABLE netshop_promotion_aggregate_state (
 platform TEXT,shop_name TEXT,business_date TEXT,source TEXT,ready INTEGER,raw_row_count INTEGER,
 product_row_count INTEGER,source_batch_id TEXT,source_batch_count INTEGER,rebuilt_at TEXT,invalidated_at TEXT
);
CREATE TABLE netshop_promotion_aggregate_manifest (
 platform TEXT,ready INTEGER,historical_data_cutoff TEXT,source_shop_count INTEGER,raw_row_count INTEGER,
 product_row_count INTEGER,shop_day_count INTEGER,state_day_count INTEGER,completed_at TEXT,
 invalidated_at TEXT,data_version INTEGER
);
CREATE TABLE netshop_promotion_aggregate_control (
 platform TEXT,bootstrap_batch_id TEXT,bootstrap_raw_row_count INTEGER,bootstrap_product_row_count INTEGER,
 bootstrap_shop_day_count INTEGER,bootstrap_data_cutoff TEXT,maintenance_token TEXT,
 maintenance_version INTEGER,maintenance_previous_ready INTEGER,maintenance_started_at TEXT,updated_at TEXT
);
CREATE TABLE netshop_promotion_scope_revisions (platform TEXT,shop_name TEXT,data_version INTEGER,updated_at TEXT);
CREATE TABLE netshop_asset_uploads (
 id TEXT,fingerprint TEXT,shop_name TEXT,snapshot_date TEXT,file_name TEXT,file_size_bytes INTEGER,
 chunk_size_bytes INTEGER,chunk_count INTEGER,received_chunk_count INTEGER,received_bytes INTEGER,
 status TEXT,processing_owner TEXT,expires_at TEXT,created_at TEXT,updated_at TEXT
);
CREATE TABLE netshop_asset_upload_chunks (
 upload_id TEXT,chunk_index INTEGER,object_key TEXT,size_bytes INTEGER,sha256 TEXT,created_at TEXT
);
CREATE TABLE netshop_asset_upload_results (upload_id TEXT,result_json TEXT,created_at TEXT);
CREATE TABLE import_content_fingerprints (
 sequence INTEGER PRIMARY KEY,domain TEXT,batch_id TEXT,scope_key TEXT,scope_json TEXT,
 import_hash TEXT,raw_file_hash TEXT,content_hash TEXT,row_count INTEGER,status TEXT,
 publication_sequence INTEGER,created_at TEXT
);
CREATE TABLE import_content_attempts (
 sequence INTEGER PRIMARY KEY,attempt_id TEXT,domain TEXT,batch_id TEXT,scope_key TEXT,scope_json TEXT,
 import_hash TEXT,raw_file_hash TEXT,content_hash TEXT,row_count INTEGER,file_name TEXT,
 file_size_bytes INTEGER,actor TEXT,warnings_json TEXT,outcome TEXT,error_code TEXT,
 recovered_from_attempt_id TEXT,created_at TEXT,updated_at TEXT
);
CREATE TABLE import_scope_heads (
 domain TEXT,scope_key TEXT,state_token TEXT,status TEXT,owner_token TEXT,current_batch_id TEXT,
 generation INTEGER,updated_at TEXT
);
CREATE TABLE netshop_write_authority (
 id INTEGER PRIMARY KEY,owner TEXT,epoch INTEGER,cutover_id TEXT,updated_at TEXT
);
"""


def create_source(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(DDL)
    batch_id = "jd_sku_daily:%E4%BA%AC%E4%B8%9C:%E4%BA%AC%E4%B8%9C%E4%B8%80%E5%BA%97:" + "1" * 64
    scope_key = "2" * 64
    state_token = "3" * 64
    content_hash = "4" * 64
    raw_hash = "5" * 64
    import_hash = "1" * 64
    scope = {"source": "jd_sku_daily", "dataset": "sku_daily", "platform": "京东", "shopName": "京东一店", "snapshotDate": None, "startDate": "2026-08-30", "endDate": "2026-08-30"}
    connection.execute(
        "INSERT INTO netshop_write_authority VALUES (1,'pending',2,'netshop-test-cutover','2026-08-31T00:00:00Z')"
    )
    connection.execute(
        "INSERT INTO netshop_import_batches VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (batch_id, "jd_sku_daily", "sku_daily", "京东", "京东一店", "daily.xlsx", 100,
         import_hash, "明细", "completed", 1, 1, 0, 0, "2026-08-30", "2026-08-30", None,
         "[]", json.dumps({"contentHash": content_hash}, ensure_ascii=False), "", "2026-08-31T00:00:00Z", "2026-08-31T00:01:00Z"),
    )
    connection.execute(
        "INSERT INTO netshop_rows VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (1, "row-1", "6" * 64, batch_id, batch_id, 2, "jd_sku_daily", "sku_daily", "京东",
         "京东一店", "2026-08-30", None, "P-1", "饮水机", "SKU-1", "SPU-1", "",
         json.dumps({"transactionAmountCents": 12345}, ensure_ascii=False),
         json.dumps({"品牌": "志高"}, ensure_ascii=False), "2026-08-31T00:00:00Z", "2026-08-31T00:00:00Z"),
    )
    connection.execute("INSERT INTO netshop_product_daily_revisions VALUES ('京东',1,'2026-08-31T00:00:00Z')")
    connection.execute("INSERT INTO netshop_product_daily_scope_revisions VALUES ('京东','京东一店',1,'2026-08-31T00:00:00Z')")
    connection.execute("INSERT INTO netshop_promotion_scope_revisions VALUES ('京东','京东一店',0,'2026-08-31T00:00:00Z')")
    connection.execute(
        "INSERT INTO import_content_fingerprints VALUES (1,'netshop',?,?,?,?,?,?,?,?,?,?)",
        (batch_id, scope_key, json.dumps(scope, ensure_ascii=False, separators=(",", ":")), import_hash,
         raw_hash, content_hash, 1, "completed", 1, "2026-08-31T00:01:00Z"),
    )
    attempt_id = "11111111-2222-4333-8444-555555555555"
    connection.execute(
        "INSERT INTO import_content_attempts (attempt_id,domain,batch_id,scope_key,scope_json,import_hash,"
        "raw_file_hash,content_hash,row_count,file_name,file_size_bytes,actor,warnings_json,outcome,error_code,"
        "recovered_from_attempt_id,created_at,updated_at) VALUES (?,'netshop',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (attempt_id, batch_id, scope_key, json.dumps(scope, ensure_ascii=False, separators=(",", ":")),
         import_hash, raw_hash, content_hash, 1, "daily.xlsx", 100, "admin@example.test", "[]",
         "imported", "", "", "2026-08-31T00:00:00Z", "2026-08-31T00:01:00Z"),
    )
    connection.execute(
        "INSERT INTO import_scope_heads VALUES ('netshop',?,?, 'ready','',?,1,'2026-08-31T00:01:00Z')",
        (scope_key, state_token, batch_id),
    )
    connection.commit()
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()


class NetshopMigrationCommandTests(TestCase):
    def test_snapshot_digest_is_order_independent_and_preserves_multiplicity(self) -> None:
        forward = {
            "records": [
                {"key": "京东", "value": 1},
                {"key": "天猫", "value": 2},
                {"key": "京东", "value": 1},
            ]
        }
        reverse = {"records": list(reversed(forward["records"]))}
        counts, digests, combined = _snapshot(forward)
        self.assertEqual(_snapshot(reverse), (counts, digests, combined))
        self.assertNotEqual(
            _snapshot({"records": forward["records"][:2]})[2],
            combined,
        )

    def test_plan_apply_and_verify_are_bound_to_one_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "netshop.sqlite3"
            create_source(source)
            planned = io.StringIO()
            call_command("migrate_netshop_from_d1", source=str(source), stdout=planned)
            plan = json.loads(planned.getvalue())
            self.assertEqual(plan["counts"]["rows"], 1)
            self.assertTrue(plan["runId"].startswith("netshop-"))

            applied = io.StringIO()
            call_command(
                "migrate_netshop_from_d1", source=str(source), apply=True,
                approved_run_id=plan["runId"], stdout=applied,
            )
            result = json.loads(applied.getvalue())
            self.assertEqual(result["status"], "applied")
            self.assertEqual(NetshopRow.objects.get().transaction_amount_cents, 12_345)
            self.assertEqual(NetshopImportBatch.objects.count(), 1)
            migration_run = NetshopMigrationRun.objects.get()
            self.assertEqual(migration_run.source_snapshot_digest, plan["sourceDigest"])
            self.assertIsNotNone(migration_run.completed_at)
            self.assertGreaterEqual(migration_run.completed_at, migration_run.created_at)

            verified = io.StringIO()
            call_command(
                "migrate_netshop_from_d1", source=str(source), verify_only=True,
                approved_run_id=plan["runId"], stdout=verified,
            )
            self.assertEqual(json.loads(verified.getvalue())["status"], "verified")

            NetshopRow.objects.update(transaction_amount_cents=12_346)
            with self.assertRaisesMessage(CommandError, "回查不一致"):
                call_command(
                    "migrate_netshop_from_d1",
                    source=str(source),
                    verify_only=True,
                    approved_run_id=plan["runId"],
                    stdout=io.StringIO(),
                )

    def test_first_import_batch_ownership_must_be_complete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "netshop.sqlite3"
            create_source(source)
            connection = sqlite3.connect(source)
            connection.execute(
                "UPDATE netshop_rows SET first_import_batch_id='missing-batch'"
            )
            connection.commit()
            connection.close()
            with self.assertRaisesMessage(CommandError, "首次导入批次"):
                call_command(
                    "migrate_netshop_from_d1", source=str(source), stdout=io.StringIO()
                )

    def test_all_source_fingerprint_statuses_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "netshop.sqlite3"
            create_source(source)
            connection = sqlite3.connect(source)
            connection.execute(
                "UPDATE import_content_fingerprints SET status='legacy_published'"
            )
            connection.commit()
            connection.close()
            planned = io.StringIO()
            call_command("migrate_netshop_from_d1", source=str(source), stdout=planned)
            plan = json.loads(planned.getvalue())
            call_command(
                "migrate_netshop_from_d1",
                source=str(source),
                apply=True,
                approved_run_id=plan["runId"],
                stdout=io.StringIO(),
            )
            self.assertEqual(
                NetshopImportFingerprint.objects.get().status, "legacy_published"
            )

    def test_authority_prepare_and_abort_are_plan_bound_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "netshop.sqlite3"
            create_source(source)
            connection = sqlite3.connect(source)
            connection.execute(
                "UPDATE netshop_write_authority SET owner='d1',epoch=1,cutover_id=''"
            )
            connection.commit()
            connection.close()
            planned = io.StringIO()
            call_command("migrate_netshop_from_d1", source=str(source), stdout=planned)
            run_id = json.loads(planned.getvalue())["runId"]
            cutover_id = "netshop-test-authority"

            for _retry in range(2):
                call_command(
                    "netshop_write_authority",
                    source=str(source),
                    prepare=True,
                    approved_run_id=run_id,
                    cutover_id=cutover_id,
                    stdout=io.StringIO(),
                )
            connection = sqlite3.connect(source)
            self.assertEqual(
                connection.execute(
                    "SELECT owner FROM netshop_write_authority WHERE id=1"
                ).fetchone()[0],
                "pending",
            )
            connection.close()

            for _retry in range(2):
                call_command(
                    "netshop_write_authority",
                    source=str(source),
                    abort_pending=True,
                    approved_run_id=run_id,
                    cutover_id=cutover_id,
                    stdout=io.StringIO(),
                )
            connection = sqlite3.connect(source)
            self.assertEqual(
                connection.execute(
                    "SELECT owner FROM netshop_write_authority WHERE id=1"
                ).fetchone()[0],
                "d1",
            )
            connection.close()

    def test_authority_activation_requires_completed_exact_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "netshop.sqlite3"
            create_source(source)
            planned = io.StringIO()
            call_command("migrate_netshop_from_d1", source=str(source), stdout=planned)
            plan = json.loads(planned.getvalue())
            options = {
                "source": str(source),
                "activate": True,
                "approved_run_id": plan["runId"],
                "cutover_id": "netshop-test-cutover",
                "stdout": io.StringIO(),
            }
            with self.assertRaisesMessage(CommandError, "完成网店迁移"):
                call_command("netshop_write_authority", **options)

            call_command(
                "migrate_netshop_from_d1",
                source=str(source),
                apply=True,
                approved_run_id=plan["runId"],
                stdout=io.StringIO(),
            )
            activated = io.StringIO()
            call_command(
                "netshop_write_authority",
                **{**options, "stdout": activated},
            )
            result = json.loads(activated.getvalue())
            self.assertEqual(result["status"], "activated")
            target = NetshopWriteAuthority.objects.get(id=1)
            self.assertEqual(target.status, "postgres")
            self.assertEqual(target.migration_verify_run_id, plan["runId"])
            self.assertIsNotNone(target.authority_epoch)
            connection = sqlite3.connect(source)
            self.assertEqual(
                connection.execute(
                    "SELECT owner FROM netshop_write_authority WHERE id=1"
                ).fetchone()[0],
                "postgresql",
            )
            connection.close()

            replay = io.StringIO()
            call_command(
                "netshop_write_authority", **{**options, "stdout": replay}
            )
            self.assertEqual(json.loads(replay.getvalue())["status"], "activated")

    def test_terminal_retirement_is_plan_bound_and_preserves_market_and_other_domains(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "netshop.sqlite3"
            smoke_path = root / "system-test-receipt.json"
            create_source(source)
            planned = io.StringIO()
            call_command("migrate_netshop_from_d1", source=str(source), stdout=planned)
            migration_plan = json.loads(planned.getvalue())
            run_id = migration_plan["runId"]
            cutover_id = "netshop-test-cutover"
            call_command(
                "migrate_netshop_from_d1",
                source=str(source),
                apply=True,
                approved_run_id=run_id,
                stdout=io.StringIO(),
            )
            call_command(
                "netshop_write_authority",
                source=str(source),
                activate=True,
                approved_run_id=run_id,
                cutover_id=cutover_id,
                stdout=io.StringIO(),
            )
            revision = NetshopDataRevision.objects.get(domain="netshop")
            projection_revision = f"{revision.revision}:{revision.source_digest[:12]}"
            connection = sqlite3.connect(source)
            connection.executescript(
                """
                CREATE TABLE market_netshop_projection_control (
                  id INTEGER PRIMARY KEY,active_revision TEXT,active_total INTEGER,
                  syncing_revision TEXT,owner_token TEXT,lease_expires_at TEXT,updated_at TEXT
                );
                CREATE TABLE market_netshop_projection (
                  projection_revision TEXT,projection_key TEXT,kind TEXT,source TEXT,dataset TEXT,
                  platform TEXT,shop_name TEXT,business_date TEXT,sku_id TEXT,spu_id TEXT,
                  product_code TEXT,transaction_amount_cents INTEGER,brand TEXT,created_at TEXT,
                  PRIMARY KEY(projection_revision,projection_key)
                );
                """
            )
            connection.execute(
                "INSERT INTO market_netshop_projection_control VALUES (1,?,1,'','',NULL,CURRENT_TIMESTAMP)",
                (projection_revision,),
            )
            connection.execute(
                "INSERT INTO market_netshop_projection VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
                (
                    projection_revision,
                    "metric:row-1",
                    "metric",
                    "jd_sku_daily",
                    "sku_daily",
                    "京东",
                    "京东一店",
                    "2026-08-30",
                    "SKU-1",
                    "SPU-1",
                    "P-1",
                    12_345,
                    "",
                ),
            )
            connection.execute(
                "INSERT INTO import_content_fingerprints VALUES "
                "(2,'inventory','inventory-batch','scope-other','{}',?,? ,?,0,'completed',2,CURRENT_TIMESTAMP)",
                ("6" * 64, "7" * 64, "8" * 64),
            )
            connection.execute(
                "INSERT INTO import_content_attempts (sequence,attempt_id,domain,batch_id,scope_key,"
                "scope_json,import_hash,raw_file_hash,content_hash,row_count,file_name,file_size_bytes,"
                "actor,warnings_json,outcome,error_code,recovered_from_attempt_id,created_at,updated_at) "
                "VALUES (2,'other-attempt','inventory','','scope-other','{}',?,?,?,0,'',0,'','[]',"
                "'imported','','',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("6" * 64, "7" * 64, "8" * 64),
            )
            connection.execute(
                "INSERT INTO import_scope_heads VALUES "
                "('inventory','scope-other','state-other','ready','','',1,CURRENT_TIMESTAMP)"
            )
            connection.commit()
            connection.close()

            smoke_path.write_text(
                json.dumps(
                    {
                        "version": "netshop-system-test-receipt-v1",
                        "status": "passed",
                        "cutoverId": cutover_id,
                        "migrationRunId": run_id,
                        "sourceDigest": migration_plan["sourceDigest"],
                        "targetDigest": migration_plan["sourceDigest"],
                        "workerBuildSha256": "9" * 64,
                        "checks": {
                            "djangoReader": "passed",
                            "djangoWriterNegative": "passed",
                            "publicOverview": "passed",
                            "publicProducts": "passed",
                            "publicPromotion": "passed",
                            "marketProjection": "passed",
                            "customerServiceProjection": "passed",
                            "globalSearchProjection": "passed",
                            "legacyD1Rejected": "passed",
                        },
                        "recordedAt": timezone.now().isoformat(),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            retirement_plan_output = io.StringIO()
            common = {
                "source": str(source),
                "cutover_id": cutover_id,
                "approved_run_id": run_id,
                "smoke_receipt": str(smoke_path),
            }
            call_command(
                "retire_netshop_d1", **common, stdout=retirement_plan_output
            )
            retirement_plan = json.loads(retirement_plan_output.getvalue())
            self.assertEqual(retirement_plan["status"], "planned")
            self.assertEqual(retirement_plan["market"]["total"], 1)

            retired_output = io.StringIO()
            call_command(
                "retire_netshop_d1",
                **common,
                apply=True,
                approved_plan_id=retirement_plan["planId"],
                stdout=retired_output,
            )
            retired = json.loads(retired_output.getvalue())
            self.assertEqual(retired["status"], "retired")
            connection = sqlite3.connect(source)
            self.assertEqual(
                connection.execute(
                    "SELECT status FROM domain_retirement_receipts WHERE domain='netshop'"
                ).fetchone()[0],
                "completed",
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM import_content_fingerprints WHERE domain='inventory'"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM market_netshop_projection").fetchone()[0],
                1,
            )
            with self.assertRaisesRegex(sqlite3.DatabaseError, "netshop_domain_retired"):
                connection.execute(
                    "INSERT INTO import_scope_heads VALUES "
                    "('netshop','reanimate','state','ready','','',1,CURRENT_TIMESTAMP)"
                )
            connection.close()

            duplicate = io.StringIO()
            call_command(
                "retire_netshop_d1",
                **common,
                apply=True,
                approved_plan_id=retirement_plan["planId"],
                stdout=duplicate,
            )
            self.assertEqual(json.loads(duplicate.getvalue())["status"], "duplicate")
