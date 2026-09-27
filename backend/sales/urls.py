from django.conf import settings
from django.urls import path

from . import views, write_views
from .analysis_continuation import continuation


analytics_patterns = [
    path("analysis-records/continuation", continuation, name="sales-analysis-continuation"),
    path("analysis-options", views.analysis_options, name="sales-analysis-options"),
    path("summary", views.summary, name="sales-summary"),
    path("category-analysis", views.category_analysis, name="sales-category-analysis"),
    path("category-analysis/detail", views.category_detail, name="sales-category-detail"),
    path("consumers/query", views.consumer_query, name="sales-consumer-query"),
]

reader_import_patterns = [
    path("imports", write_views.imports, name="sales-imports"),
    path(
        "imports/uploads",
        write_views.raw_upload_status,
        name="sales-raw-upload-status",
    ),
    path("imports/verify", write_views.verify_import, name="sales-import-verify"),
]

write_patterns = [
    path("imports", write_views.imports, name="sales-imports"),
    path("imports/uploads", write_views.raw_uploads, name="sales-raw-uploads"),
    path(
        "imports/uploads/chunk",
        write_views.raw_upload_chunk,
        name="sales-raw-upload-chunk",
    ),
    path("imports/staged", write_views.staged_imports, name="sales-staged-imports"),
    path("imports/verify", write_views.verify_import, name="sales-import-verify"),
]

# Production reader and writer processes expose disjoint domain surfaces.  The
# development role keeps both so contract tests and local integration stay
# convenient without weakening either production process gate.
urlpatterns = []
if settings.DJANGO_PROCESS_ROLE == "reader":
    urlpatterns.extend([*analytics_patterns, *reader_import_patterns])
elif settings.DJANGO_PROCESS_ROLE == "sales_writer":
    urlpatterns.extend(write_patterns)
elif settings.DJANGO_PROCESS_ROLE == "development":
    urlpatterns.extend([*analytics_patterns, *write_patterns])
