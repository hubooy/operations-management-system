"""Versioned, bounded deterministic retrieval from the AI knowledge authority."""

import json
import re
from . import models as m
from .policy import AiError, canonical, integer, record, text


def rank(query, principal):
    normalized = re.sub(r"\s+", " ", query.lower()).strip()
    segments = [
        v for v in re.split(r"[\s,，。；;：:、/|()\[\]{}]+", normalized) if len(v) >= 2
    ]
    terms = list(
        dict.fromkeys(
            [
                *segments,
                *(
                    v[i : i + 2]
                    for v in segments
                    if len(v) > 4
                    for i in range(len(v) - 1)
                ),
            ]
        )
    )[:20]
    results = []
    rows = m.AiKnowledgeEntries.objects.filter(status="active").order_by(
        "-updated_at", "id"
    )[:100]
    for row in rows:
        if principal.role not in json.loads(row.allowed_roles_json):
            continue
        if len(row.content) > 16000 or len(row.title) > 240:
            raise AiError("知识条目超过有界契约", "service_unavailable", 503)
        tags = json.loads(row.tags_json)[:12]
        title, content = row.title.lower(), row.content.lower()
        tags = [tag.lower() for tag in tags if isinstance(tag, str)]
        score = (
            (120 if title == normalized else 80 if normalized in title else 0)
            + (70 if normalized in tags else 0)
            + (50 if normalized in content else 0)
        )
        for term in terms:
            score += (
                (18 if term in title else 0)
                + (14 if any(term in tag or tag in term for tag in tags) else 0)
                + (5 if term in content else 0)
            )
        if score >= 12:
            results.append((row, score))
    return sorted(results, key=lambda entry: -entry[1])


def search(query, principal, limit=4):
    query = text(query, "query", 80)
    if len(query) < 2:
        raise AiError("知识 query 至少 2 个字符")
    limit = integer(limit, "limit", 1, 8)
    ranked = rank(query, principal)
    items = []
    for row, score in ranked[:limit]:
        item = record(
            row,
            "id source_type source_ref title tags_json version content_digest updated_at",
            json_fields={"tags_json"},
        )
        start = max(0, row.content.lower().find(query.lower()) - 80)
        item.update(
            excerpt=("…" if start else "")
            + row.content[start : start + 600]
            + ("…" if start + 600 < len(row.content) else ""),
            score=score,
        )
        items.append(item)
    return {
        "query": query,
        "matchMode": "deterministic_lexical",
        "filtersApplied": {"role": principal.role, "status": "active"},
        "totalMatched": len(ranked),
        "returned": len(items),
        "truncated": len(ranked) > len(items),
        "items": items,
    }


def context(query, principal):
    if len(query.strip()) < 2:
        return ""
    blocks = []
    for row, _ in rank(query[:80], principal)[:4]:
        item = dict(
            id=row.id, source=row.source_ref, title=row.title, content=row.content
        )
        encoded = canonical(item).replace("<", "\\u003c")
        if sum(len(v) for v in blocks) + len(encoded) > 3000:
            break
        blocks.append(encoded)
    return "\n<knowledge>" + "\n".join(blocks) + "</knowledge>" if blocks else ""
