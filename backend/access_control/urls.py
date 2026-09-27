from django.conf import settings
from django.urls import path

from . import views


reader_patterns = [
    path("principal/resolve", views.principal_resolve, name="access-principal-resolve"),
    path("principal/authorize-background", views.background_authorize, name="access-background-authorize"),
    path("roles", views.roles, name="access-roles"),
    path("users", views.users, name="access-users"),
    path("audits", views.audits, name="access-audits"),
]
writer_patterns = [
    path("users", views.users, name="access-users-write"),
]

urlpatterns = []
if settings.DJANGO_PROCESS_ROLE == "access_control_reader":
    urlpatterns.extend(reader_patterns)
elif settings.DJANGO_PROCESS_ROLE == "access_control_writer":
    urlpatterns.extend(writer_patterns)
elif settings.DJANGO_PROCESS_ROLE == "development":
    urlpatterns.extend(reader_patterns + writer_patterns)
