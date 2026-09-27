from django.conf import settings
from django.urls import path

from . import views
from .read_health import endpoint as read_health
from .analysis_continuation import continuation


read_patterns = [
    path("health/reads", read_health, name="market-read-health"),
    path("analysis-records/continuation", continuation, name="market-analysis-continuation"),
    path("analysis-options", views.analysis_options, name="market-analysis-options"),
    path("queries", views.queries, name="market-queries"),
    path("consumers/query", views.consumers, name="market-consumers"),
]
write_patterns = [
    path("commands", views.commands, name="market-commands"),
    path("imports", views.imports, name="market-imports"),
]

urlpatterns = []
if settings.DJANGO_PROCESS_ROLE == "market_reader":
    urlpatterns.extend(read_patterns)
elif settings.DJANGO_PROCESS_ROLE == "market_writer":
    urlpatterns.extend(write_patterns)
elif settings.DJANGO_PROCESS_ROLE == "development":
    urlpatterns.extend(read_patterns)
    urlpatterns.extend(write_patterns)
