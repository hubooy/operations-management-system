"""Live-authorized ERP exact source pages; reads only published metadata."""
from django.core import signing
from django.db import connection
from django.db.models import Q
from django.db.models.functions import Collate
from . import analysis_options_contract as contract
from .analysis_options_projection import OptionsError, current_principal, revision_snapshot, state_snapshot, row_entry, FIELDS
from .models import SalesAnalysisOption

SALT = "sales-analysis-options-v1"


def validate_request(params):
    allowed = set(contract.IDENTITY_FIELDS) | {"cursor", "limit"}
    if len(params) > len(allowed) or set(params)-allowed or any(len(params.getlist(key)) != 1 for key in params):
        raise OptionsError("ERP来源选项参数未知或重复")
    try:
        query = contract.normalize_query({key: params[key] for key in contract.IDENTITY_FIELDS if key in params})
        if "limit" in params and params["limit"] != "20": raise contract.OptionsContractError("每页固定20项")
        cursor = params.get("cursor")
        if cursor is not None: contract._text(cursor, contract.MAX_CURSOR_LENGTH)
    except contract.OptionsContractError as error:
        raise OptionsError(str(error)) from error
    return query, cursor


def read_page(principal, query, cursor=None):
    actor = current_principal(principal)
    try:
        query = contract.normalize_query(query)
        if cursor is not None: contract._text(cursor, contract.MAX_CURSOR_LENGTH)
    except contract.OptionsContractError as error:
        raise OptionsError(str(error)) from error
    before = revision_snapshot()
    state = state_snapshot(before)
    material = {"schemaVersion":"business-analysis-options-v1", "domain":"sales", "actor":actor,
        "query":query, "revision":before, "generation":state["generation"], "directoryDigest":state["directory_digest"], "limit":20}
    binding, last = contract.digest(material), None
    if cursor is not None:
        try:
            value = signing.loads(cursor, salt=SALT, max_age=3600)
            if type(value) is not dict or set(value) != {"actor","binding","last"}: raise ValueError()
            if value["actor"] != actor: raise OptionsError("账号或权限版本已变化", code="access_denied", status=403)
            if value["binding"] != binding: raise ValueError()
            last = contract.normalize_identity(value["last"])
        except (signing.BadSignature, ValueError, TypeError, RecursionError) as error:
            if isinstance(error, OptionsError): raise
            raise OptionsError("目录或游标已变化，请从首页重新读取", code="options_revision_changed", status=409) from error
    collation = "C" if connection.vendor == "postgresql" else "BINARY"
    keys = [f"key_{i}" for i in range(3)]
    values = SalesAnalysisOption.objects.annotate(**{key:Collate(field,collation) for key,field in zip(keys,FIELDS)})
    for index,field in enumerate(FIELDS):
        if field in query: values=values.filter(**{keys[index]:query[field]})
    if last is not None:
        after=Q(pk__in=[])
        for index,key in enumerate(keys):
            clause=Q(**{key+"__gt":last[FIELDS[index]]})
            for prior in range(index): clause &= Q(**{keys[prior]:last[FIELDS[prior]]})
            after |= clause
        values=values.filter(after)
    selected=list(values.order_by(*keys).values("platform","shop","channel","first_date","last_date","row_count","entry_json","entry_digest")[:21])
    groups=[row_entry(row) for row in selected[:20]]
    more=len(selected)>20
    next_cursor=signing.dumps({"actor":actor,"binding":binding,"last":contract.normalize_group(groups[-1])["identity"]},salt=SALT,compress=True) if more else None
    try:
        result=contract.make_page(groups,query=query,revision=before,has_more=more,next_cursor=next_cursor,previous_identity=last)
    except contract.OptionsContractError as error:
        raise OptionsError(str(error),code="invalid_source_metadata",status=413) from error
    result.update(authorityVerified=True,directoryGeneration=state["generation"],directoryDigest=state["directory_digest"])
    result["limitations"][0]="已核验当前账号与完整初始化的ERP精确身份目录；日期仍不代表连续覆盖。"
    result.pop("pageDigest")
    result["pageDigest"]=contract.digest(result)
    if len(contract.canonical(result).encode())>contract.MAX_PAGE_BYTES:
        raise OptionsError("完整ERP来源页超限，未截断",code="payload_too_large",status=413)
    if actor != current_principal(principal): raise OptionsError("读取期间权限已变化",code="access_denied",status=403)
    after=revision_snapshot()
    if before!=after or state!=state_snapshot(after): raise OptionsError("读取期间来源已变化，请重读",code="options_revision_changed",status=409)
    return result
