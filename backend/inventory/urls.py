from django.conf import settings
from django.urls import path

from . import views
from . import guangdong_views as gd

read_patterns = [
    path("guangdong-monitor", gd.monitor),
    path("guangdong-monitor/watchlist", gd.watchlist),
    path("guangdong-monitor/products", gd.products),
    path("guangdong-monitor/preview", gd.preview),
    path("guangdong-monitor/suppliers", gd.suppliers),
    path("guangdong-monitor/export", gd.export),
    path("overview", views.overview, name="inventory-overview"),
    path("age-analysis", views.age_analysis, name="inventory-age-analysis"),
    path("inbound-monitor", views.inbound_monitor, name="inventory-inbound-monitor"),
    path("imports", views.imports, name="inventory-imports"),
    path("replenishment", views.replenishment, name="inventory-replenishment"),
    path("settings", views.settings_view, name="inventory-settings"),
    path("warehouse-mappings", views.warehouse_mappings, name="inventory-warehouse-mappings"),
    path("consumers/query", views.consumer_query, name="inventory-consumer-query"),
]
write_patterns = [
    path("guangdong-monitor/import", gd.imports),
    path("guangdong-monitor/items", gd.items),
    path("guangdong-monitor/suppliers", gd.suppliers),
    path("imports", views.imports, name="inventory-imports"),
    path("uploads", views.uploads, name="inventory-uploads"),
    path("uploads/chunk", views.upload_chunk, name="inventory-upload-chunk"),
    path("replenishment", views.replenishment, name="inventory-replenishment"),
    path("replenishment/import", views.replenishment_import, name="inventory-replenishment-import"),
    path("replenishment/dingtalk", views.replenishment_dingtalk, name="inventory-replenishment-dingtalk"),
    path("replenishment/dingtalk/group", views.replenishment_dingtalk_group, name="inventory-replenishment-dingtalk-group"),
    path("settings", views.settings_view, name="inventory-settings"),
    path("warehouse-mappings", views.warehouse_mappings, name="inventory-warehouse-mappings"),
]

urlpatterns: list[object] = []
if settings.DJANGO_PROCESS_ROLE == "inventory_reader":
    urlpatterns.extend(read_patterns)
elif settings.DJANGO_PROCESS_ROLE == "inventory_writer":
    urlpatterns.extend(write_patterns)
elif settings.DJANGO_PROCESS_ROLE == "development":
    urlpatterns.extend(read_patterns + write_patterns)
