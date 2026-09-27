"""Pure lossless role transport over validated storage; never an authority.

The owning caller must supply the exact Reader.sources/Reader.info and fixed
selection plan from the same report. No facts, database, models or caches here.
Opaque source metadata is preserved under its existing owning-reader contract;
transport columns, records, candidate facts and references use fixed schemas.
"""
import json
import math
from dataclasses import dataclass

from . import diagnostic_screening as scan, screening_plan as planning, screening_storage as storage
from .contracts import AnalysisContractError, MAX_SAFE_INTEGER, canonical, digest
from .evidence_v2 import normalize_sources

SCHEMA = "business-screening-role-package-v1"
POLICY = "screening-role-package-policy-v1"
ROLES = ("commerce", "promotion", "market_b2b", "independent_review", "report")
MAX_PAGE_BYTES, PAGE_SIZE = 38000, 100
MAX_INPUT_BYTES, MAX_PACKAGE_BYTES, MAX_ALL_PACKAGE_BYTES = 32*1024*1024, 24*1024*1024, 96*1024*1024
MAX_RECORDS, MAX_NODES = 10000, 1500000
COUNT = ("ruleId", "eligibleRows", "matchedRows", "ineligibleReasons", "ineligibleRows", "scannedRows",
    "retainedRows", "omittedRows", "supported", "unavailableReason")
SCHEMAS = {
    "source": ("source", "info"),
    "tableBinding": ("tableKey", "sourceKey", "baselineKey", "mode", "dimension", "masterKey", "pairKey", "baselinePairKey"),
    "family": ("familyKey", "domain", "query", "family", "sources"),
    "requested": ("coverageKey", "familyKey", "domain", "mode", "dimension", "window", "kind",
        "sourceKey", "baselineKey", "status", "reason", "tableKeys"),
    "table": ("tableKey", "headerDigest", "expectedRows", "scannedRows", "rowDigest", "rulePartitionIndices",
        "sourceCoverage", "baselineCoverage", "sourcePeriod", "baselinePeriod", "dateCoverageComparable"),
    "partition": ("partitionKey", "tableKey", *COUNT),
    "candidate": ("constant", "candidateId", "reference", "entity", "matched", "score", "current",
        "baseline", "differences", "identityQualified"),
}
CONSTANT_FIELDS = ("ruleId", "tableKey", "meaning")
CANDIDATE_FIELDS = set(SCHEMAS["candidate"][1:]) | set(CONSTANT_FIELDS)
PLAN_FIELDS = {"schemaVersion", "selectionPolicy", "authorityVerified", "analysisRequest", "analysisRequestDigest",
    "sourceCount", "catalogDigest", "sourceProofs", "mappingPlanDigest", "families", "requestedCoverage", "descriptors",
    "limits", "capacity", "canScreen", "admissionFailures", "requestedCoveragePlanned", "limitations", "planDigest"}
MEANING = "numeric_pattern_not_causality_or_final_profit"
BINDING_FIELDS = {"reportId", "workflowId", "ownerEmail", "scope", "role", "snapshotDigest", "workflowInputDigest",
    "executionProfile", "evidenceRunId", "evidenceVersion", "evidencePlanDigest", "catalogDigest", "sealedDigest",
    "sourceCount", "sourceInfosDigest", "sourcesDigest", "analysisRequestDigest", "mappingPlanDigest", "budgetRef", "algorithmVersion"}
AUTHORITY_FIELDS = {"completeSourceTraversalForExecutedTables", "executedTablesComplete", "requestedCoveragePlanned",
    "requestedTablesExecutedComplete", "requestedSourceDateCoverageComplete", "entityDailyCoverageVerified", "binding",
    "reportId", "evidenceRunId", "selectionPolicy", "selectionPlanDigest", "pureResultDigest", "tableCount", "rowVisits",
    "partitionCount", "limitations"}
HEADER_FIELDS = {"schemaVersion", "policy", "authorityVerified", "role", "reportId", "evidenceRunId", "bindingDigest",
    "selectionPlanDigest", "serviceResultDigest", "storageManifestDigest", "totalRecords"}
DIRECTORY_FIELDS = {"recordSchemas", "candidateConstants", "candidateConstantSchema", "candidateMeaning", "tableKeys",
    "binding", "authority", "recordCounts", "requiredPartitionKeys"}


def _require(ok, message="角色包合同无效"):
    if not ok: raise AnalysisContractError(message)


def _same(a, b): return canonical(a) == canonical(b)


def _copy(value, maximum):
    pending, nodes, size = [(value, 0)], 0, 0
    while pending:
        item, depth = pending.pop(); nodes += 1
        _require(nodes <= MAX_NODES and depth <= 32, "角色包结构超过容量")
        if type(item) is dict:
            _require(len(item) <= MAX_RECORDS)
            for key, child in item.items():
                _require(type(key) is str and len(key) <= 200)
                pending.extend(((key, depth+1), (child, depth+1)))
        elif type(item) is list:
            _require(len(item) <= 81920)
            pending.extend((child, depth+1) for child in item)
        else:
            _require(item is None or type(item) in (str, int, bool, float))
            if type(item) is int: scan._integer(item, -MAX_SAFE_INTEGER)
            if type(item) is float: _require(math.isfinite(item))
            try: size += len(canonical(item).encode("utf-8"))
            except (ValueError, UnicodeError) as error: raise AnalysisContractError("角色包文本无效") from error
            _require(size <= maximum, "角色包字节超过容量")
    raw = canonical(value)
    _require(len(raw.encode("utf-8")) <= maximum, "角色包字节超过容量")
    return json.loads(raw)


def _role(role, descriptor):
    source = descriptor["source"]
    if role in ("independent_review", "report"): return True
    if source["domain"] == "sales": return role == "commerce"
    if source["domain"] != "netshop": return False
    dataset = source["query"]["dataset"]
    return ((role == "promotion" and dataset == "promotion") or (role == "market_b2b" and dataset == "b2b")
        or (role == "commerce" and dataset in {"sku", "spu"}))


def _validate_common(binding, authority, sources, infos, plan, coverage, *, transport=False):
    scan._fields(binding, BINDING_FIELDS); scan._fields(authority, AUTHORITY_FIELDS)
    if not transport: scan._fields(plan, PLAN_FIELDS)
    for key in ("reportId", "workflowId", "evidenceRunId"): scan._identifier(binding[key])
    for key in ("snapshotDigest", "workflowInputDigest", "evidencePlanDigest", "catalogDigest", "sealedDigest",
            "sourceInfosDigest", "sourcesDigest", "analysisRequestDigest"): scan._sha(binding[key])
    scan._integer(binding["evidenceVersion"],1); scan._integer(binding["sourceCount"],1,48)
    _require(binding["algorithmVersion"] == scan.ALGORITHM_VERSION)
    if binding["mappingPlanDigest"] is not None: scan._sha(binding["mappingPlanDigest"])
    scan._integer(authority["tableCount"],1,scan.LIMITS["maxTables"])
    scan._integer(authority["partitionCount"],1,scan.LIMITS["maxPartitions"])
    scan._integer(authority["rowVisits"],0,scan.LIMITS["maxRowVisits"])
    scan._sha(authority["pureResultDigest"]); scan._sha(authority["selectionPlanDigest"])
    _require(_same(authority["binding"], binding) and authority["reportId"] == binding["reportId"]
        and authority["evidenceRunId"] == binding["evidenceRunId"])
    _require(authority["executedTablesComplete"] is True and authority["completeSourceTraversalForExecutedTables"] is True
        and authority["entityDailyCoverageVerified"] is False)
    for key in ("requestedCoveragePlanned", "requestedTablesExecutedComplete", "requestedSourceDateCoverageComplete"):
        _require(type(authority[key]) is bool)
    entries = normalize_sources(sources)
    plain = [{k:e[k] for k in ("key", "domain", "query")} for e in entries]
    _require(_same(plain, sources) and type(infos) is dict and set(infos) == {s["key"] for s in sources})
    for info in infos.values(): planning._info(info)
    _require(binding["sourcesDigest"] == digest(sources) and binding["sourceInfosDigest"] == digest(infos)
        and binding["sourceCount"] == len(sources) and type(binding["sourceCount"]) is int)
    catalog = digest({"schemaVersion":"business-evidence-directory-v2", "entries":entries})
    _require(binding["catalogDigest"] == catalog and authority["selectionPolicy"] == planning.SELECTION_POLICY)
    if not transport:
        scan._integer(plan["sourceCount"],1,48)
        _require(plan["catalogDigest"] == catalog and plan["schemaVersion"] == planning.SCHEMA_VERSION
            and plan["selectionPolicy"] == planning.SELECTION_POLICY and plan["authorityVerified"] is False
            and plan["canScreen"] is True and plan["admissionFailures"] == [])
        _require(plan["planDigest"] == digest({k:v for k,v in plan.items() if k != "planDigest"}) == authority["selectionPlanDigest"])
        _require(plan["analysisRequestDigest"] == binding["analysisRequestDigest"] == digest(plan["analysisRequest"])
            and plan["mappingPlanDigest"] == binding["mappingPlanDigest"] and plan["sourceCount"] == len(sources))
        _require(_same(plan["sourceProofs"], [{"sourceKey":s["key"], "infoDigest":digest(infos[s["key"]]),
            "pageCount":infos[s["key"]]["pageCount"], "rowCount":infos[s["key"]]["expected"]["rowCount"]} for s in sources]))
    by_source = {s["key"]:s for s in sources}
    descriptors = {}
    for desc in plan["descriptors"]:
        _require(_same(scan._descriptor(desc), desc))
        for src in (desc["source"], desc["baseline"], (desc["mapping"] or {}).get("master")):
            if src is None: continue
            fixed = by_source.get(src["key"])
            _require(fixed is not None)
            _require(_same(src, {**fixed, **{k:infos[src["key"]]["expected"][k] for k in ("sourceRef", "evidenceDigest")}}))
        if desc["mapping"] is not None: _require(desc["mapping"]["planDigest"] == plan["mappingPlanDigest"])
        key = digest({k:v for k,v in desc.items() if k != "ruleIds"})
        _require(key not in descriptors); descriptors[key] = desc
    _require(1 <= len(descriptors) <= scan.LIMITS["maxTables"] and list(descriptors) == sorted(descriptors))
    groups = {kind:[] for kind in ("family", "requested", "table", "partition")}
    order = -1
    for record in coverage:
        scan._fields(record, {"kind", "value"})
        kind, value = record["kind"], record["value"]
        _require(type(kind) is str and kind in groups)
        index = list(groups).index(kind); _require(index >= order); order = index
        expected_fields = (set(SCHEMAS[kind]) - {"rulePartitionIndices"}) | {"rules"} if kind == "table" else set(SCHEMAS[kind])
        scan._fields(value, expected_fields); groups[kind].append(value)
    _require(_same(groups["family"], plan["families"]) and _same(groups["requested"], plan["requestedCoverage"]))
    tables = {row["tableKey"]:row for row in groups["table"]}
    _require(len(tables) == len(groups["table"]) == authority["tableCount"] and set(tables) == set(descriptors))
    for table in tables.values():
        for key in ("expectedRows", "scannedRows"): scan._integer(table[key],0,scan.LIMITS["maxRowsPerTable"])
        for key in ("headerDigest", "rowDigest"): scan._sha(table[key])
        _require(type(table["dateCoverageComparable"]) is bool)
    _require(authority["rowVisits"] == sum(t["scannedRows"] for t in tables.values()))
    partitions = {}
    for p in groups["partition"]:
        key = scan._sha(p["partitionKey"]); desc = descriptors.get(p["tableKey"])
        _require(key not in partitions and desc is not None and p["ruleId"] in desc["ruleIds"]
            and key == digest([p["tableKey"],p["ruleId"]]))
        for name in ("eligibleRows", "matchedRows", "ineligibleRows", "scannedRows", "retainedRows", "omittedRows"):
            scan._integer(p[name], 0, scan.LIMITS["maxRowsPerTable"])
        _require(type(p["ineligibleReasons"]) is dict)
        for count in p["ineligibleReasons"].values(): scan._integer(count)
        _require(p["ineligibleRows"] == sum(p["ineligibleReasons"].values())
            and p["eligibleRows"]+p["ineligibleRows"] == p["scannedRows"]
            and p["matchedRows"] == p["retainedRows"]+p["omittedRows"] <= p["eligibleRows"]
            and p["retainedRows"] <= scan.LIMITS["candidatesPerPartition"])
        table = tables[p["tableKey"]]
        _require(p["scannedRows"] == table["scannedRows"] == table["expectedRows"]
            and type(p["supported"]) is bool)
        partitions[key] = p
    _require(len(partitions) == authority["partitionCount"] <= scan.LIMITS["maxPartitions"])
    for key, table in tables.items():
        expected = [{k:v for k,v in p.items() if k not in {"partitionKey", "tableKey"}}
            for p in groups["partition"] if p["tableKey"] == key]
        _require(_same(table["rules"], expected) and {p["ruleId"] for p in expected} == set(descriptors[key]["ruleIds"]))
    return descriptors, partitions


def _candidate(candidate, partition, desc):
    scan._fields(candidate, CANDIDATE_FIELDS)
    _require(candidate["ruleId"] == partition["ruleId"] and candidate["tableKey"] == partition["tableKey"]
        and candidate["meaning"] == "numeric_pattern_not_causality_or_final_profit"
        and candidate["matched"] is True and type(candidate["identityQualified"]) is bool)
    scan._sha(candidate["candidateId"]); scan._integer(candidate["score"], 1)
    ref = candidate["reference"]
    fields = {"dimension", "rowIndex", "rowId"} | ({"sourceKey", "baselineKey"} if desc["mode"] == "native" else {"pairKey", "baselinePairKey"})
    scan._fields(ref, fields); scan._sha(ref["rowId"]); scan._integer(ref["rowIndex"], 0, partition["scannedRows"]-1)
    _require(ref["dimension"] == desc["dimension"])
    expected = ({"sourceKey":desc["source"]["key"], "baselineKey":desc["baseline"]["key"] if desc["baseline"] else None}
        if desc["mode"] == "native" else {k:desc["mapping"][k] for k in ("pairKey", "baselinePairKey")})
    _require(all(ref[k] == v for k,v in expected.items()))
    entity = candidate["entity"]
    scan._fields(entity, {"platform", "shopName", *scan.VIEWS[desc["dimension"]]} | ({"mappingStatus"} if desc["mode"] == "mapped" else set()))
    _require(entity["platform"] == desc["source"]["query"]["platform"] and entity["shopName"] == desc["source"]["query"].get("shop", ""))
    for key,value in entity.items(): _require(value is None or type(value) is str)
    metrics = set(scan.RULES[partition["ruleId"]][1]); comparison = scan.RULES[partition["ruleId"]][2]
    scan._fields(candidate["current"], metrics)
    if comparison:
        scan._fields(candidate["baseline"], metrics); scan._fields(candidate["differences"], metrics)
        _require(all(candidate["differences"][k] == candidate["current"][k]-candidate["baseline"][k] for k in metrics))
    else: _require(candidate["baseline"] is None and candidate["differences"] == {})
    for field in ("current", "baseline", "differences"):
        for value in (candidate[field] or {}).values(): scan._integer(value, -MAX_SAFE_INTEGER)


def _encoded(records):
    constants, index, encoded = [], {}, []
    tables = [r["value"]["tableKey"] for r in records if r["kind"] == "table"]
    partitions = [r["value"] for r in records if r["kind"] == "partition"]
    for record in records:
        kind, value = record["kind"], record["value"]
        if kind == "candidate":
            _require(value["meaning"] == MEANING)
            const = [value["ruleId"], tables.index(value["tableKey"])]; raw = canonical(const)
            if raw not in index: index[raw] = len(constants); constants.append(const)
            value = {"constant":index[raw], **{k:v for k,v in value.items() if k not in CONSTANT_FIELDS}}
        elif kind == "table":
            indices = [i for i,p in enumerate(partitions) if p["tableKey"] == value["tableKey"]]
            _require(_same(value["rules"], [{k:p[k] for k in COUNT} for p in (partitions[i] for i in indices)]))
            value = {**{k:v for k,v in value.items() if k != "rules"},"rulePartitionIndices":indices}
        scan._fields(value, set(SCHEMAS[kind]))
        encoded.append([kind, *[value[k] for k in SCHEMAS[kind]]])
    return constants, encoded


def _records(sources, infos, plan, coverage, candidates):
    return ([{"kind":"source", "value":{"source":s,"info":infos[s["key"]]}} for s in sources]
        + [{"kind":"tableBinding", "value":{"tableKey":digest({k:v for k,v in d.items() if k != "ruleIds"}),
            "sourceKey":d["source"]["key"],"baselineKey":d["baseline"]["key"] if d["baseline"] else None,
            "mode":d["mode"],"dimension":d["dimension"],"masterKey":d["mapping"]["master"]["key"] if d["mapping"] else None,
            "pairKey":d["mapping"]["pairKey"] if d["mapping"] else None,
            "baselinePairKey":d["mapping"]["baselinePairKey"] if d["mapping"] else None}} for d in plan["descriptors"]] + coverage
        + [{"kind":"candidate", "value":c} for group in candidates for c in group["items"]])


@dataclass(frozen=True, slots=True, init=False)
class Package:
    """Frozen bounded JSON; every public result is an independent copy."""
    _raw: str
    _digest: str

    def __init__(self, header, directory, records):
        value = {"header":header, "directory":directory, "records":records}
        _copy(value, MAX_PACKAGE_BYTES)
        object.__setattr__(self, "_raw", canonical(value))
        object.__setattr__(self, "_digest", digest(value))

    @property
    def package_digest(self):
        _require(type(self._raw) is str and len(self._raw.encode("utf-8")) <= MAX_PACKAGE_BYTES
            and storage.raw_digest(self._raw) == self._digest, "角色包内部字节已变化")
        return self._digest

    def page(self, offset=0):
        self.package_digest  # Check immutable byte binding before parsing privately held data.
        data = json.loads(self._raw); header = data["header"]
        scan._integer(offset, 0, header["totalRecords"])
        _require(offset < header["totalRecords"], "角色包偏移须指向完整记录")
        selected = []
        def render():
            end = offset+len(selected)
            value = {**header, "packageDigest":self.package_digest, "directory":data["directory"] if offset == 0 else None,
                "records":selected, "pagination":{"offset":offset,"limit":PAGE_SIZE,"returned":len(selected),
                    "total":header["totalRecords"],"nextOffset":end if end < header["totalRecords"] else None}}
            value["pageDigest"] = digest(value)
            return value
        for record in data["records"][offset:offset+PAGE_SIZE]:
            selected.append(record)
            if len(canonical(render()).encode("utf-8")) > MAX_PAGE_BYTES:
                selected.pop(); break
        value = render()
        _require(bool(selected) and len(canonical(value).encode("utf-8")) <= MAX_PAGE_BYTES, "角色包目录或完整单记录超过页容量")
        return value

    def pages(self):
        offset = 0
        while offset is not None:
            page = self.page(offset)
            yield page
            offset = page["pagination"]["nextOffset"]

    def unpack(self): return decode_pages(self.pages())


def build(bundle, *, sources, source_infos, selection_plan):
    """Return all five role packages, or fail the entire construction."""
    try:
        value = _copy({"bundle":bundle, "sources":sources, "infos":source_infos, "plan":selection_plan}, MAX_INPUT_BYTES)
        bundle, sources, infos, plan = (value[k] for k in ("bundle", "sources", "infos", "plan"))
        manifest = storage.validate(bundle); binding = json.loads(bundle["bindingJson"])
        coverage, by_partition, authority = [], {}, None
        for page in bundle["pages"]:
            payload = json.loads(page["payloadJson"]); authority = payload["authority"]
            if page["kind"] == "coverage": coverage.extend(payload["items"])
            else: by_partition.setdefault(page["partitionKey"], []).extend(payload["items"])
        _require(plan["planDigest"] == manifest["selectionPlanDigest"])
        descriptors, partitions = _validate_common(binding, authority, sources, infos, plan, coverage)
        for key, partition in partitions.items():
            candidates = by_partition[key]
            _require(len(candidates) == partition["retainedRows"])
            for candidate in candidates: _candidate(candidate, partition, descriptors[partition["tableKey"]])
            _require(len({c["candidateId"] for c in candidates}) == len(candidates))
            _require(candidates == sorted(candidates, key=lambda c:(-c["score"], c["reference"]["rowId"])))
        packages, total_bytes = {}, 0
        for role in ROLES:
            keys = sorted(k for k,p in partitions.items() if _role(role, descriptors[p["tableKey"]]))
            candidates = [{"partitionKey":key, "items":by_partition[key]} for key in keys]
            original = _records(sources, infos, plan, coverage, candidates)
            constants, records = _encoded(original)
            _require(1 <= len(records) <= MAX_RECORDS)
            header = {"schemaVersion":SCHEMA,"policy":POLICY,"authorityVerified":False,"role":role,
                "reportId":binding["reportId"],"evidenceRunId":binding["evidenceRunId"],"bindingDigest":digest(binding),
                "selectionPlanDigest":plan["planDigest"],"serviceResultDigest":manifest["serviceResultDigest"],
                "storageManifestDigest":storage.raw_digest(bundle["manifestJson"]),"totalRecords":len(records)}
            package = Package(header, {"recordSchemas":{k:list(v) for k,v in SCHEMAS.items()},"candidateConstants":constants,
                "candidateConstantSchema":["ruleId","tableIndex"],"candidateMeaning":MEANING,
                "tableKeys":[r["value"]["tableKey"] for r in coverage if r["kind"] == "table"],
                "binding":binding,"authority":authority,
                "recordCounts":{kind:sum(r["kind"] == kind for r in original) for kind in SCHEMAS},"requiredPartitionKeys":keys}, records)
            total_bytes += len(package._raw.encode("utf-8"))
            _require(total_bytes <= MAX_ALL_PACKAGE_BYTES, "五角色完整包合计超过容量")
            for _ in package.pages(): pass  # No late oversized record after returning successful construction.
            packages[role] = package
        return packages
    except AnalysisContractError: raise
    except (ValueError, TypeError, KeyError, IndexError, UnicodeError, RecursionError) as error:
        raise AnalysisContractError("角色包输入未通过完整校验") from error


def decode_pages(pages):
    """Validate complete consecutive pages and reconstruct every original value.

    Self-consistency is not source authority; an owning caller must compare the
    package digest/binding with its own fixed report and actual workflow role.
    """
    try:
        header = directory = package_digest = None
        encoded, originals, offset, total_bytes = [], [], 0, 0
        for supplied in pages:
            _require(offset is not None, "终页之后不得追加角色包页")
            page = _copy(supplied, MAX_PAGE_BYTES); total_bytes += len(canonical(page).encode("utf-8"))
            _require(total_bytes <= MAX_ALL_PACKAGE_BYTES, "角色包分页总字节超过容量")
            scan._fields(page, HEADER_FIELDS | {"packageDigest", "directory", "records", "pagination", "pageDigest"})
            _require(page["pageDigest"] == digest({k:v for k,v in page.items() if k != "pageDigest"}))
            current = {k:page[k] for k in HEADER_FIELDS}
            if header is None:
                header, directory, package_digest = current, page["directory"], scan._sha(page["packageDigest"])
                _require(header["schemaVersion"] == SCHEMA and header["policy"] == POLICY and header["authorityVerified"] is False
                    and type(header["role"]) is str and header["role"] in ROLES)
                scan._integer(header["totalRecords"], 1, MAX_RECORDS)
                scan._fields(directory, DIRECTORY_FIELDS)
                _require(_same(directory["recordSchemas"], {k:list(v) for k,v in SCHEMAS.items()}))
                _require(type(directory["candidateConstants"]) is list and len(directory["candidateConstants"]) <= 64)
                _require(directory["candidateConstantSchema"] == ["ruleId","tableIndex"] and directory["candidateMeaning"] == MEANING)
                _require(type(directory["tableKeys"]) is list and 1 <= len(directory["tableKeys"]) <= scan.LIMITS["maxTables"])
                for key in directory["tableKeys"]: scan._sha(key)
                _require(len(set(directory["tableKeys"])) == len(directory["tableKeys"]))
                for const in directory["candidateConstants"]:
                    _require(type(const) is list and len(const) == 2 and type(const[0]) is str and const[0] in scan.RULES)
                    scan._integer(const[1],0,len(directory["tableKeys"])-1)
            else: _require(_same(header, current) and page["packageDigest"] == package_digest and page["directory"] is None)
            records, pagination = page["records"], page["pagination"]
            _require(type(records) is list and 1 <= len(records) <= PAGE_SIZE)
            scan._fields(pagination, {"offset","limit","returned","total","nextOffset"})
            for key in ("offset","limit","returned","total"): scan._integer(pagination[key])
            end = offset+len(records); next_offset = end if end < header["totalRecords"] else None
            if pagination["nextOffset"] is not None: scan._integer(pagination["nextOffset"], 1)
            _require(end <= header["totalRecords"] and pagination == {"offset":offset,"limit":PAGE_SIZE,
                "returned":len(records),"total":header["totalRecords"],"nextOffset":next_offset})
            encoded.extend(records); originals.append(page); offset = next_offset
        _require(header is not None and offset is None, "角色包未完整读完")
        _require(digest({"header":header,"directory":directory,"records":encoded}) == package_digest)
        records = []
        for row in encoded:
            _require(type(row) is list and bool(row) and type(row[0]) is str and row[0] in SCHEMAS)
            kind = row[0]; _require(len(row) == len(SCHEMAS[kind])+1)
            value = dict(zip(SCHEMAS[kind], row[1:]))
            if kind == "candidate":
                index = scan._integer(value.pop("constant"),0,len(directory["candidateConstants"])-1)
                rule, table_index = directory["candidateConstants"][index]
                value = {"ruleId":rule,"tableKey":directory["tableKeys"][table_index],"meaning":MEANING, **value}
            records.append({"kind":kind,"value":value})
        groups = {kind:[r["value"] for r in records if r["kind"] == kind] for kind in SCHEMAS}
        for table in groups["table"]:
            indices = table.pop("rulePartitionIndices")
            _require(type(indices) is list and 1 <= len(indices) <= len(scan.RULES))
            for index in indices: scan._integer(index,0,len(groups["partition"])-1)
            _require(len(set(indices)) == len(indices))
            table["rules"] = [{k:groups["partition"][i][k] for k in COUNT} for i in indices]
            _require(all(groups["partition"][i]["tableKey"] == table["tableKey"] for i in indices))
        sources = [r["source"] for r in groups["source"]]
        infos = {r["source"]["key"]:r["info"] for r in groups["source"]}
        coverage = [r for r in records if r["kind"] in {"family","requested","table","partition"}]
        binding, authority = directory["binding"], directory["authority"]
        _require(directory["tableKeys"] == [r["tableKey"] for r in groups["table"]])
        by_source = {s["key"]:s for s in sources}; tables = {t["tableKey"]:t for t in groups["table"]}
        def source(key):
            return {**by_source[key],**{k:infos[key]["expected"][k] for k in ("sourceRef","evidenceDigest")}}
        descriptors = []
        for fixed in groups["tableBinding"]:
            mapped = None
            if fixed["mode"] == "mapped":
                mapped = {"algorithmVersion":"exact-product-partition-v1","planDigest":binding["mappingPlanDigest"],
                    "master":source(fixed["masterKey"]),"pairKey":fixed["pairKey"],"baselinePairKey":fixed["baselinePairKey"]}
            else: _require(all(fixed[k] is None for k in ("masterKey","pairKey","baselinePairKey")))
            descriptor = {"schemaVersion":scan.DESCRIPTOR_SCHEMA,"source":source(fixed["sourceKey"]),
                "baseline":source(fixed["baselineKey"]) if fixed["baselineKey"] is not None else None,
                "mode":fixed["mode"],"dimension":fixed["dimension"],"mapping":mapped,
                "ruleIds":[r["ruleId"] for r in tables[fixed["tableKey"]]["rules"]]}
            _require(fixed["tableKey"] == digest({k:v for k,v in descriptor.items() if k != "ruleIds"}))
            descriptors.append(descriptor)
        plan = {"descriptors":descriptors,"families":groups["family"],"requestedCoverage":groups["requested"],
            "mappingPlanDigest":binding["mappingPlanDigest"],"planDigest":authority["selectionPlanDigest"]}
        descriptors, partitions = _validate_common(binding,authority,sources,infos,plan,coverage,transport=True)
        keys = sorted(k for k,p in partitions.items() if _role(header["role"],descriptors[p["tableKey"]]))
        _require(_same(directory["requiredPartitionKeys"],keys) and header["bindingDigest"] == digest(binding)
            and header["reportId"] == binding["reportId"] and header["evidenceRunId"] == binding["evidenceRunId"]
            and header["selectionPlanDigest"] == plan["planDigest"])
        for key in ("serviceResultDigest","storageManifestDigest"): scan._sha(header[key])
        selected, index = [], 0
        for key in keys:
            p = partitions[key]; items = groups["candidate"][index:index+p["retainedRows"]]; index += len(items)
            _require(len(items) == p["retainedRows"])
            for candidate in items: _candidate(candidate,p,descriptors[p["tableKey"]])
            _require(len({c["candidateId"] for c in items}) == len(items))
            _require(items == sorted(items,key=lambda c:(-c["score"],c["reference"]["rowId"])))
            selected.append({"partitionKey":key,"items":items})
        _require(index == len(groups["candidate"]))
        ordered = _records(sources,infos,plan,coverage,selected)
        constants, rebuilt = _encoded(ordered)
        _require(_same(rebuilt, encoded) and _same(constants,directory["candidateConstants"]))
        counts = {kind:sum(r["kind"] == kind for r in ordered) for kind in SCHEMAS}
        _require(_same(counts,directory["recordCounts"]))
        reconstructed = Package(header,directory,rebuilt)
        _require(_same(list(reconstructed.pages()),originals), "角色包页不是确定性完整前缀")
        return {"role":header["role"],"authorityVerified":False,"binding":binding,"authority":authority,
            "sources":sources,"sourceInfos":infos,"tableBindings":groups["tableBinding"],"coverage":coverage,"candidates":selected}
    except AnalysisContractError: raise
    except (ValueError,TypeError,KeyError,IndexError,UnicodeError,RecursionError) as error:
        raise AnalysisContractError("角色包解码未通过完整校验") from error
