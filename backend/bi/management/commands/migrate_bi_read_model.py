from __future__ import annotations

import hashlib
import json
import uuid

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from bi.models import BiMigrationRun
from bi.query import BI_CONTRACT_VERSION, source_revisions
from inventory.models import InventoryAgeLine, InventoryImportBatch, InventoryStockLine
from sales.models import SalesImportBatch, SalesOrderLine


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _snapshot_once(revisions: dict[str, str]) -> dict[str, object]:
    latest_sales = (
        SalesImportBatch.objects.filter(status="completed").order_by("-completed_at", "-id").first()
    )
    owned_stock_batches = InventoryStockLine.objects.values("batch_id")
    latest_inventory = (
        InventoryImportBatch.objects.filter(
            dataset="stock", status="completed", id__in=owned_stock_batches
        )
        .order_by("-snapshot_date", "-completed_at", "-id")
        .first()
    )
    owned_age_batches = InventoryAgeLine.objects.values("batch_id")
    latest_inventory_age = (
        InventoryImportBatch.objects.filter(
            dataset="age", status="completed", id__in=owned_age_batches
        )
        .order_by("-snapshot_date", "-completed_at", "-id")
        .first()
    )
    counts = {
        "salesBusinessRows": SalesOrderLine.objects.filter(is_business_row=True).count(),
        "latestInventoryStockRows": (
            InventoryStockLine.objects.filter(batch_id=latest_inventory.id).count()
            if latest_inventory
            else 0
        ),
        "latestInventoryAgeRows": (
            InventoryAgeLine.objects.filter(batch_id=latest_inventory_age.id).count()
            if latest_inventory_age
            else 0
        ),
        "legacyBiFactRows": 0,
    }
    snapshot = {
        "contractVersion": BI_CONTRACT_VERSION,
        "sourceAuthorities": {
            "sales": "postgresql",
            "erp": "d1-via-postgresql-read-projection",
            "inventory": "postgresql",
        },
        "sourceRevisions": revisions,
        "sourceCounts": counts,
        "latestSalesBatchId": latest_sales.id if latest_sales else None,
        "latestInventoryBatchId": latest_inventory.id if latest_inventory else None,
        "latestInventoryAgeBatchId": latest_inventory_age.id if latest_inventory_age else None,
        "factCopyRequired": False,
    }
    digest = hashlib.sha256(_canonical(snapshot).encode("utf-8")).hexdigest()
    return {**snapshot, "sourceDigest": digest, "planId": f"bi-plan-{digest[:32]}"}


def _snapshot() -> dict[str, object]:
    for _attempt in range(2):
        before = source_revisions()
        material = _snapshot_once(before)
        if source_revisions() == before:
            return material
    raise CommandError("BI 源 revision 持续变化，无法生成一致迁移材料")


def _require_role() -> None:
    if settings.DJANGO_ENVIRONMENT == "production" and settings.DJANGO_PROCESS_ROLE != "migration_writer":
        raise CommandError("生产 BI 迁移只允许 migration_writer 进程角色")


class Command(BaseCommand):
    help = "Plan, apply, or verify the read-only BI projection adoption."

    def add_arguments(self, parser) -> None:
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--plan", action="store_true")
        mode.add_argument("--apply", action="store_true")
        mode.add_argument("--verify", action="store_true")
        parser.add_argument("--approved-plan-id", default="")
        parser.add_argument("--approved-run-id", default="")

    def handle(self, *args, **options):
        _require_role()
        material = _snapshot()
        if options["plan"]:
            self.stdout.write(_canonical({"mode": "plan", "status": "planned", **material}))
            return

        if options["apply"]:
            approved = str(options["approved_plan_id"] or "")
            if approved != material["planId"]:
                raise CommandError("approved plan id 与当前 BI 源材料不一致")
            with transaction.atomic():
                existing = BiMigrationRun.objects.select_for_update().filter(plan_id=approved).first()
                if existing is None:
                    existing = BiMigrationRun.objects.create(
                        id=f"bi-apply-{uuid.uuid4().hex}",
                        plan_id=approved,
                        status="applied",
                        contract_version=BI_CONTRACT_VERSION,
                        source_digest=str(material["sourceDigest"]),
                        source_revisions_json=material["sourceRevisions"],
                        source_counts_json=material["sourceCounts"],
                        source_snapshot_json=material,
                    )
                elif existing.source_digest != material["sourceDigest"]:
                    raise CommandError("既有 BI apply 与当前源材料不一致")
            self.stdout.write(_canonical({"mode": "apply", "status": existing.status, "runId": existing.id, **material}))
            return

        approved_run_id = str(options["approved_run_id"] or "")
        with transaction.atomic():
            try:
                run = BiMigrationRun.objects.select_for_update().get(id=approved_run_id)
            except BiMigrationRun.DoesNotExist as error:
                raise CommandError("approved run id 不存在") from error
            if (
                run.plan_id != material["planId"]
                or run.source_digest != material["sourceDigest"]
                or run.contract_version != BI_CONTRACT_VERSION
            ):
                raise CommandError("BI 源材料在 apply 与 verify 之间发生变化")
            run.status = "verified"
            run.verified_at = timezone.now()
            run.save(update_fields=["status", "verified_at"])
        self.stdout.write(_canonical({"mode": "verify", "status": "verified", "runId": run.id, **material}))
