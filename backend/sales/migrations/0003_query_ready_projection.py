from datetime import date

from django.db import migrations, models


UNCATEGORIZED = "未分类"
EXCLUDED_SOURCE_CATEGORIES = {"配件", "赠品配件"}
PROJECTION_FIELDS = [
    "business_date",
    "platform_key",
    "channel_key",
    "shop_key",
    "resolved_category",
    "order_identity",
    "is_business_row",
    "is_net_sales_row",
    "is_net_quantity_row",
]


def _text(value):
    return "" if value is None else str(value)


def _trimmed(value):
    return _text(value).strip()


def populate_query_ready_projection(apps, schema_editor):
    SalesOrderLine = apps.get_model("sales", "SalesOrderLine")
    ErpProductMaster = apps.get_model("sales", "ErpProductMaster")
    erp_categories = dict(ErpProductMaster.objects.values_list("product_code", "category"))
    last_id = 0
    while True:
        rows = list(SalesOrderLine.objects.filter(id__gt=last_id).order_by("id")[:500])
        if not rows:
            break
        for row in rows:
            try:
                row.business_date = date.fromisoformat(_text(row.ship_time)[:10])
            except ValueError as error:
                raise RuntimeError(
                    f"sales_order_lines id={row.id} has an invalid ship_time business date"
                ) from error
            platform = _trimmed(row.platform)
            channel = _trimmed(row.channel)
            shop = _trimmed(row.shop_name)
            source_category = _trimmed(row.category)
            included_category = bool(source_category) and source_category not in EXCLUDED_SOURCE_CATEGORIES
            row.platform_key = platform or UNCATEGORIZED
            row.channel_key = channel or UNCATEGORIZED
            row.shop_key = shop or channel or platform or UNCATEGORIZED
            row.resolved_category = (
                _trimmed(erp_categories.get(row.product_code)) or source_category or UNCATEGORIZED
            )
            row.order_identity = row.order_no or row.online_order_no or row.source_line_key
            row.is_business_row = _trimmed(row.warehouse) != "刷刷仓"
            row.is_net_sales_row = included_category
            row.is_net_quantity_row = (
                included_category
                and row.product_code != "ERP_PRICE_ADJUSTMENT"
                and _trimmed(row.product_name) != "补差价专用"
            )
        SalesOrderLine.objects.bulk_update(rows, PROJECTION_FIELDS, batch_size=500)
        last_id = rows[-1].id


class Migration(migrations.Migration):
    dependencies = [("sales", "0002_sales_migration_approval")]

    operations = [
        migrations.AddField(
            model_name="salesorderline",
            name="business_date",
            field=models.DateField(null=True),
        ),
        migrations.AddField(
            model_name="salesorderline",
            name="platform_key",
            field=models.TextField(default=""),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="salesorderline",
            name="channel_key",
            field=models.TextField(default=""),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="salesorderline",
            name="shop_key",
            field=models.TextField(default=""),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="salesorderline",
            name="resolved_category",
            field=models.TextField(default=""),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="salesorderline",
            name="order_identity",
            field=models.TextField(default=""),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="salesorderline",
            name="is_business_row",
            field=models.BooleanField(default=False),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="salesorderline",
            name="is_net_sales_row",
            field=models.BooleanField(default=False),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="salesorderline",
            name="is_net_quantity_row",
            field=models.BooleanField(default=False),
            preserve_default=False,
        ),
        migrations.RunPython(populate_query_ready_projection, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="salesorderline",
            name="business_date",
            field=models.DateField(),
        ),
        migrations.RemoveIndex(model_name="salesorderline", name="sales_line_ship_idx"),
        migrations.RemoveIndex(model_name="salesorderline", name="sales_line_sale_idx"),
        migrations.RemoveIndex(model_name="salesorderline", name="sales_line_channel_idx"),
        migrations.RemoveIndex(model_name="salesorderline", name="sales_line_platform_idx"),
        migrations.RemoveIndex(model_name="salesorderline", name="sales_line_product_idx"),
        migrations.AddIndex(
            model_name="salesorderline",
            index=models.Index(
                condition=models.Q(("is_business_row", True)),
                fields=["business_date"],
                name="sales_biz_date_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="salesorderline",
            index=models.Index(
                condition=models.Q(("is_business_row", True)),
                fields=["platform_key", "shop_key", "business_date"],
                name="sales_platform_shop_date_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="salesorderline",
            index=models.Index(
                condition=models.Q(("is_business_row", True)),
                fields=["channel_key", "business_date"],
                name="sales_channel_date_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="salesorderline",
            index=models.Index(
                condition=models.Q(("is_business_row", True)),
                fields=["resolved_category", "business_date"],
                name="sales_category_date_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="salesorderline",
            index=models.Index(
                condition=models.Q(("is_business_row", True)),
                fields=["product_code", "business_date"],
                name="sales_product_date_idx",
            ),
        ),
    ]
