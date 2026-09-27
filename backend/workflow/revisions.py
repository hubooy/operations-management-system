from __future__ import annotations

import hashlib
import json

from django.db import transaction

from .models import WorkflowDataRevision


def revision_value(*, for_update: bool = False) -> str:
    query = WorkflowDataRevision.objects
    if for_update:
        query = query.select_for_update()
    row = query.get(domain="workflow")
    digest = (row.source_digest or "0" * 64)[:12]
    return f"{int(row.revision)}:{digest}"


def bump_revision(reason: object) -> str:
    with transaction.atomic():
        row = WorkflowDataRevision.objects.select_for_update().get(domain="workflow")
        payload = json.dumps(
            {"previous": int(row.revision), "reason": reason},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        row.revision = int(row.revision) + 1
        row.source_digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        row.save(update_fields=["revision", "source_digest", "updated_at"])
        return f"{row.revision}:{row.source_digest[:12]}"
