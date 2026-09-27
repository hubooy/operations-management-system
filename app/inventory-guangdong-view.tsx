"use client";

import { useAiPageDetails } from "./ai-page-context-provider";

import { useCallback, useEffect, useRef, useState } from "react";
import InventoryFilterBar, { type InventorySharedFilters } from "./inventory-filter-bar";
import { formatCount, formatRate, useDebouncedValue } from "./module-view-shared";
import type { GuangdongIdentity, GuangdongItem, GuangdongMonitor, GuangdongPreview, GuangdongWatchRow } from "@/lib/inventory/guangdong-contract";
import styles from "./inventory-guangdong.module.css";

const BASE = "/api/inventory/guangdong-monitor";
type Watchlist = { version: string; items: Array<GuangdongWatchRow & GuangdongIdentity> };
type Cycles = { version: string; items: Array<{ supplier: string; leadDays: number | null; bufferDays: number }> };
const numberText = (value: number | null) => value === null ? "—" : formatCount(value);
const daysText = (value: number | null) => value === null ? "—" : `${value.toFixed(1)} 天`;
const riskOptions = [["no_stock", "无库存可用"], ["urgent", "紧急补货"], ["warning", "补货预警"], ["unknown", "积压风险"], ["stale", "低周转"], ["healthy", "库存健康"]] as const;

async function jsonRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(BASE + path, { cache: "no-store", ...init });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "广东监控请求失败");
  return data as T;
}
const jsonBody = (value: unknown, method = "POST") => ({ method, headers: { "content-type": "application/json" }, body: JSON.stringify(value) });

export default function GuangdongInventoryView({ canManage, filters, onFiltersChange, onAskAi }: {
  canManage: boolean; filters: InventorySharedFilters; onFiltersChange: (value: InventorySharedFilters) => void; onAskAi: (prompt: string) => void;
}) {
  const [dataState, setDataState] = useState<{ key: string; value: GuangdongMonitor } | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [risk, setRisk] = useState(() => typeof window === "undefined" ? "" : new URL(window.location.href).searchParams.get("inventoryGuangdongRisk") || "");
  const [pageState, setPageState] = useState({ scope: "", page: 1 });
  const [panel, setPanel] = useState<"watchlist" | "suppliers" | null>(null);
  const [list, setList] = useState<Watchlist | null>(null);
  const [cycles, setCycles] = useState<Cycles | null>(null);
  const [paste, setPaste] = useState("");
  const [preview, setPreview] = useState<GuangdongPreview | null>(null);
  const [source, setSource] = useState({ name: "页面维护", rawHash: "" });
  const [productQuery, setProductQuery] = useState("");
  const [products, setProducts] = useState<GuangdongIdentity[]>([]);
  const [listQuery, setListQuery] = useState("");
  const [listPage, setListPage] = useState(1);
  const [editing, setEditing] = useState<GuangdongItem | null>(null);
  const [editingVersion, setEditingVersion] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const generation = useRef(0);
  const managementGeneration = useRef(0);
  const searchGeneration = useRef(0);
  const productSearch = useDebouncedValue(filters.productQuery, 250);
  useAiPageDetails("inventory", {
    period: null,
    filters: { dataset: "inventory_guangdong", query: productSearch.trim(), warehouses: ["广东仓"], brands: filters.brands, categories: filters.categories, suppliers: filters.suppliers, risk },
  });

  const params = new URLSearchParams();
  if (productSearch.trim()) params.set("q", productSearch.trim());
  filters.brands.forEach((value) => params.append("brand", value));
  filters.categories.forEach((value) => params.append("category", value));
  filters.suppliers.forEach((value) => params.append("supplier", value));
  if (risk) params.set("risk", risk);
  const scope = params.toString();
  const page = pageState.scope === scope ? pageState.page : 1;
  params.set("page", String(page));
  const query = params.toString();
  const data = dataState?.key === query ? dataState.value : null;

  useEffect(() => {
    const controller = new AbortController(); const id = ++generation.current;
    const timer = window.setTimeout(() => {
      setLoading(true); setError("");
      void jsonRequest<GuangdongMonitor>(`?${query}`, { signal: controller.signal }).then((value) => {
        if (id === generation.current && !controller.signal.aborted) setDataState({ key: query, value });
      }).catch((err) => { if (!controller.signal.aborted && id === generation.current) setError(err.message); })
        .finally(() => { if (!controller.signal.aborted && id === generation.current) setLoading(false); });
    }, 0);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [query, refresh]);
  useEffect(() => {
    const restore = () => setRisk(new URL(window.location.href).searchParams.get("inventoryGuangdongRisk") || "");
    window.addEventListener("popstate", restore); return () => window.removeEventListener("popstate", restore);
  }, []);
  const changeRisk = (value: string) => {
    setRisk(value); const url = new URL(window.location.href);
    if (value) url.searchParams.set("inventoryGuangdongRisk", value); else url.searchParams.delete("inventoryGuangdongRisk");
    window.history.replaceState(window.history.state, "", url);
  };
  const run = async (operation: () => Promise<void>) => {
    setBusy(true); setError(""); setNotice("");
    try { await operation(); } catch (err) { setError(err instanceof Error ? err.message : "操作失败"); }
    finally { setBusy(false); }
  };
  const loadManagement = useCallback(async (next: "watchlist" | "suppliers") => {
    const id = ++managementGeneration.current;
    if (next === "watchlist") {
      const result = await jsonRequest<Watchlist>("/watchlist");
      if (id === managementGeneration.current) setList(result);
    } else {
      const result = await jsonRequest<Cycles>("/suppliers");
      if (id === managementGeneration.current) setCycles(result);
    }
  }, []);
  const openPanel = (next: "watchlist" | "suppliers") => {
    setPanel(next); setPreview(null);
    void run(() => loadManagement(next));
  };
  const prepare = async (rows: GuangdongWatchRow[], name = "页面维护", rawHash = "") => {
    setPreview(null);
    setPreview(await jsonRequest<GuangdongPreview>("/preview", jsonBody({ rows })));
    setSource({ name, rawHash });
  };
  const commit = () => void run(async () => {
    if (!preview?.valid) return;
    const result = await jsonRequest<{ status: string }>("/import", jsonBody({ action: "import", rows: preview.items.map(({ productCode, active, notes }) => ({ productCode, active, notes })), version: preview.version, contentHash: preview.contentHash, source: source.name, rawHash: source.rawHash }));
    setPreview(null); setNotice(result.status === "unchanged" ? "清单内容未变化，已核验一致。" : "监控清单已保存并回查。");
    await loadManagement("watchlist"); setRefresh((value) => value + 1);
  });
  const download = (kind: "template" | "watchlist" | "monitor") => void run(async () => {
    let path = "/template";
    if (kind !== "template") {
      const token = kind === "watchlist" ? (list ?? await jsonRequest<Watchlist>("/watchlist")).version : data?.version;
      if (!token) throw new Error("请先刷新监控数据");
      const exportParams = new URLSearchParams(kind === "monitor" ? query : "");
      exportParams.set("kind", kind); exportParams.set("version", token);
      path = `/export?${exportParams}`;
    }
    const response = await fetch(BASE + path);
    if (!response.ok) throw new Error((await response.json()).error || "导出失败");
    const url = URL.createObjectURL(await response.blob()); const link = document.createElement("a");
    link.href = url; link.download = kind === "template" ? "广东监控清单模板.xlsx" : kind === "watchlist" ? "广东监控清单.xlsx" : "广东入仓监控.xlsx";
    link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  const visibleList = (list?.items ?? []).filter((row) => `${row.productCode} ${row.productName}`.toLowerCase().includes(listQuery.toLowerCase()));
  const saveItem = (form: HTMLFormElement) => void run(async () => {
    if (!editing || !editingVersion) return;
    const values = new FormData(form);
    const leadText = String(values.get("lead") ?? "").trim();
    const bufferText = String(values.get("buffer") ?? "").trim();
    const riskText = String(values.get("risk") ?? "").trim();
    const riskReason = String(values.get("riskReason") ?? "").trim();
    if (!!leadText !== !!bufferText) throw new Error("生产周期和安全天数必须同时填写或同时留空");
    if (!!riskText !== !!riskReason) throw new Error("手工风险和原因说明必须同时填写或同时留空");
    await jsonRequest("/items", jsonBody({
      action: "item", productCode: editing.productCode,
      leadDays: leadText ? Number(leadText) : null, bufferDays: bufferText ? Number(bufferText) : null,
      operatorName: String(values.get("operatorName") ?? ""), buyer: String(values.get("buyer") ?? ""),
      risk: riskText, riskReason,
      version: editingVersion,
    }, "PATCH"));
    setEditing(null); setNotice("型号设置已保存并回查。"); setRefresh((value) => value + 1);
  });

  return <div className={styles.root}>
    <section className="inventory-sync-bar"><div><h2>广东入仓库存监控</h2><p>库存快照 {data?.sync.inventoryAsOf ?? "—"} · 库龄快照 {data?.sync.inventoryAgeAsOf ?? "—"} · 销售截至 {data?.sync.salesThrough ?? "—"} · 固定仓库：广东仓</p></div><div className={styles.actions}>
      <button className="row-action" disabled={busy || loading} onClick={() => setRefresh((value) => value + 1)}>刷新</button>
      <button className="secondary-button" disabled={busy} onClick={() => openPanel("watchlist")}>监控清单</button>
      <button className="secondary-button" disabled={busy} onClick={() => openPanel("suppliers")}>供应商备货周期</button>
      <button className="secondary-button" disabled={busy || !data || loading || !!error} onClick={() => download("monitor")}>导出全部筛选结果</button>
      <button className="row-action" onClick={() => onAskAi(`请使用广东入仓监控只读工具分析当前风险。货品搜索：${filters.productQuery || "全部"}；品牌：${filters.brands.join("、") || "全部"}；品类：${filters.categories.join("、") || "全部"}；供应商：${filters.suppliers.join("、") || "全部"}；风险：${risk || "全部"}。不要创建备货计划。`)}>问问小特</button>
    </div></section>
    {error && <div className="inventory-feedback inventory-feedback-error" role="alert">{error}<button className="row-action" onClick={() => setRefresh((value) => value + 1)}>重新读取</button></div>}
    {notice && <p role="status">{notice}</p>}
    <InventoryFilterBar activeTab="guangdong" filters={filters} onChange={onFiltersChange} updating={loading} options={{ warehouses: ["广东仓"], brands: data?.filters.brands ?? [], categories: data?.filters.categories ?? [], suppliers: data?.filters.suppliers ?? [], ageBuckets: [] }} />
    {data?.sync.inventoryStale && <p className={styles.warning}>库存快照待更新：风险与最晚下单日期是基于所示快照的估算。</p>}
    {!data && !error && <section className="panel data-state" role="status">正在读取广东仓监控数据…</section>}
    {panel && <section className={`panel ${styles.management}`} aria-label={panel === "watchlist" ? "监控清单管理" : "供应商周期管理"}>
      <div className={styles.heading}><h3>{panel === "watchlist" ? "监控清单管理" : "供应商备货周期"}</h3><button className="row-action" onClick={() => { setPanel(null); managementGeneration.current++; }}>收起</button></div>
      {panel === "watchlist" ? <>
        <div className={styles.actions}><button className="row-action" disabled={busy} onClick={() => download("template")}>下载Excel模板</button><button className="row-action" disabled={busy} onClick={() => download("watchlist")}>导出完整清单</button>{canManage && <button className="primary-button" disabled={busy} onClick={() => fileRef.current?.click()}>导入Excel</button>}</div>
        <input ref={fileRef} type="file" accept=".xlsx" hidden onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (!file) return; void run(async () => {
          setPreview(null); if (file.size > 4 * 1024 * 1024) throw new Error("Excel文件不能超过4MiB");
          const result = await jsonRequest<GuangdongPreview & { rawHash: string }>("/file-preview", { method: "POST", body: file });
          setPreview(result); setSource({ name: file.name, rawHash: result.rawHash });
        }); }} />
        {canManage && <>
          <form className={styles.actions} onSubmit={(event) => { event.preventDefault(); const id = ++searchGeneration.current; void run(async () => { const result = await jsonRequest<{ items: GuangdongIdentity[] }>(`/products?q=${encodeURIComponent(productQuery)}`); if (id === searchGeneration.current) setProducts(result.items); }); }}><input aria-label="添加货品搜索" value={productQuery} maxLength={100} placeholder="搜索编码或品名，选定后添加" onChange={(event) => { setProductQuery(event.target.value); searchGeneration.current++; setProducts([]); }} /><button className="secondary-button" disabled={busy || !productQuery.trim()}>搜索</button></form>
          {products.length > 0 && <div className={styles.searchResults}>{products.map((item) => <button className="row-action" key={item.productCode} disabled={busy} onClick={() => void run(() => prepare([{ productCode: item.productCode, active: true, notes: list?.items.find((row) => row.productCode === item.productCode)?.notes ?? "" }]))}>{item.productCode} · {item.productName} · 添加到预览</button>)}</div>}
          <label>从Excel粘贴多行（编码、启用/暂停、备注；也可只粘贴一列编码）<textarea aria-label="批量粘贴监控型号" rows={4} value={paste} onChange={(event) => { setPaste(event.target.value); setPreview(null); }} placeholder={"001234\t启用\t重点关注\n005678\t暂停\t季节型号"} /></label>
          <button className="secondary-button" disabled={busy || !paste.trim()} onClick={() => void run(async () => {
            const { parseWatchPaste } = await import("@/lib/inventory/guangdong-workbook");
            const bytes = new TextEncoder().encode(paste); const hash = [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))].map((v) => v.toString(16).padStart(2, "0")).join("");
            await prepare(parseWatchPaste(paste), "粘贴多行", hash);
          })}>预览批量变更</button>
        </>}
        {preview && <div className={styles.preview}><h4>导入预览：新增 {preview.counts.added} · 更新 {preview.counts.updated} · 未变化 {preview.counts.unchanged}</h4><p>只更新本次提供的编码；清单中其他型号保持原状。</p>{preview.errors.slice(0, 50).map((item, index) => <p role="alert" key={index}>{item.row ? `第${item.row}行` : item.productCode}：{item.error}</p>)}<div className={styles.searchResults}>{preview.items.slice(0, 50).map((item) => <span key={item.productCode}>{item.productCode} · {item.active ? "启用" : "暂停"} · {item.notes}</span>)}</div><p>预览前50条，共{preview.items.length}条；错误{preview.errors.length}条。</p><button className="primary-button" disabled={!preview.valid || busy || !canManage} onClick={commit}>确认保存清单</button><button className="row-action" onClick={() => setPreview(null)}>取消预览</button></div>}
        <input aria-label="筛选监控清单" placeholder="筛选已添加编码或品名" value={listQuery} onChange={(event) => { setListQuery(event.target.value); setListPage(1); }} />
        <div className="data-table-wrap"><table className="data-table"><thead><tr><th>编码 / 品名</th><th>状态</th><th>备注</th><th>操作</th></tr></thead><tbody>{visibleList.slice((listPage - 1) * 50, listPage * 50).map((row) => <tr key={`${row.productCode}:${list?.version}`}><td>{row.productCode}<small className="cell-note">{row.productName}</small></td><td>{row.active ? "启用" : "暂停"}</td><td colSpan={2}><form className={styles.actions} onSubmit={(event) => { event.preventDefault(); const form = new FormData(event.currentTarget); void run(() => prepare([{ productCode: row.productCode, active: row.active, notes: String(form.get("notes") ?? "") }])); }}><input name="notes" aria-label={`${row.productCode}备注`} defaultValue={row.notes} maxLength={1000} disabled={!canManage || busy} />{canManage && <><button className="row-action" disabled={busy}>预览备注</button><button type="button" className="row-action" disabled={busy} onClick={() => void run(() => prepare([{ productCode: row.productCode, active: !row.active, notes: row.notes }]))}>{row.active ? "暂停" : "恢复"}</button></>}</form></td></tr>)}</tbody></table></div><div className={styles.actions}><span>共 {visibleList.length} 个型号</span><button className="row-action" disabled={listPage <= 1} onClick={() => setListPage((v) => v - 1)}>上一页</button><button className="row-action" disabled={listPage * 50 >= visibleList.length} onClick={() => setListPage((v) => v + 1)}>下一页</button></div>
      </> : <><p>填写从下单到可用入库的完整生产周期。同一供应商共用设置，安全天数默认7天。</p>{cycles?.items.length === 0 && <p>清单暂无已映射供应商，请先添加型号并补齐ERP供应商。</p>}<div className="data-table-wrap"><table className="data-table"><thead><tr><th>供应商</th><th>生产周期 / 安全天数</th></tr></thead><tbody>{cycles?.items.map((item) => <tr key={`${item.supplier}:${cycles.version}`}><td>{item.supplier}</td><td><form className={styles.actions} onSubmit={(event) => { event.preventDefault(); const form = new FormData(event.currentTarget); void run(async () => { await jsonRequest("/suppliers", jsonBody({ action: "supplier", supplier: item.supplier, leadDays: Number(form.get("lead")), bufferDays: Number(form.get("buffer")), version: cycles.version }, "PATCH")); setNotice("供应商周期已保存并回查。"); await loadManagement("suppliers"); setRefresh((value) => value + 1); }); }}><input name="lead" aria-label={`${item.supplier}生产周期`} type="number" min={1} max={365} step={1} required defaultValue={item.leadDays ?? ""} placeholder="待设置" disabled={!canManage || busy} /><input name="buffer" aria-label={`${item.supplier}安全天数`} type="number" min={0} max={365} step={1} required defaultValue={item.bufferDays} disabled={!canManage || busy} />{canManage && <button className="primary-button" disabled={busy}>保存</button>}</form></td></tr>)}</tbody></table></div></>}
    </section>}
    {data && <>
      {data.watchCount === 0 ? <section className="panel data-state"><h3>还没有启用的监控型号</h3><p>添加吉客云货品编码后，将自动关联广东仓库存、出库和供应商。</p><button className="primary-button" onClick={() => openPanel("watchlist")}>{canManage ? "添加或导入监控型号" : "查看监控清单"}</button></section> : <>
        <section className={`panel ${styles.healthPanel}`}><div className={styles.heading}><div><h3>风险库存健康分布</h3><p>按搜索、品牌、品类及供应商统计；点击分类查看明细。</p></div><button className="row-action" onClick={() => changeRisk("")}>全部风险</button></div>
          <div className={styles.distribution}>{data.distribution.map((item) => <button key={item.risk} aria-pressed={risk === item.risk} className={`${styles.riskCard} ${styles[item.risk]}`} onClick={() => changeRisk(risk === item.risk ? "" : item.risk)}><strong>{item.label}</strong><b>{item.itemCount} <small>个型号 · {formatRate(item.itemRate)}</small></b><progress aria-label={`${item.label}型号占比`} max={1} value={item.itemRate} /></button>)}</div>
          <p className={styles.note}>生产周期包含下单至可用入库，安全天数默认7天。库龄取最新吉客云库龄表；当前缺库存记录 {data.metrics.missingStockCount} 个型号。</p>
        </section>
        {editing && <section className={`panel ${styles.itemEditor}`} aria-label={`${editing.productCode}型号设置`}>
          <div className={styles.heading}><div><h3>编辑型号设置：{editing.productCode}</h3><p>{editing.productName} · 留空后自动使用默认来源。</p></div><button className="row-action" disabled={busy} onClick={() => setEditing(null)}>取消</button></div>
          <form key={`${editing.productCode}:${data.version}`} className={styles.editorGrid} onSubmit={(event) => { event.preventDefault(); saveItem(event.currentTarget); }}>
            <label>生产周期（天）<input name="lead" aria-label="型号生产周期" type="number" min={1} max={365} step={1} defaultValue={editing.leadDaysOverride ?? ""} placeholder={editing.supplierLeadDays === null ? "供应商未设置" : `供应商默认 ${editing.supplierLeadDays}`} disabled={busy} /></label>
            <label>安全天数<input name="buffer" aria-label="型号安全天数" type="number" min={0} max={365} step={1} defaultValue={editing.bufferDaysOverride ?? ""} placeholder={`供应商默认 ${editing.supplierBufferDays}`} disabled={busy} /></label>
            <label>运营负责人<input name="operatorName" aria-label="型号运营负责人" maxLength={200} defaultValue={editing.operatorNameOverride ?? ""} placeholder={editing.planOperatorName || "最新备货计划未设置"} disabled={busy} /></label>
            <label>采购负责人<input name="buyer" aria-label="型号采购负责人" maxLength={200} defaultValue={editing.buyerOverride ?? ""} placeholder={editing.planBuyer || "最新备货计划未设置"} disabled={busy} /></label>
            <label>风险判定<select name="risk" aria-label="型号风险判定" defaultValue={editing.riskOverride ?? ""} disabled={busy}><option value="">系统自动判定</option>{riskOptions.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
            <label className={styles.reasonField}>原因说明<textarea name="riskReason" aria-label="型号风险原因说明" rows={2} maxLength={1000} defaultValue={editing.riskReasonOverride ?? ""} placeholder={`系统当前判定：${editing.autoRiskLabel}`} disabled={busy} /></label>
            <div className={styles.editorActions}><button className="primary-button" disabled={busy}>保存</button><small>周期留空继承供应商设置；负责人留空继承最新未取消备货计划；风险与原因都留空时恢复系统判定。</small></div>
          </form>
        </section>}
        <section className="panel table-panel"><div className="table-toolbar"><h3>广东入仓型号明细</h3><span>{data.pagination.total} 条 · 第 {page} / {Math.max(1, data.pagination.totalPages)} 页</span></div><div className="data-table-wrap" aria-busy={loading}><table className="data-table"><thead><tr>{["商品", "规格编码", "供应商", "运营负责人", "采购负责人", "库存 / 在途", "7 / 15 / 30日出库", "销售周转 / 库龄", "生产周期 / 安全天数", "最晚下单", "备货数量 / 下单剩余库存", "风险及原因", "操作"].map((title) => <th key={title}>{title}</th>)}</tr></thead><tbody>{data.items.map((item) => <tr key={item.productCode}>
          <td><strong>{item.productName}</strong></td>
          <td><strong>{item.productCode}</strong><small className="cell-note">{item.specification || "无规格"}</small></td>
          <td>{item.supplier}<small className="cell-note">{item.supplierSource}</small></td>
          <td>{item.operatorName || "—"}<small className="cell-note">{item.operatorNameSource}</small></td>
          <td>{item.buyer || "—"}<small className="cell-note">{item.buyerSource}</small></td>
          <td>{numberText(item.availableQuantity)}<small className="cell-note">在途 {numberText(item.inTransitQuantity)}</small></td>
          <td>{numberText(item.outbound7dQuantity)} / {numberText(item.outbound15dQuantity)} / {numberText(item.outbound30dQuantity)}</td>
          <td>{daysText(item.turnoverDays)}<small className="cell-note">库龄 {numberText(item.inventoryAgeDays)} 天</small></td>
          <td>{item.leadDays === null ? "待设置" : `${item.leadDays} 天`}<small className="cell-note">安全 {item.bufferDays} 天 · {item.cycleSource}</small></td>
          <td>{item.latestOrderDate ?? "—"}</td>
          <td title={item.replenishmentRemainingReason || `下单日期 ${item.latestReplenishmentOrderDate ?? "—"}；下单后累计增库 ${numberText(item.replenishmentStockIncreaseQuantity)}；剩余=备货数量−累计增库`}>{numberText(item.replenishmentQuantity)}<small className="cell-note">剩余 {item.replenishmentRemainingQuantity == null ? "待核算" : numberText(item.replenishmentRemainingQuantity)}</small></td>
          <td className={styles.reason}><strong>{item.riskLabel}</strong><small className="cell-note">{item.riskSource} · {item.riskReasons.join("；") || "可售天数充足"}</small></td>
          <td>{canManage ? <button className="row-action" aria-label={`${item.productCode}编辑型号设置`} disabled={busy} onClick={() => { setEditing(item); setEditingVersion(data.version); }}>编辑</button> : "—"}</td>
        </tr>)}{data.items.length === 0 && <tr><td colSpan={13}>当前筛选没有监控结果。</td></tr>}</tbody></table></div><footer className="jd-sku-pagination"><button className="row-action" disabled={page <= 1 || loading} onClick={() => setPageState({ scope, page: page - 1 })}>上一页</button><button className="row-action" disabled={page >= data.pagination.totalPages || loading} onClick={() => setPageState({ scope, page: page + 1 })}>下一页</button></footer></section>
      </>}
    </>}

  </div>;
}
