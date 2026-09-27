"use client";

import { useRef, type RefObject } from "react";

import {
  isGlobalSearchGroupKey,
  isGlobalSearchModuleForGroup,
  isGlobalSearchNavigationTargetForGroup,
  type GlobalSearchGroupKey as SearchGroupKey,
} from "@/lib/search/target-contract";

import {
  isModuleKey,
  isModuleViewKey,
  navItems,
  type ModuleKey,
  type ModuleViewKey,
} from "./shell/navigation-catalog";
import Dialog from "./ui/dialog";

export { globalSearchGroupKeys } from "@/lib/search/target-contract";
export type GlobalSearchGroupKey = SearchGroupKey;

export const globalSearchEntityKinds = [
  "product",
  "order",
  "inventory",
  "market_sku",
  "customer_conversation",
  "finance_record",
  "target",
  "workflow_task",
  "import_batch",
] as const;

export type GlobalSearchEntityKind = (typeof globalSearchEntityKinds)[number];

export type GlobalSearchTarget<M extends ModuleKey = ModuleKey> = M extends ModuleKey
  ? {
      module: M;
      view: ModuleViewKey<M>;
      entity?: {
        kind: GlobalSearchEntityKind;
        id: string;
      };
    }
  : never;

type GlobalSearchItemForModule<M extends ModuleKey> = {
  kind: GlobalSearchGroupKey;
  id: string;
  title: string;
  subtitle: string;
  detail: string;
  updatedAt: string;
  amountCents: number | null;
  module: M;
  target: GlobalSearchTarget<M>;
};

export type GlobalSearchItem = {
  [M in ModuleKey]: GlobalSearchItemForModule<M>;
}[ModuleKey];

export type GlobalSearchGroup = {
  key: GlobalSearchGroupKey;
  label: string;
  icon: string;
  module: ModuleKey;
  available: boolean;
  page?: number;
  total: number;
  totalExact?: boolean;
  hasMore: boolean;
  items: GlobalSearchItem[];
};

export type GlobalSearchResult = {
  query: string;
  page?: number;
  returned: number;
  truncated: boolean;
  deadlineExceeded?: boolean;
  timedOutDomains?: string[];
  groups: GlobalSearchGroup[];
  unavailableDomains: string[];
  error?: string;
};

export type GlobalSearchDialogProps = {
  open: boolean;
  query: string;
  result: GlobalSearchResult | null;
  loading: boolean;
  error: string;
  onQueryChange: (query: string) => void;
  onClose: () => void;
  onSelectItem: (item: GlobalSearchItem) => void;
  onSelectQuickModule: (module: ModuleKey) => void;
  onLoadMoreGroup?: (groupKey: GlobalSearchGroupKey, nextPage: number) => void;
  loadingGroup?: GlobalSearchGroupKey | null;
  loadMoreError?: string;
  returnFocusRef?: RefObject<HTMLElement | null>;
};

export type GlobalSearchPresentation = {
  showGuide: boolean;
  showShortQuery: boolean;
  showLoading: boolean;
  showError: boolean;
  showResult: boolean;
};

const entityKindSet: ReadonlySet<string> = new Set(globalSearchEntityKinds);
const globalSearchEntityModuleAllowlist: Record<GlobalSearchEntityKind, readonly ModuleKey[]> = {
  product: ["product", "shop"],
  order: ["sales"],
  inventory: ["inventory"],
  market_sku: ["market"],
  customer_conversation: ["customer_service"],
  finance_record: ["sales"],
  target: ["sales"],
  workflow_task: ["workflow"],
  import_batch: ["import"],
};
const globalSearchEntityGroupAllowlist: Partial<Record<GlobalSearchGroupKey, readonly GlobalSearchEntityKind[]>> = {
  products: ["product"],
  orders: ["order"],
  jd_products: ["product"],
  inventory: ["inventory"],
  inventory_age: ["inventory"],
  combos: ["product"],
  replenishment: ["inventory"],
  market_skus: ["market_sku"],
  market_annotations: ["market_sku"],
  customer_service: ["customer_conversation"],
  finance: ["finance_record"],
  targets: ["target"],
  workflow: ["workflow_task"],
  imports: ["import_batch"],
};
const targetKeys = new Set(["module", "view", "entity"]);
const entityKeys = new Set(["kind", "id"]);

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasOnlyKeys(value: Record<string, unknown>, allowed: ReadonlySet<string>): boolean {
  return Object.keys(value).every((key) => allowed.has(key));
}

/** Runtime gate for an optional server-provided target. Arbitrary URLs and keys are rejected. */
export function parseGlobalSearchTarget(value: unknown): GlobalSearchTarget | null {
  if (!isPlainRecord(value) || !hasOnlyKeys(value, targetKeys)) return null;
  const moduleKey = value.module;
  if (typeof moduleKey !== "string" || !isModuleKey(moduleKey)) return null;
  const view = value.view;
  if (typeof view !== "string" || !isModuleViewKey(moduleKey, view)) return null;

  const entity = value.entity;
  if (entity !== undefined) {
    if (!isPlainRecord(entity) || !hasOnlyKeys(entity, entityKeys)) return null;
    const kind = entity.kind;
    const id = entity.id;
    if (typeof kind !== "string" || !entityKindSet.has(kind)) return null;
    if (typeof id !== "string" || !id.trim() || id.length > 256 || /[\u0000-\u001f\u007f]/.test(id)) return null;
  }

  return {
    module: moduleKey,
    view,
    ...(isPlainRecord(entity)
      ? { entity: { kind: entity.kind as GlobalSearchEntityKind, id: String(entity.id).trim() } }
      : {}),
  } as GlobalSearchTarget;
}

export function isGlobalSearchItemModuleValid(item: Pick<GlobalSearchItem, "kind" | "module">): boolean {
  return isGlobalSearchGroupKey(item.kind)
    && isModuleKey(item.module)
    && isGlobalSearchModuleForGroup(item.kind, item.module);
}

export function isGlobalSearchTargetForItem(item: GlobalSearchItem, target: GlobalSearchTarget): boolean {
  if (!isGlobalSearchItemModuleValid(item) || target.module !== item.module) return false;
  if (!isGlobalSearchNavigationTargetForGroup(item.kind, target)) return false;
  if (!target.entity) return true;
  return globalSearchEntityModuleAllowlist[target.entity.kind].includes(target.module)
    && (globalSearchEntityGroupAllowlist[item.kind] ?? []).includes(target.entity.kind);
}

export function deriveGlobalSearchPresentation(
  query: string,
  loading: boolean,
  error: string,
  result: GlobalSearchResult | null,
): GlobalSearchPresentation {
  const trimmed = query.trim();
  const hasQuery = trimmed.length > 0;
  return {
    showGuide: !hasQuery,
    showShortQuery: hasQuery && Array.from(trimmed).length < 2,
    showLoading: hasQuery && loading,
    showError: hasQuery && Boolean(error),
    showResult: hasQuery && Array.from(trimmed).length >= 2 && !error && result !== null,
  };
}

export function nextGlobalSearchGroupPage(resultPage?: number, groupPage?: number): number {
  const candidate = groupPage ?? resultPage ?? 1;
  const current = Number.isInteger(candidate) && candidate > 0
    ? Math.min(candidate, 9_999)
    : 1;
  return current + 1;
}

const countFormatter = new Intl.NumberFormat("zh-CN");
const currencyFormatter = new Intl.NumberFormat("zh-CN", {
  style: "currency",
  currency: "CNY",
  maximumFractionDigits: 0,
});

function formatCount(value: number): string {
  return countFormatter.format(Number.isFinite(value) ? Math.max(0, value) : 0);
}

function formatAmount(cents: number | null): string | null {
  return typeof cents === "number" && Number.isFinite(cents)
    ? currencyFormatter.format(cents / 100)
    : null;
}

export default function GlobalSearchDialog({
  open,
  query,
  result,
  loading,
  error,
  onQueryChange,
  onClose,
  onSelectItem,
  onSelectQuickModule,
  onLoadMoreGroup,
  loadingGroup = null,
  loadMoreError = "",
  returnFocusRef,
}: GlobalSearchDialogProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const presentation = deriveGlobalSearchPresentation(query, loading, error, result);
  const visibleGroups = Array.isArray(result?.groups)
    ? result.groups.filter((group) => Array.isArray(group.items) && group.items.length > 0)
    : [];

  return (
    <Dialog
      open={open}
      onClose={onClose}
      dialogId="global-search-dialog"
      ariaLabel="全系统业务搜索"
      className="search-modal search-modal-global"
      initialFocusRef={inputRef}
      returnFocusRef={returnFocusRef}
    >
      <div className="modal-search">
        <span aria-hidden="true">⌕</span>
        <input
          ref={inputRef}
          type="search"
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder="搜索商品、订单、库存、市场、客服、财务或批次…"
          aria-label="搜索系统全部已接入数据"
          autoComplete="off"
        />
        <button type="button" onClick={onClose} aria-label="关闭全系统搜索">ESC</button>
      </div>

      {!presentation.showGuide && (
        <div className="search-results data-refresh-region" aria-live="polite" aria-busy={presentation.showLoading}>
          {presentation.showShortQuery && <div className="search-state" role="status">请输入至少 2 个字符。</div>}
          {presentation.showLoading && !presentation.showResult && <div className="search-state" role="status">正在按业务域搜索已接入数据…</div>}
          {presentation.showError && <div className="search-state search-state-error" role="alert">{error}</div>}
          {presentation.showResult && result && (
            <>
              {visibleGroups.map((group) => {
                const headingId = `global-search-group-${group.key}`;
                const groupLoading = loadingGroup === group.key;
                const nextPage = nextGlobalSearchGroupPage(result.page, group.page);
                return (
                  <section className="search-result-section" key={group.key} aria-labelledby={headingId}>
                    <div>
                      <p id={headingId}>{group.label}</p>
                      <small>显示 {formatCount(group.items.length)} 条 · {group.totalExact === false ? "至少 " : "共 "}{formatCount(group.total)} 条{group.hasMore ? " · 可继续分页" : ""}</small>
                      {group.hasMore && onLoadMoreGroup && (
                        <button
                          type="button"
                          className="row-action"
                          disabled={loading || groupLoading}
                          aria-busy={groupLoading}
                          aria-label={`加载更多${group.label}结果`}
                          onClick={() => onLoadMoreGroup(group.key, nextPage)}
                        >
                          {groupLoading ? "加载中…" : "加载更多"}
                        </button>
                      )}
                    </div>
                    {group.items.map((item) => {
                      const amount = formatAmount(item.amountCents);
                      return (
                        <button
                          type="button"
                          className="search-result-item"
                          key={`${group.key}-${item.id}`}
                          disabled={loading}
                          onClick={() => onSelectItem(item)}
                        >
                          <span className={`search-result-icon search-result-icon-${group.key}`} aria-hidden="true">{group.icon}</span>
                          <div>
                            <strong title={item.title}>{item.title || "未命名记录"}</strong>
                            <small>{item.subtitle || item.detail || "暂无摘要"}</small>
                            {item.subtitle && item.detail && <small className="search-result-detail">{item.detail}</small>}
                          </div>
                          <em>{amount && <b>{amount}</b>}{item.updatedAt && <small>{item.updatedAt.slice(0, 10)}</small>}</em>
                        </button>
                      );
                    })}
                  </section>
                );
              })}
              {result.returned === 0 && <div className="search-state" role="status">未在当前已接入业务域中找到匹配数据。</div>}
              {loadMoreError && <div className="search-state search-state-error" role="alert">{loadMoreError}</div>}
              <div className="search-coverage-note">
                按字段白名单搜索，单域和总结果均有限额
                {result.deadlineExceeded ? `；${result.timedOutDomains?.length ?? 0} 个业务域达到本次搜索时限，可指定业务域重试` : ""}
                {result.unavailableDomains.length > 0 ? `；${result.unavailableDomains.length} 个未建表业务域已安全跳过` : ""}。
              </div>
            </>
          )}
        </div>
      )}

      {presentation.showGuide && (
        <>
          <p>全系统搜索</p>
          <div className="search-guide">
            <strong>覆盖货品、订单、京东商品、库存、市场 SKU、客服、财务、目标、事务与导入批次</strong>
            <small>按业务域分组返回；仅在明确搜索客服分组时匹配聊天正文，结果只展示必要摘要。</small>
          </div>
          <p>快速访问</p>
          <div className="quick-links">
            {navItems.slice(0, 5).map((item) => (
              <button type="button" key={item.key} onClick={() => onSelectQuickModule(item.key)}>
                <span>{item.short}</span>
                <div><strong>{item.label}</strong><small>{item.description}</small></div>
                <em aria-hidden="true">↗</em>
              </button>
            ))}
          </div>
        </>
      )}
    </Dialog>
  );
}
