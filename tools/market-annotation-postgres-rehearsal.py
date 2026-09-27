"""Run market regressions and real runtime-role probes in a disposable PostgreSQL cluster.

Only PostgreSQL/Python executables are reused. No production configuration,
credentials, data, listeners or service lifecycle commands are accessed.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import uuid
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
PORT = 55447
DATABASE = "market_annotation_rehearsal"
ADMIN = "market_rehearsal_admin"


def role_probe() -> None:
    """Exercise ORM operations with exactly the deployed role grant contract."""
    target = urlparse(os.environ.get("TERUISI_DJANGO_DATABASE_URL", ""))
    if (
        os.environ.get("TERUISI_DJANGO_ENVIRONMENT") != "test"
        or target.scheme != "postgresql" or target.hostname != "127.0.0.1"
        or target.port != PORT or target.path != "/" + DATABASE
        or target.username not in {"teruisi_market_reader", "teruisi_market_writer"}
    ):
        raise RuntimeError("Role probe requires the isolated test database")
    import django

    django.setup()
    from django.db import connection, transaction
    from market.annotations import execute_annotation_command, execute_annotation_query
    from market.models import MarketAnnotationCommitReceipt, MarketAnnotationItem, MarketSkuAnnotation
    from sales.auth import Principal

    principal = Principal("fixture@example.invalid", "Fixture", "admin", None)
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_user, inet_server_port()")
        role, port = cursor.fetchone()
        if role not in {"teruisi_market_reader", "teruisi_market_writer"} or port != PORT:
            raise RuntimeError("Runtime probe escaped the isolated identity")
    query = {"operation": "annotations", "view": "progress", "params": {"jobId": "role-probe-job"}}
    progress = execute_annotation_query(query, principal)
    if progress["job"]["id"] != "role-probe-job":
        raise AssertionError("Runtime progress did not return the synthetic job")
    execute_annotation_query({"operation": "annotations", "view": "review", "params": {"category": "Role probe"}}, principal)
    from market.query import filter_options, overview
    filters = filter_options()
    if not filters["categories"]:
        raise AssertionError("Runtime role could not read independent market filters")
    ranking = overview(principal, {"operation": "overview", "view": "ranking", "page": 1, "pageSize": 20,
        "filters": {"categories": ["Role probe"]}}, sales_loader=lambda _principal, request: (
            {"rows": [{"productCode": code, "owned": False, "ownSalesCents": 0} for code in request["productCodes"]]}, "1:1"))
    if len(ranking["items"]) != 1:
        raise AssertionError("Runtime role could not read database-paginated ranking")
    dispatch = execute_annotation_query({"operation": "annotations", "view": "dispatch", "params": {"limit": 5}}, principal)
    if not any(job["jobId"] == "role-probe-job" for job in dispatch["jobs"]):
        raise AssertionError("Least-privilege role could not read synthetic dispatch capacity")
    if role == "teruisi_market_writer":
        with transaction.atomic():
            prompt = execute_annotation_command({"action": "create_prompt", "category": "Role probe",
                "segments": ["Synthetic"], "promptBody": "Synthetic category lock probe"}, principal)
        if prompt.get("version") != 2:
            raise AssertionError("Least-privilege writer could not allocate the next category prompt version")
        with transaction.atomic():
            claim = execute_annotation_command({"action": "claim_task", "jobId": "role-probe-job"}, principal)
        task = claim.get("task")
        if not task:
            raise AssertionError("Least-privilege writer could not claim a synthetic item")
        with transaction.atomic():
            completion = execute_annotation_command({
                "action": "complete_task", "itemId": task["itemId"], "leaseToken": task["leaseToken"],
                "result": {"segment": "Synthetic", "imagePriceCents": 12300, "priceType": "标准售价", "confidenceBps": 9500},
            }, principal)
        if not completion.get("ok") or MarketAnnotationItem.objects.get(pk=task["itemId"]).status != "review_pending":
            raise AssertionError("Least-privilege writer could not save a synthetic completion")
        item = MarketAnnotationItem.objects.get(pk=task["itemId"])
        with transaction.atomic():
            execute_annotation_command({"action": "review", "jobId": item.job_id, "updates": [{
                "id": item.id, "version": item.version, "segment": "Synthetic", "imagePriceCents": 12300, "selected": True,
            }]}, principal)
        with transaction.atomic():
            committed = execute_annotation_command({
                "action": "commit_selected", "aggregateJobs": True, "idempotencyKey": "role-probe-commit",
            }, principal)
        receipt = MarketAnnotationCommitReceipt.objects.get(job_item_id=item.id)
        if committed.get("committed") != 1 or not isinstance(receipt.before_json.get("reviewed_at"), str):
            raise AssertionError("Least-privilege writer could not audit the existing annotation")
        if MarketSkuAnnotation.objects.get(pk="role-probe-annotation").version != 2:
            raise AssertionError("Existing annotation was not updated exactly once")
    connection.close()
    print(json.dumps({"role": role, "readQueries": True, "rankingPagination": True, "independentFilters": True, "promptVersionAllocation": role.endswith("writer"), "claimComplete": role.endswith("writer"), "commitExistingAnnotation": role.endswith("writer"), "externalModelCalls": 0}), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tests-only", action="store_true", help="Skip synthetic role probes")
    parser.add_argument("--role-probe", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    root = ROOT.resolve()
    if root == Path(r"D:\运营管理系统").resolve() or not (root / ".git").is_file():
        raise RuntimeError("An isolated Git worktree is required")
    if args.role_probe:
        role_probe()
        return
    runtime = root / ".runtime"
    runtime.mkdir(exist_ok=True)
    if runtime.resolve().parent != root:
        raise RuntimeError("Rehearsal runtime must remain inside this worktree")
    run_root = runtime / ("market-annotation-pg-" + secrets.token_hex(6))
    run_root.mkdir()
    run_root = run_root.resolve()
    if not run_root.is_relative_to(runtime.resolve()):
        raise RuntimeError("Rehearsal path escaped the isolated runtime")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", PORT))
    password = secrets.token_hex(32)
    passwords = [password]
    password_file = run_root / "password.txt"
    password_file.write_text(password, encoding="ascii")
    os.chmod(password_file, 0o600)
    environment = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith(("TERUISI_", "AI_", "PG", "DJANGO_", "OPENAI_", "ARK_", "OLLAMA_", "CLOUDFLARE_"))
    }
    environment.update({
        "PGHOST": "127.0.0.1", "PGPORT": str(PORT), "PGUSER": ADMIN,
        "PGPASSWORD": password, "PGDATABASE": DATABASE,
        "DJANGO_SECRET_KEY": secrets.token_hex(32),
        "TERUISI_DJANGO_INTERNAL_SECRET": secrets.token_hex(32),
        "TERUISI_DJANGO_DATABASE_URL": f"postgresql://{ADMIN}:{password}@127.0.0.1:{PORT}/{DATABASE}",
        "TERUISI_DJANGO_ENVIRONMENT": "test", "TERUISI_DJANGO_PROCESS_ROLE": "development",
        "DJANGO_SETTINGS_MODULE": "teruisi_backend.settings", "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(root / "backend"),
    })

    def run(command, *, env=None, timeout=300, label="step"):
        output = run_root / (label + "-" + secrets.token_hex(4) + ".log")
        # Files avoid pg_ctl descendant pipe inheritance delaying completion.
        with output.open("wb") as stream:
            result = subprocess.run(
                [str(value) for value in command], cwd=root, env=env or environment,
                stdout=stream, stderr=stream, timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        raw = output.read_bytes()
        for secret in passwords:
            raw = raw.replace(secret.encode(), b"[redacted]")
        output.write_bytes(raw)
        if result.returncode:
            raise RuntimeError("Isolated command failed; see " + str(output))
        return raw.decode("utf-8", errors="replace")

    def manage(*arguments, **options):
        return run([sys.executable, root / "backend/manage.py", *arguments], **options)

    started = False
    report = {"port": PORT, "productionTouched": False, "externalModelCalls": 0}
    try:
        run([BIN / "initdb.exe", "-D", run_root / "data", "-U", ADMIN,
             "--auth=scram-sha-256", "--encoding=UTF8", "--locale=C", "--pwfile", password_file])
        with (run_root / "data/postgresql.conf").open("a", encoding="ascii") as config:
            config.write(f"\nlisten_addresses='127.0.0.1'\nport={PORT}\nmax_connections=96\n")
        run([BIN / "pg_ctl.exe", "-D", run_root / "data", "-l", run_root / "postgres.log", "-w", "-t", "30", "start"], timeout=60)
        started = True
        run([BIN / "createdb.exe", DATABASE])
        print(json.dumps({"stage": "isolated_cluster_started", **report}), flush=True)
        tests = manage("test", "market", "--noinput", "--verbosity", "2", label="market-tests")
        (run_root / "tests.log").write_text(tests, encoding="utf-8")
        summary = re.search(r"Ran (\d+) tests in ([\d.]+)s", tests)
        report["tests"] = {"count": int(summary[1]) if summary else None, "log": str(run_root / "tests.log")}
        print(json.dumps({"stage": "market_tests_passed", "tests": report["tests"]}), flush=True)
        if not args.tests_only:
            manage("migrate", "--noinput", "--verbosity", "0", label="migrate")
            manage("makemigrations", "market", "--check", "--dry-run", label="migration-check")
            import psycopg

            admin_url = environment["TERUISI_DJANGO_DATABASE_URL"]
            reader_password, writer_password = secrets.token_hex(32), secrets.token_hex(32)
            passwords.extend([reader_password, writer_password])
            provision_text = (root / "tools/django-market-service.ps1").read_text(encoding="utf-8")
            provision_start = provision_text.index("function Provision-MarketRoles {")
            provision = provision_text[provision_start:].split("$code = @'", 1)[1].split("\n'@", 1)[0]
            # Execute only this checked-in Python grant contract, never the
            # PowerShell wrapper, its DPAPI access or its lifecycle operations.
            provision_path = run_root / "provision-roles.py"
            provision_path.write_text(provision, encoding="utf-8")
            provision_env = {**environment,
                "TERUISI_PROVISION_DATABASE_URL": admin_url,
                "TERUISI_PROVISION_MARKET_READER_PASSWORD": reader_password,
                "TERUISI_PROVISION_MARKET_WRITER_PASSWORD": writer_password,
            }
            run([sys.executable, provision_path], env=provision_env, label="role-provision")

            # Seed wholly synthetic fixtures with the isolated owner. Runtime
            # operations below reconnect using the actual reader/writer grants.
            os.environ.clear()
            os.environ.update(environment)
            sys.path.insert(0, str(root / "backend"))
            import django
            django.setup()
            from django.db import connections
            from django.utils import timezone
            from market.models import (
                MarketAnnotationCloudRun, MarketAnnotationConcurrencySetting,
                MarketAnnotationItem, MarketAnnotationJob, MarketAnnotationPromptVersion,
                MarketPriceSnapshot, MarketRankingEntry, MarketSkuAnnotation,
                MarketSubcategoryTaxonomy, MarketWriteAuthority,
            )
            epoch = str(uuid.uuid4())
            MarketWriteAuthority.objects.filter(pk=1).update(status="postgres", authority_epoch=epoch,
                cutover_id="market-annotation-role-probe", activated_at=timezone.now(), migration_verify_run_id="synthetic-role-probe")
            MarketAnnotationPromptVersion.objects.create(id="role-probe-prompt", category="Role probe", version=1,
                source="manual", status="active", segments_json=["Synthetic"], prompt_body="Synthetic only", created_by="fixture@example.invalid")
            MarketAnnotationJob.objects.create(id="role-probe-job", category="Role probe", prompt_version_id="role-probe-prompt",
                executor="cloud", model_id="fixture-model", status="queued", created_by="fixture@example.invalid")
            MarketAnnotationCloudRun.objects.create(job_id="role-probe-job", state="running")
            MarketAnnotationConcurrencySetting.objects.create(category="Role probe", executor="cloud", concurrency=2, updated_by="fixture@example.invalid")
            MarketAnnotationItem.objects.create(id="role-probe-item", job_id="role-probe-job", category="Role probe",
                scope="pop", sku_code="fixture-sku", month="2026-09", image_content_sha256="a" * 64,
                source_image_url="https://example.invalid/no-network.jpg")
            identity = dict(category="Role probe", scope="pop", sku_code="fixture-sku", ranking_dimension="SKU")
            MarketSubcategoryTaxonomy.objects.create(id="role-probe-taxonomy", category="Role probe", subcategory="Synthetic")
            MarketPriceSnapshot.objects.create(id="role-probe-snapshot", **identity, month="2026-09", image_content_sha256="a" * 64)
            MarketRankingEntry.objects.create(natural_key="role-probe-ranking", **identity, period_start="2026-09-01",
                period_end="2026-09-12", source_row_number=1, last_import_batch_id="fixture")
            MarketSkuAnnotation.objects.create(id="role-probe-annotation", **identity, image_content_sha256="a" * 64,
                segment="Previous", source_job_item_id="historical-item", prompt_version_id="role-probe-prompt",
                reviewed_by="fixture@example.invalid", reviewed_at=timezone.now())
            connections.close_all()
            role_results = []
            for role, secret in (("reader", reader_password), ("writer", writer_password)):
                role_url = f"postgresql://teruisi_market_{role}:{secret}@127.0.0.1:{PORT}/{DATABASE}"
                role_env = {**environment, "TERUISI_DJANGO_DATABASE_URL": role_url,
                    "TERUISI_DJANGO_PROCESS_ROLE": "market_" + role,
                    "TERUISI_DJANGO_EXPECT_READ_ONLY": str(role == "reader").lower(),
                    "TERUISI_DJANGO_MARKET_AUTHORITY_EPOCH": epoch,
                    "TERUISI_DJANGO_MARKET_CUTOVER_ID": "market-annotation-role-probe",
                }
                role_results.append(json.loads(run([sys.executable, __file__, "--role-probe"], env=role_env, label="role-" + role)))
                with psycopg.connect(role_url, autocommit=True) as limited:
                    rejected = 0
                    forbidden = [
                        "CREATE TABLE public.forbidden_probe(id integer)",
                        "UPDATE market_write_authority SET status='legacy'",
                        "UPDATE sales_order_lines SET allocated_amount_cents=0",
                        "TRUNCATE market_annotation_items",
                    ]
                    if role == "reader":
                        forbidden.append("UPDATE market_annotation_items SET selected=true")
                    for statement in forbidden:
                        try:
                            limited.execute(statement)
                        except psycopg.errors.InsufficientPrivilege:
                            rejected += 1
                        except psycopg.errors.ReadOnlySqlTransaction:
                            rejected += 1
                        else:
                            raise AssertionError("Runtime role escaped its grant boundary")
                    role_results[-1]["negativePermissionsRejected"] = rejected
            report["roles"] = role_results
        report["status"] = "passed"
    finally:
        if started:
            run([BIN / "pg_ctl.exe", "-D", run_root / "data", "-m", "fast", "-w", "-t", "30", "stop"], timeout=60, label="stop-isolated")
        password_file.unlink(missing_ok=True)
    report["isolatedClusterStopped"] = True
    report_path = run_root / "result.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**report, "evidence": str(report_path)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
