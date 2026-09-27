"""Rehearse Guangdong changes in a fresh, private PostgreSQL 17 cluster.

Uses synthetic fixtures only. Never connects to the production database or calls
production controllers; PostgreSQL binaries are a read-only input.
"""
from __future__ import annotations

import hashlib
from datetime import timedelta
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
PORT = 55458
TABLES = ("inventory_guangdong_monitor_items", "inventory_guangdong_supplier_cycles", "inventory_guangdong_monitor_audits")


def main():
    if ROOT == Path(r"D:\运营管理系统"):
        raise RuntimeError("Independent worktree required")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", PORT))
    run = ROOT / ".runtime" / ("guangdong-pg-" + uuid.uuid4().hex[:12])
    run.mkdir(parents=True)
    for ancestor in [run, *run.parents]:
        if ancestor.is_symlink() or ancestor.is_junction(): raise RuntimeError("Linked mirror path rejected")
    password = secrets.token_hex(24)
    password_file = run / "init-password"
    password_file.write_text(password, encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if not key.startswith(("TERUISI_", "PG", "DJANGO_"))}
    env.update(PGHOST="127.0.0.1", PGPORT=str(PORT), PGUSER="gd_owner", PGPASSWORD=password,
               TERUISI_DJANGO_DATABASE_URL=f"postgresql://gd_owner:{password}@127.0.0.1:{PORT}/gd_mirror",
               TERUISI_DJANGO_ENVIRONMENT="test", TERUISI_DJANGO_PROCESS_ROLE="development", DJANGO_DEBUG="true", PYTHONUTF8="1")
    def command(args, name, timeout=240):
        with (run / (name + ".log")).open("w", encoding="utf-8") as log:
            result = subprocess.run([str(arg) for arg in args], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise RuntimeError(f"{name} failed; see {run / (name + '.log')}")
        print(json.dumps({"stage": name, "status": "passed"}), flush=True)
    started = False
    try:
        command([BIN / "initdb.exe", "-D", run / "data", "-U", "gd_owner", "--pwfile", password_file, "--auth=scram-sha-256", "--encoding=UTF8", "--no-locale"], "initdb")
        password_file.unlink()
        command([BIN / "pg_ctl.exe", "-D", run / "data", "-l", run / "postgres.log", "-o", f"-p {PORT} -h 127.0.0.1", "-w", "-t", "45", "start"], "start")
        started = True
        command([BIN / "createdb.exe", "gd_mirror"], "create")
        manage = [sys.executable, ROOT / "backend/manage.py"]
        command(manage + ["migrate", "--plan"], "migration-plan")
        command(manage + ["migrate", "--noinput"], "migration-apply")
        command(manage + ["makemigrations", "--check", "--dry-run"], "migration-drift")
        command(manage + ["test", "inventory.tests", "sales.tests.test_consumers_api", "workflow.tests.test_import_chain_status", "--noinput"], "postgres-tests")

        # The only imported settings belong to this fresh private cluster.
        os.environ.update(env)
        os.environ["DJANGO_SETTINGS_MODULE"] = "teruisi_backend.settings"
        sys.path.insert(0, str(ROOT / "backend"))
        import django
        django.setup()
        import psycopg
        from psycopg import sql
        from django.db import connection
        from django.utils import timezone
        from inventory.models import InventoryWriteAuthority
        from inventory import guangdong as gd
        from sales.models import ErpProductMaster
        from teruisi_backend.health import INVENTORY_WRITER_TABLE_PRIVILEGES, REQUIRED_INVENTORY_COLUMNS, INVENTORY_WRITER_AUTO_ID_TABLES, _validate_inventory_schema, _validate_inventory_writer_permissions

        InventoryWriteAuthority.objects.filter(id=1).update(status="postgres", authority_epoch=uuid.uuid4(), cutover_id="gd-mirror", migration_verify_run_id="gd-mirror", activated_at=timezone.now())
        ErpProductMaster.objects.create(product_code="00123", product_name="镜像测试风扇", supplier="镜像供应商", source_row_number=1, last_import_batch_id="mirror", created_at="", updated_at="")
        with connection.cursor() as cursor:
            for role in ("gd_reader", "gd_writer", "gd_bi"):
                cursor.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE").format(sql.Identifier(role)))
                cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
            for table in REQUIRED_INVENTORY_COLUMNS:
                cursor.execute(sql.SQL("GRANT SELECT ON {} TO gd_reader").format(sql.Identifier(table)))
                if table != "inventory_guangdong_monitor_audits":
                    cursor.execute(sql.SQL("GRANT SELECT ON {} TO gd_bi").format(sql.Identifier(table)))
            # Reader dependencies are taken from the writer's SELECT-only contracts.
            for table, privileges in INVENTORY_WRITER_TABLE_PRIVILEGES.items():
                cursor.execute(sql.SQL("GRANT {} ON {} TO gd_writer").format(sql.SQL(", ".join(privileges)), sql.Identifier(table)))
                if privileges == ("SELECT",): cursor.execute(sql.SQL("GRANT SELECT ON {} TO gd_reader").format(sql.Identifier(table)))
            for table in INVENTORY_WRITER_AUTO_ID_TABLES:
                cursor.execute("SELECT pg_get_serial_sequence(%s, 'id')", [table])
                sequence = cursor.fetchone()[0]
                if sequence:
                    cursor.execute(sql.SQL("GRANT USAGE, SELECT ON SEQUENCE {} TO gd_writer").format(sql.Identifier(*sequence.split("."))))
            cursor.execute("SET ROLE gd_writer")
            _validate_inventory_schema(cursor, writer=True)
            _validate_inventory_writer_permissions(cursor)
        initial = gd.preview([{"productCode": "00123", "notes": "私有镜像"}])
        gd.mutate({"action": "import", "rows": [{"productCode": "00123", "notes": "私有镜像"}], "version": initial["version"], "contentHash": initial["contentHash"]}, "mirror@example.invalid")
        gd.mutate({"action": "supplier", "supplier": "镜像供应商", "leadDays": 14, "version": gd.version()}, "mirror@example.invalid")
        gd.mutate({"action": "item", "productCode": "00123", "leadDays": 21, "bufferDays": 5, "operatorName": "镜像运营", "buyer": "镜像采购", "risk": "healthy", "riskReason": "镜像人工核实", "version": gd.version()}, "mirror@example.invalid")
        with connection.cursor() as cursor:
            cursor.execute("RESET ROLE")
            cursor.execute("SET ROLE gd_reader")
            _validate_inventory_schema(cursor, writer=False)
        assert gd.list_items()["items"][0]["productCode"] == "00123"
        from sales.auth import Principal
        monitor_item = gd.monitor(Principal("mirror@example.invalid", "Mirror", "admin", None), {})["items"][0]
        assert monitor_item["autoRisk"] == "unknown"
        assert (monitor_item["leadDays"], monitor_item["bufferDays"], monitor_item["cycleSource"]) == (21, 5, "型号设置")
        assert (monitor_item["operatorName"], monitor_item["buyer"]) == ("镜像运营", "镜像采购")
        assert (monitor_item["risk"], monitor_item["riskSource"], monitor_item["riskReasonOverride"]) == ("healthy", "型号设置", "镜像人工核实")
        with connection.cursor() as cursor:
            cursor.execute("RESET ROLE")
            cursor.execute("SET ROLE gd_bi")
            _validate_inventory_schema(cursor, writer=False)
            for table in TABLES:
                cursor.execute("SELECT has_table_privilege(current_user, %s, 'SELECT')", [table])
                assert cursor.fetchone() == (table != "inventory_guangdong_monitor_audits",)
            cursor.execute("RESET ROLE")
            for table in TABLES:
                cursor.execute("SELECT has_table_privilege('gd_reader', %s, 'INSERT'), has_table_privilege('gd_reader', %s, 'UPDATE'), has_table_privilege('gd_writer', %s, 'DELETE')", [table] * 3)
                assert cursor.fetchone() == (False, False, False)
            cursor.execute("SELECT has_table_privilege('gd_writer','sales_order_lines','UPDATE'), has_table_privilege('gd_writer','erp_product_master','UPDATE')")
            assert cursor.fetchone() == (False, False)
        print(json.dumps({"stage": "minimum-roles-and-readback", "status": "passed"}), flush=True)

        from inventory.plans import upsert_plan
        from inventory.models import ReplenishmentPlanItem
        with connection.cursor() as cursor:
            cursor.execute("SET ROLE gd_writer")
        plan = upsert_plan({"sourceBatchId": "mirror", "productCode": "00123", "productName": "镜像测试风扇",
                            "warehouse": "广东仓", "plannedQuantity": 20, "suggestedQuantity": 20, "reason": "镜像备货",
                            "orderDate": (timezone.localdate() - timedelta(days=2)).isoformat()},
                           "mirror@example.invalid")
        with connection.cursor() as cursor:
            cursor.execute("RESET ROLE")
            cursor.execute("SET ROLE gd_reader")
        assert ReplenishmentPlanItem.objects.get(id=plan.id).guangdong_health["active"] is True
        item = gd.monitor(Principal("mirror@example.invalid", "Mirror", "admin", None), {})["items"][0]
        assert (item["risk"], item["riskSource"]) == ("healthy", "备货跟进")
        with connection.cursor() as cursor:
            cursor.execute("RESET ROLE")

        # Exercise stock-triggered reset with the real minimum writer grants.
        from inventory.import_service import import_inventory_payload
        from inventory.tests.test_imports import stock_payload, stock_row
        from inventory.models import GuangdongMonitorItem, GuangdongMonitorAudit
        from inventory.warehouse_mapping import classify_warehouse
        with connection.cursor() as cursor:
            cursor.execute("SET ROLE gd_writer")
        for quantity, day_offset in ((100, -2), (90, -1), (91, 0)):
            row = stock_row("00123", 2, warehouse="广东仓", available=quantity)
            mapping = classify_warehouse("广东仓")
            row.update(warehouseType=mapping.warehouse_type, warehouseCategory=mapping.category,
                       includeInInventory=mapping.include_in_inventory, onHandQuantity=quantity)
            row["snapshotDate"] = (timezone.localdate() + timedelta(days=day_offset)).isoformat()
            payload = stock_payload(row, raw_seed=uuid.uuid4().hex)
            payload["snapshotDate"] = row["snapshotDate"]
            import_inventory_payload(payload, "mirror@example.invalid")
            if quantity in (100, 90):
                assert GuangdongMonitorItem.objects.get(product_code="00123").risk_override == "healthy"
                assert not GuangdongMonitorAudit.objects.filter(action="stock_risk_reset").exists()
                assert ReplenishmentPlanItem.objects.get(id=plan.id).guangdong_health["active"] is True
        assert GuangdongMonitorItem.objects.get(product_code="00123").risk_override is None
        assert GuangdongMonitorAudit.objects.filter(action="stock_risk_reset").count() == 1
        assert ReplenishmentPlanItem.objects.get(id=plan.id).guangdong_health["active"] is False
        with connection.cursor() as cursor:
            cursor.execute("RESET ROLE")
        print(json.dumps({"stage": "minimum-writer-stock-risk-reset", "status": "passed"}), flush=True)
        with connection.cursor() as cursor:
            cursor.execute("SET ROLE gd_reader")
        item = gd.monitor(Principal("mirror@example.invalid", "Mirror", "admin", None), {})["items"][0]
        assert (item["replenishmentQuantity"], item["replenishmentStockIncreaseQuantity"], item["replenishmentRemainingQuantity"]) == (20, 1, 19)
        with connection.cursor() as cursor:
            cursor.execute("RESET ROLE")
        print(json.dumps({"stage": "minimum-reader-order-remaining", "status": "passed"}), flush=True)

        def summary(db):
            with psycopg.connect(host="127.0.0.1", port=PORT, user="gd_owner", password=password, dbname=db) as conn:
                result = {}
                for table in (*TABLES, "replenishment_plan_items", "inventory_data_revisions"):
                    rows = conn.execute(sql.SQL("SELECT row_to_json(t)::text FROM {} t ORDER BY row_to_json(t)::text").format(sql.Identifier(table))).fetchall()
                    result[table] = {"rows": len(rows), "sha256": hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()}
                return result
        before = summary("gd_mirror")
        command([BIN / "pg_dump.exe", "-Fc", "-f", run / "mirror.dump", "gd_mirror"], "backup")
        command([BIN / "createdb.exe", "gd_restore"], "restore-create")
        command([BIN / "pg_restore.exe", "--no-owner", "--no-privileges", "--exit-on-error", "-d", "gd_restore", run / "mirror.dump"], "restore")
        assert summary("gd_restore") == before
        evidence = {"status": "passed", "postgresPort": PORT, "syntheticDataOnly": True, "migration": "0010_replenishment_health", "tables": before, "minimumRoleChecks": "passed", "restoreMatches": True}
        (run / "result.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print(json.dumps({"stage": "restore-readback", "status": "passed", "evidence": str(run / "result.json")}), flush=True)
        connection.close()
    finally:
        if password_file.exists(): password_file.unlink()
        if started:
            command([BIN / "pg_ctl.exe", "-D", run / "data", "-m", "fast", "-w", "-t", "45", "stop"], "stop")


if __name__ == "__main__":
    main()
