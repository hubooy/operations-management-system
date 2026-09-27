from django.conf import settings
from django.urls import path
from django.views.decorators.http import require_GET, require_POST

from . import views
from .analysis_continuation import continuation


read_patterns = [
    path("imports", require_GET(views.imports), name="netshop-imports"),
    path("analysis-records", views.analysis_records, name="netshop-analysis-records"),
    path("analysis-records/continuation", continuation, name="netshop-analysis-continuation"),
    path("analysis-options", views.analysis_options, name="netshop-analysis-options"),
    path("overview", views.overview, name="netshop-overview"),
    path("products", views.products, name="netshop-products"),
    path("product-performance", views.product_performance, name="netshop-product-performance"),
    path("promotion-performance", views.promotion_performance, name="netshop-promotion-performance"),
    path("promotion-performance/overview", views.promotion_overview, name="netshop-promotion-overview"),
    path("promotion-diagnostic", views.promotion_diagnostic, name="netshop-promotion-diagnostic"),
    path("promotion-performance/items", views.promotion_items, name="netshop-promotion-items"),
    path("product-images/<str:content_hash>/metadata", views.product_image, name="netshop-product-image"),
    path("consumers/query", require_POST(views.consumers), name="netshop-consumers"),
]
write_patterns = [
    path("imports", require_POST(views.imports), name="netshop-imports"),
    path("asset-uploads", views.asset_uploads, name="netshop-asset-uploads"),
]

urlpatterns = []
if settings.DJANGO_PROCESS_ROLE == "netshop_reader":
    urlpatterns.extend(read_patterns)
elif settings.DJANGO_PROCESS_ROLE == "netshop_writer":
    urlpatterns.extend(write_patterns)
elif settings.DJANGO_PROCESS_ROLE == "development":
    urlpatterns.append(path("imports", views.imports, name="netshop-imports"))
    urlpatterns.extend(read_patterns[1:])
    urlpatterns.extend(write_patterns[1:])
