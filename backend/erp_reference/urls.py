from django.conf import settings
from django.urls import path

from . import views


read_patterns = [
    path("imports", views.imports, name="erp-imports"),
    path("consumers/query", views.consumer_query, name="erp-consumer-query"),
]
write_patterns = [
    path("imports", views.imports, name="erp-imports"),
    path("uploads", views.uploads, name="erp-uploads"),
    path("uploads/chunk", views.upload_chunk, name="erp-upload-chunk"),
]

urlpatterns: list[object] = []
if settings.DJANGO_PROCESS_ROLE == "erp_reference_reader":
    urlpatterns.extend(read_patterns)
elif settings.DJANGO_PROCESS_ROLE == "erp_reference_writer":
    urlpatterns.extend(write_patterns)
elif settings.DJANGO_PROCESS_ROLE == "development":
    urlpatterns.extend(read_patterns + write_patterns)
