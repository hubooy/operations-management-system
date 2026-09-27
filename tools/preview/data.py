"""Synthetic UI fixtures and private SQLite snapshots; no production connections."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from datetime import timedelta

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".runtime" / "preview"


def checked_path(value):
    target = Path(value).resolve()
    if target.parent != RUNTIME.resolve() or target.suffix != ".sqlite3":
        raise RuntimeError("Preview database must be a direct child of .runtime/preview")
    return target


def seed():
    import django
    sys.path.insert(0, str(ROOT / "backend"))
    os.environ["DJANGO_SETTINGS_MODULE"] = "teruisi_backend.settings"
    django.setup()
    from django.core.management import call_command
    from django.db import transaction
    from django.utils import timezone
    from sales.models import SalesImportBatch, SalesOrderLine, ErpProductMaster, SalesDataRevision, sales_projection_values
    from inventory.models import InventoryImportBatch, InventoryStockLine, InventoryAgeLine, InventoryImportScopeHead, InventoryDataRevision, GuangdongMonitorItem, GuangdongSupplierCycle
    call_command("migrate", interactive=False, verbosity=0)
    now = timezone.now()
    today = timezone.localdate()
    stamp = now.isoformat()
    digest = hashlib.sha256(b"teruisi-synthetic-preview-v1").hexdigest()
    with transaction.atomic():
        SalesImportBatch.objects.create(id="preview-sales", source="synthetic-preview", file_name="合成演示销售", file_size_bytes=0, file_hash=digest, sheet_name="演示", status="completed", row_count=180, inserted_count=180, created_at=stamp, completed_at=stamp)
        for i in range(6):
            code = f"DEMO-{i+1:03d}"
            name = ["商用电风扇", "台式绞肉机", "不锈钢切片机", "多功能料理机", "商用电热锅", "立式搅拌机"][i]
            ErpProductMaster.objects.create(product_code=code, product_name=name, category="演示设备", supplier=f"演示供应商{i%2+1}", source_row_number=i+1, last_import_batch_id="preview-erp", created_at=stamp, updated_at=stamp)
            GuangdongMonitorItem.objects.create(product_code=code, updated_by="preview@teruisi.local")
            for day in range(30):
                date = today - timedelta(days=day+1)
                qty = (day+i)%5+1
                raw = dict(source_line_key=f"preview-{i}-{day}", ship_time=f"{date}T10:00:00+08:00", product_code=code, product_name=name, warehouse="广东仓", category="演示设备", channel="自营", platform="京东" if i%2 else "天猫", shop_name=f"演示店铺{i%2+1}", order_no=f"DEMO-{i}-{day}")
                values = {field.name: "" for field in SalesOrderLine._meta.fields if field.get_internal_type() == "TextField" and not field.has_default()}
                values.update(raw)
                values.update(sales_projection_values(raw))
                values.update(source_row_hash=hashlib.sha256(raw["source_line_key"].encode()).hexdigest(), first_import_batch_id="preview-sales", last_import_batch_id="preview-sales", source_row_number=i*30+day+1, quantity=qty, list_unit_price_cents=29900, cost_amount_cents=19000*qty, allocated_unit_price_cents=29900, allocated_amount_cents=29900*qty, fee_allocation_cents=1000*qty, gross_profit_cents=9900*qty, gross_margin_bps=3311, untaxed_gross_profit_cents=9900*qty, untaxed_gross_margin_bps=3311, created_at=stamp, updated_at=stamp)
                SalesOrderLine.objects.create(**values)
        for dataset, model in [("stock", InventoryStockLine), ("age", InventoryAgeLine)]:
            head = InventoryImportScopeHead.objects.get(dataset=dataset)
            batch_id = f"preview-{dataset}"
            batch_hash = hashlib.sha256(batch_id.encode()).hexdigest()
            InventoryImportBatch.objects.create(id=batch_id, dataset=dataset, source="synthetic-preview", file_name="合成演示库存", file_size_bytes=0, file_hash=batch_hash, raw_file_hash=batch_hash, content_hash=batch_hash, scope_key=head.scope_key, sheet_name="演示", snapshot_date=today, status="completed", row_count=6, inserted_count=6, completed_at=now)
            for i in range(6):
                fields = dict(batch_id=batch_id, row_key=f"广东仓:DEMO-{i+1:03d}", source_row_number=i+1, snapshot_date=today, warehouse="广东仓", warehouse_type="owned", product_code=f"DEMO-{i+1:03d}", product_name=ErpProductMaster.objects.get(pk=f"DEMO-{i+1:03d}").product_name, available_quantity=[0,8,30,90,200,500][i], unit_cost_cents=19000, inventory_age_days=i*40, sales_7d_quantity=20, sales_30d_quantity=90)
                if dataset == "stock":
                    fields.update(on_hand_quantity=fields["available_quantity"], supplier=f"演示供应商{i%2+1}", in_transit_quantity=i*5)
                model.objects.create(**fields)
            head.current_batch_id = batch_id
            head.state_token = batch_hash
            head.save()
        for i in (1,2):
            GuangdongSupplierCycle.objects.create(supplier=f"演示供应商{i}", lead_days=14, buffer_days=7, updated_by="preview@teruisi.local")
        for domain in ("sales", "erp"):
            SalesDataRevision.objects.update_or_create(domain=domain, defaults={"revision":1})
        InventoryDataRevision.objects.update_or_create(domain="inventory", defaults={"revision":1})
        from market.models import MarketRankingEntry
        yesterday = today - timedelta(days=1)
        for i in range(40):
            MarketRankingEntry.objects.create(natural_key=f"preview-market-{i}", source_row_number=i+1,
                period_start=str(yesterday.replace(day=1)), period_end=str(yesterday),
                category="演示净水设备" if i < 20 else "演示厨房设备", scope="全部", ranking_dimension="SKU",
                operation_mode="POP" if i % 2 else "自营", subcategory="演示细分类目", rank=i+1,
                sku_code=f"MARKET-DEMO-{i+1:03d}", product_name=f"合成演示商品 {i+1:02d}", brand="演示品牌",
                gmv_cents=10000*(40-i), quantity=40-i, visitors=(40-i)*10, last_import_batch_id="preview-market")
        from ai_assistant.models import AiConversations, AiConversationMessages, AiConversationScopes, AiConversationWorkspace
        from ai_assistant.prompt_settings import snapshot, compose
        conversation = AiConversations.objects.create(id="preview-chat", title="广东仓库存复盘 · 合成演示", created_by="local-admin@teruisi.local")
        AiConversationWorkspace.objects.create(conversation=conversation, module_key="ai")
        AiConversationScopes.objects.create(conversation=conversation, scope_json="null")
        _, guidance = compose(snapshot(), "库存健康", None, [{"name":"get_inventory_health"}])
        content = "\n\n".join([
            "## 广东仓库存复盘\n这是页面排版演示，以下为合成数据，不代表实际经营结果。",
            "### 结论\n优先关注库存偏低的演示型号，随后复核积压商品。缺少记录的型号应核验数据覆盖，不能直接判断为零库存。",
            "### 数据范围\n- 来源：独立预览环境的合成库存\n- 范围：6 个演示型号、精确广东仓\n- 单位：件；以下风险说明只用于展示长回复",
            "### 型号明细\n| 型号 | 当前库存 | 建议动作 |\n|---|---:|---|\n| DEMO-001 | 0 | 核验后优先补货 |\n| DEMO-002 | 8 | 复核到货安排 |\n| DEMO-003 | 30 | 关注周转与供应周期 |\n| DEMO-004 | 90 | 持续观察 |\n| DEMO-005 | 200 | 复核需求变化 |\n| DEMO-006 | 500 | 复核库存结构 |",
            "### 下一步建议\n1. 核对在途、供应周期和当前销售趋势。\n2. 将需要备货的型号整理为待确认清单。\n3. 持续区分库存总览、广东人工监控及备货计划的范围。",
            "### 口径说明\n库存结论应携带数据截止日期、仓库范围和计算依据；广东监控清单不能代表全仓。实际值与估算值分开说明。",
        ])
        AiConversationMessages.objects.create(id="preview-chat-user", conversation_id=conversation.id, role="user", content="帮我复盘广东仓库存，给出结论、明细与下一步建议。", ordinal=1)
        AiConversationMessages.objects.create(id="preview-chat-answer", conversation_id=conversation.id, role="assistant", content=content, ordinal=2,
            execution_json=json.dumps({"durationMs":3200,"providerCalls":1,"toolCalls":1,"stopReason":"stop","guidance":guidance},ensure_ascii=False))
    from report_fixture import seed_report
    seed_report(today)
    from workflow_annual_fixture import seed_workflow_annual
    seed_workflow_annual(today)
    print(json.dumps({"fixture":"synthetic-v1", "anchorDate":str(today), "sales":180, "products":6, "stock":6, "age":6}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["seed", "copy"])
    parser.add_argument("target")
    parser.add_argument("--source")
    args = parser.parse_args()
    target = checked_path(args.target)
    if target.exists():
        raise RuntimeError("Refusing to overwrite a preview database; choose a fresh generation")
    if os.getenv("TERUISI_DJANGO_DATABASE_URL"):
        raise RuntimeError("Preview forbids PostgreSQL connection variables")
    os.environ["TERUISI_DJANGO_SQLITE_PATH"] = str(target)
    if args.action == "seed":
        seed()
    else:
        source = checked_path(args.source)
        with sqlite3.connect(source.as_uri()+"?mode=ro", uri=True) as src, sqlite3.connect(target) as dst:
            if src.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Invalid preview snapshot")
            src.backup(dst)
        print("Preview snapshot copied")
