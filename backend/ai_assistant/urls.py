from django.conf import settings
from django.urls import re_path
from .views import dispatch

urlpatterns = []
if settings.DJANGO_PROCESS_ROLE in {"development", "ai_reader", "ai_writer"}:
    urlpatterns = [re_path(r"^(?P<path>[A-Za-z0-9_/-]+)$", dispatch)]
