"use client";

import {
  Suspense,
  useCallback,
  useEffect,
  useRef,
  useState,
  type ComponentProps,
  type KeyboardEvent,
} from "react";

import type { ModuleViewKey } from "./shell/navigation-catalog";
import { createReloadableLazy } from "./shell/reloadable-lazy";

type MarketDataImportPanelProps = ComponentProps<typeof import("./market-view").MarketDataImportPanel>;
type MarketWorkflowPanelProps = ComponentProps<typeof import("./market-view").MarketWorkflowPanel>;

const { Component: LazyMarketMasterAdminPanel } = createReloadableLazy("settings", () => import("./market-master-admin-panel"));
const { Component: LazyMarketDataImportPanel } = createReloadableLazy<MarketDataImportPanelProps>("settings", () => import("./market-view").then((module) => ({
  default: module.MarketDataImportPanel,
})));
const { Component: LazyMarketWorkflowPanel } = createReloadableLazy<MarketWorkflowPanelProps>("settings", () => import("./market-view").then((module) => ({
  default: module.MarketWorkflowPanel,
})));
const { Component: LazyMarketAnnotationView } = createReloadableLazy("settings", () => import("./market-annotation-view"));
const { Component: LazyWarehouseMappingSettings } = createReloadableLazy("settings", () => import("./warehouse-mapping-settings"));
const { Component: LazyDingTalkRobotSettings } = createReloadableLazy<{ canWrite: boolean }>("settings", () => import("./dingtalk-robot-settings"));
const { Component: LazyAccessControlManagement } = createReloadableLazy<{ canManage: boolean }>("settings", () => import("./access-control-management"));

export type SettingsTab = ModuleViewKey<"settings">;

export type SettingsCurrentUser = {
  email: string;
  displayName?: string;
  role: "viewer" | "analyst" | "operator" | "admin";
  roleLabel?: string;
  scopeRestricted?: boolean;
};

export type SettingsViewProps = {
  currentUser: SettingsCurrentUser | null;
  moduleView: SettingsTab;
  onModuleViewChange: (view: SettingsTab) => void;
};

type OperatingSettings = {
  targetDays: number;
  criticalDays: number;
  slowDays: number;
  stagnantDays: number;
  autoReplenishment: boolean;
  inventoryAlert: boolean;
  allowNegativeInventory: boolean;
  updatedAt: string | null;
  updatedBy: string | null;
};

type LoadState = "idle" | "loading" | "ready" | "error";
type MarketSettingsStatusData = MarketDataImportPanelProps["data"];
type NumericSettingKey = "targetDays" | "criticalDays" | "slowDays" | "stagnantDays";
type BooleanSettingKey = "autoReplenishment" | "inventoryAlert" | "allowNegativeInventory";
export type MarketSettingsPane = "master-data" | "imports" | "annotation";

const settingsTabs = ["parameters", "master", "warehouses", "dingtalk", "permissions"] as const satisfies readonly SettingsTab[];
const marketSettingsPanes = ["master-data", "imports", "annotation"] as const satisfies readonly MarketSettingsPane[];

const settingsTabLabels: Record<SettingsTab, string> = {
  parameters: "系统参数",
  master: "主数据与映射",
  warehouses: "仓库映射",
  dingtalk: "钉钉机器人",
  permissions: "权限管理",
};

export function nextSettingsTab(current: SettingsTab, key: string): SettingsTab | null {
  const currentIndex = settingsTabs.indexOf(current);
  if (key === "Home") return settingsTabs[0];
  if (key === "End") return settingsTabs[settingsTabs.length - 1];
  if (key === "ArrowRight" || key === "ArrowDown") {
    return settingsTabs[(currentIndex + 1) % settingsTabs.length];
  }
  if (key === "ArrowLeft" || key === "ArrowUp") {
    return settingsTabs[(currentIndex - 1 + settingsTabs.length) % settingsTabs.length];
  }
  return null;
}

export function nextMarketSettingsPane(
  current: MarketSettingsPane,
  key: string,
): MarketSettingsPane | null {
  const currentIndex = marketSettingsPanes.indexOf(current);
  if (key === "Home") return marketSettingsPanes[0];
  if (key === "End") return marketSettingsPanes[marketSettingsPanes.length - 1];
  if (key === "ArrowRight" || key === "ArrowDown") {
    return marketSettingsPanes[(currentIndex + 1) % marketSettingsPanes.length];
  }
  if (key === "ArrowLeft" || key === "ArrowUp") {
    return marketSettingsPanes[(currentIndex - 1 + marketSettingsPanes.length) % marketSettingsPanes.length];
  }
  return null;
}

export function shouldLoadMarketSettingsStatus(
  activeTab: SettingsTab,
  pane: MarketSettingsPane,
): boolean {
  return activeTab === "master" && pane === "imports";
}

export function marketStatusMatchesCurrentRequest(
  requestKey: string,
  responseScopeKey: string,
  responseReloadScope: number,
  currentReloadScope: number,
): boolean {
  return requestKey !== ""
    && responseScopeKey === requestKey
    && responseReloadScope === currentReloadScope;
}

export function canEditOperatingSettings(
  user: Pick<SettingsCurrentUser, "role"> | null,
): boolean {
  return user?.role === "admin";
}

export function canEditDingTalkSettings(
  user: Pick<SettingsCurrentUser, "role"> | null,
): boolean {
  return user?.role === "operator" || user?.role === "admin";
}

function isAbortError(reason: unknown) {
  return reason instanceof Error && reason.name === "AbortError";
}

function payloadError(payload: unknown, fallback: string) {
  if (
    payload
    && typeof payload === "object"
    && "error" in payload
    && typeof payload.error === "string"
    && payload.error.trim()
  ) return payload.error;
  return fallback;
}

function isOperatingSettings(payload: unknown): payload is OperatingSettings {
  if (!payload || typeof payload !== "object") return false;
  const value = payload as Record<string, unknown>;
  return ["targetDays", "criticalDays", "slowDays", "stagnantDays"]
    .every((key) => typeof value[key] === "number" && Number.isFinite(value[key]))
    && ["autoReplenishment", "inventoryAlert", "allowNegativeInventory"]
      .every((key) => typeof value[key] === "boolean")
    && (value.updatedAt === null || typeof value.updatedAt === "string")
    && (value.updatedBy === null || typeof value.updatedBy === "string");
}

function formatDateTime(value?: string | null) {
  if (!value) return "—";
  const normalized = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(value)
    ? `${value.replace(" ", "T")}Z`
    : value;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Asia/Shanghai",
  }).format(date);
}

function SectionHeader({ title, note }: { title: string; note: string }) {
  return <div className="section-header"><div><h2>{title}</h2><p>{note}</p></div></div>;
}

function LoadingState({ children }: { children: string }) {
  return <section className="panel data-state" role="status">
    <span className="state-spinner" />
    <strong>{children}</strong>
    <p>正在加载最新配置，请稍候…</p>
  </section>;
}

export default function SettingsView({
  currentUser,
  moduleView: activeTab,
  onModuleViewChange,
}: SettingsViewProps) {
  const canEdit = canEditOperatingSettings(currentUser);
  const canEditDingTalk = canEditDingTalkSettings(currentUser);
  const [settings, setSettings] = useState<OperatingSettings | null>(null);
  const [settingsState, setSettingsState] = useState<LoadState>("idle");
  const [settingsError, setSettingsError] = useState("");
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const settingsGenerationRef = useRef(0);
  const settingsControllerRef = useRef<AbortController | null>(null);
  const saveGenerationRef = useRef(0);
  const saveControllerRef = useRef<AbortController | null>(null);

  const [marketData, setMarketData] = useState<MarketSettingsStatusData>(null);
  const [marketState, setMarketState] = useState<LoadState>("idle");
  const [marketError, setMarketError] = useState("");
  const [marketReloadKey, setMarketReloadKey] = useState(0);
  const [marketPane, setMarketPane] = useState<MarketSettingsPane>("master-data");
  const [marketStatusRequestKey, setMarketStatusRequestKey] = useState("");
  const [marketStatusScopeKey, setMarketStatusScopeKey] = useState("");
  const [marketStatusReloadScope, setMarketStatusReloadScope] = useState(-1);
  const marketGenerationRef = useRef(0);
  const marketControllerRef = useRef<AbortController | null>(null);

  const loadSettings = useCallback(async () => {
    const generation = ++settingsGenerationRef.current;
    settingsControllerRef.current?.abort();
    const controller = new AbortController();
    settingsControllerRef.current = controller;
    setSettingsState("loading");
    setSettingsError("");
    setNotice("");
    try {
      const response = await fetch("/api/settings", { cache: "no-store", signal: controller.signal });
      const payload: unknown = await response.json().catch(() => null);
      if (!response.ok || !isOperatingSettings(payload)) {
        throw new Error(payloadError(payload, "系统设置读取失败"));
      }
      if (generation !== settingsGenerationRef.current || controller.signal.aborted) return;
      setSettings(payload);
      setSettingsState("ready");
    } catch (reason) {
      if (generation !== settingsGenerationRef.current || controller.signal.aborted || isAbortError(reason)) return;
      setSettingsError(reason instanceof Error ? reason.message : "暂时无法读取系统设置");
      setSettingsState("error");
    } finally {
      if (generation === settingsGenerationRef.current && settingsControllerRef.current === controller) {
        settingsControllerRef.current = null;
      }
    }
  }, []);

  const loadMarketSettingsStatus = useCallback(async (reloadScope: number) => {
    const generation = ++marketGenerationRef.current;
    const requestKey = `market-settings:${reloadScope}:${generation}`;
    marketControllerRef.current?.abort();
    const controller = new AbortController();
    marketControllerRef.current = controller;
    setMarketState("loading");
    setMarketError("");
    setMarketStatusRequestKey(requestKey);
    try {
      const response = await fetch("/api/market/master?view=settings_status", { cache: "no-store", signal: controller.signal });
      const payload = await response.json().catch(() => null) as MarketSettingsStatusData;
      if (!response.ok || !payload) throw new Error(payloadError(payload, "市场导入与任务状态读取失败"));
      if (generation !== marketGenerationRef.current || controller.signal.aborted) return;
      setMarketData(payload);
      setMarketStatusScopeKey(requestKey);
      setMarketStatusReloadScope(reloadScope);
      setMarketState("ready");
    } catch (reason) {
      if (generation !== marketGenerationRef.current || controller.signal.aborted || isAbortError(reason)) return;
      setMarketError(reason instanceof Error ? reason.message : "市场导入与任务状态读取失败");
      setMarketState("error");
    } finally {
      if (generation === marketGenerationRef.current && marketControllerRef.current === controller) {
        marketControllerRef.current = null;
      }
    }
  }, []);

  const invalidateMarketSettingsStatus = useCallback(() => {
    marketGenerationRef.current += 1;
    marketControllerRef.current?.abort();
    marketControllerRef.current = null;
    setMarketData(null);
    setMarketState("idle");
    setMarketError("");
    setMarketStatusRequestKey("");
    setMarketStatusScopeKey("");
    setMarketStatusReloadScope(-1);
  }, []);

  useEffect(() => {
    if (activeTab !== "parameters") return;
    const timer = window.setTimeout(() => void loadSettings(), 0);
    return () => {
      window.clearTimeout(timer);
      settingsGenerationRef.current += 1;
      settingsControllerRef.current?.abort();
      settingsControllerRef.current = null;
    };
  }, [activeTab, loadSettings]);

  useEffect(() => {
    if (!shouldLoadMarketSettingsStatus(activeTab, marketPane)) {
      invalidateMarketSettingsStatus();
      return;
    }
    const reloadScope = marketReloadKey;
    const timer = window.setTimeout(() => void loadMarketSettingsStatus(reloadScope), 0);
    return () => {
      window.clearTimeout(timer);
      marketGenerationRef.current += 1;
      marketControllerRef.current?.abort();
      marketControllerRef.current = null;
    };
  }, [activeTab, invalidateMarketSettingsStatus, loadMarketSettingsStatus, marketPane, marketReloadKey]);

  useEffect(() => () => {
    saveGenerationRef.current += 1;
    saveControllerRef.current?.abort();
    saveControllerRef.current = null;
  }, []);

  const updateNumber = (key: NumericSettingKey, value: number) => {
    if (!canEdit) return;
    setSettings((current) => current
      ? { ...current, [key]: Number.isFinite(value) ? value : 0 }
      : current);
  };

  const toggle = (key: BooleanSettingKey) => {
    if (!canEdit) return;
    setSettings((current) => current ? { ...current, [key]: !current[key] } : current);
  };

  const saveSettings = async () => {
    if (!canEdit || !settings || saving) return;
    const generation = ++saveGenerationRef.current;
    saveControllerRef.current?.abort();
    const controller = new AbortController();
    saveControllerRef.current = controller;
    setSaving(true);
    setSettingsError("");
    setNotice("");
    try {
      const response = await fetch("/api/settings", {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(settings),
        signal: controller.signal,
      });
      const payload: unknown = await response.json().catch(() => null);
      if (!response.ok || !isOperatingSettings(payload)) {
        throw new Error(payloadError(payload, "保存系统设置失败"));
      }
      if (generation !== saveGenerationRef.current || controller.signal.aborted) return;
      setSettings(payload);
      setSettingsState("ready");
      setNotice("系统设置已保存，后续库存分析会使用新的规则。");
    } catch (reason) {
      if (generation !== saveGenerationRef.current || controller.signal.aborted || isAbortError(reason)) return;
      setSettingsError(reason instanceof Error ? reason.message : "保存系统设置失败");
    } finally {
      if (generation === saveGenerationRef.current) setSaving(false);
      if (saveControllerRef.current === controller) saveControllerRef.current = null;
    }
  };

  const selectTab = (tab: SettingsTab) => onModuleViewChange(tab);
  const handleTabKeyDown = (event: KeyboardEvent<HTMLButtonElement>, tab: SettingsTab) => {
    const next = nextSettingsTab(tab, event.key);
    if (!next) return;
    event.preventDefault();
    event.currentTarget.parentElement
      ?.querySelector<HTMLButtonElement>(`#settings-tab-${next}`)
      ?.focus();
    selectTab(next);
  };
  const handleMarketPaneKeyDown = (
    event: KeyboardEvent<HTMLButtonElement>,
    pane: MarketSettingsPane,
  ) => {
    const next = nextMarketSettingsPane(pane, event.key);
    if (!next) return;
    event.preventDefault();
    setMarketPane(next);
    event.currentTarget.parentElement
      ?.querySelector<HTMLButtonElement>(`#settings-master-tab-${next}`)
      ?.focus();
  };

  const marketStatusIsCurrent = marketStatusMatchesCurrentRequest(
    marketStatusRequestKey,
    marketStatusScopeKey,
    marketStatusReloadScope,
    marketReloadKey,
  );
  const currentMarketData = shouldLoadMarketSettingsStatus(activeTab, marketPane)
    && marketData
    && (marketStatusIsCurrent || marketState === "loading" || marketState === "error" || marketStatusReloadScope < marketReloadKey)
    ? marketData
    : null;

  const renderParameters = () => {
    if ((settingsState === "idle" || settingsState === "loading") && !settings) {
      return <LoadingState>正在读取系统设置</LoadingState>;
    }
    if (!settings) {
      return <section className="panel data-state data-state-error" role="alert">
        <span className="state-symbol" aria-hidden="true">!</span>
        <strong>系统设置加载失败</strong>
        <p>{settingsError || "暂时无法读取系统设置"}</p>
        <button type="button" className="secondary-button" onClick={() => void loadSettings()}>重新加载</button>
      </section>;
    }

    const switches: Array<{ key: BooleanSettingKey; label: string; note: string }> = [
      { key: "autoReplenishment", label: "自动生成补货建议", note: "自动计算建议补货量，仍需人工确认草稿" },
      { key: "inventoryAlert", label: "库存异常提醒", note: "在 BI 看板集中显示库存健康风险" },
      { key: "allowNegativeInventory", label: "允许负库存", note: "仅影响导入校验，不会修改已有库存" },
    ];

    return <>
      {(settingsError || notice) && <section
        className={`inventory-feedback ${settingsError ? "inventory-feedback-error" : "inventory-feedback-success"}`}
        role={settingsError ? "alert" : "status"}
      >
        <span>{settingsError ? "!" : "✓"}</span>
        <div><strong>{settingsError ? "处理失败" : "保存成功"}</strong><p>{settingsError || notice}</p></div>
      </section>}
      <section className="settings-grid">
        <article className="panel settings-menu">
          <h2>设置中心</h2>
          <p>管理员可保存库存健康、库龄和预警规则。</p>
          {[
            ["parameters", "库存参数", "周转、库龄与补货规则", "库"],
            ["master", "主数据与映射", "TOP SKU、价格带、细分类目和 AI 工作流", "主"],
            ["permissions", "权限管理", "仅管理员可保存设置", "权"],
          ].map(([tab, label, note, icon]) => <button
            type="button"
            className={tab === "parameters" ? "active" : ""}
            key={tab}
            onClick={() => selectTab(tab as SettingsTab)}
          >
            <span>{icon}</span><div><strong>{label}</strong><small>{note}</small></div><em>›</em>
          </button>)}
        </article>
        <article className="panel settings-form">
          <SectionHeader title="库存分析参数" note="保存后适用于后续库存健康、库龄分析与备货建议" />
          {!canEdit && <section className="inventory-feedback" id="settings-readonly-notice" role="note">
            <span aria-hidden="true">i</span>
            <div><strong>当前为只读模式</strong><p>仅管理员可修改并保存系统设置。</p></div>
          </section>}
          <div className="form-section">
            <h3>周转与预警</h3>
            <div className="form-grid">
              <label><span>目标库存天数</span><div><input type="number" min={1} max={365} value={settings.targetDays} disabled={!canEdit} onChange={(event) => updateNumber("targetDays", Number(event.target.value))} /><em>天</em></div><small>用于计算建议补货数量</small></label>
              <label><span>低库存预警线</span><div><input type="number" min={1} max={120} value={settings.criticalDays} disabled={!canEdit} onChange={(event) => updateNumber("criticalDays", Number(event.target.value))} /><em>天</em></div><small>低于该天数触发库存预警</small></label>
              <label><span>低周转判定</span><div><input type="number" min={1} max={730} value={settings.slowDays} disabled={!canEdit} onChange={(event) => updateNumber("slowDays", Number(event.target.value))} /><em>天</em></div><small>用于识别低动销库存</small></label>
              <label><span>呆滞库存判定</span><div><input type="number" min={1} max={1460} value={settings.stagnantDays} disabled={!canEdit} onChange={(event) => updateNumber("stagnantDays", Number(event.target.value))} /><em>天</em></div><small>用于生成滞销清理清单</small></label>
            </div>
          </div>
          <div className="form-section">
            <h3>自动化规则</h3>
            {switches.map(({ key, label, note }) => <div className="toggle-row" key={key}>
              <div><strong>{label}</strong><small>{note}</small></div>
              <button
                type="button"
                role="switch"
                aria-checked={settings[key]}
                aria-label={label}
                disabled={!canEdit}
                onClick={() => toggle(key)}
                className={`toggle ${settings[key] ? "on" : ""}`}
              ><i /></button>
            </div>)}
          </div>
          <footer className="form-actions">
            <span>上次保存：{settings.updatedAt ? `${formatDateTime(settings.updatedAt)}${settings.updatedBy ? ` · ${settings.updatedBy}` : ""}` : "尚未保存"}</span>
            <button type="button" className="primary-button" disabled={!canEdit || saving || settingsState === "loading"} onClick={() => void saveSettings()}>{saving ? "保存中…" : canEdit ? "保存设置" : "仅管理员可保存"}</button>
          </footer>
        </article>
      </section>
    </>;
  };

  return <>
    <div className="subnav" role="tablist" aria-label="系统设置工作区">
      {settingsTabs.map((tab) => <button
        key={tab}
        id={`settings-tab-${tab}`}
        type="button"
        role="tab"
        className={activeTab === tab ? "active" : ""}
        aria-selected={activeTab === tab}
        aria-controls={`settings-panel-${tab}`}
        tabIndex={activeTab === tab ? 0 : -1}
        onClick={() => selectTab(tab)}
        onKeyDown={(event) => handleTabKeyDown(event, tab)}
      >{settingsTabLabels[tab]}</button>)}
    </div>

    {activeTab === "parameters" && <div
      id="settings-panel-parameters"
      className="data-refresh-region"
      role="tabpanel"
      aria-labelledby="settings-tab-parameters"
      aria-busy={settingsState === "loading"}
      tabIndex={0}
    >{renderParameters()}</div>}

    {activeTab === "master" && <section
      id="settings-panel-master"
      className="settings-market-master"
      role="tabpanel"
      aria-labelledby="settings-tab-master"
      tabIndex={0}
    >
      <div className="subnav settings-master-subnav" role="tablist" aria-label="主数据与映射工作区">
        {marketSettingsPanes.map((pane) => {
          const label = pane === "master-data"
            ? "市场主数据"
            : pane === "imports"
              ? "导入与任务"
              : "AI 图片标注";
          return <button
            key={pane}
            id={`settings-master-tab-${pane}`}
            type="button"
            role="tab"
            className={marketPane === pane ? "active" : ""}
            aria-selected={marketPane === pane}
            aria-controls={`settings-master-panel-${pane}`}
            tabIndex={marketPane === pane ? 0 : -1}
            onClick={() => setMarketPane(pane)}
            onKeyDown={(event) => handleMarketPaneKeyDown(event, pane)}
          >{label}</button>;
        })}
      </div>

      {marketPane === "master-data" && <section
        id="settings-master-panel-master-data"
        role="tabpanel"
        aria-labelledby="settings-master-tab-master-data"
        tabIndex={0}
      >
        <Suspense fallback={<LoadingState>正在加载市场主数据</LoadingState>}>
          <LazyMarketMasterAdminPanel currentUser={currentUser} />
        </Suspense>
      </section>}

      {marketPane === "imports" && <section
        id="settings-master-panel-imports"
        className="data-refresh-region"
        role="tabpanel"
        aria-labelledby="settings-master-tab-imports"
        aria-busy={marketState === "loading"}
        tabIndex={0}
      >
        {(marketState === "idle" || marketState === "loading") && !currentMarketData && <LoadingState>正在读取导入与任务数据</LoadingState>}
        {marketError && <section className="inventory-feedback inventory-feedback-error" role="alert">
          <span>!</span><div><strong>导入与任务数据加载失败</strong><p>{marketError}</p></div>
          <button type="button" className="secondary-button" onClick={() => setMarketReloadKey((key) => key + 1)}>重新加载</button>
        </section>}
        {currentMarketData && <Suspense fallback={<LoadingState>正在加载导入与任务工作区</LoadingState>}>
          <LazyMarketDataImportPanel currentUser={currentUser} data={currentMarketData} onImported={() => setMarketReloadKey((key) => key + 1)} />
          <LazyMarketWorkflowPanel data={currentMarketData} />
        </Suspense>}
      </section>}

      {marketPane === "annotation" && <section
        id="settings-master-panel-annotation"
        role="tabpanel"
        aria-labelledby="settings-master-tab-annotation"
        tabIndex={0}
      >
        <Suspense fallback={<LoadingState>正在加载 AI 图片标注</LoadingState>}>
          <LazyMarketAnnotationView currentUser={currentUser} />
        </Suspense>
      </section>}
    </section>}

    {activeTab === "warehouses" && <section
      id="settings-panel-warehouses"
      role="tabpanel"
      aria-labelledby="settings-tab-warehouses"
      tabIndex={0}
    >
      <Suspense fallback={<LoadingState>正在加载仓库映射</LoadingState>}>
        <LazyWarehouseMappingSettings canEdit={canEdit} />
      </Suspense>
    </section>}

    {activeTab === "dingtalk" && <section
      id="settings-panel-dingtalk"
      role="tabpanel"
      aria-labelledby="settings-tab-dingtalk"
      tabIndex={0}
    >
      <Suspense fallback={<LoadingState>正在加载钉钉机器人设置</LoadingState>}>
        <LazyDingTalkRobotSettings canWrite={canEditDingTalk} />
      </Suspense>
    </section>}

    {activeTab === "permissions" && <section
      id="settings-panel-permissions"
      className="settings-permissions-panel"
      role="tabpanel"
      aria-labelledby="settings-tab-permissions"
      tabIndex={0}
    >
      <SectionHeader title="用户、角色与数据范围" note="Django/PostgreSQL 是用户权限与变更审计的唯一权威；AI 与后台任务在派发前重新校验当前权限。" />
      <Suspense fallback={<LoadingState>正在加载权限管理</LoadingState>}>
        <LazyAccessControlManagement canManage={currentUser?.role === "admin" && !currentUser.scopeRestricted} />
      </Suspense>
    </section>}
  </>;
}
