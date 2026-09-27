from __future__ import annotations

from sales.models import SalesDataRevision


def revision_value(*, for_update: bool = False) -> str:
    query = SalesDataRevision.objects
    if for_update:
        query = query.select_for_update()
    row = query.get(domain="erp")
    digest = (row.source_digest or "0" * 64)[:12]
    return f"{int(row.revision)}:{digest}"
