"""Synthetic, non-authoritative capacity probe for the attributed-SKU view.

Uses the real native projection fixture once, then streams deterministic
canonical pages twice: once for the independent reconciliation and once for
the candidate. No customer data, Django connection, or production call.
"""
import argparse
from copy import deepcopy
import ctypes
import json
from pathlib import Path
import sys
from threading import Event, Thread
from time import perf_counter
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from business_analysis import partitioned, promotion_attributed_sku_relation as relation
from business_analysis import test_promotion_attributed_sku_relation as fixture
from business_analysis import test_promotion_views as native_fixture
from business_analysis.contracts import PageReconciler, digest


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_ulong), ("pageFaultCount", ctypes.c_ulong)] + [
        (name, ctypes.c_size_t) for name in (
            "peakWorkingSetSize", "workingSetSize", "quotaPeakPagedPoolUsage",
            "quotaPagedPoolUsage", "quotaPeakNonPagedPoolUsage",
            "quotaNonPagedPoolUsage", "pagefileUsage", "peakPagefileUsage")]


def _resident_bytes():
    if sys.platform != "win32":
        return None
    value = _ProcessMemoryCounters()
    value.cb = ctypes.sizeof(value)
    handle = ctypes.windll.kernel32.GetCurrentProcess
    handle.restype = ctypes.c_void_p
    memory = ctypes.windll.psapi.GetProcessMemoryInfo
    memory.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong)
    okay = memory(handle(), ctypes.byref(value), value.cb)
    return value.workingSetSize if okay else None


def pages(base, rows, groups, page_size):
    source, samples, _ = base
    template = samples[0]
    row = template["items"][0]
    for offset in range(0, rows, page_size):
        size = min(page_size, rows - offset)
        items = []
        for index in range(offset, offset + size):
            item = deepcopy(row)
            identity = index % groups
            item["rowId"] = str(index + 1)
            item["sourceRowHash"] = digest(["synthetic-attributed", index])
            item["dimensions"].update({"planId": f"P{identity:06d}",
                "unitId": f"U{identity:06d}", "keyword": f"K{identity:06d}",
                "searchTerm": f"T{identity:06d}",
                "attributedSkuId": f"A{identity:06d}"})
            items.append(item)
        page = {**template, "items": items,
            "pageEvidence": {"rowCount": size, "sha256": digest(items)},
            "pagination": {"limit": page_size,
                "hasMore": offset + size < rows,
                "nextCursor": f"synthetic-{offset + size}"
                    if offset + size < rows else None}}
        if offset:
            page.update(control=None, coverage=None, availableDates=None)
        else:
            page["control"] = {**template["control"], "rowCount": rows,
                "typedTotals": {key: value * rows
                    for key, value in template["control"]["typedTotals"].items()}}
        yield page


def probe(rows, groups, page_size):
    if not 1 <= groups <= rows:
        raise ValueError("groups must be in 1..rows")
    if not 1 <= page_size <= 100 or rows > 200_000 or (rows + page_size - 1) // page_size > 2_000:
        raise ValueError("probe must stay within the existing v2 row/page contract")
    full_started = perf_counter()
    base = native_fixture.fixture([fixture.fact()], limit=page_size)
    verifier = PageReconciler()
    for page in pages(base, rows, groups, page_size):
        verifier.consume(page, request_cursor=verifier.expected_cursor)
    expected = verifier.result()
    preparation_seconds = perf_counter() - full_started
    source = {**base[0], "evidenceDigest": expected["evidenceDigest"]}
    active_dir = [None]
    stop = Event()
    measurements = {"peakScratchDirectoryBytes": 0,
        "baselineResidentBytes": _resident_bytes(),
        "peakResidentBytes": _resident_bytes()}
    def poll():
        while not stop.wait(0.25):
            directory = active_dir[0]
            try:
                if directory and directory.exists():
                    size = sum(path.stat().st_size for path in directory.iterdir()
                        if path.is_file())
                    measurements["peakScratchDirectoryBytes"] = max(
                        measurements["peakScratchDirectoryBytes"], size)
            except OSError:
                pass  # The context may remove its disposable directory now.
            resident = _resident_bytes()
            if resident is not None:
                measurements["peakResidentBytes"] = max(
                    measurements["peakResidentBytes"] or 0, resident)
    original = partitioned.PartitionedGroups.__enter__
    def capture(self):
        result = original(self)
        active_dir[0] = Path(self.directory.name)
        return result
    monitor = Thread(target=poll, daemon=True)
    monitor.start()
    started = perf_counter()
    try:
        with patch.object(partitioned.PartitionedGroups, "__enter__", capture):
            with relation.table(source, pages(base, rows, groups, page_size),
                    expected, view="keyword_searchterm_plan_unit_match_attributed_sku") as table:
                header = table.header()
                observed_rows = observed_spend = 0
                for row in table.scan():
                    observed_rows += row["currentRowCount"]
                    observed_spend += row["metrics"]["spendCents"]["value"]
                directory = active_dir[0]
                main_file = directory / "groups.sqlite"
                measurements["mainSqliteBytes"] = main_file.stat().st_size
                measurements["scratchDirectoryBytes"] = sum(
                    path.stat().st_size for path in directory.iterdir()
                    if path.is_file())
                measurements["peakScratchDirectoryBytes"] = max(
                    measurements["peakScratchDirectoryBytes"],
                    measurements["scratchDirectoryBytes"])
                assert header["total"] == groups
                assert observed_rows == expected["rowCount"] == rows
                assert observed_spend == expected["metrics"]["spendCents"]["value"]
                measurements.update(groups=header["total"], rows=observed_rows,
                    spendCents=observed_spend, pages=header["sourceTraversal"]["pages"])
    finally:
        stop.set(); monitor.join()
    measurements.update(schemaVersion="attributed-sku-synthetic-scale-v1",
        sourceAuthorityVerified=False,
        reconciliationSeconds=round(preparation_seconds, 3),
        relationSeconds=round(perf_counter() - started, 3),
        totalSeconds=round(perf_counter() - full_started, 3),
        requestedRows=rows, requestedGroups=groups, pageSize=page_size)
    return measurements


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, required=True)
    parser.add_argument("--groups", type=int, required=True)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = probe(args.rows, args.groups, args.page_size)
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
