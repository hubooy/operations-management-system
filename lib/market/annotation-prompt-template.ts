/**
 * 新三级类目在人工写出第一版 Prompt 之前没有任何可激活版本，标注任务因此无法创建。
 * 这里只提供一个可编辑的起草模板：正式版本仍必须由人工保存子版本并通过激活门禁，
 * 模板本身不会被自动落库，也不会绕过冻结验证。
 */
export function defaultAnnotationPromptBody(category: string, segments: readonly string[]) {
  const categoryLabel = category.trim() || "该三级类目";
  const segmentLabel = segments.map((item) => item.trim()).filter(Boolean).join("、");
  return [
    `你是电商商品分类专家。根据商品主图和商品名称，判断该商品属于「${categoryLabel}」下的哪个细分品类。`,
    "",
    "判定要求：",
    "1. 细分品类必须从下方允许列表中原样选择一个，不得自造、合并或改写；",
    "2. 优先依据主图中的商品本体形态、结构和使用场景，商品名称只作为辅助证据；",
    "3. 主图主体是滤芯、配件、耗材或包装箱时，按配件类细分品类归类，不要按整机归类；",
    "4. 证据不足以区分时才选择列表中的兜底项，并在依据里写明缺少哪些证据；",
    "5. 依据用一句话说明判定理由，不要复述商品名称。",
    "",
    segmentLabel ? `当前允许的细分品类：${segmentLabel}。` : "当前类目尚未维护细分品类字典，请先在细分品类设置中补齐。",
  ].join("\n");
}
