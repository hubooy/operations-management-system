from django.conf import settings
from django.urls import path

from . import views


read_patterns = [
    path("summary", views.summary, name="products-summary"),
    path("imports", views.imports, name="products-imports"),
    path("consumers/query", views.consumer_query, name="products-consumer-query"),
]
write_patterns = [
    path("imports", views.imports, name="products-imports"),
    path("uploads", views.uploads, name="products-uploads"),
    path("uploads/chunk", views.upload_chunk, name="products-upload-chunk"),
    path("inventory-projection", views.inventory_projection, name="products-inventory-projection"),
]

urlpatterns = []
if settings.DJANGO_PROCESS_ROLE == "products_reader":
    urlpatterns.extend(read_patterns)
elif settings.DJANGO_PROCESS_ROLE == "products_writer":
    urlpatterns.extend(write_patterns)
elif settings.DJANGO_PROCESS_ROLE == "development":
    urlpatterns.extend(read_patterns + write_patterns)

