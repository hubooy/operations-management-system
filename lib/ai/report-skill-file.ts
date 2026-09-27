/** The v1 portable format has a JSON YAML-compatible frontmatter object and Markdown body. */
export function parseReportSkill(source: string) {
  if (new TextEncoder().encode(source).length > 65536) throw new Error("技能文件超过64 KiB。");
  const match = /^---\n([\s\S]*?)\n---\n([\s\S]*)$/.exec(source.replace(/\r\n/g, "\n"));
  if (!match) throw new Error("文件须包含本系统导出的JSON元数据头与Markdown正文。");
  const value = JSON.parse(match[1]) as Record<string, unknown>;
  const keys = ["id", "name", "enabled", "description", "keywords", "domains"];
  if (!value || typeof value !== "object" || Array.isArray(value) || Object.keys(value).length !== keys.length || keys.some(k => !Object.hasOwn(value, k))) throw new Error("技能元数据字段无效，不支持脚本或附件。");
  const text = (v: unknown, maximum: number) => {
    if (typeof v !== "string" || !v.trim() || v.trim().length > maximum) throw new Error("技能字段为空或超过上限。");
    return v.trim();
  };
  const list = (v: unknown, maximum: number, length: number) => {
    if (!Array.isArray(v) || !v.length || v.length > maximum) throw new Error("技能列表字段无效。");
    const result = v.map(x => text(x, length));
    if (new Set(result).size !== result.length) throw new Error("技能列表不能重复。");
    return result;
  };
  const id = text(value.id, 64);
  if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(id) || typeof value.enabled !== "boolean") throw new Error("技能标识或启停状态无效。");
  const domains = list(value.domains, 6, 32);
  if (domains.some(d => !["sales", "inventory", "netshop", "market", "customer_service", "finance"].includes(d))) throw new Error("技能领域无效。");
  return { id, name: text(value.name, 80), description: text(value.description, 240), enabled: value.enabled, keywords: list(value.keywords, 8, 32), domains, body: text(match[2], 2000) };
}
