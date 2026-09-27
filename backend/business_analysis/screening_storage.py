"""Bounded immutable screening pages; serialization is not authorization.

Only the owning service may publish a bundle after revalidating its internal
VerifiedScreening. This module neither reads facts nor accepts authority from
the presence of a JSON digest.
"""
import hashlib
import json
import math

from . import diagnostic_screening as scanner
from .contracts import AnalysisContractError, MAX_SAFE_INTEGER, canonical, digest

SCHEMA = "business-screening-storage-v1"
MANIFEST_SCHEMA = "business-screening-storage-manifest-v1"
CAPACITY_PROFILE = "screening-storage-v1"
MAX_BINDING_BYTES = 8192
MAX_MANIFEST_BYTES = 65536
MAX_PAGE_BYTES = 38000
MAX_PAGES = 4096
MAX_RUN_BYTES = 16*1024*1024
OWNER_BYTES, OWNER_ROWS = 64*1024*1024, 20
GLOBAL_BYTES, GLOBAL_ROWS = 256*1024*1024, 200
PAGE_SIZE = 20
MAX_INPUT_BYTES = 24*1024*1024
MAX_INPUT_NODES = 1000000
INITIAL_CHAIN = digest([])
GROUP_FIELDS = {"kind", "partitionKey", "total", "pageCount", "firstSequence", "lastSequence", "pagesDigest"}
MANIFEST_FIELDS = {"schemaVersion", "capacityProfile", "bindingDigest", "selectionPlanDigest", "pureResultDigest",
    "serviceResultDigest", "algorithmVersion", "selectionPolicy", "groups", "pageCount", "contentRootDigest"}
PAGE_FIELDS = {"sequence", "kind", "partitionKey", "offset", "returned", "total", "nextOffset", "payloadJson", "payloadDigest"}


def _require(ok, message="筛查持久页合同不一致"):
    if not ok: raise AnalysisContractError(message)


def raw_digest(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def chain(previous, ordinal, payload_digest):
    scanner._sha(previous); scanner._sha(payload_digest); scanner._integer(ordinal, 1, MAX_PAGES)
    return raw_digest(previous+":"+str(ordinal)+":"+payload_digest)


def _json(raw, maximum):
    _require(type(raw) is str and 0 < len(raw.encode("utf-8")) <= maximum, "筛查JSON容量无效")
    try:
        value = json.loads(raw)
        _require(type(value) is dict and canonical(value) == raw, "筛查JSON必须为规范对象")
        scanner._bounded(value, maximum)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise AnalysisContractError("筛查JSON无效") from error


def _page(base, rows, offset):
    selected = []
    def render():
        end = offset+len(selected)
        value = {**base, "items":selected, "pagination":{"offset":offset, "limit":PAGE_SIZE,
            "returned":len(selected), "total":len(rows), "nextOffset":end if end < len(rows) else None}}
        value["pageDigest"] = digest(value)
        return value
    for row in rows[offset:offset+PAGE_SIZE]:
        selected.append(row)
        if len(canonical(render()).encode("utf-8")) > MAX_PAGE_BYTES:
            selected.pop(); break
    value = render()
    _require((offset == len(rows) or selected) and len(canonical(value).encode()) <= MAX_PAGE_BYTES,
        "完整筛查记录超过单页容量")
    return value


def _bounded_result(value):
    # The completed object contains many individually bounded tables and
    # candidates. A scanner's *single object* 20k-node limit is not its total
    # result limit. Bound this distinct serialization traversal explicitly.
    pending, nodes, scalar_bytes = [(value,0)], 0, 0
    while pending:
        item, depth = pending.pop(); nodes += 1
        _require(depth <= 20 and nodes <= MAX_INPUT_NODES, "筛查完整结果结构超过容量")
        if type(item) is dict:
            _require(len(item) <= 512)
            for key, child in item.items():
                _require(type(key) is str and len(key) <= 200)
                pending.extend(((key,depth+1),(child,depth+1)))
        elif type(item) is list:
            _require(len(item) <= 1024)
            pending.extend((child,depth+1) for child in item)
        else:
            _require(item is None or type(item) in (str,int,bool,float))
            if type(item) is str: _require(len(item) <= MAX_INPUT_BYTES)
            if type(item) is int: scanner._integer(item,-MAX_SAFE_INTEGER)
            if type(item) is float: _require(math.isfinite(item))
            scalar_bytes += len(canonical(item).encode("utf-8"))
            _require(scalar_bytes <= MAX_INPUT_BYTES, "筛查完整结果超过容量")
    _require(len(canonical(value).encode("utf-8")) <= MAX_INPUT_BYTES, "筛查完整结果超过容量")


def materialize(value):
    """Create all fixed pages from a completed service result, without trust."""
    _bounded_result(value)
    _require(type(value) is dict and value.get("schemaVersion") == "business-diagnostic-screening-v1")
    _require(value.get("resultDigest") == digest({k:v for k,v in value.items() if k != "resultDigest"}))
    authority, plan, prepared = value["authority"], value["plan"], value["prepared"]
    binding = authority["binding"]
    _require(value["bindingDigest"] == digest(binding) and value["planDigest"] == plan["planDigest"]
        and plan["planDigest"] == digest({k:v for k,v in plan.items() if k != "planDigest"}))
    _require(prepared["resultDigest"] == digest({k:v for k,v in prepared.items() if k != "resultDigest"})
        and authority["pureResultDigest"] == prepared["resultDigest"]
        and authority["selectionPlanDigest"] == plan["planDigest"]
        and prepared["algorithmVersion"] == binding["algorithmVersion"] == scanner.ALGORITHM_VERSION
        and authority["selectionPolicy"] == plan["selectionPolicy"] == "screen-selection-v1")
    _require(authority["executedTablesComplete"] is True and authority["completeSourceTraversalForExecutedTables"] is True)
    binding_json = canonical(binding)
    _json(binding_json, MAX_BINDING_BYTES)
    partitions = prepared["partitions"]
    _require(type(partitions) is list and 1 <= len(partitions) <= scanner.LIMITS["maxPartitions"])
    partition_keys = [scanner._sha(row["partitionKey"]) for row in partitions]
    _require(len(set(partition_keys)) == len(partition_keys), "筛查分区重复")
    entries = [{"kind":"family", "value":row} for row in plan.get("families", [])]
    entries += [{"kind":"requested", "value":row} for row in plan["requestedCoverage"]]
    entries += [{"kind":"table", "value":row} for row in prepared["coverage"]["tables"]]
    entries += [{"kind":"partition", "value":{k:v for k,v in row.items() if k != "candidates"}} for row in partitions]
    common = {k:value[k] for k in ("bindingDigest", "planDigest", "resultDigest", "authority")}
    streams = [("coverage", "", entries, {})]
    for partition in sorted(partitions, key=lambda p:p["partitionKey"]):
        for key in ("matchedRows", "retainedRows", "omittedRows"): scanner._integer(partition[key])
        _require(type(partition["candidates"]) is list and len(partition["candidates"]) == partition["retainedRows"]
            and partition["matchedRows"] == partition["retainedRows"]+partition["omittedRows"])
        streams.append(("candidates", partition["partitionKey"], partition["candidates"],
            {"partition":{k:v for k,v in partition.items() if k != "candidates"}}))
    pages, groups, root = [], [], INITIAL_CHAIN
    stored_bytes = len(binding_json.encode())
    for kind, key, rows, extra in streams:
        base = {"schemaVersion":"business-diagnostic-screening-"+kind+"-v1", **common, **extra}
        offset, group_chain, first, group_count = 0, INITIAL_CHAIN, len(pages)+1, 0
        while True:
            payload = _page(base, rows, offset)
            raw = canonical(payload); summary = payload["pagination"]
            page = {"sequence":len(pages)+1, "kind":kind, "partitionKey":key,
                **{k:summary[k] for k in ("offset", "returned", "total", "nextOffset")},
                "payloadJson":raw, "payloadDigest":raw_digest(raw)}
            pages.append(page); group_count += 1
            _require(len(pages) <= MAX_PAGES, "筛查持久页数超过容量")
            stored_bytes += len(raw.encode())
            _require(stored_bytes <= MAX_RUN_BYTES, "筛查持久结果超过容量")
            root = chain(root, len(pages), page["payloadDigest"])
            group_chain = chain(group_chain, group_count, page["payloadDigest"])
            if summary["nextOffset"] is None: break
            offset = summary["nextOffset"]
        groups.append({"kind":kind, "partitionKey":key, "total":len(rows), "pageCount":group_count,
            "firstSequence":first, "lastSequence":len(pages), "pagesDigest":group_chain})
    manifest = {"schemaVersion":MANIFEST_SCHEMA, "capacityProfile":CAPACITY_PROFILE,
        "bindingDigest":value["bindingDigest"], "selectionPlanDigest":value["planDigest"],
        "pureResultDigest":prepared["resultDigest"], "serviceResultDigest":value["resultDigest"],
        "algorithmVersion":binding["algorithmVersion"], "selectionPolicy":plan["selectionPolicy"],
        "groups":groups, "pageCount":len(pages), "contentRootDigest":root}
    manifest_json = canonical(manifest)
    bundle = {"bindingJson":binding_json, "manifestJson":manifest_json, "pages":pages,
        "storedBytes":stored_bytes+len(manifest_json.encode())}
    validate(bundle)
    return bundle


def manifest(binding_json, manifest_json):
    binding, value = _json(binding_json, MAX_BINDING_BYTES), _json(manifest_json, MAX_MANIFEST_BYTES)
    scanner._fields(value, MANIFEST_FIELDS)
    _require(value["schemaVersion"] == MANIFEST_SCHEMA and value["capacityProfile"] == CAPACITY_PROFILE
        and value["algorithmVersion"] == binding["algorithmVersion"] == scanner.ALGORITHM_VERSION
        and value["selectionPolicy"] == "screen-selection-v1" and value["bindingDigest"] == digest(binding))
    for key in ("bindingDigest", "selectionPlanDigest", "pureResultDigest", "serviceResultDigest", "contentRootDigest"):
        scanner._sha(value[key])
    count = scanner._integer(value["pageCount"], 1, MAX_PAGES)
    groups = value["groups"]
    _require(type(groups) is list and 2 <= len(groups) <= scanner.LIMITS["maxPartitions"]+1)
    next_sequence, keys = 1, []
    for index, group in enumerate(groups):
        scanner._fields(group, GROUP_FIELDS)
        _require(group["kind"] == ("coverage" if index == 0 else "candidates"))
        if index == 0: _require(group["partitionKey"] == "")
        else: keys.append(scanner._sha(group["partitionKey"]))
        scanner._integer(group["total"], 0, MAX_PAGES*PAGE_SIZE if index == 0 else scanner.LIMITS["candidatesPerPartition"])
        page_count = scanner._integer(group["pageCount"], 1, MAX_PAGES)
        scanner._integer(group["firstSequence"], 1, MAX_PAGES); scanner._integer(group["lastSequence"], 1, MAX_PAGES)
        _require(group["firstSequence"] == next_sequence and group["lastSequence"] == next_sequence+page_count-1)
        scanner._sha(group["pagesDigest"])
        next_sequence += page_count
    _require(keys == sorted(set(keys)) and next_sequence == count+1)
    return binding, value


def validate_page(page, fixed_manifest, binding):
    scanner._fields(page, PAGE_FIELDS)
    sequence = scanner._integer(page["sequence"], 1, fixed_manifest["pageCount"])
    groups = [g for g in fixed_manifest["groups"] if g["firstSequence"] <= sequence <= g["lastSequence"]]
    _require(len(groups) == 1)
    group = groups[0]
    _require((page["kind"], page["partitionKey"], page["total"]) == (group["kind"], group["partitionKey"], group["total"]))
    for key in ("offset", "returned", "total"): scanner._integer(page[key])
    _require(page["returned"] <= PAGE_SIZE and page["offset"]+page["returned"] <= page["total"])
    if page["nextOffset"] is not None: scanner._integer(page["nextOffset"], 1)
    last = page["offset"]+page["returned"]
    _require(page["nextOffset"] == (last if last < page["total"] else None)
        and (page["returned"] > 0 or page["total"] == 0))
    payload = _json(page["payloadJson"], MAX_PAGE_BYTES)
    _require(page["payloadDigest"] == raw_digest(page["payloadJson"]) and payload.get("pageDigest") == digest(
        {k:v for k,v in payload.items() if k != "pageDigest"}))
    expected_fields = {"schemaVersion", "bindingDigest", "planDigest", "resultDigest", "authority", "items", "pagination", "pageDigest"}
    scanner._fields(payload, expected_fields | ({"partition"} if page["kind"] == "candidates" else set()))
    _require(payload["schemaVersion"] == "business-diagnostic-screening-"+page["kind"]+"-v1"
        and payload["bindingDigest"] == fixed_manifest["bindingDigest"]
        and payload["planDigest"] == fixed_manifest["selectionPlanDigest"]
        and payload["resultDigest"] == fixed_manifest["serviceResultDigest"]
        and type(payload["items"]) is list and len(payload["items"]) == page["returned"]
        and canonical(payload["authority"]["binding"]) == canonical(binding)
        and payload["authority"]["selectionPlanDigest"] == fixed_manifest["selectionPlanDigest"]
        and payload["authority"]["pureResultDigest"] == fixed_manifest["pureResultDigest"])
    _require(payload["authority"].get("executedTablesComplete") is True
        and payload["authority"].get("completeSourceTraversalForExecutedTables") is True)
    scanner._fields(payload["pagination"], {"offset", "limit", "returned", "total", "nextOffset"})
    for key in ("offset", "limit", "returned", "total"):
        scanner._integer(payload["pagination"][key])
    if payload["pagination"]["nextOffset"] is not None:
        scanner._integer(payload["pagination"]["nextOffset"],1)
    _require(payload["pagination"] == {"offset":page["offset"], "limit":PAGE_SIZE,
        "returned":page["returned"], "total":page["total"], "nextOffset":page["nextOffset"]})
    if page["kind"] == "candidates":
        partition = payload["partition"]
        for key in ("matchedRows", "retainedRows", "omittedRows"): scanner._integer(partition[key])
        _require(partition["partitionKey"] == page["partitionKey"] and partition["retainedRows"] == page["total"]
            and partition["matchedRows"] == partition["retainedRows"]+partition["omittedRows"])
    return payload


def validate(bundle):
    """Check complete pages, group sets and exact charged bytes; no authority."""
    scanner._fields(bundle, {"bindingJson", "manifestJson", "pages", "storedBytes"})
    binding, fixed = manifest(bundle["bindingJson"], bundle["manifestJson"])
    pages = bundle["pages"]
    _require(type(pages) is list and len(pages) == fixed["pageCount"])
    total_bytes = len(bundle["bindingJson"].encode())+len(bundle["manifestJson"].encode())
    root, coverage_partitions, observed, tables, authority_json = INITIAL_CHAIN, {}, {}, {}, None
    for group in fixed["groups"]:
        offset, group_chain = 0, INITIAL_CHAIN
        for ordinal, sequence in enumerate(range(group["firstSequence"], group["lastSequence"]+1), 1):
            page = pages[sequence-1]
            _require(page["sequence"] == sequence and page["offset"] == offset)
            payload = validate_page(page, fixed, binding)
            serialized_authority = canonical(payload["authority"])
            if authority_json is None: authority_json = serialized_authority
            _require(serialized_authority == authority_json, "筛查各页覆盖声明不一致")
            root = chain(root, sequence, page["payloadDigest"])
            group_chain = chain(group_chain, ordinal, page["payloadDigest"])
            total_bytes += len(page["payloadJson"].encode())
            _require(total_bytes <= MAX_RUN_BYTES, "筛查持久结果超过容量")
            if group["kind"] == "coverage":
                for item in payload["items"]:
                    scanner._fields(item, {"kind", "value"})
                    _require(item["kind"] in {"family", "requested", "table", "partition"})
                    if item["kind"] == "partition":
                        key = scanner._sha(item["value"]["partitionKey"])
                        _require(key not in coverage_partitions)
                        coverage_partitions[key] = item["value"]
                    elif item["kind"] == "table":
                        key = scanner._sha(item["value"]["tableKey"])
                        _require(key not in tables)
                        tables[key] = item["value"]
            else:
                key = group["partitionKey"]
                _require(key in coverage_partitions and canonical(payload["partition"]) == canonical(coverage_partitions[key]))
                observed[key] = True
            offset += page["returned"]
            _require((page["nextOffset"] is None) == (sequence == group["lastSequence"]))
        _require(offset == group["total"] and group_chain == group["pagesDigest"])
    _require(set(observed) == set(coverage_partitions) and root == fixed["contentRootDigest"])
    authority = json.loads(authority_json)
    scanner._integer(authority["tableCount"],1,scanner.LIMITS["maxTables"])
    scanner._integer(authority["partitionCount"],1,scanner.LIMITS["maxPartitions"])
    _require(authority["tableCount"] == len(tables) and authority["partitionCount"] == len(coverage_partitions)
        and {p["tableKey"] for p in coverage_partitions.values()} == set(tables), "筛查覆盖表与分区集合不一致")
    scanner._integer(bundle["storedBytes"], 1, MAX_RUN_BYTES)
    _require(total_bytes == bundle["storedBytes"])
    return fixed
