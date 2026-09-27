"""Synthetic fixtures for the reviewed workflow and annual-target screens."""
from datetime import timedelta
from pathlib import Path
import os


def seed_workflow_annual(today):
    from django.conf import settings
    from django.utils import timezone
    from workflow.models import NewProductLine, NewProductLineCode, NewProductProject, NewProductStage
    from finance.models import FinanceMonth, FinanceLine, FinanceTarget
    from sales.models import ErpProductMaster
    root = Path(__file__).resolve().parents[2]
    database = Path(settings.DATABASES["default"]["NAME"]).resolve()
    if settings.DATABASES["default"]["ENGINE"] != "django.db.backends.sqlite3" or database.parent != root / ".runtime/preview" or os.getenv("TERUISI_DJANGO_DATABASE_URL"):
        raise RuntimeError("This fixture requires the worktree's synthetic preview SQLite")
    stamp = timezone.now().isoformat()
    from workflow.models import WorkflowTask, WorkflowOperationRecord
    for index, (status, days) in enumerate((("待开始", 3), ("工作中", -1), ("工作中", 0), ("已完成", -2))):
        WorkflowTask.objects.create(id=f"preview-card-task-{index}", title=f"合成卡片任务 {index + 1}", status=status, due_date=(today + timedelta(days=days)).isoformat(), owner="演示负责人", shop_name="演示店铺", created_by="preview@teruisi.local", updated_by="preview@teruisi.local")
    for kind in ("inspection", "review"):
        for index, status in enumerate(("待处理", "处理中", "已关闭") if kind == "inspection" else ("待回复", "处理中", "已回复")):
            WorkflowOperationRecord.objects.create(id=f"preview-card-{kind}-{index}", record_type=kind, title=f"合成{kind}记录 {index + 1}", status=status, priority="high" if index == 1 else "normal", occurred_at=timezone.now(), created_by="preview@teruisi.local", updated_by="preview@teruisi.local")
    line = NewProductLine.objects.create(name="商用设备 · 合成演示", match_terms=["商用"], monitoring_start_date=today-timedelta(days=30), created_by="preview@teruisi.local", updated_by="preview@teruisi.local")
    NewProductLineCode.objects.create(product_line=line, product_code="DEMO-001", product_name="商用电风扇", source="manual", added_by="preview@teruisi.local")
    for index, status in enumerate(("in_progress", "blocked", "completed", "not_started")):
        project = NewProductProject.objects.create(product_name=f"合成演示商品 {index+1}", supplier_name="演示供应商", owner="演示负责人", proposed_by="演示负责人", proposed_date=today-timedelta(days=10), created_by="preview@teruisi.local", updated_by="preview@teruisi.local")
        project.shop_plan = "京东演示店：先上架\n天猫演示店：图片确认后上架" if index == 0 else ""
        project.notes = "等待供应商确认样品。\n图片确认后安排上架，负责人下周继续跟进。" if index == 1 else ""
        project.save(update_fields=["shop_plan", "notes"])
        for stage in ("modeling", "pricing", "image", "video", "listing", "stocking", "review"):
            NewProductStage.objects.create(project=project, stage_key=stage, status=status if stage == "modeling" or status == "completed" else "not_started", planned_due_date=today+timedelta(days=-2 if index == 1 else 10), updated_by="preview@teruisi.local")
    year = str(today.year)
    months = sorted({f"{year}-01", f"{year}-02", (today.replace(day=1)-timedelta(days=1)).strftime("%Y-%m")})
    for month in months:
        FinanceMonth.objects.create(month=month, batch_id="preview-finance", sheet_name="合成财报", business_name="演示事业部", source_file_name="合成示例", status="completed", shop_count=2, subject_count=3, imported_at=stamp)
        for platform, name, sales, profit in (("京东", "演示店铺1", 1_000_000, 180_000), ("天猫", "演示店铺2", 800_000, 120_000)):
            for metric, amount in (("net_sales", sales), ("gross_sales", sales), ("gross_profit", sales * 4 // 10), ("profit", profit)):
                FinanceLine.objects.create(month=month, section="summary", metric_key=metric, subject_name=metric, scope_key=f"shop:{platform}:{name}", scope_type="shop", scope_name=name, group_name=platform, value_type="amount", amount_cents=amount, created_at=stamp)
            FinanceLine.objects.create(month=month, section="kingdee", metric_key="", subject_name=f"销售费用_推广费用_{platform}", scope_key=f"shop:{platform}:{name}", scope_type="shop", scope_name=name, group_name=platform, value_type="amount", amount_cents=sales * 5 // 100, created_at=stamp)
    FinanceTarget.objects.create(id="preview-annual-target", period_type="year", period_key=year, platform="京东", shop_name="演示店铺1", manager="演示负责人", sales_target_cents=6_000_000, profit_target_cents=1_000_000, gross_margin_bps=4_200, promotion_fee_ratio_bps=600, created_at=stamp, updated_at=stamp)
