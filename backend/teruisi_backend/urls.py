from django.urls import include, path

from . import health
from system_datasets.reader import endpoint as dataset_records

urlpatterns = [
    path("api/ai/dataset-records", dataset_records),
    path("health/live", health.live, name="health-live"),
    path("health/ready", health.ready, name="health-ready"),
    path("api/sales/", include("sales.urls")),
    path("api/erp-reference/", include("erp_reference.urls")),
    path("api/finance/", include("finance.urls")),
    path("api/netshop/", include("netshop.urls")),
    path("api/market/", include("market.urls")),
    path("api/products/", include("products.urls")),
    path("api/inventory/", include("inventory.urls")),
    path("api/workflow/", include("workflow.urls")),
    path("api/customer-service/", include("customer_service.urls")),
    path("api/bi/", include("bi.urls")),
    path("api/access-control/", include("access_control.urls")),
    path("api/ai/", include("ai_assistant.urls")),
]
