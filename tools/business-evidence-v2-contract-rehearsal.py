"""Synthetic-only v2 catalog rehearsal; no Django, DB, source reads or writes.

Prints compact proof JSON. This checks protocol size/coverage, not persistence,
owning-reader plans, fact volume, paid Agent behavior or production readiness.
"""
from copy import deepcopy
import ast
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from business_analysis import evidence_v2 as v2
from business_analysis.contracts import AnalysisContractError, canonical, digest


RUN = "evidence-00000000-0000-0000-0000-000000000001"
VERSION = 101
DATES = {"startDate": "2024-02-01", "endDate": "2024-02-29"}
WINDOWS = ("current", "previous", "yearAgo")
REQUEST = {"schemaVersion": "business-analysis-request-v1", "question": "中"*1000,
    "requestedDimensions": ["shop", "category", "spu", "sku", "keyword"], "requestedWindows": list(WINDOWS)}


def source(domain, query):
    return {"key": "source-"+digest({"domain": domain, "query": query}), "domain": domain, "query": query}


def shop(index):
    identity = {"platform": "京东", "shop": "合成店铺"+str(index)}
    rows = []
    for dataset in ("promotion", "sku", "spu", "b2b", "master"):
        for window in (("current",) if dataset == "master" else WINDOWS):
            rows.append(source("netshop", {**identity, **DATES, "dataset": dataset, "window": window}))
    for window in WINDOWS:
        rows.append(source("sales", {**identity, **DATES, "channel": "合成ERP渠道"+str(index), "window": window}))
    return rows


def market(index, wide=False):
    # Astral Unicode maximizes UTF-8 width while staying within 200 characters.
    category = (str(index).zfill(2)+"\U0001f4ca"*198) if wide else "合成类目"+str(index)
    return [source("market", {"platform": "京东", "category": category,
        "scope": "\U0001f4ca"*200 if wide else "POP", "rankingDimension": "SKU",
        "priceBandFilter": "\U0001f4ca"*200 if wide else "全部", **DATES, "window": window}) for window in WINDOWS]


def measure(value, *, name, page_limit):
    catalog = v2.build_catalog(value, analysis_request=REQUEST)
    reference = v2.workflow_reference(value, run_id=RUN, evidence_version=VERSION,
        sealed_digest="a"*64, question=REQUEST["question"], analysis_request=REQUEST)
    assert len(canonical(catalog["header"]).encode()) <= 16000
    assert len(canonical(reference).encode()) <= 8000
    assert catalog["header"]["limits"] == {"factBytes": 67108864, "factPages": 2000}
    assert "sources" not in catalog["header"] and "sources" not in reference
    pages, offset = [], 0
    while True:
        page = v2.directory_page(value, run_id=RUN, evidence_version=VERSION,
            offset=offset, limit=page_limit, analysis_request=REQUEST)
        assert len(canonical(page).encode()) <= 38000
        pages.append(page)
        if page["nextOffset"] is None:
            break
        assert page["nextOffset"] == offset+page["returned"]
        offset = page["nextOffset"]
    proof = v2.validate_directory_pages(iter(pages), value, run_id=RUN, evidence_version=VERSION, analysis_request=REQUEST)
    assert [entry for page in pages for entry in page["items"]] == catalog["entries"]
    assert proof["sourceCount"] == len(value)
    return {"case": name, "sourceCount": len(value), "headerBytes": len(canonical(catalog["header"]).encode()),
        "workflowBytes": len(canonical(reference).encode()), "catalogBytes": len(canonical(catalog["entries"]).encode()),
        "queryBytes": sum(len(canonical(entry["query"]).encode()) for entry in catalog["entries"]),
        "pageCount": len(pages), "pageReturned": [p["returned"] for p in pages],
        "maxPageBytes": max(len(canonical(p).encode()) for p in pages),
        "catalogDigest": proof["catalogDigest"], "planDigest": proof["planDigest"], "complete": proof["complete"]}, pages, catalog


def main():
    # Compare the actual constant without importing Django or executing readers.
    tree = ast.parse((ROOT / "backend/netshop/analysis.py").read_text(encoding="utf-8"))
    assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "SOURCES" for target in node.targets))
    owning_support = ast.literal_eval(assignment.value)
    assert {dataset: set(platforms) for dataset, platforms in owning_support.items()} == v2.NETSHOP_SOURCES
    cases = [("single_shop_16", shop(1)), ("single_shop_market_19", shop(1)+market(1)),
        ("two_shops_market_35", shop(1)+shop(2)+market(1)), ("three_shops_48", shop(1)+shop(2)+shop(3)),
        ("wide_market_48", [entry for i in range(16) for entry in market(i, True)])]
    results = []
    for name, value in cases:
        proof, _, _ = measure(value, name=name, page_limit=20)
        results.append(proof)
    assert results[-1]["pageCount"] > 3  # Actual byte boundary, not a patched constant.
    assert results[-1]["queryBytes"] > 100000
    source_rows = cases[3][1]
    _, saved_pages, catalog = measure(source_rows, name="negative_base", page_limit=10)
    rejected = []
    def must_reject(name, action):
        try:
            action()
        except AnalysisContractError:
            rejected.append(name)
        else:
            raise AssertionError("Expected fail-closed: "+name)
    def verify(items, **overrides):
        return v2.validate_directory_pages(items, source_rows,
            **{"run_id": RUN, "evidence_version": VERSION, "analysis_request": REQUEST, **overrides})
    must_reject("49_sources", lambda: v2.build_catalog([*source_rows, market(99)[0]], analysis_request=REQUEST))
    must_reject("cross_run", lambda: verify(saved_pages, run_id="evidence-other"))
    must_reject("cross_version", lambda: verify(saved_pages, evidence_version=VERSION+1))
    altered_header = deepcopy(catalog["header"])
    altered_header["limits"]["factBytes"] *= 2
    must_reject("changed_header", lambda: v2.validate_header(altered_header, source_rows, analysis_request=REQUEST))
    must_reject("missing_tail", lambda: verify(saved_pages[:-1]))
    must_reject("duplicate_page", lambda: verify([saved_pages[0], *saved_pages]))
    must_reject("reordered_pages", lambda: verify([saved_pages[1], saved_pages[0], *saved_pages[2:]]))
    tampered = deepcopy(saved_pages)
    tampered[0]["items"][0]["query"]["shop"] = "合成篡改店"
    tampered[0]["items"][0]["queryDigest"] = digest(tampered[0]["items"][0]["query"])
    tampered[0]["pageDigest"] = digest({k: v for k, v in tampered[0].items() if k != "pageDigest"})
    must_reject("rehashed_tampering", lambda: verify(tampered))
    proof = {"schemaVersion": "business-evidence-v2-contract-rehearsal-v1", "syntheticOnly": True,
        "persistenceEnabled": False, "productionOrModelCalls": False, "cases": results,
        "rejected": rejected, "factLimitsUnchanged": {"bytes": 67108864, "pages": 2000},
        "readerContractReview": {"netshopSupportMatchesSourceConstant": True, "readerValidationExecuted": False,
            "knownConservativeDifferences": ["sales_identity_100_chars_matches_collector_but_reader_allows_200",
                "master_current_only_prevents_latest_snapshot_as_history", "unicode_control_characters_rejected_more_strictly"],
            "sourceAvailabilityVerified": False}}
    print(json.dumps({**proof, "proofDigest": digest(proof)}, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
