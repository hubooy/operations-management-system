"""Strict screening answers and fixed numeric references; no runtime registration."""
from copy import deepcopy
import json
import math
import re

from business_analysis.results import VIEWS
from business_analysis.mapped_results import METRICS as MAPPED_METRICS
from . import business_screening_tools as tools, business_screening_claims as claims
from . import business_screening_runtime_contract as contract
from .policy import AiError, canonical, fields, identifier, integer, text

SCHEMA = "business-screening-diagnosis-v1"
VALUE_FIELDS = frozenset(("value","ratio","baseline","difference","changeRate","percentagePoints"))
MAX_REFERENCES = 32


def _reject(message="筛查分析结构无效，保留已知结果供复核"):
    raise AiError(message,"conflict",409)


def _bounded(value, maximum):
    # Bound structure before canonical encoding; cycles and extreme nesting
    # cannot reach the serializer or turn a known provider result into a crash.
    stack, count = [(value,0)],0
    while stack:
        item,depth = stack.pop();count += 1
        if count>4000 or depth>16: _reject("筛查分析 JSON 结构超限")
        if type(item) is dict:
            if len(item)>40 or any(type(k) is not str for k in item): _reject()
            stack.extend((v,depth+1) for v in item.values())
        elif type(item) is list:
            if len(item)>100: _reject()
            stack.extend((v,depth+1) for v in item)
        elif item is not None and type(item) not in (str,int,bool,float): _reject()
    try: raw = canonical(value)
    except (ValueError,TypeError,UnicodeError,RecursionError) as error:
        raise AiError("筛查分析 JSON 无效","conflict",409) from error
    try: size = len(raw.encode("utf-8"))
    except UnicodeError as error: raise AiError("筛查分析编码无效","conflict",409) from error
    if size>maximum: raise AiError("筛查分析完整输出超过容量","payload_too_large",413)
    return json.loads(raw)


def _role(role):
    if type(role) is not str or role not in contract.ROLES: _reject("筛查分析角色无效")
    return role


def _sha(value):
    if type(value) is not str or re.fullmatch("[a-f0-9]{64}",value) is None: _reject("引用必须使用完整固定摘要")
    return value


def _reference(value):
    if type(value) is not dict: _reject()
    if "candidateId" in value:
        fields(value,{"candidateId","metric","field"},{"candidateId","metric","field"})
        _sha(value["candidateId"]);identifier(value["metric"],"metric")
        if type(value["field"]) is not str or value["field"] not in claims.FIELDS: _reject("候选数值字段无效")
    else:
        mapped = "pairKey" in value
        source,base = ("pairKey","baselinePairKey") if mapped else ("sourceKey","baselineKey")
        keys = {source,base,"dimension","rowIndex","rowId","metric","field"}
        fields(value,keys,keys-{base})
        for key in (source,base):
            if key in value: (_sha if mapped else identifier)(value[key])
        _sha(value["rowId"]);integer(value["rowIndex"],"rowIndex",lo=0,hi=249999)
        identifier(value["metric"],"metric")
        if type(value["dimension"]) is not str or value["dimension"] not in ({"sku","spu"} if mapped else VIEWS): _reject()
        if type(value["field"]) is not str or value["field"] not in (
                {"value","baseline","difference","changeRate"} if mapped else VALUE_FIELDS): _reject()
        if mapped and value["metric"] not in MAPPED_METRICS: _reject()
        if value["field"] not in {"value","ratio"} and base not in value: _reject("比较引用缺少固定基期")
    return value


def _diagnosis(value, role):
    fields(value,{"summary","findings"},{"summary","findings"})
    value["summary"] = text(value["summary"],"summary",2000)
    findings = value["findings"]
    if type(findings) is not list or not 1<=len(findings)<=(12 if role=="report" else 2): _reject("诊断结论数量无效")
    ids,count = set(),0
    for finding in findings:
        keys = {"id","kind","title","explanation","references","action"}
        fields(finding,keys,keys-{"action"})
        key = identifier(finding["id"])
        if key in ids or type(finding["kind"]) is not str or finding["kind"] not in {"observation","hypothesis","action","gap"}: _reject()
        ids.add(key)
        finding["title"] = text(finding["title"],"title",160)
        finding["explanation"] = text(finding["explanation"],"explanation",1200)
        refs = finding["references"]
        if type(refs) is not list or not (0 if finding["kind"]=="gap" else 1)<=len(refs)<=6: _reject("结论缺少引用或引用过多")
        count += len(refs)
        if count>MAX_REFERENCES: _reject("诊断引用超过 32 项")
        for ref in refs: _reference(ref)
        if finding["kind"]=="action":
            action = finding.get("action");fields(action,contract.ACTION_FIELDS,contract.ACTION_FIELDS)
            for name in contract.ACTION_FIELDS-{"observationDays","priority"}: action[name]=text(action[name],name,600)
            integer(action["observationDays"],"observationDays",lo=1,hi=90)
            if type(action["priority"]) is not str or action["priority"] not in {"high","medium","low"}: _reject()
        elif "action" in finding: _reject("只有动作结论可以包含执行规划")
    return value


def _unique(pairs):
    value = {}
    for key,item in pairs:
        if key in value: _reject("筛查分析不能包含重复 JSON 键")
        value[key]=item
    return value


def validate_answer(role, answer):
    """Pure shape validation, including raw UTF-8 output limit; never a proof."""
    _role(role)
    if type(answer) is not str: _reject("模型分析必须为 JSON 文本")
    try:
        if len(answer.encode("utf-8"))>contract.OUTPUT_LIMITS[role]:
            raise AiError("专业分析输出超过固定容量","payload_too_large",413)
        value=json.loads(answer,object_pairs_hook=_unique,parse_constant=lambda _: _reject("非有限 JSON 数值"))
    except (ValueError,TypeError,UnicodeError,RecursionError) as error:
        raise AiError("专业分析不是有效 JSON","conflict",409) from error
    value=_bounded(value,contract.OUTPUT_LIMITS[role])
    if role=="independent_review":
        fields(value,{"approved","conflicts","limitations"},{"approved","conflicts","limitations"})
        if type(value["approved"]) is not bool: _reject()
        for key in ("conflicts","limitations"):
            if type(value[key]) is not list or len(value[key])>20: _reject()
            value[key]=[text(item,key,1000) for item in value[key]]
    elif role=="report":
        fields(value,{"sections","diagnosis"},{"sections","diagnosis"})
        sections=value["sections"]
        if type(sections) is not list or len(sections)!=len(contract.SECTIONS): _reject("报告章节缺失")
        for section,title in zip(sections,contract.SECTIONS):
            fields(section,{"title","body"},{"title","body"})
            if section["title"]!=title: _reject("报告章节顺序不符")
            section["body"]=text(section["body"],"body",10000)
        _diagnosis(value["diagnosis"],role)
    else: _diagnosis(value,role)
    return value


def _detail(prepared, reference, principal, cache):
    mode="mapped" if "pairKey" in reference else "native"
    selector={k:reference[k] for k in ("sourceKey","baselineKey","pairKey","baselinePairKey","dimension") if k in reference}
    args={"runId":prepared.reference["evidenceRunId"],"reportId":prepared.report_id,
        "screeningId":prepared.reference["screeningIntent"]["id"],"mode":mode,
        "offset":reference["rowIndex"],**selector}
    key=canonical(args)
    if key not in cache: cache[key]=tools.analysis_from(prepared,args,principal)
    page=cache[key];table=page["table"]
    if (page["reference"]!=prepared.reference or page["mode"]!=mode or page["selector"]!=selector): _reject("明细引用跨固定范围")
    rows=table["rows"]
    if not rows or rows[0]["id"]!=reference["rowId"] or rows[0]["rowIndex"]!=reference["rowIndex"]: _reject("引用分析行不存在或身份不同")
    row,metric,field=rows[0],reference["metric"],reference["field"]
    if field=="value":
        entry=row["metrics"].get(metric) or {};number=entry.get("value");partial=bool(entry.get("missingRows"))
    elif field=="ratio": number=row["ratios"].get(metric);partial=False
    else:
        number=row["comparisons"].get(metric,{}).get(field)
        partial=bool(((row.get("baselineMetrics") or {}).get(metric) or {}).get("missingRows")) if field=="baseline" else False
    if number is None: _reject("引用数值缺失，须作为数据缺口披露")
    if type(number) not in (int,float) or type(number) is float and not math.isfinite(number): _reject("明细引用数值无效")
    result={"reference":deepcopy(reference),"value":number,"partial":partial,"entity":deepcopy(row["entity"]),
        "sourceRef":table["source"]["sourceRef"],"evidenceDigest":table["source"]["evidenceDigest"],
        "coverage":deepcopy(table["sourceMetadata"]["coverage"]),"screeningReference":prepared.reference,
        "verification":{"numericReferenceVerified":True,"causalityVerified":False,"humanReviewRequired":True,"agentReadVerified":False}}
    if mode=="mapped":
        result.update(mappingBindingDigest=table["bindingDigest"],mappingBinding=deepcopy(table["binding"]),
            baselineMappingBinding=deepcopy(table["baselineBinding"]),historicalMapping=False)
    result["limitations"]=deepcopy(table["limitations"])
    return result


def validate(value, report, principal, *, role="report", prepared=None, claims_verified=None):
    """Resolve fixed facts. Caller must separately prove this actual job read them."""
    _role(role)
    if role=="independent_review": _reject("独立复核不使用诊断结论结构")
    value=_diagnosis(_bounded(value,contract.OUTPUT_LIMITS[role]),role)
    prepared=tools.prepare_for_report(report,principal) if prepared is None else prepared
    actual,snapshot,_,_,_=tools._checked(prepared,principal)
    if actual.id!=report.id or actual.snapshot_json!=report.snapshot_json: _reject("诊断准备对象不属于此报告")
    has_candidates=any("candidateId" in ref for finding in value["findings"] for ref in finding["references"])
    if has_candidates:
        verified=claims.prepare(prepared.packages,role,principal) if claims_verified is None else claims_verified
        row,_=claims._checked(verified,principal)
        if (verified._role!=role or row.id!=snapshot["screeningIntent"]["id"] or row.report_id!=actual.id
                or verified._package_digest!=dict(json.loads(prepared._package_digests_json))[role]): _reject("候选解析对象跨角色或报告")
    output,cache=[],{}
    for finding in value["findings"]:
        item={k:deepcopy(v) for k,v in finding.items() if k!="references"}
        item["facts"]=[claims.resolve(verified,ref,principal) if "candidateId" in ref else _detail(prepared,ref,principal,cache)
            for ref in finding["references"]]
        output.append(item)
    tools._checked(prepared,principal)
    return {"schemaVersion":SCHEMA,"evidenceRunId":snapshot["evidenceRunId"],"reportId":actual.id,
        "screeningId":snapshot["screeningIntent"]["id"],"role":role,"summary":value["summary"],"findings":output,
        "factsVerified":True,"humanReviewRequired":True,"causalityVerified":False,"agentReadVerified":False,
        "limitations":["仅核验结构化数值引用；文字、缺口解释与因果仍需独立和人工复核",
            "候选不是完整明细；同一事实可能被多个规则或维度引用，不得相加为总损失",
            "调整规划不自动修改预算、商品或业务数据"]}
