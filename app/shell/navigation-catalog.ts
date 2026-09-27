export const moduleKeys = [
  "n8n_workflows",
  "dashboard",
  "shop",
  "market",
  "customer_service",
  "sales",
  "inventory",
  "product",
  "workflow",
  "import",
  "settings",
  "ai",
] as const;

export type ModuleKey = (typeof moduleKeys)[number];

/**
 * Canonical, refresh-safe primary workspaces for every shell module. Nested
 * controls that require extra identity (for example a selected product detail)
 * deliberately stay outside this one-level URL contract.
 */
export const moduleViewCatalog = {
  n8n_workflows: {
    defaultView: "jackyun",
    views: ["jackyun", "tmall", "jd", "jd_market", "jd_promotion", "jd_promotion_cut_meat"],
  },
  dashboard: { defaultView: "overview", views: ["overview"] },
  shop: { defaultView: "analysis", views: ["analysis", "outlets", "platforms", "products", "promotion"] },
  market: { defaultView: "ranking", views: ["ranking", "overview", "compare", "settings"] },
  customer_service: { defaultView: "conversations", views: ["conversations"] },
  sales: { defaultView: "overview", views: ["overview", "channel", "category", "finance", "targets"] },
  inventory: { defaultView: "overview", views: ["overview", "age", "plan", "stale", "inbound", "guangdong"] },
  product: { defaultView: "overview", views: ["overview", "calculator"] },
  workflow: { defaultView: "plan", views: ["plan", "inspection", "reviews", "launch", "launch-followup", "variables"] },
  import: { defaultView: "files", views: ["files", "history", "chains"] },
  settings: { defaultView: "parameters", views: ["parameters", "master", "warehouses", "dingtalk", "permissions"] },
  ai: { defaultView: "assistant", views: ["assistant", "agents", "memory", "space", "management", "scheduled", "configuration", "reports", "skills", "pipelines"] },
} as const satisfies Record<ModuleKey, { defaultView: string; views: readonly string[] }>;

export type ModuleViewKey<M extends ModuleKey = ModuleKey> =
  (typeof moduleViewCatalog)[M]["views"][number];

export function getDefaultModuleView<M extends ModuleKey>(module: M): ModuleViewKey<M> {
  return moduleViewCatalog[module].defaultView as ModuleViewKey<M>;
}

export function getModuleViews<M extends ModuleKey>(module: M): readonly ModuleViewKey<M>[] {
  return moduleViewCatalog[module].views as readonly ModuleViewKey<M>[];
}

export function isModuleViewKey<M extends ModuleKey>(module: M, value: string): value is ModuleViewKey<M> {
  return (moduleViewCatalog[module].views as readonly string[]).includes(value);
}

export type NavItem = {
  key: ModuleKey;
  label: string;
  short: string;
  description: string;
  badge?: string;
};

export const navItems = [
  { key: "dashboard", label: "BI 看板", short: "BI", description: "经营驾驶舱" },
  { key: "market", label: "市场分析", short: "市", description: "榜单、行业与竞品洞察" },
  { key: "sales", label: "销售分析", short: "销", description: "利润与渠道表现" },
  { key: "shop", label: "网店分析", short: "店", description: "多网店经营分析" },
  { key: "customer_service", label: "客服分析", short: "服", description: "会话导入与聊天分析" },
  { key: "product", label: "商品经营", short: "品", description: "商品、成本与毛利分析" },
  { key: "inventory", label: "库存管理", short: "库", description: "库存健康与备货" },
  { key: "workflow", label: "运营事务", short: "务", description: "计划、巡店与新品" },
  { key: "n8n_workflows", label: "自动化中心", short: "流", description: "流程运行、核验与恢复" },
  { key: "ai", label: "AI 助理", short: "AI", description: "对话、Agent、记忆、分析沙箱与模型治理" },
  { key: "import", label: "数据导入", short: "入", description: "批次导入与校验" },
  { key: "settings", label: "系统设置", short: "设", description: "参数、映射与权限" },
] as const satisfies readonly NavItem[];

export type NavGroup = {
  label: string;
  keys: readonly ModuleKey[];
};

export const navGroups = [
  {
    label: "协同执行",
    keys: ["workflow", "n8n_workflows"],
  },
  {
    label: "经营分析",
    keys: ["dashboard", "shop", "market", "customer_service", "sales"],
  },
  {
    label: "商品与供应链",
    keys: ["inventory", "product", "import"],
  },
  {
    label: "系统与智能",
    keys: ["settings", "ai"],
  },
] as const satisfies readonly NavGroup[];

export const importSourceKeys = [
  "sales",
  "inventory",
  "products",
  "sku_shipping_rates",
  "inventory_age",
  "combos",
  "finance",
  "jd_sku",
  "jd_sku_images",
  "jd_sku_daily",
  "jd_spu_daily",
  "tmall_product_master",
  "tmall_product_assets",
  "tmall_product_daily",
  "tmall_promotion",
  "customer_service",
] as const;

export type ImportSourceKey = (typeof importSourceKeys)[number];

const moduleKeySet: ReadonlySet<string> = new Set(moduleKeys);
const importSourceKeySet: ReadonlySet<string> = new Set(importSourceKeys);

export function isModuleKey(value: string): value is ModuleKey {
  return moduleKeySet.has(value);
}

export function isImportSourceKey(value: string): value is ImportSourceKey {
  return importSourceKeySet.has(value);
}
