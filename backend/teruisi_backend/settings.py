"""Environment-driven settings for the API-only TERUISI backend."""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


BASE_DIR = Path(__file__).resolve().parent.parent

# Opt-in metadata-only n8n reader. Never falls back to a user's home/profile.
N8N_STATUS_DATABASE_PATH = os.getenv("TERUISI_N8N_STATUS_DATABASE_PATH", "")


TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}
SECRET_PLACEHOLDERS = {
    "unsafe-local-development-key",
    "replace-me",
    "replace-with-at-least-32-random-bytes",
    "change-me",
    "changeme",
    "placeholder",
}


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise RuntimeError(f"{name} 必须是明确的布尔值")


def env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as error:
        raise RuntimeError(f"{name} 必须是整数") from error
    if value < minimum or value > maximum:
        raise RuntimeError(f"{name} 必须在 {minimum} 到 {maximum} 之间")
    return value


def validate_secret(name: str, value: str, *, required: bool) -> str:
    """Fail closed on missing, short, or documented placeholder secrets."""

    if not required and not value:
        return value
    normalized = value.strip()
    if (
        len(normalized.encode("utf-8")) < 32
        or normalized.lower() in SECRET_PLACEHOLDERS
        or len(set(normalized)) < 4
        or re.fullmatch(
            r"(?:replace|change|example|sample|test|secret|password)[-_a-z0-9]*",
            normalized,
            re.I,
        )
    ):
        raise RuntimeError(f"{name} 必须至少 32 字节且不能使用占位值")
    return value


def database_from_url(value: str) -> dict[str, object]:
    parsed = urlparse(value)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise RuntimeError("TERUISI_DJANGO_DATABASE_URL 仅支持 postgresql://")
    options = {key: values[-1] for key, values in parse_qs(parsed.query).items() if values}
    configuration = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(parsed.path.lstrip("/")),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "",
        "PORT": str(parsed.port or ""),
        "CONN_MAX_AGE": env_int("TERUISI_DJANGO_DB_CONN_MAX_AGE", 60, 0, 600),
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": options,
    }
    if DJANGO_ENVIRONMENT == "production":
        if (
            not configuration["NAME"]
            or not configuration["USER"]
            or not configuration["PASSWORD"]
            or configuration["HOST"] != "127.0.0.1"
            or configuration["PORT"] != "5432"
        ):
            raise RuntimeError("生产 Django 数据库必须固定为带凭据的 127.0.0.1:5432 PostgreSQL")
    return configuration


DJANGO_ENVIRONMENT = os.getenv("TERUISI_DJANGO_ENVIRONMENT", "development").strip().lower()
if DJANGO_ENVIRONMENT not in {"development", "test", "production"}:
    raise RuntimeError("TERUISI_DJANGO_ENVIRONMENT 仅支持 development、test 或 production")

database_url = os.getenv("TERUISI_DJANGO_DATABASE_URL", "").strip()
DEBUG = env_bool("DJANGO_DEBUG", default=not bool(database_url))
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "unsafe-local-development-key")
DJANGO_INTERNAL_SECRET = os.getenv("TERUISI_DJANGO_INTERNAL_SECRET", "")
if DJANGO_ENVIRONMENT == "production" and (DEBUG or not database_url):
    raise RuntimeError("生产 Django 必须关闭 DEBUG 并显式配置 PostgreSQL")
if not DEBUG:
    SECRET_KEY = validate_secret("DJANGO_SECRET_KEY", SECRET_KEY, required=True)
    DJANGO_INTERNAL_SECRET = validate_secret(
        "TERUISI_DJANGO_INTERNAL_SECRET", DJANGO_INTERNAL_SECRET, required=True
    )
ALLOWED_HOSTS = [
    item.strip()
    for item in os.getenv(
        "DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost,testserver"
    ).split(",")
    if item.strip()
]
if DJANGO_ENVIRONMENT == "production" and (
    not ALLOWED_HOSTS or not set(ALLOWED_HOSTS).issubset({"127.0.0.1", "localhost"})
):
    raise RuntimeError("生产 Django ALLOWED_HOSTS 只能包含 127.0.0.1 和 localhost")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "ai_assistant.apps.AiAssistantConfig",
    "access_control.apps.AccessControlConfig",
    "sales.apps.SalesConfig",
    "erp_reference.apps.ErpReferenceConfig",
    "finance.apps.FinanceConfig",
    "netshop.apps.NetshopConfig",
    "market.apps.MarketConfig",
    "products.apps.ProductsConfig",
    "inventory.apps.InventoryConfig",
    "workflow.apps.WorkflowConfig",
    "customer_service.apps.CustomerServiceConfig",
    "bi.apps.BiConfig",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "teruisi_backend.security.LoopbackOnlyMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "teruisi_backend.urls"
TEMPLATES: list[dict[str, object]] = []
WSGI_APPLICATION = "teruisi_backend.wsgi.application"
ASGI_APPLICATION = "teruisi_backend.asgi.application"

if database_url:
    DATABASES = {"default": database_from_url(database_url)}
else:
    sqlite_path = Path(
        os.getenv(
            "TERUISI_DJANGO_SQLITE_PATH",
            BASE_DIR.parent / ".runtime" / "django" / "teruisi.sqlite3",
        )
    )
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": sqlite_path,
            "OPTIONS": {"timeout": 30},
        }
    }

if DJANGO_ENVIRONMENT == "test" and database_url:
    isolated = DATABASES["default"]
    if (isolated["HOST"] == "127.0.0.1" and
            isolated["NAME"] == "teruisi_ai_rehearsal" and
            55440 <= int(isolated["PORT"]) <= 55999):
        TEST_RUNNER = "teruisi_backend.isolated_test_runner.IsolatedPostgresTestRunner"

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
SALES_READ_CACHE_SECONDS = env_int("TERUISI_DJANGO_SALES_CACHE_SECONDS", 60, 0, 3600)
ERP_REFERENCE_SYNC_MAX_AGE_SECONDS = env_int(
    "TERUISI_DJANGO_ERP_SYNC_MAX_AGE_SECONDS", 60, 30, 3600
)
DJANGO_EXPECT_READ_ONLY = env_bool("TERUISI_DJANGO_EXPECT_READ_ONLY", False)
DJANGO_PROCESS_ROLE = os.getenv("TERUISI_DJANGO_PROCESS_ROLE", "development").strip().lower()
AI_MARKET_V2_PREVIEW_ENABLED = env_bool(
    "TERUISI_DJANGO_AI_MARKET_V2_PREVIEW_ENABLED", False
)
AI_MARKET_V2_AGENT_RUNTIME_ENABLED = env_bool(
    "TERUISI_DJANGO_AI_MARKET_V2_AGENT_RUNTIME_ENABLED", False
)
# Separate from the read-only five-tool surface.  The 0065 cost row has no
# spendable reservation; enabling this flag alone never permits a model call.
AI_MARKET_V2_PAID_RUNTIME_ENABLED = env_bool(
    "TERUISI_DJANGO_AI_MARKET_V2_PAID_RUNTIME_ENABLED", False
)
SALES_WRITE_AUTHORITY_EPOCH = os.getenv(
    "TERUISI_DJANGO_SALES_AUTHORITY_EPOCH", ""
).strip()
SALES_WRITE_CUTOVER_ID = os.getenv("TERUISI_DJANGO_SALES_CUTOVER_ID", "").strip()
ERP_WRITE_AUTHORITY_EPOCH = os.getenv(
    "TERUISI_DJANGO_ERP_AUTHORITY_EPOCH", ""
).strip()
ERP_WRITE_CUTOVER_ID = os.getenv("TERUISI_DJANGO_ERP_CUTOVER_ID", "").strip()
FINANCE_WRITE_AUTHORITY_EPOCH = os.getenv(
    "TERUISI_DJANGO_FINANCE_AUTHORITY_EPOCH", ""
).strip()
FINANCE_WRITE_CUTOVER_ID = os.getenv(
    "TERUISI_DJANGO_FINANCE_CUTOVER_ID", ""
).strip()
NETSHOP_WRITE_AUTHORITY_EPOCH = os.getenv(
    "TERUISI_DJANGO_NETSHOP_AUTHORITY_EPOCH", ""
).strip()
NETSHOP_WRITE_CUTOVER_ID = os.getenv(
    "TERUISI_DJANGO_NETSHOP_CUTOVER_ID", ""
).strip()
MARKET_WRITE_AUTHORITY_EPOCH = os.getenv(
    "TERUISI_DJANGO_MARKET_AUTHORITY_EPOCH", ""
).strip()
MARKET_WRITE_CUTOVER_ID = os.getenv(
    "TERUISI_DJANGO_MARKET_CUTOVER_ID", ""
).strip()
PRODUCTS_WRITE_AUTHORITY_EPOCH = os.getenv(
    "TERUISI_DJANGO_PRODUCTS_AUTHORITY_EPOCH", ""
).strip()
PRODUCTS_WRITE_CUTOVER_ID = os.getenv(
    "TERUISI_DJANGO_PRODUCTS_CUTOVER_ID", ""
).strip()
INVENTORY_WRITE_AUTHORITY_EPOCH = os.getenv(
    "TERUISI_DJANGO_INVENTORY_AUTHORITY_EPOCH", ""
).strip()
INVENTORY_WRITE_CUTOVER_ID = os.getenv(
    "TERUISI_DJANGO_INVENTORY_CUTOVER_ID", ""
).strip()
WORKFLOW_WRITE_AUTHORITY_EPOCH = os.getenv(
    "TERUISI_DJANGO_WORKFLOW_AUTHORITY_EPOCH", ""
).strip()
WORKFLOW_WRITE_CUTOVER_ID = os.getenv(
    "TERUISI_DJANGO_WORKFLOW_CUTOVER_ID", ""
).strip()
WORKFLOW_OPERATIONS_WRITE_AUTHORITY_EPOCH = os.getenv(
    "TERUISI_DJANGO_WORKFLOW_OPERATIONS_AUTHORITY_EPOCH", ""
).strip()
WORKFLOW_OPERATIONS_WRITE_CUTOVER_ID = os.getenv(
    "TERUISI_DJANGO_WORKFLOW_OPERATIONS_CUTOVER_ID", ""
).strip()
CUSTOMER_SERVICE_WRITE_AUTHORITY_EPOCH = os.getenv(
    "TERUISI_DJANGO_CUSTOMER_SERVICE_AUTHORITY_EPOCH", ""
).strip()
CUSTOMER_SERVICE_WRITE_CUTOVER_ID = os.getenv(
    "TERUISI_DJANGO_CUSTOMER_SERVICE_CUTOVER_ID", ""
).strip()
ACCESS_CONTROL_WRITE_AUTHORITY_EPOCH = os.getenv(
    "TERUISI_DJANGO_ACCESS_CONTROL_AUTHORITY_EPOCH", ""
).strip()
ACCESS_CONTROL_WRITE_CUTOVER_ID = os.getenv(
    "TERUISI_DJANGO_ACCESS_CONTROL_CUTOVER_ID", ""
).strip()
AI_WRITE_AUTHORITY_EPOCH = os.getenv("TERUISI_DJANGO_AI_AUTHORITY_EPOCH", "").strip()
AI_WRITE_CUTOVER_ID = os.getenv("TERUISI_DJANGO_AI_CUTOVER_ID", "").strip()
if DJANGO_ENVIRONMENT == "production" and DJANGO_PROCESS_ROLE not in {
    "reader",
    "migration_writer",
    "sales_writer",
    "erp_reference_reader",
    "erp_reference_writer",
    "finance_reader",
    "finance_writer",
    "netshop_reader",
    "netshop_writer",
    "market_reader",
    "market_writer",
    "products_reader",
    "products_writer",
    "inventory_reader",
    "inventory_writer",
    "workflow_reader",
    "workflow_writer",
    "customer_service_reader",
    "customer_service_writer",
    "bi_reader",
    "access_control_reader",
    "access_control_writer",
    "ai_reader",
    "ai_writer",
}:
    raise RuntimeError(
        "生产 Django 必须显式声明已登记的 reader、writer、migration_writer 或同步进程角色"
    )
if DJANGO_PROCESS_ROLE == "reader" and not DJANGO_EXPECT_READ_ONLY:
    raise RuntimeError("Django reader 进程必须启用只读连接门禁")
if DJANGO_PROCESS_ROLE == "ai_reader" and not DJANGO_EXPECT_READ_ONLY:
    raise RuntimeError("Django ai_reader 必须使用只读连接")
if DJANGO_PROCESS_ROLE in {"ai_reader", "ai_writer"}:
    if DJANGO_PROCESS_ROLE == "ai_writer" and DJANGO_EXPECT_READ_ONLY:
        raise RuntimeError("Django ai_writer 不能使用只读连接")
    try:
        uuid.UUID(AI_WRITE_AUTHORITY_EPOCH)
    except ValueError as error:
        raise RuntimeError("Django ai_writer 缺少有效 authority epoch") from error
    if not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", AI_WRITE_CUTOVER_ID):
        raise RuntimeError("Django ai_writer 缺少有效 cutover id")
if DJANGO_PROCESS_ROLE == "sales_writer" and DJANGO_EXPECT_READ_ONLY:
    raise RuntimeError("Django sales_writer 进程不能使用只读连接")
if DJANGO_PROCESS_ROLE == "erp_reference_reader" and not DJANGO_EXPECT_READ_ONLY:
    raise RuntimeError("Django erp_reference_reader 进程必须启用只读连接门禁")
if DJANGO_PROCESS_ROLE == "erp_reference_writer" and DJANGO_EXPECT_READ_ONLY:
    raise RuntimeError("Django erp_reference_writer 进程不能使用只读连接")
if DJANGO_PROCESS_ROLE == "finance_reader" and not DJANGO_EXPECT_READ_ONLY:
    raise RuntimeError("Django finance_reader 进程必须启用只读连接门禁")
if DJANGO_PROCESS_ROLE == "finance_writer" and DJANGO_EXPECT_READ_ONLY:
    raise RuntimeError("Django finance_writer 进程不能使用只读连接")
if DJANGO_PROCESS_ROLE == "netshop_reader" and not DJANGO_EXPECT_READ_ONLY:
    raise RuntimeError("Django netshop_reader 进程必须启用只读连接门禁")
if DJANGO_PROCESS_ROLE == "netshop_writer" and DJANGO_EXPECT_READ_ONLY:
    raise RuntimeError("Django netshop_writer 进程不能使用只读连接")
if DJANGO_PROCESS_ROLE == "market_reader" and not DJANGO_EXPECT_READ_ONLY:
    raise RuntimeError("Django market_reader 进程必须启用只读连接门禁")
if DJANGO_PROCESS_ROLE == "market_writer" and DJANGO_EXPECT_READ_ONLY:
    raise RuntimeError("Django market_writer 进程不能使用只读连接")
if DJANGO_PROCESS_ROLE == "products_reader" and not DJANGO_EXPECT_READ_ONLY:
    raise RuntimeError("Django products_reader 进程必须启用只读连接门禁")
if DJANGO_PROCESS_ROLE == "products_writer" and DJANGO_EXPECT_READ_ONLY:
    raise RuntimeError("Django products_writer 进程不能使用只读连接")
if DJANGO_PROCESS_ROLE == "inventory_reader" and not DJANGO_EXPECT_READ_ONLY:
    raise RuntimeError("Django inventory_reader 进程必须启用只读连接门禁")
if DJANGO_PROCESS_ROLE == "inventory_writer" and DJANGO_EXPECT_READ_ONLY:
    raise RuntimeError("Django inventory_writer 进程不能使用只读连接")
if DJANGO_PROCESS_ROLE == "workflow_reader" and not DJANGO_EXPECT_READ_ONLY:
    raise RuntimeError("Django workflow_reader 进程必须启用只读连接门禁")
if DJANGO_PROCESS_ROLE == "workflow_writer" and DJANGO_EXPECT_READ_ONLY:
    raise RuntimeError("Django workflow_writer 进程不能使用只读连接")
if DJANGO_PROCESS_ROLE == "customer_service_reader" and not DJANGO_EXPECT_READ_ONLY:
    raise RuntimeError("Django customer_service_reader 进程必须启用只读连接门禁")
if DJANGO_PROCESS_ROLE == "customer_service_writer" and DJANGO_EXPECT_READ_ONLY:
    raise RuntimeError("Django customer_service_writer 进程不能使用只读连接")
if DJANGO_PROCESS_ROLE == "bi_reader" and not DJANGO_EXPECT_READ_ONLY:
    raise RuntimeError("Django bi_reader 进程必须启用只读连接门禁")
if DJANGO_PROCESS_ROLE == "access_control_reader" and not DJANGO_EXPECT_READ_ONLY:
    raise RuntimeError("Django access_control_reader 进程必须启用只读连接门禁")
if DJANGO_PROCESS_ROLE == "access_control_writer" and DJANGO_EXPECT_READ_ONLY:
    raise RuntimeError("Django access_control_writer 进程不能使用只读连接")
if DJANGO_PROCESS_ROLE == "sales_writer":
    try:
        uuid.UUID(SALES_WRITE_AUTHORITY_EPOCH)
    except (ValueError, AttributeError) as error:
        raise RuntimeError("Django sales_writer 必须配置有效的销售 authority epoch") from error
    if not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", SALES_WRITE_CUTOVER_ID):
        raise RuntimeError("Django sales_writer 必须配置有效的销售 cutover id")
if DJANGO_PROCESS_ROLE == "erp_reference_writer":
    try:
        uuid.UUID(ERP_WRITE_AUTHORITY_EPOCH)
    except (ValueError, AttributeError) as error:
        raise RuntimeError("Django erp_reference_writer 必须配置有效的 ERP authority epoch") from error
    if not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", ERP_WRITE_CUTOVER_ID):
        raise RuntimeError("Django erp_reference_writer 必须配置有效的 ERP cutover id")
if DJANGO_PROCESS_ROLE == "finance_writer":
    try:
        uuid.UUID(FINANCE_WRITE_AUTHORITY_EPOCH)
    except (ValueError, AttributeError) as error:
        raise RuntimeError("Django finance_writer 必须配置有效的财务 authority epoch") from error
    if not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", FINANCE_WRITE_CUTOVER_ID):
        raise RuntimeError("Django finance_writer 必须配置有效的财务 cutover id")
if DJANGO_PROCESS_ROLE == "netshop_writer":
    try:
        uuid.UUID(NETSHOP_WRITE_AUTHORITY_EPOCH)
    except (ValueError, AttributeError) as error:
        raise RuntimeError("Django netshop_writer 必须配置有效的网店 authority epoch") from error
    if not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", NETSHOP_WRITE_CUTOVER_ID):
        raise RuntimeError("Django netshop_writer 必须配置有效的网店 cutover id")
if DJANGO_PROCESS_ROLE == "market_writer":
    try:
        uuid.UUID(MARKET_WRITE_AUTHORITY_EPOCH)
    except (ValueError, AttributeError) as error:
        raise RuntimeError("Django market_writer 必须配置有效的市场 authority epoch") from error
    if not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", MARKET_WRITE_CUTOVER_ID):
        raise RuntimeError("Django market_writer 必须配置有效的市场 cutover id")
if DJANGO_PROCESS_ROLE == "products_writer":
    try:
        uuid.UUID(PRODUCTS_WRITE_AUTHORITY_EPOCH)
    except (ValueError, AttributeError) as error:
        raise RuntimeError("Django products_writer 必须配置有效的商品经营 authority epoch") from error
    if not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", PRODUCTS_WRITE_CUTOVER_ID):
        raise RuntimeError("Django products_writer 必须配置有效的商品经营 cutover id")
if DJANGO_PROCESS_ROLE == "inventory_writer":
    try:
        uuid.UUID(INVENTORY_WRITE_AUTHORITY_EPOCH)
    except (ValueError, AttributeError) as error:
        raise RuntimeError("Django inventory_writer 必须配置有效的库存 authority epoch") from error
    if not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", INVENTORY_WRITE_CUTOVER_ID):
        raise RuntimeError("Django inventory_writer 必须配置有效的库存 cutover id")
if DJANGO_PROCESS_ROLE == "workflow_writer":
    try:
        uuid.UUID(WORKFLOW_WRITE_AUTHORITY_EPOCH)
    except (ValueError, AttributeError) as error:
        raise RuntimeError("Django workflow_writer 必须配置有效的运营事务 authority epoch") from error
    if not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", WORKFLOW_WRITE_CUTOVER_ID):
        raise RuntimeError("Django workflow_writer 必须配置有效的运营事务 cutover id")
    try:
        uuid.UUID(WORKFLOW_OPERATIONS_WRITE_AUTHORITY_EPOCH)
    except (ValueError, AttributeError) as error:
        raise RuntimeError("Django workflow_writer 必须配置有效的运营事务全板块 authority epoch") from error
    if not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", WORKFLOW_OPERATIONS_WRITE_CUTOVER_ID):
        raise RuntimeError("Django workflow_writer 必须配置有效的运营事务全板块 cutover id")
if DJANGO_PROCESS_ROLE == "customer_service_writer":
    try:
        uuid.UUID(CUSTOMER_SERVICE_WRITE_AUTHORITY_EPOCH)
    except (ValueError, AttributeError) as error:
        raise RuntimeError("Django customer_service_writer 必须配置有效的客服 authority epoch") from error
    if not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", CUSTOMER_SERVICE_WRITE_CUTOVER_ID):
        raise RuntimeError("Django customer_service_writer 必须配置有效的客服 cutover id")
if DJANGO_PROCESS_ROLE == "access_control_writer":
    try:
        uuid.UUID(ACCESS_CONTROL_WRITE_AUTHORITY_EPOCH)
    except (ValueError, AttributeError) as error:
        raise RuntimeError("Django access_control_writer 必须配置有效的权限 authority epoch") from error
    if not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", ACCESS_CONTROL_WRITE_CUTOVER_ID):
        raise RuntimeError("Django access_control_writer 必须配置有效的权限 cutover id")
DJANGO_SIGNATURE_MAX_AGE_SECONDS = env_int(
    "TERUISI_DJANGO_SIGNATURE_MAX_AGE_SECONDS", 60, 1, 300
)
DJANGO_MAX_HEADER_BYTES = env_int("TERUISI_DJANGO_MAX_HEADER_BYTES", 32_768, 8_192, 65_536)
DJANGO_MAX_BODY_BYTES = env_int(
    "TERUISI_DJANGO_MAX_BODY_BYTES",
    67_108_864 if DJANGO_PROCESS_ROLE in {"netshop_writer", "market_writer"}
    else 67_108_864 if DJANGO_PROCESS_ROLE == "inventory_writer"
    else 16_777_216 if DJANGO_PROCESS_ROLE == "customer_service_writer"
    else 33_554_432 if DJANGO_PROCESS_ROLE == "products_writer"
    else 67_108_864 if DJANGO_PROCESS_ROLE == "erp_reference_writer"
    else 16_777_216 if DJANGO_PROCESS_ROLE == "finance_writer"
    else 8_388_608 if DJANGO_PROCESS_ROLE == "sales_writer"
    else 1_048_576,
    0,
    134_217_728,
)
FINANCE_RAW_WORKBOOK_BYTES_V2_ENABLED = env_bool(
    "TERUISI_FINANCE_RAW_WORKBOOK_BYTES_V2_ENABLED", False
)
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "teruisi-sales-read-projection",
        "OPTIONS": {"MAX_ENTRIES": 200},
    }
}

# Django is an HTTP-only loopback service behind the local edge adapter.  It
# must never infer public identity or network origin from forwarded headers.
SECURE_PROXY_SSL_HEADER = None
USE_X_FORWARDED_HOST = False
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "no-referrer"
X_FRAME_OPTIONS = "DENY"
# CSRF, HSTS and HTTPS redirects are intentionally not used on this signed
# loopback API. Mutations authenticate the method and exact body digest and
# additionally require a replay-fenced request id. The middleware above rejects
# non-loopback peers before request dispatch.
SILENCED_SYSTEM_CHECKS = ["security.W003", "security.W004", "security.W008"]
DATA_UPLOAD_MAX_MEMORY_SIZE = DJANGO_MAX_BODY_BYTES
DATA_UPLOAD_MAX_NUMBER_FIELDS = 100

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "service": {
            "format": "{asctime} level={levelname} logger={name} message={message}",
            "style": "{",
        }
    },
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "service"}},
    "root": {"handlers": ["console"], "level": os.getenv("TERUISI_DJANGO_LOG_LEVEL", "INFO")},
}
