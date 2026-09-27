from django.conf import settings
from django.urls import path

from . import views


shared_patterns = [
    path("imports", views.imports, name="customer-service-imports"),
    path("conversations", views.conversations, name="customer-service-conversations"),
]
read_patterns = [
    path("conversations/snapshots", views.snapshots, name="customer-service-snapshots"),
    path("consumers/query", views.consumers, name="customer-service-consumers"),
]
write_patterns = [
    path("uploads", views.uploads, name="customer-service-uploads"),
    path("uploads/chunk", views.upload_chunk, name="customer-service-upload-chunk"),
]

urlpatterns: list[object] = []
if settings.DJANGO_PROCESS_ROLE == "customer_service_reader":
    urlpatterns.extend(shared_patterns + read_patterns)
elif settings.DJANGO_PROCESS_ROLE == "customer_service_writer":
    urlpatterns.extend(shared_patterns + write_patterns)
elif settings.DJANGO_PROCESS_ROLE == "development":
    urlpatterns.extend(shared_patterns + read_patterns + write_patterns)
