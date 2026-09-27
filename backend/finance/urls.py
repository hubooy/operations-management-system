from django.conf import settings
from django.urls import path
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from . import views


read_patterns = [
    path("analysis", require_GET(views.analysis), name="finance-analysis"),
    path("imports", require_GET(views.imports), name="finance-imports"),
    path("targets", require_GET(views.targets), name="finance-targets"),
    path("consumers/query", require_POST(views.consumer_query), name="finance-consumer-query"),
    path("business-evidence/page", require_POST(views.business_evidence_page), name="finance-business-evidence-page"),
]
write_patterns = [
    path("imports", require_POST(views.imports), name="finance-imports"),
    path("imports/raw-attest", require_POST(views.raw_workbook_attest),
         name="finance-raw-workbook-attest"),
    path("targets/import", require_POST(views.target_import), name="finance-target-import"),
    path("targets", require_http_methods(["POST", "DELETE"])(views.targets), name="finance-targets"),
]
development_patterns = [
    path("analysis", views.analysis, name="finance-analysis"),
    path("imports", views.imports, name="finance-imports"),
    path("imports/raw-attest", views.raw_workbook_attest,
         name="finance-raw-workbook-attest"),
    path("targets/import", views.target_import, name="finance-target-import"),
    path("targets", views.targets, name="finance-targets"),
    path("consumers/query", views.consumer_query, name="finance-consumer-query"),
    path("business-evidence/page", views.business_evidence_page, name="finance-business-evidence-page"),
]

urlpatterns = []
if settings.DJANGO_PROCESS_ROLE == "finance_reader":
    urlpatterns.extend(read_patterns)
elif settings.DJANGO_PROCESS_ROLE == "finance_writer":
    urlpatterns.extend(write_patterns)
elif settings.DJANGO_PROCESS_ROLE == "development":
    urlpatterns.extend(development_patterns)
