"""Complete screening proof appendices; candidates never replace fact tables."""

from business_analysis import screening_package
from . import business_screening_runtime as runtime, business_screening_store as store
from . import business_screening_packages as packages
from .policy import AiError, canonical


def binding(report, principal):
    """Live immutable roots only; safe for each individual chunk download."""
    actual, snapshot, _, _, _, _ = runtime.bound(report, principal)
    saved, _, _ = store._loaded(snapshot["screeningIntent"]["id"], principal)
    if saved.report_id != actual.id:
        raise AiError("筛查文件不属于当前报告", "conflict", 409)
    return {"screeningRef":store._reference(saved), "screeningPackagePolicy":screening_package.POLICY}


def metadata(report, principal):
    fixed = binding(report, principal)
    prepared = packages.prepare(fixed["screeningRef"]["id"], principal)
    described = packages.describe(prepared, principal)
    if described["reference"] != fixed["screeningRef"] or binding(report, principal) != fixed:
        raise AiError("筛查文件固定结果已变化", "conflict", 409)
    return {**fixed, "screeningPackageDigests":{role:described["roles"][role]["packageDigest"] for role in screening_package.ROLES}}


def _chunks(records):
    # One exact canonical record split into bounded cells, never flatten dynamic
    # metric keys into >160 columns or truncate a long source/coverage record.
    for index, record in enumerate(records):
        raw = canonical(record)
        for offset in range(0, max(1, len(raw)), 7000):
            yield {"recordIndex":index, "fragmentIndex":offset//7000+1,
                "fragmentCount":max(1,(len(raw)+6999)//7000), "canonicalJson":raw[offset:offset+7000]}


def append(spool, report, principal, expected_metadata, *, read_proofs=None, checkpoint=None):
    fixed = binding(report, principal)
    prepared = packages.prepare(fixed["screeningRef"]["id"], principal)
    described = packages.describe(prepared, principal)
    actual_metadata = {**fixed,"screeningPackageDigests":{role:described["roles"][role]["packageDigest"] for role in screening_package.ROLES}}
    if canonical(actual_metadata) != canonical({k:expected_metadata[k] for k in actual_metadata}):
        raise AiError("筛查文件证明与完整清单绑定不一致", "conflict", 409)
    _, package, _ = next(entry for entry in prepared._packages if entry[0] == "report")
    decoded = packages._call(screening_package.decode_pages, package.pages())
    groups = [("authority", "筛查完整性声明", [decoded["authority"]]),
        ("sources", "筛查来源", [{"source":s,"info":decoded["sourceInfos"][s["key"]]} for s in decoded["sources"]]),
        ("table-bindings", "筛查表绑定", decoded["tableBindings"])]
    for kind, title in (("family","筛查来源族"),("requested","筛查请求覆盖"),("table","筛查完整表核对"),("partition","筛查规则分区")):
        groups.append((kind,title,[r["value"] for r in decoded["coverage"] if r["kind"] == kind]))
    groups.append(("candidates","全部保留候选",[c for group in decoded["candidates"] for c in group["items"]]))
    groups.append(("roles","五角色候选阅读范围",[{"role":role, **described["roles"][role]} for role in screening_package.ROLES]))
    if read_proofs is not None:
        groups.append(("read-proofs","五角色实际读取回执",[{"role":role,"proof":read_proofs[role]} for role in screening_package.ROLES]))
    note = "完整规范JSON按recordIndex/fragmentIndex拼接；候选仅为保留集合，未保留计数见分区，不能替代全明细或跨规则加总。"
    index = []
    for key,title,records in groups:
        if checkpoint: checkpoint()
        count = sum(max(1,(len(canonical(record))+6999)//7000) for record in records)
        spool.add("screening-"+key,title,note,_chunks(records),count)
        index.append({"tableKey":"screening-"+key,"recordCount":len(records),"fragmentRows":count})
    spool.add("screening-index","筛查证明附表目录",note,index,len(index))
    packages.describe(prepared,principal)
    if binding(report,principal) != fixed:
        raise AiError("筛查文件出口身份已变化", "conflict", 409)
