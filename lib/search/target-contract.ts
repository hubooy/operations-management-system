export const globalSearchGroupKeys = [
  "products",
  "orders",
  "jd_products",
  "inventory",
  "inventory_age",
  "combos",
  "replenishment",
  "market_skus",
  "market_annotations",
  "customer_service",
  "finance",
  "targets",
  "workflow",
  "imports",
] as const;

export type GlobalSearchGroupKey = (typeof globalSearchGroupKeys)[number];

export const globalSearchDefaultTargets = {
  products: { module: "product", view: "overview" },
  orders: { module: "sales", view: "overview" },
  jd_products: { module: "shop", view: "products" },
  inventory: { module: "inventory", view: "overview" },
  inventory_age: { module: "inventory", view: "age" },
  combos: { module: "product", view: "overview" },
  replenishment: { module: "inventory", view: "plan" },
  market_skus: { module: "market", view: "ranking" },
  market_annotations: { module: "market", view: "settings" },
  customer_service: { module: "customer_service", view: "conversations" },
  finance: { module: "sales", view: "finance" },
  targets: { module: "sales", view: "targets" },
  workflow: { module: "workflow", view: "plan" },
  imports: { module: "import", view: "history" },
} as const satisfies Record<GlobalSearchGroupKey, { module: string; view: string }>;

const workflowTargets = {
  task: globalSearchDefaultTargets.workflow,
  inspection: { module: "workflow", view: "inspection" },
  review: { module: "workflow", view: "reviews" },
  launch: { module: "workflow", view: "launch" },
} as const;

export type GlobalSearchNavigationTarget =
  | (typeof globalSearchDefaultTargets)[GlobalSearchGroupKey]
  | (typeof workflowTargets)[keyof typeof workflowTargets];

export type GlobalSearchNavigationModule = GlobalSearchNavigationTarget["module"];

const groupKeySet: ReadonlySet<string> = new Set(globalSearchGroupKeys);
const workflowViews: ReadonlySet<string> = new Set(
  Object.values(workflowTargets).map((target) => target.view),
);

export function isGlobalSearchGroupKey(value: unknown): value is GlobalSearchGroupKey {
  return typeof value === "string" && groupKeySet.has(value);
}

export function getGlobalSearchNavigationTarget(
  group: GlobalSearchGroupKey,
  workflowHint?: string | null,
): GlobalSearchNavigationTarget {
  if (group !== "workflow") return globalSearchDefaultTargets[group];
  return workflowHint === "inspection" || workflowHint === "review" || workflowHint === "launch"
    ? workflowTargets[workflowHint]
    : workflowTargets.task;
}

export function isGlobalSearchModuleForGroup(group: GlobalSearchGroupKey, module: string): boolean {
  return globalSearchDefaultTargets[group].module === module;
}

export function isGlobalSearchNavigationTargetForGroup(
  group: GlobalSearchGroupKey,
  target: { module: string; view: string },
): boolean {
  if (!isGlobalSearchModuleForGroup(group, target.module)) return false;
  if (group === "workflow") return workflowViews.has(target.view);
  return globalSearchDefaultTargets[group].view === target.view;
}
