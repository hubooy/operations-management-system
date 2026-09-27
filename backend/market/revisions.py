from __future__ import annotations

import hashlib
import json

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .errors import MarketApiError
from .models import MarketDataRevision, MarketWriteAuthority


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def revision_value() -> str:
    row = MarketDataRevision.objects.filter(domain="market").values(
        "revision", "source_digest"
    ).first()
    if row is None or int(row["revision"]) < 0:
        raise MarketApiError(
            "市场数据版本尚未就绪", code="service_unavailable", status=503
        )
    digest = str(row["source_digest"] or "")
    if len(digest) != 64:
        raise MarketApiError(
            "市场数据版本尚未就绪", code="service_unavailable", status=503
        )
    return f"{int(row['revision'])}:{digest[:12]}"


def bump_revision(event: object) -> str:
    row = MarketDataRevision.objects.select_for_update().get(domain="market")
    next_revision = int(row.revision) + 1
    row.revision = next_revision
    row.source_digest = sha256_text(
        canonical_json(
            {
                "domain": "market",
                "previous": row.source_digest,
                "revision": next_revision,
                "event": event,
            }
        )
    )
    row.save(update_fields=["revision", "source_digest", "updated_at"])
    return f"{next_revision}:{row.source_digest[:12]}"


def assert_write_authority() -> MarketWriteAuthority:
    try:
        # The business writer has deliberately read-only access to the terminal
        # authority receipt.  Only migration_writer may ever mutate that row;
        # SELECT ... FOR UPDATE would incorrectly require UPDATE privilege and
        # weaken the least-privilege boundary just to acquire a useless lock.
        authority = MarketWriteAuthority.objects.get(id=1)
    except MarketWriteAuthority.DoesNotExist as error:
        raise MarketApiError(
            "市场写入所有权尚未配置", code="service_unavailable", status=503
        ) from error
    if authority.status != "postgres":
        raise MarketApiError(
            "市场 PostgreSQL 写入所有权尚未激活",
            code="service_unavailable",
            status=503,
        )
    if (
        str(authority.authority_epoch or "") != settings.MARKET_WRITE_AUTHORITY_EPOCH
        or authority.cutover_id != settings.MARKET_WRITE_CUTOVER_ID
    ):
        raise MarketApiError(
            "市场写入所有权与当前进程不一致",
            code="service_unavailable",
            status=503,
        )
    return authority


def iso(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat().replace("+00:00", "Z")
    return str(value)


@transaction.atomic
def set_migration_revision(*, revision: int, digest: str) -> None:
    if revision < 0 or len(digest) != 64:
        raise MarketApiError("市场迁移 revision 无效")
    row = MarketDataRevision.objects.select_for_update().get(domain="market")
    row.revision = revision
    row.source_digest = digest
    row.updated_at = timezone.now()
    row.save(update_fields=["revision", "source_digest", "updated_at"])
