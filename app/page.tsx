"use client";

import { Component, lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, useTransition } from "react";
import type { ReactNode, RefObject } from "react";
import { requestJson } from "@/lib/http/api-client";
import {
  createAiPageContext,
  normalizeAiPageFilters,
  type AiPageContext,
} from "@/lib/ai/page-context";
import AiWorkspaceHost from "./ai-workspace-host";
import { AiPageContextProvider, type AiPageRegistration } from "./ai-page-context-provider";
import AppShell from "./shell/app-shell";
import GlobalHeader from "./shell/global-header";
import ModuleErrorBoundary from "./shell/module-error-boundary";
import {
  createReloadableLazy,
  resetReloadableLazyScope,
} from "./shell/reloadable-lazy";
import {
  getDefaultModuleView,
  isModuleKey,
  navItems,
  type ImportSourceKey,
  type ModuleKey,
  type ModuleViewKey,
} from "./shell/navigation-catalog";
import {
  normalizeShellLocation,
  parseShellLocation,
  serializeShellLocation,
  type ShellPeriodState,
} from "./shell/navigation-contract";
import { normalizeModuleView } from "./shell/module-view-contract";
import SidebarNavigation from "./shell/sidebar-navigation";
import { useModuleViewState } from "./shell/use-module-view-state";
import {
  type CurrentUser,
  type SalesRangeLabel,
  addIsoDays,
  isoDayDifference,
  shanghaiIsoToday,
  selectedMonthPeriod,
  skuSalesPeriod,
  shellPeriodForRange,
  rangeForShellPeriod,
  useDebouncedValue,
  startOfIsoMonth,
  endOfIsoMonth,
  addIsoMonths,
  previousYearPeriod,
  clampIsoDate,
} from "./module-view-shared";
export { canManageFinanceTargets, validateFinanceTargetDeletionReason } from "./module-view-shared";
import type {
  GlobalSearchGroupKey,
  GlobalSearchItem,
  GlobalSearchResult,
} from "./global-search-dialog";
import Dialog from "./ui/dialog";
import { SearchableSelect } from "./ui/searchable-select";
import TableColumnFilters from "./ui/table-column-filters";

const { Component: MarketView } = createReloadableLazy("market", () => import("./market-view"));
const { Component: N8nWorkflowView } = createReloadableLazy("n8n_workflows", () => import("./n8n-workflow-view"));
const { Component: OperationsView } = createReloadableLazy("workflow", () => import("./operations-view"));
const { Component: SettingsView } = createReloadableLazy("settings", () => import("./settings-view"));
const GlobalSearchDialog = lazy(() => import("./global-search-dialog"));
const { Component: CustomerServiceView } = createReloadableLazy("customer_service", () => import("./customer-service-view"));
const { Component: AiModuleView } = createReloadableLazy("ai", () => import("./ai-module-view"));
const { Component: DashboardView } = createReloadableLazy("dashboard", () => import("./dashboard-module-view"));
const { Component: ShopView } = createReloadableLazy("shop", () => import("./shop-module-view"));
const { Component: SalesView } = createReloadableLazy("sales", () => import("./sales-module-view"));
const { Component: InventoryView } = createReloadableLazy("inventory", () => import("./inventory-module-view"));
const { Component: ProductView } = createReloadableLazy("product", () => import("./product-module-view"));
const { Component: ImportView } = createReloadableLazy("import", () => import("./import-module-view"));

type GlobalSearchLoadBoundaryProps = {
  children: ReactNode;
  resetKey: number;
  onRetry: () => void;
  onClose: () => void;
  returnFocusRef: RefObject<HTMLElement | null>;
};

class GlobalSearchLoadBoundary extends Component<GlobalSearchLoadBoundaryProps, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidUpdate(previousProps: GlobalSearchLoadBoundaryProps) {
    if (this.state.failed && previousProps.resetKey !== this.props.resetKey) {
      this.setState({ failed: false });
    }
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return <Dialog open onClose={this.props.onClose} dialogId="global-search-load-error" ariaLabel="全系统搜索加载失败" className="panel search-modal" returnFocusRef={this.props.returnFocusRef}>
      <div className="data-state data-state-error" role="alert" aria-live="assertive">
        <span className="state-symbol" aria-hidden="true">!</span>
        <strong>全系统搜索加载失败</strong>
        <p>搜索工作区未载入，当前页面与筛选未受影响。</p>
        <div className="data-state-actions">
          <button type="button" className="primary-button" onClick={this.props.onRetry}>重新加载搜索</button>
          <button type="button" className="secondary-button" onClick={this.props.onClose}>关闭</button>
        </div>
      </div>
    </Dialog>;
  }
}

function GlobalSearchLoadingDialog({ onClose, returnFocusRef }: { onClose: () => void; returnFocusRef: RefObject<HTMLElement | null> }) {
  return <Dialog open onClose={onClose} dialogId="global-search-loading" ariaLabel="正在加载全系统搜索" className="panel search-modal" returnFocusRef={returnFocusRef}>
    <div className="data-state" role="status" aria-live="polite">
      <span className="state-spinner" aria-hidden="true" />
      <strong>正在加载全系统搜索</strong>
      <p>正在载入搜索工作区…</p>
      <button type="button" className="secondary-button" onClick={onClose}>取消</button>
    </div>
  </Dialog>;
}

type PickerPeriod = { startDate: string; endDate: string };

function CalendarMonth({ month, minDate, maxDate, startDate, endDate, onSelect }: {
  month: string;
  minDate: string;
  maxDate: string;
  startDate: string | null;
  endDate: string | null;
  onSelect: (date: string) => void;
}) {
  const firstDate = `${month}-01`;
  const firstWeekday = new Date(`${firstDate}T00:00:00Z`).getUTCDay();
  const calendarStart = addIsoDays(firstDate, -firstWeekday);
  const title = new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "long", timeZone: "UTC" }).format(new Date(`${firstDate}T00:00:00Z`));
  const weekNames = ["日", "一", "二", "三", "四", "五", "六"];
  return <div className="period-calendar"><h4>{title}</h4><div className="period-weekdays">{weekNames.map((day) => <span key={day}>{day}</span>)}</div><div className="period-days">{Array.from({ length: 42 }, (_, index) => {
    const date = addIsoDays(calendarStart, index);
    const outside = !date.startsWith(month);
    const disabled = date < minDate || date > maxDate;
    const selected = date === startDate || date === endDate;
    const inRange = Boolean(startDate && endDate && date > startDate && date < endDate);
    return <button type="button" key={date} disabled={disabled} className={`${outside ? "outside" : ""} ${selected ? "selected" : ""} ${inRange ? "in-range" : ""}`} onClick={() => onSelect(date)}>{date.slice(8)}</button>;
  })}</div></div>;
}

function StatisticalPeriodPicker({ minDate, maxDate, startDate, endDate, onApply }: {
  minDate: string;
  maxDate: string;
  startDate: string;
  endDate: string;
  onApply: (startDate: string, endDate: string) => void;
}) {
  const [draftStart, setDraftStart] = useState<string | null>(startDate);
  const [draftEnd, setDraftEnd] = useState<string | null>(endDate);
  const [leftMonth, setLeftMonth] = useState(startDate.slice(0, 7));
  useEffect(() => {
    setDraftStart(startDate); setDraftEnd(endDate); setLeftMonth(startDate.slice(0, 7));
  }, [endDate, startDate]);
  const clampPeriod = (period: PickerPeriod): PickerPeriod => {
    const nextStart = clampIsoDate(period.startDate, minDate, maxDate);
    const nextEnd = clampIsoDate(period.endDate, minDate, maxDate);
    return nextStart <= nextEnd ? { startDate: nextStart, endDate: nextEnd } : { startDate: nextEnd, endDate: nextEnd };
  };
  const year = Number(maxDate.slice(0, 4));
  const month = Number(maxDate.slice(5, 7));
  const quarterStartMonth = Math.floor((month - 1) / 3) * 3 + 1;
  const shortcuts: Array<{ label: string; period: PickerPeriod }> = [
    { label: "去年", period: { startDate: `${year - 1}-01-01`, endDate: `${year - 1}-12-31` } },
    { label: "今年", period: { startDate: `${year}-01-01`, endDate: maxDate } },
    { label: "本季", period: { startDate: `${year}-${String(quarterStartMonth).padStart(2, "0")}-01`, endDate: maxDate } },
    { label: "本月", period: { startDate: startOfIsoMonth(maxDate), endDate: maxDate } },
    { label: "近一年", period: { startDate: addIsoMonths(maxDate, -12), endDate: maxDate } },
    { label: "近6月", period: { startDate: addIsoMonths(maxDate, -6), endDate: maxDate } },
    { label: "近3月", period: { startDate: addIsoMonths(maxDate, -3), endDate: maxDate } },
    { label: "上月", period: { startDate: startOfIsoMonth(addIsoMonths(maxDate, -1)), endDate: endOfIsoMonth(addIsoMonths(maxDate, -1)) } },
    { label: "近1月", period: { startDate: addIsoMonths(maxDate, -1), endDate: maxDate } },
    { label: "近7天", period: { startDate: addIsoDays(maxDate, -6), endDate: maxDate } },
    { label: "前7天", period: { startDate: addIsoDays(maxDate, -13), endDate: addIsoDays(maxDate, -7) } },
    { label: "昨天", period: { startDate: addIsoDays(maxDate, -1), endDate: addIsoDays(maxDate, -1) } },
    { label: "今天", period: { startDate: maxDate, endDate: maxDate } },
  ].map((item) => ({ ...item, period: clampPeriod(item.period) }));
  const chooseDate = (date: string) => {
    if (!draftStart || draftEnd) { setDraftStart(date); setDraftEnd(null); return; }
    if (date < draftStart) { setDraftStart(date); setDraftEnd(draftStart); return; }
    setDraftEnd(date);
  };
  const applyShortcut = (period: PickerPeriod) => { setDraftStart(period.startDate); setDraftEnd(period.endDate); setLeftMonth(period.startDate.slice(0, 7)); };
  const selectedShortcut = shortcuts.find((item) => item.period.startDate === draftStart && item.period.endDate === draftEnd)?.label;
  const rightMonth = addIsoMonths(`${leftMonth}-01`, 1).slice(0, 7);
  const exceedsMaximumDays = Boolean(draftStart && draftEnd && isoDayDifference(draftStart, draftEnd) + 1 > 366);
  return <div className="stat-period-picker" aria-label="自定义统计周期">
    <div className="period-shortcuts">{shortcuts.map((item) => <button type="button" key={item.label} className={selectedShortcut === item.label ? "active" : ""} onClick={() => applyShortcut(item.period)}>{item.label}</button>)}</div>
    <div className="period-calendars"><button type="button" className="period-nav" onClick={() => setLeftMonth(addIsoMonths(`${leftMonth}-01`, -1).slice(0, 7))} aria-label="上一月">‹</button><CalendarMonth month={leftMonth} minDate={minDate} maxDate={maxDate} startDate={draftStart} endDate={draftEnd} onSelect={chooseDate} /><CalendarMonth month={rightMonth} minDate={minDate} maxDate={maxDate} startDate={draftStart} endDate={draftEnd} onSelect={chooseDate} /><button type="button" className="period-nav" onClick={() => setLeftMonth(addIsoMonths(`${leftMonth}-01`, 1).slice(0, 7))} aria-label="下一月">›</button></div>
    <div className="period-picker-footer"><span>{draftStart ? `${draftStart} 00:00:00` : "请选择开始日期"}</span><i className={exceedsMaximumDays ? "period-limit-warning" : ""}>{exceedsMaximumDays ? "最长366天" : "—"}</i><span>{draftEnd ? `${draftEnd} 23:59:59` : "请选择结束日期"}</span><div><button type="button" onClick={() => { setDraftStart(null); setDraftEnd(null); }}>清空</button><button type="button" className="primary-button" disabled={!draftStart || !draftEnd || exceedsMaximumDays} onClick={() => draftStart && draftEnd && onApply(draftStart, draftEnd)}>确定</button></div></div>
  </div>;
}
type ShellViewProps = {
  range: SalesRangeLabel;
  customStartDate: string;
  customEndDate: string;
  importSource?: ImportSourceKey;
  moduleView: ModuleViewKey;
  onNavigate: (key: ModuleKey, importSource?: ImportSourceKey) => void;
  onAskAi: (prompt: string) => void;
  aiContextPrompt: string;
  aiPageContext: AiPageContext | null;
  onModuleViewChange: (view: ModuleViewKey) => void;
  onApplyPeriod?: (startDate: string, endDate: string) => void;
  currentUser: CurrentUser | null;
};

const viewMap: Record<ModuleKey, (props: ShellViewProps) => React.ReactNode> = {
  n8n_workflows: ({ currentUser, moduleView, onModuleViewChange }) => <N8nWorkflowView currentUser={currentUser} moduleView={normalizeModuleView("n8n_workflows", moduleView)} onModuleViewChange={(view) => onModuleViewChange(view)} />,
  dashboard: DashboardView,
  shop: ({ range, customStartDate, customEndDate, onNavigate, moduleView, onModuleViewChange }) => <ShopView range={range} customStartDate={customStartDate} customEndDate={customEndDate} onNavigate={onNavigate} moduleView={normalizeModuleView("shop", moduleView)} onModuleViewChange={(view) => onModuleViewChange(view)} />,
  market: ({ customStartDate, customEndDate, currentUser, moduleView, onModuleViewChange, onApplyPeriod }) => <MarketView customStartDate={customStartDate} customEndDate={customEndDate} currentUser={currentUser} moduleView={normalizeModuleView("market", moduleView)} onModuleViewChange={(view) => onModuleViewChange(view)} onApplyPeriod={onApplyPeriod} />,
  customer_service: ({ customStartDate, customEndDate, currentUser, onNavigate }) => <CustomerServiceView customStartDate={customStartDate} customEndDate={customEndDate} currentUser={currentUser} onNavigate={onNavigate} />,
  sales: ({ range, customStartDate, customEndDate, currentUser, moduleView, onModuleViewChange }) => <SalesView range={range} customStartDate={customStartDate} customEndDate={customEndDate} currentUser={currentUser} moduleView={normalizeModuleView("sales", moduleView)} onModuleViewChange={(view) => onModuleViewChange(view)} />,
  inventory: ({ customStartDate, customEndDate, currentUser, moduleView, onModuleViewChange, onAskAi }) => <InventoryView customStartDate={customStartDate} customEndDate={customEndDate} currentUser={currentUser} moduleView={normalizeModuleView("inventory", moduleView)} onModuleViewChange={(view) => onModuleViewChange(view)} onAskAi={onAskAi} />,
  product: ({ range, customStartDate, customEndDate, moduleView, onModuleViewChange }) => <ProductView range={range} customStartDate={customStartDate} customEndDate={customEndDate} moduleView={normalizeModuleView("product", moduleView)} onModuleViewChange={(view) => onModuleViewChange(view)} />,
  workflow: ({ currentUser, moduleView, onModuleViewChange }) => <OperationsView currentUser={currentUser} moduleView={normalizeModuleView("workflow", moduleView)} onModuleViewChange={(view) => onModuleViewChange(view)} />,
  import: ({ importSource, currentUser, moduleView, onModuleViewChange }) => <ImportView importSource={importSource} currentUser={currentUser} moduleView={normalizeModuleView("import", moduleView)} onModuleViewChange={(view) => onModuleViewChange(view)} />,
  settings: ({ currentUser, moduleView, onModuleViewChange }) => <SettingsView currentUser={currentUser} moduleView={normalizeModuleView("settings", moduleView)} onModuleViewChange={(view) => onModuleViewChange(view)} />,
  ai: ({ currentUser, aiContextPrompt, aiPageContext, moduleView, onModuleViewChange }) => <AiModuleView currentUser={currentUser} externalChat initialContextPrompt={aiContextPrompt} initialPageContext={aiPageContext} moduleView={normalizeModuleView("ai", moduleView)} onModuleViewChange={(view) => onModuleViewChange(view)} />,
};

export default function Home() {
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [active, setActive] = useState<ModuleKey>("dashboard");
  const [moduleTransitionPending, startModuleTransition] = useTransition();
  const [shellLocationReady, setShellLocationReady] = useState(false);
  const { selection: moduleViewSelection, syncFromLocation: syncModuleViewFromLocation, setSelection: setModuleViewSelection, pushView: pushModuleView } = useModuleViewState();
  const [importSource, setImportSource] = useState<ImportSourceKey | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [mobileMenu, setMobileMenu] = useState(false);
  const [range, setRange] = useState<SalesRangeLabel>("本月");
  const [customEndDate, setCustomEndDate] = useState(shanghaiIsoToday);
  const [customStartDate, setCustomStartDate] = useState(() => addIsoDays(shanghaiIsoToday(), -29));
  const [selectedMonth, setSelectedMonth] = useState(() => shanghaiIsoToday().slice(0, 7));
  const [statPeriodPickerOpen, setStatPeriodPickerOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [globalSearchLoadVersion, setGlobalSearchLoadVersion] = useState(0);
  const [aiContextPrompt, setAiContextPrompt] = useState("");
  const [aiPanelOpen, setAiPanelOpen] = useState(false);
  const [aiPromptId, setAiPromptId] = useState(0);
  const [aiDetails, setAiDetails] = useState<AiPageRegistration | null>(null);
  const publishAiDetails = useCallback((registration: AiPageRegistration) => {
    setAiDetails(registration);
    return () => setAiDetails(current => current?.token === registration.token ? null : current);
  }, []);
  const closeAiPanel = useCallback(() => setAiPanelOpen(false), []);
  const [aiPageContext, setAiPageContext] = useState<AiPageContext | null>(null);
  const [GlobalSearchDialogView, setGlobalSearchDialogView] = useState(() => GlobalSearchDialog);
  const [globalSearchQuery, setGlobalSearchQuery] = useState("");
  const [globalSearchResult, setGlobalSearchResult] = useState<GlobalSearchResult | null>(null);
  const [globalSearchLoading, setGlobalSearchLoading] = useState(false);
  const [globalSearchError, setGlobalSearchError] = useState("");
  const [globalSearchLoadingGroup, setGlobalSearchLoadingGroup] = useState<GlobalSearchGroupKey | null>(null);
  const [globalSearchGroupError, setGlobalSearchGroupError] = useState("");
  const mobileMenuButtonRef = useRef<HTMLButtonElement>(null);
  const pageTitleRef = useRef<HTMLHeadingElement>(null);
  const accountButtonRef = useRef<HTMLButtonElement>(null);
  const globalSearchGenerationRef = useRef(0);
  const globalSearchControllerRef = useRef<AbortController | null>(null);
  const globalSearchGroupGenerationRef = useRef(0);
  const globalSearchGroupControllerRef = useRef<AbortController | null>(null);
  const globalSearchGroupRequestKeyRef = useRef("");
  const debouncedGlobalSearchQuery = useDebouncedValue(globalSearchQuery, 220);
  const customMaxDate = shanghaiIsoToday();
  const customMinDate = `${Number(customMaxDate.slice(0, 4)) - 1}-01-01`;
  const globalPeriod = useMemo(
    () => skuSalesPeriod(range, customStartDate, customEndDate),
    [customEndDate, customStartDate, range],
  );
  const shellPeriod = useMemo(
    () => shellPeriodForRange(range, selectedMonth, customStartDate, customEndDate),
    [customEndDate, customStartDate, range, selectedMonth],
  );
  const activeModuleView = moduleViewSelection.module === active
    ? moduleViewSelection.view
    : getDefaultModuleView(active);
  const closeMobileMenu = useCallback(() => setMobileMenu(false), []);
  const toggleCollapsed = useCallback(() => {
    setCollapsed((value) => {
      const next = !value;
      try {
        window.localStorage.setItem("teruisi.shell.v1", JSON.stringify({ sidebarCollapsed: next }));
      } catch {
        // A storage failure must not prevent the navigation from working.
      }
      return next;
    });
  }, []);

  useEffect(() => {
    try {
      const stored = JSON.parse(window.localStorage.getItem("teruisi.shell.v1") ?? "null") as { sidebarCollapsed?: unknown } | null;
      if (typeof stored?.sidebarCollapsed === "boolean") setCollapsed(stored.sidebarCollapsed);
    } catch {
      // Ignore malformed or unavailable optional preferences.
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void requestJson<{ user?: CurrentUser }>("/api/auth/me", { signal: controller.signal })
      .then((payload) => {
        if (payload.user) setCurrentUser(payload.user);
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!shellLocationReady || !currentUser || active !== "market" || activeModuleView === "settings" || activeModuleView === "compare") return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void import("./market-view")
        .then(({ prefetchMarketRankingOverview }) => prefetchMarketRankingOverview(globalPeriod.startDate, globalPeriod.endDate, controller.signal))
        .catch(() => undefined);
    }, 0);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [active, activeModuleView, currentUser, globalPeriod.endDate, globalPeriod.startDate, shellLocationReady]);

  const applyLocationState = useCallback(() => {
    const state = parseShellLocation(window.location.href);
    const today = shanghaiIsoToday();
    const minDate = `${Number(today.slice(0, 4)) - 1}-01-01`;
    setActive(state.module);
    syncModuleViewFromLocation(window.location.href);
    setImportSource(state.source ?? null);
    setRange(rangeForShellPeriod(state.period));
    setStatPeriodPickerOpen(false);
    let appliedPeriod = state.period;
    if (state.period.kind === "calendar_month") {
      const month = state.period.month > today.slice(0, 7) ? today.slice(0, 7) : state.period.month;
      const period = selectedMonthPeriod(month);
      setSelectedMonth(month);
      setCustomStartDate(period.startDate);
      setCustomEndDate(period.endDate > today ? today : period.endDate);
      appliedPeriod = { kind: "calendar_month", month };
    } else if (state.period.kind === "previous_year") {
      setCustomStartDate(state.period.from);
      setCustomEndDate(state.period.to);
    } else if (state.period.kind === "custom") {
      const endDate = state.period.to > today ? today : state.period.to < minDate ? minDate : state.period.to;
      const startDate = state.period.from < minDate ? minDate : state.period.from > endDate ? endDate : state.period.from;
      setCustomStartDate(startDate);
      setCustomEndDate(endDate);
      appliedPeriod = { kind: "custom", from: startDate, to: endDate };
    }
    const normalizedContractUrl = normalizeShellLocation(window.location.href);
    const normalized = serializeShellLocation({
      module: state.module,
      view: state.view,
      ...(state.source ? { source: state.source } : {}),
      period: appliedPeriod,
    }, normalizedContractUrl);
    const currentUrl = `${window.location.pathname}${window.location.search}${window.location.hash}`;
    if (normalized !== currentUrl) window.history.replaceState(null, "", normalized);
    setShellLocationReady(true);
  }, [syncModuleViewFromLocation]);

  useEffect(() => {
    applyLocationState();
    const onPopState = () => {
      closeMobileMenu();
      applyLocationState();
      window.requestAnimationFrame(() => pageTitleRef.current?.focus());
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, [applyLocationState, closeMobileMenu]);

  useEffect(() => {
    const handleShortcut = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setSearchOpen(true);
      }
    };
    window.addEventListener("keydown", handleShortcut);
    return () => window.removeEventListener("keydown", handleShortcut);
  }, []);

  const cancelGlobalSearchRequests = useCallback(() => {
    globalSearchGenerationRef.current += 1;
    globalSearchControllerRef.current?.abort();
    globalSearchControllerRef.current = null;
    globalSearchGroupGenerationRef.current += 1;
    globalSearchGroupControllerRef.current?.abort();
    globalSearchGroupControllerRef.current = null;
    globalSearchGroupRequestKeyRef.current = "";
  }, []);

  useEffect(() => {
    if (!searchOpen) return;
    const query = debouncedGlobalSearchQuery.trim();
    if (Array.from(query).length < 2) return;
    const generation = globalSearchGenerationRef.current + 1;
    globalSearchGenerationRef.current = generation;
    globalSearchControllerRef.current?.abort();
    globalSearchGroupGenerationRef.current += 1;
    globalSearchGroupControllerRef.current?.abort();
    globalSearchGroupControllerRef.current = null;
    globalSearchGroupRequestKeyRef.current = "";
    const controller = new AbortController();
    globalSearchControllerRef.current = controller;
    void (async () => {
      setGlobalSearchLoading(true);
      setGlobalSearchLoadingGroup(null);
      setGlobalSearchGroupError("");
      setGlobalSearchError("");
      try {
        const payload = await requestJson<GlobalSearchResult>(`/api/search?q=${encodeURIComponent(query)}`, { signal: controller.signal });
        if (!payload || payload.query !== query || !Array.isArray(payload.groups) || !payload.groups.every((group) => Array.isArray(group.items)) || !Array.isArray(payload.unavailableDomains)) throw new Error("搜索结果格式不完整");
        if (controller.signal.aborted || generation !== globalSearchGenerationRef.current) return;
        setGlobalSearchResult(payload);
      } catch (error) {
        if (controller.signal.aborted || generation !== globalSearchGenerationRef.current) return;
        setGlobalSearchError(error instanceof Error ? error.message : "搜索失败");
        setGlobalSearchResult(null);
      } finally {
        if (!controller.signal.aborted && generation === globalSearchGenerationRef.current) {
          setGlobalSearchLoading(false);
          if (globalSearchControllerRef.current === controller) globalSearchControllerRef.current = null;
        }
      }
    })();
    return () => controller.abort();
  }, [debouncedGlobalSearchQuery, searchOpen]);

  const isAiChat = active === "ai" && activeModuleView === "assistant";
  const current = isAiChat ? { label: "AI 对话", description: "小特对话工作台" } : navItems.find((item) => item.key === active) ?? navItems[0];
  const View = viewMap[active];

  const replacePeriodUrl = useCallback((period: ShellPeriodState) => {
    const nextUrl = serializeShellLocation({
      module: active,
      view: normalizeModuleView(active, activeModuleView),
      ...(active === "import" && importSource ? { source: importSource } : {}),
      period,
    }, window.location.href);
    const currentUrl = `${window.location.pathname}${window.location.search}${window.location.hash}`;
    if (nextUrl !== currentUrl) window.history.replaceState(null, "", nextUrl);
  }, [active, activeModuleView, importSource]);

  const hrefForModule = useCallback((key: ModuleKey, requestedView?: ModuleViewKey) => {
    const currentUrl = typeof window === "undefined" ? "/" : window.location.href;
    return serializeShellLocation({ module: key, view: requestedView ?? getDefaultModuleView(key), period: shellPeriod }, currentUrl);
  }, [shellPeriod]);

  const selectModule = useCallback((key: ModuleKey, nextImportSource?: ImportSourceKey, requestedView?: ModuleViewKey) => {
    const nextSource = key === "import" ? nextImportSource : undefined;
    const nextView = requestedView ? normalizeModuleView(key, requestedView) : getDefaultModuleView(key);
    const nextUrl = serializeShellLocation({
      module: key,
      view: nextView,
      ...(nextSource ? { source: nextSource } : {}),
      period: shellPeriod,
    }, window.location.href);
    const currentUrl = `${window.location.pathname}${window.location.search}${window.location.hash}`;
    if (nextUrl !== currentUrl) window.history.pushState(null, "", nextUrl);
    startModuleTransition(() => {
      setModuleViewSelection(key, nextView);
      setImportSource(nextSource ?? null);
      setActive(key);
    });
    closeMobileMenu();
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    window.scrollTo({ top: 0, behavior: reducedMotion ? "auto" : "smooth" });
    window.requestAnimationFrame(() => window.requestAnimationFrame(() => pageTitleRef.current?.focus()));
  }, [closeMobileMenu, setModuleViewSelection, shellPeriod, startModuleTransition]);

  const selectModuleView = useCallback((view: ModuleViewKey) => {
    pushModuleView(active, normalizeModuleView(active, view));
  }, [active, pushModuleView]);

  const currentAiContext = useMemo(() => {
    const details = aiDetails?.module === active && aiDetails.view === activeModuleView ? aiDetails.details : null;
    const usesPeriod = ["dashboard", "shop", "market", "customer_service", "sales", "product"].includes(active)
      && !(active === "market" && activeModuleView === "settings")
      && !(active === "sales" && ["finance", "targets"].includes(activeModuleView));
    const period = details?.period !== undefined ? details.period : usesPeriod ? globalPeriod : null;
    return createAiPageContext({ module: active, view: normalizeModuleView(active, activeModuleView),
      startDate: period?.startDate, endDate: period?.endDate,
      importSource: active === "import" ? importSource : null, filters: normalizeAiPageFilters(details?.filters) ?? undefined });
  }, [active, activeModuleView, aiDetails, globalPeriod, importSource]);
  const aiContextError = aiDetails?.module === active && aiDetails.view === activeModuleView
    ? aiDetails.details.blockedReason || (normalizeAiPageFilters(aiDetails.details.filters) === null
      ? "当前筛选项目过多或查询文字过长。请缩小筛选，或移除页面上下文后提问。" : "") : "";

  const askAiWithContext = useCallback((prompt: string) => {
    const boundedPrompt = Array.from(prompt.trim()).slice(0, 4_000).join("");
    if (!boundedPrompt) return;
    setAiPageContext(currentAiContext);
    setAiContextPrompt(boundedPrompt);
    setAiPromptId(value => value + 1);
    setAiPanelOpen(true);
  }, [currentAiContext]);

  const askAiAboutCurrentPage = useCallback(() => {
    askAiWithContext("请结合当前页面的数据分析重点变化、异常及可能原因，并说明数据来源和统计范围。");
  }, [askAiWithContext]);

  const handleSidebarNavigate = useCallback((event: React.MouseEvent<HTMLAnchorElement>, key: ModuleKey, view?: ModuleViewKey) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    selectModule(key, undefined, view);
  }, [selectModule]);

  const loadMoreGlobalSearchGroup = useCallback(async (groupKey: GlobalSearchGroupKey, page: number) => {
    const query = globalSearchQuery.trim();
    if (!searchOpen || Array.from(query).length < 2) return;
    const generation = globalSearchGroupGenerationRef.current + 1;
    globalSearchGroupGenerationRef.current = generation;
    globalSearchGroupControllerRef.current?.abort();
    const controller = new AbortController();
    globalSearchGroupControllerRef.current = controller;
    const nextPage = Number.isInteger(page) ? Math.min(10_000, Math.max(2, page)) : 2;
    const requestKey = `${query}\u001f${groupKey}\u001f${nextPage}`;
    globalSearchGroupRequestKeyRef.current = requestKey;
    setGlobalSearchLoadingGroup(groupKey);
    setGlobalSearchGroupError("");
    try {
      const params = new URLSearchParams({ q: query, group: groupKey, page: String(nextPage) });
      const payload = await requestJson<GlobalSearchResult>(`/api/search?${params.toString()}`, { signal: controller.signal });
      const incomingGroup = payload?.groups?.find((group) => group.key === groupKey);
      if (payload?.query !== query || !incomingGroup || !Array.isArray(incomingGroup.items) || !Array.isArray(payload.unavailableDomains)) throw new Error("搜索分组响应格式不完整");
      if (controller.signal.aborted || generation !== globalSearchGroupGenerationRef.current || requestKey !== globalSearchGroupRequestKeyRef.current) return;
      setGlobalSearchResult((currentResult) => {
        if (!currentResult || currentResult.query !== query) return currentResult;
        const currentGroup = currentResult.groups.find((group) => group.key === groupKey);
        if (!currentGroup) return currentResult;
        const seenIds = new Set(currentGroup.items.map((item) => item.id));
        const mergedItems = [...currentGroup.items];
        for (const item of incomingGroup.items) {
          if (seenIds.has(item.id)) continue;
          seenIds.add(item.id);
          mergedItems.push(item);
        }
        const mergedGroup = { ...currentGroup, ...incomingGroup, page: nextPage, hasMore: nextPage < 10_000 && incomingGroup.hasMore, items: mergedItems };
        const groups = currentResult.groups.map((group) => group.key === groupKey ? mergedGroup : group);
        return {
          ...currentResult,
          groups,
          returned: groups.reduce((total, group) => total + group.items.length, 0),
          truncated: groups.some((group) => group.hasMore),
          unavailableDomains: [...new Set([...currentResult.unavailableDomains, ...payload.unavailableDomains])],
        };
      });
    } catch (error) {
      if (controller.signal.aborted || generation !== globalSearchGroupGenerationRef.current || requestKey !== globalSearchGroupRequestKeyRef.current) return;
      setGlobalSearchGroupError(error instanceof Error ? error.message : "加载更多搜索结果失败");
    } finally {
      if (!controller.signal.aborted && generation === globalSearchGroupGenerationRef.current && requestKey === globalSearchGroupRequestKeyRef.current) {
        setGlobalSearchLoadingGroup(null);
        if (globalSearchGroupControllerRef.current === controller) globalSearchGroupControllerRef.current = null;
      }
    }
  }, [globalSearchQuery, searchOpen]);

  const closeGlobalSearch = useCallback(() => {
    cancelGlobalSearchRequests();
    setSearchOpen(false);
    setGlobalSearchQuery("");
    setGlobalSearchResult(null);
    setGlobalSearchError("");
    setGlobalSearchGroupError("");
    setGlobalSearchLoading(false);
    setGlobalSearchLoadingGroup(null);
  }, [cancelGlobalSearchRequests]);
  const retryGlobalSearchDialog = useCallback(() => {
    setGlobalSearchDialogView(() => lazy(() => import("./global-search-dialog")));
    setGlobalSearchLoadVersion((version) => version + 1);
  }, []);
  const updateGlobalSearchQuery = useCallback((value: string) => {
    cancelGlobalSearchRequests();
    setGlobalSearchQuery(value);
    setGlobalSearchError("");
    setGlobalSearchGroupError("");
    setGlobalSearchLoading(Array.from(value.trim()).length >= 2);
    setGlobalSearchLoadingGroup(null);
  }, [cancelGlobalSearchRequests]);
  const selectGlobalSearchItem = useCallback(async (item: GlobalSearchItem) => {
    let targetModule: ModuleKey | null = null;
    let targetView: ModuleViewKey | undefined;
    try {
      const {
        isGlobalSearchItemModuleValid,
        isGlobalSearchTargetForItem,
        parseGlobalSearchTarget,
      } = await import("./global-search-dialog");
      const target = parseGlobalSearchTarget(item.target);
      if (target && isGlobalSearchTargetForItem(item, target)) {
        targetModule = target.module;
        targetView = target.view;
      } else if (isGlobalSearchItemModuleValid(item)) {
        // A malformed optional deep link may only fall back to its validated module default.
        targetModule = item.module;
      }
    } catch {
      // Navigation fails closed if the validation chunk cannot be loaded.
    }
    if (!targetModule) return;
    selectModule(targetModule, undefined, targetView);
    closeGlobalSearch();
  }, [closeGlobalSearch, selectModule]);
  const selectRange = (nextRange: SalesRangeLabel) => {
    if (nextRange === "去年同期" && range === "去年同期") return;
    setRange(nextRange);
    setStatPeriodPickerOpen(nextRange === "自定义");
    if (nextRange === "去年同期") {
      const period = previousYearPeriod(globalPeriod);
      setCustomStartDate(period.startDate);
      setCustomEndDate(period.endDate);
      replacePeriodUrl({ kind: "previous_year", from: period.startDate, to: period.endDate });
    } else if (nextRange === "月度") {
      const period = selectedMonthPeriod(selectedMonth);
      setCustomStartDate(period.startDate);
      setCustomEndDate(period.endDate > customMaxDate ? customMaxDate : period.endDate);
      replacePeriodUrl({ kind: "calendar_month", month: selectedMonth });
    } else if (nextRange === "自定义") {
      const endDate = customEndDate > customMaxDate ? customMaxDate : customEndDate < customMinDate ? customMinDate : customEndDate;
      const startDate = customStartDate < customMinDate ? customMinDate : customStartDate > endDate ? endDate : customStartDate;
      setCustomStartDate(startDate);
      setCustomEndDate(endDate);
    } else {
      replacePeriodUrl(shellPeriodForRange(nextRange, selectedMonth, customStartDate, customEndDate));
    }
  };
  const updateSelectedMonth = (month: string) => {
    if (!/^\d{4}-\d{2}$/.test(month)) return;
    setSelectedMonth(month);
    const period = selectedMonthPeriod(month);
    setCustomStartDate(period.startDate);
    setCustomEndDate(period.endDate > customMaxDate ? customMaxDate : period.endDate);
    replacePeriodUrl({ kind: "calendar_month", month });
  };

  const applyCustomPeriod = (startDate: string, endDate: string) => {
    setRange("自定义");
    setCustomStartDate(startDate);
    setCustomEndDate(endDate);
    setStatPeriodPickerOpen(false);
    replacePeriodUrl({ kind: "custom", from: startDate, to: endDate });
  };

  return (
    <>
      <AppShell
        collapsed={collapsed}
        mobileOpen={mobileMenu}
        onCloseMobile={closeMobileMenu}
        sidebar={<SidebarNavigation active={active} activeView={activeModuleView} collapsed={collapsed} hrefForModule={hrefForModule} onNavigate={handleSidebarNavigate} onToggleCollapsed={toggleCollapsed} />}
        header={<GlobalHeader
          title={current.label}
          description={`${current.description}${active !== "n8n_workflows" ? ` · ${globalPeriod.startDate} 至 ${globalPeriod.endDate}` : ""}`}
          menuButtonRef={mobileMenuButtonRef}
          titleRef={pageTitleRef}
          mobileOpen={mobileMenu}
          onOpenMobile={() => setMobileMenu(true)}
          actions={<>
            {active !== "ai" && <button type="button" className="secondary-button ai-context-button" onClick={askAiAboutCurrentPage} aria-label={`让 AI 分析当前${current.label}页面`}>问当前页面</button>}
            {active !== "n8n_workflows" && <div title={`${globalPeriod.startDate} 至 ${globalPeriod.endDate}`} className={`date-selector ${range === "月度" || (range === "自定义" && statPeriodPickerOpen) ? "date-selector-expanded" : ""}`}>
              <span>统计周期</span>
              <SearchableSelect value={range} onChange={(value) => selectRange(value as SalesRangeLabel)} ariaLabel="统计周期" searchPlaceholder="搜索统计周期" options={["今日", "昨天", "近7天", "近15天", "近30天", "本月", "月度", "去年同期", "自定义"].map((value) => ({ value, label: value }))} />
              {range === "月度" && <label className="month-selector"><span>选择月份</span><input type="month" value={selectedMonth} max={customMaxDate.slice(0, 7)} onChange={(event) => updateSelectedMonth(event.target.value)} aria-label="选择统计月份" /></label>}
              {range === "自定义" && statPeriodPickerOpen && <StatisticalPeriodPicker minDate={customMinDate} maxDate={customMaxDate} startDate={customStartDate} endDate={customEndDate} onApply={applyCustomPeriod} />}
            </div>}
            <div className="shell-account">
              <button ref={accountButtonRef} type="button" className="shell-account-avatar" popoverTarget="shell-account-actions" aria-label="打开账号操作" title="账号操作">章</button>
              <div id="shell-account-actions" className="shell-account-popover" popover="auto" role="group" aria-label="账号操作">
                <button type="button" onClick={() => window.location.assign(currentUser ? "/signout-with-chatgpt?return_to=%2F" : "/signin-with-chatgpt?return_to=%2F")}>{currentUser ? "退出登录" : "管理员登录"}</button>
              </div>
            </div>
          </>}
        />}
      >
        <div className={`content${isAiChat ? " content-ai-chat" : ""}`}>
          <div className={`module-stage${active === "ai" && activeModuleView === "assistant" ? " module-stage-ai-chat" : ""}${moduleTransitionPending ? " module-stage-pending" : ""}`} aria-busy={moduleTransitionPending}>
            <ModuleErrorBoundary
              resetKey={`${active}:${activeModuleView}:${importSource ?? ""}`}
              onRetry={() => { resetReloadableLazyScope(active); }}
              onOpenDashboard={() => selectModule("dashboard")}
            >
              {shellLocationReady ? <Suspense fallback={<section className="panel data-state" role="status" aria-live="polite"><span className="state-spinner" /><strong>正在加载{current.label}</strong><p>正在按需载入当前业务工作区…</p></section>}>
                <AiPageContextProvider module={active} view={activeModuleView} publish={publishAiDetails}><View range={range} customStartDate={globalPeriod.startDate} customEndDate={globalPeriod.endDate} importSource={importSource ?? undefined} moduleView={activeModuleView} onNavigate={selectModule} onAskAi={askAiWithContext} aiContextPrompt={aiContextPrompt} aiPageContext={aiPageContext} onModuleViewChange={selectModuleView} onApplyPeriod={applyCustomPeriod} currentUser={currentUser} /></AiPageContextProvider>
              </Suspense> : <section className="panel data-state" role="status" aria-live="polite"><span className="state-spinner" /><strong>正在打开目标工作区</strong><p>正在读取当前页面位置与统计周期…</p></section>}
            </ModuleErrorBoundary>
          </div>
          {currentUser && shellLocationReady && <AiWorkspaceHost
            key={JSON.stringify([currentUser.email, currentUser.role, currentUser.scopeRestricted])}
            currentUser={currentUser} module={active} context={currentAiContext}
            contextError={aiContextError}
            open={aiPanelOpen} fullPage={active === "ai" && activeModuleView === "assistant"}
            onClose={closeAiPanel} prompt={aiContextPrompt} promptModule={aiPageContext?.module} promptId={aiPromptId}
          />}
          {!isAiChat && <footer className="page-footer"><span>TERUISI 电商运营中台 · 业务数据中心</span><span>销售分析以最近成功导入批次为准</span></footer>}
        </div>
      </AppShell>

      <TableColumnFilters />

      {searchOpen && <GlobalSearchLoadBoundary resetKey={globalSearchLoadVersion} onRetry={retryGlobalSearchDialog} onClose={closeGlobalSearch} returnFocusRef={accountButtonRef}>
        <Suspense fallback={<GlobalSearchLoadingDialog onClose={closeGlobalSearch} returnFocusRef={accountButtonRef} />}>
        <GlobalSearchDialogView
          open={searchOpen}
          query={globalSearchQuery}
          result={globalSearchResult}
          loading={globalSearchLoading}
          error={globalSearchError}
          loadingGroup={globalSearchLoadingGroup}
          loadMoreError={globalSearchGroupError}
          onQueryChange={updateGlobalSearchQuery}
          onClose={closeGlobalSearch}
          returnFocusRef={accountButtonRef}
          onSelectItem={(item) => void selectGlobalSearchItem(item)}
          onSelectQuickModule={(module) => {
            if (!isModuleKey(module)) return;
            selectModule(module);
            closeGlobalSearch();
          }}
          onLoadMoreGroup={(groupKey, nextPage) => void loadMoreGlobalSearchGroup(groupKey, nextPage)}
        />
        </Suspense>
      </GlobalSearchLoadBoundary>}
    </>
  );
}
