"""Synthetic grouping scale rehearsal, no Django DB, source APIs or model calls."""
import argparse
import json
from pathlib import Path
import sys
import time
import tracemalloc

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from business_analysis.contracts import PageReconciler, SCHEMA_VERSION, comparison_periods, coverage, digest
from business_analysis.results import build_table

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--rows", type=int, default=149841)
parser.add_argument("--without-memory-tracing", action="store_true", help="Measure elapsed time without tracemalloc overhead")
args = parser.parse_args()
if not 100 <= args.rows <= 250000:
    parser.error("rows must be within 100..250000")
n = args.rows
periods = comparison_periods("2026-09-01", "2026-09-01")


def pages():
    for start in range(1, n+1, 100):
        rows = [{"rowId": str(i), "platform": "合成平台", "shopName": "合成店", "dimensions": {"keyword": f"合成词{i:06d}"},
            "metrics": {"spendCents": i, "clicks": None if i % 7 == 0 else 1, "impressions": 100}}
            for i in range(start, min(start+100, n+1))]
        more = start+100 <= n
        yield {"schemaVersion": SCHEMA_VERSION, "sourceRef": "synthetic-fixed", "source": "synthetic", "sourceDataset": "promotion",
            "filters": {"platform": "合成平台", "shop": "合成店", "periods": periods, "window": "current"},
            "coverage": coverage(periods["current"], ["2026-09-01"]), "items": rows,
            "control": {"rowCount": n, "typedTotals": {"spendCents": n*(n+1)//2, "clicks": n-n//7, "impressions": 100*n}} if start == 1 else None,
            "pageEvidence": {"rowCount": len(rows), "sha256": digest(rows)},
            "pagination": {"hasMore": more, "nextCursor": str(start+100) if more else None}}


verifier = PageReconciler()
for page in pages():
    verifier.consume(page, request_cursor=verifier.expected_cursor)
expected = verifier.result()
assert expected["metrics"]["clicks"]["missingRows"] == n//7
if not args.without_memory_tracing:
    tracemalloc.start()
started = time.monotonic()
table = build_table(pages(), "keyword", expected, offset=n-100, limit=100)
elapsed = time.monotonic()-started
peak = None
if not args.without_memory_tracing:
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
assert table["total"] == n and len(table["rows"]) == 100
assert table["source"]["metrics"]["spendCents"]["value"] == n*(n+1)//2
for i, row in enumerate(table["rows"], n-99):
    assert row["rowIndex"] == i-1 and row["entity"]["keyword"] == f"合成词{i:06d}"
    assert row["metrics"]["spendCents"]["value"] == i
    assert row["metrics"]["clicks"]["value"] == (None if i % 7 == 0 else 1)
assert peak is None or peak < 32*1024*1024, "Python allocations must remain page-bounded"
print(json.dumps({"synthetic": True, "rows": n, "groups": table["total"], "lastPageRows": len(table["rows"]),
    "verifiedSumCents": n*(n+1)//2, "pythonPeakBytes": peak, "elapsedSeconds": round(elapsed, 3),
    "memoryScope": "tracemalloc Python allocations only; excludes SQLite native cache, file cache and process RSS",
    "fullXlsxHtmlAcceptance": False, "productionReads": False, "modelCalls": False}))
