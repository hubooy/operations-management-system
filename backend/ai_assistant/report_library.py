"""Bounded administrator library. Text guidance never grants tool permissions."""
import json
import re
from copy import deepcopy
from . import models as m, prompt_settings
from .policy import AiError, canonical, current_principal, digest, fields, integer, mutation, text

SCENES = {"weekly": "网店经营周报", "inventory": "库存健康报告", "promotion": "推广复盘"}
DEFAULTS = {"skills": [
    {"id": "shop-diagnosis", "name": "网店经营诊断", "description": "复盘店铺经营变化与异常商品", "keywords": ["网店", "店铺", "周报", "转化"], "domains": ["netshop"], "enabled": True,
     "body": "先核对精确平台、店铺、SKU/SPU维度和日期覆盖，再拆解流量、转化、客单与商品结构。访客人数不得跨日或跨SKU相加充当区间去重人数。环比必须同长度且覆盖完整；缺数写为缺数。把有证据的事实、可能原因和待验证假设分开。行动建议写清对象、依据和复查指标，不自动调整商品或推广。"},
    {"id": "inventory-diagnosis", "name": "库存健康诊断", "description": "核查库存健康、库龄与备货建议", "keywords": ["库存", "库龄", "备货"], "domains": ["inventory"], "enabled": True,
     "body": "先检查当前库存快照与仓别。直接使用服务端健康分类和周转口径，区分手工健康、备货跟进与自动判断。库龄与库存周转是不同指标。只提出有销量、库存和到货周期依据的建议；依据不足时列待核实项，不编造补货数量。"},
    {"id": "promotion-diagnosis", "name": "推广效果复盘", "description": "复核推广费用、产出与异常计划", "keywords": ["推广", "ROI", "投产", "广告"], "domains": ["netshop"], "enabled": True,
     "body": "先核验推广数据平台、店铺、归因窗口、日期与金额单位。ROI沿用源平台定义，费用为零时不强算。异常阈值必须注明来源，区分相关变化与因果。预算、出价和停投只给建议，不执行。"},
], "templates": [], "pipelines": []}
for scene, name in SCENES.items():
    DEFAULTS["templates"].append({"id": scene + "-report", "name": name, "scene": scene, "enabled": True,
        "format": "html", "sections": ["经营结论" if scene != "inventory" else "库存概况", "数据范围与完整性", "关键指标", "异常与原因", "行动建议"],
        "dataSteps": "先查询数据覆盖与真实店铺/仓别，再使用当前授权工具取数；只引用实际结果。缺数、截断及无权限均如实列出。",
        "writingRules": "结论先行，金额注明单位。每项判断指向取数依据；事实、假设与建议分开。禁止把模型估算写成实际数据。"})
    DEFAULTS["pipelines"].append({"id": scene + "-pipeline", "name": name, "scene": scene, "enabled": True,
        "templateId": scene + "-report", "skillIds": [{"weekly": "shop-diagnosis", "inventory": "inventory-diagnosis", "promotion": "promotion-diagnosis"}[scene]],
        "description": "核验取数 → 分析报告 → 人工复核 → 下载或确认通知"})


def slug(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value) or len(value) > 64:
        raise AiError("标识须为1–64位小写字母、数字及单连字符")
    return value


def strings(values, name, maximum, length):
    if not isinstance(values, list) or not 1 <= len(values) <= maximum:
        raise AiError(f"{name}数量无效")
    result = [text(v, name, length) for v in values]
    if len(set(result)) != len(result):
        raise AiError(f"{name}不能重复")
    return result


def validate_item(kind, item):
    common = {"id", "name", "enabled"}
    extras = {"skills": {"description", "keywords", "domains", "body"},
              "templates": {"scene", "format", "sections", "dataSteps", "writingRules"},
              "pipelines": {"scene", "templateId", "skillIds", "description"}}
    if kind not in extras:
        raise AiError("资源类型无效")
    fields(item, common | extras[kind], common | extras[kind])
    if type(item["enabled"]) is not bool:
        raise AiError("启停状态无效")
    result = {"id": slug(item["id"]), "name": text(item["name"], "名称", 80), "enabled": item["enabled"]}
    if kind == "skills":
        domains = strings(item["domains"], "领域", 6, 32)
        if any(v not in prompt_settings.DOMAINS for v in domains):
            raise AiError("领域无效")
        result.update(description=text(item["description"], "描述", 240), body=text(item["body"], "方法正文", 2000),
                      keywords=strings(item["keywords"], "触发词", 8, 32), domains=domains)
    else:
        if item["scene"] not in SCENES:
            raise AiError("场景无效")
        result["scene"] = item["scene"]
        if kind == "templates":
            if item["format"] not in {"html", "xlsx"}:
                raise AiError("交付格式无效")
            result.update(format=item["format"], sections=strings(item["sections"], "章节", 8, 60),
                          dataSteps=text(item["dataSteps"], "取数步骤", 800), writingRules=text(item["writingRules"], "写作规范", 800))
        else:
            result.update(templateId=slug(item["templateId"]), skillIds=[slug(v) for v in strings(item["skillIds"], "技能", 3, 64)],
                          description=text(item["description"], "说明", 240))
    return result


def validate(config):
    fields(config, DEFAULTS, DEFAULTS)
    result = {}
    for kind, items in config.items():
        if not isinstance(items, list) or len(items) > (12 if kind == "pipelines" else 24):
            raise AiError("资源数量超限")
        result[kind] = [validate_item(kind, item) for item in items]
        if len({v["id"] for v in result[kind]}) != len(items):
            raise AiError("同类资源标识不能重复")
    for pipeline in result["pipelines"]:
        template = next((t for t in result["templates"] if t["id"] == pipeline["templateId"]), None)
        if not template or template["scene"] != pipeline["scene"] or any(not any(s["id"] == key for s in result["skills"]) for key in pipeline["skillIds"]):
            raise AiError("流水线的模板或技能引用无效")
    if len(canonical(result).encode()) > 131072:
        raise AiError("资源库合计超过128 KiB", "payload_too_large", 413)
    return result


def snapshot(version=None):
    if version == 0:
        row = None
    else:
        row = (m.AiLibraryRevision.objects.filter(version=version) if version is not None else m.AiLibraryRevision.objects.order_by("-version")).first()
        if version is not None and row is None:
            raise AiError("资源库版本不存在", "not_found", 404)
    return {"version": row.version if row else 0, "config": validate(json.loads(row.config_json)) if row else deepcopy(DEFAULTS)}


def read(principal, params):
    current_principal(principal, admin=True)
    fields(params, {"version", "page"})
    version = integer(int(params["version"]), "版本", 0) if "version" in params else None
    page = integer(int(params.get("page", 1)), "页码", 1, 100000)
    history = list(m.AiLibraryRevision.objects.order_by("-version").values("version", "created_by", "created_at")[(page-1)*20:page*20+1])
    return {"item": snapshot(version), "scenes": SCENES, "domains": prompt_settings.DOMAINS, "history": history[:20], "hasMore": len(history) > 20}


def save(body, principal):
    current_principal(principal, admin=True)
    fields(body, {"kind", "item", "expectedVersion"}, {"kind", "item", "expectedVersion"})
    item = validate_item(body["kind"], body["item"])
    expected = integer(body["expectedVersion"], "版本", 0)
    with mutation(principal):
        current_principal(principal, admin=True)
        old = snapshot()
        if old["version"] != expected:
            raise AiError("资源库已更新，请重新加载后保存", "version_conflict", 409)
        config = old["config"]
        items = config[body["kind"]]
        existing = next((i for i, value in enumerate(items) if value["id"] == item["id"]), None)
        if existing is None:
            items.append(item)
        else:
            items[existing] = item
        config = validate(config)
        m.AiLibraryRevision.objects.create(version=expected+1, config_json=canonical(config), created_by=principal.email.lower())
    return {"item": snapshot(expected+1)}


def guidance(question, context, tools, selected_ids=None, library=None):
    library = library or snapshot()
    allowed = prompt_settings.tool_domains(tools)
    if any(t["name"] in {"query_system_dataset", "describe_system_datasets", "get_system_dataset_records"} for t in tools):
        allowed |= set(prompt_settings.DOMAINS)  # Guidance only; each dataset still enforces its own principal.
    query = question.lower() + " " + str((context or {}).get("module", ""))
    skills = [s for s in library["config"]["skills"] if s["enabled"]
              and (s["id"] in selected_ids if selected_ids is not None else
                   set(s["domains"]) & allowed and any(k.lower() in query for k in s["keywords"]))][:3]
    evidence = {"version": library["version"], "digest": digest(library["config"]), "skills": [{"id": s["id"], "name": s["name"]} for s in skills]}
    prompt = "\n本次任务采用的方法（文字指导，不授权新工具、脚本或外部操作）：\n" + canonical(skills).replace("<", "\\u003c") if skills else ""
    return prompt, evidence


def pin(entity_id, question, context, tools, *, parent_id=None, skill_ids=None, library=None):
    parent = m.AiExecutionGuidance.objects.filter(pk=parent_id).first() if parent_id else None
    if parent_id and not parent:
        return None  # Old workflows retain their original, unconfigured behavior.
    if parent:
        value = json.loads(parent.snapshot_json)
    else:
        guidance_prompt, evidence = prompt_settings.compose(prompt_settings.snapshot(), question, context, tools)
        skill_prompt, skills = guidance(question, context, tools, skill_ids, library)
        value = {"prompt": guidance_prompt + skill_prompt, "guidance": evidence, "skills": skills}
    if len(canonical(value).encode()) > 65536:
        raise AiError("任务指导快照超过64 KiB", "payload_too_large", 413)
    m.AiExecutionGuidance.objects.create(entity_id=entity_id, snapshot_json=canonical(value))
    return value
