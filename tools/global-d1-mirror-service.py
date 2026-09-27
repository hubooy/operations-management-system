"""Relocate only network coordinates for the isolated full-stack mirror."""
import os
from pathlib import Path
import socket
import sys
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
run_root = Path(os.environ["TERUISI_MIRROR_ROOT"]).resolve()
database = urlparse(os.environ["TERUISI_DJANGO_DATABASE_URL"])
port = int(os.environ["TERUISI_MIRROR_HTTP_PORT"])
if (not run_root.is_relative_to((ROOT / ".runtime").resolve())
    or not run_root.name.startswith("global-d1-mirror-")
    or ROOT == Path(r"D:\运营管理系统")
    or database.hostname != "127.0.0.1" or database.port != 55444
    or database.path != "/teruisi_sales" or not database.password
    or os.environ.get("TERUISI_DJANGO_SALES_READER_BASE_URL") != "http://127.0.0.1:18001"
    or port not in {18000 + 10 * domain + offset for domain in range(12) for offset in (1, 2)}
    or os.environ.get("TERUISI_DJANGO_ENVIRONMENT") != "test"):
    raise RuntimeError("mirror_service_coordinate_guard_failed")
sys.path.insert(0, str(ROOT / "backend"))
import django
django.setup()
from django.conf import settings
# Production business/permission gates remain enabled. Only the already
# validated independent database port differs from production settings.
settings.DJANGO_ENVIRONMENT = "production"
original_connect = socket.socket.connect
# Waitress creates a private loopback socket pair on Windows. Build it before
# closing application egress; its trigger is not a business HTTP connection.
from waitress import create_server
from teruisi_backend.wsgi import application
server = create_server(application, host="127.0.0.1", port=port, threads=2, connection_limit=16)
def mirror_connect(self, address):
    if not isinstance(address, tuple) or address[0] != "127.0.0.1" or address[1] not in {55444, 18001}:
        raise OSError("mirror_external_network_disabled")
    return original_connect(self, address)
socket.socket.connect = mirror_connect
server.run()
