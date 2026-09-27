"""Synthetic v4 single-window followed-SKU capacity rehearsal; no authority."""
import argparse
from pathlib import Path
import json
import runpy
import sys
from threading import Event, Thread
from time import perf_counter
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from business_analysis import partitioned, promotion_attributed_sku_relation_v4 as relation
from business_analysis import test_promotion_attributed_sku_relation as fixture
from business_analysis import test_promotion_views as native_fixture
from business_analysis.contracts import PageReconciler, canonical, digest

old_probe = runpy.run_path(str(Path(__file__).with_name(
    "business-promotion-attributed-sku-scale.py")))
pages = old_probe["pages"]
resident_bytes = old_probe["_resident_bytes"]


def probe(rows, groups, window):
    if not 1 <= groups <= rows <= 575_095:
        raise ValueError("synthetic rows/groups outside requested scale")
    started = perf_counter()
    base = native_fixture.fixture([fixture.fact()], window=window, limit=100)
    verifier = PageReconciler()
    page_count = stored_bytes = 0
    for page in pages(base, rows, groups, 100):
        verifier.consume(page, request_cursor=verifier.expected_cursor)
        page_count += 1
        stored_bytes += len(canonical(page).encode("utf-8"))
    source = {key:base[0][key] for key in ("key", "domain", "query")}
    proof = {"schemaVersion":relation.v4.REPLAY_SCHEMA,
        "runId":"synthetic-v4-scale", "sourceId":"synthetic-v4-scale-source",
        "sourceKey":source["key"], "runVersion":page_count+1,
        "sourceVersion":page_count+1, "queryDigest":digest(source["query"]),
        "sourceRef":base[0]["sourceRef"],
        "sourceRevision":base[1][0]["sourceRevision"],
        "receiptChainDigest":digest(["synthetic-internal-audit"]),
        "pageCount":page_count, "rowCount":rows,
        "storedBytes":stored_bytes, "reconciliation":verifier.result(),
        "coverage":base[1][0]["coverage"],
        "fullSourceReplayVerified":True, "internalToolAuditBound":True,
        "payloadCursorChainVerified":True,
        "requestCursorAuditVerified":True,
        "upstreamSignatureVerified":False, "sealed":False,
        "reportGenerationSupported":False}
    proof["proofDigest"] = digest(proof)
    prep_seconds = perf_counter() - started
    active_dir = [None]
    stop = Event()
    observed = {"peakScratchDirectoryBytes":0,
        "baselineResidentBytes":resident_bytes(),
        "peakResidentBytes":resident_bytes()}
    def poll():
        while not stop.wait(0.25):
            directory = active_dir[0]
            try:
                if directory and directory.exists():
                    size = sum(path.stat().st_size for path in directory.iterdir()
                        if path.is_file())
                    observed["peakScratchDirectoryBytes"] = max(
                        observed["peakScratchDirectoryBytes"], size)
            except OSError:
                pass
            resident = resident_bytes()
            if resident is not None:
                observed["peakResidentBytes"] = max(
                    observed["peakResidentBytes"] or 0, resident)
    original = partitioned.PartitionedGroups.__enter__
    def capture(self):
        result = original(self)
        active_dir[0] = Path(self.directory.name)
        return result
    monitor = Thread(target=poll, daemon=True)
    monitor.start()
    relation_started = perf_counter()
    try:
        with patch.object(partitioned.PartitionedGroups, "__enter__", capture):
            with relation.table(source, pages(base, rows, groups, 100), proof,
                    view="keyword_searchterm_plan_unit_match_attributed_sku") as table:
                header = table.header()
                count = spend = 0
                for row in table.scan():
                    count += row["currentRowCount"]
                    spend += row["metrics"]["spendCents"]["value"]
                _dir = active_dir[0]
                observed["mainSqliteBytes"] = (_dir / "groups.sqlite").stat().st_size
                observed["scratchDirectoryBytes"] = sum(path.stat().st_size
                    for path in _dir.iterdir() if path.is_file())
                observed["peakScratchDirectoryBytes"] = max(
                    observed["peakScratchDirectoryBytes"],
                    observed["scratchDirectoryBytes"])
                assert header["total"] == groups
                assert count == rows == header["sourceRowCount"]
                assert spend == rows * 100 == header["sourceMetrics"]["spendCents"]["value"]
                observed.update(sourcePages=page_count, sourceBytes=stored_bytes,
                    sourceRows=count, relationGroups=header["total"],
                    spendCents=spend, outputBytes=header["outputBytes"])
    finally:
        stop.set(); monitor.join()
    observed.update(schemaVersion="attributed-sku-v4-synthetic-scale-v1",
        sourceAuthorityVerified=False, requestedRows=rows, requestedGroups=groups,
        window=window, reconciliationSeconds=round(prep_seconds,3),
        relationSeconds=round(perf_counter()-relation_started,3),
        totalSeconds=round(perf_counter()-started,3))
    return observed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, required=True)
    parser.add_argument("--groups", type=int, required=True)
    parser.add_argument("--window", choices=("current","previous","yearAgo"),
        default="current")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = probe(args.rows, args.groups, args.window)
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
