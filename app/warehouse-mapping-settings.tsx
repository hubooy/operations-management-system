"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import warehouseMappingData from "@/config/inventory-warehouse-mapping.json";
import {
  inventoryWarehouseCategoryLabels,
  inventoryWarehouseCategoryOrder,
} from "@/lib/inventory/warehouse-classification";
import {
  parseWarehouseMappingPayload,
  type WarehouseMappingPayload,
  type WarehouseMappingRow,
} from "@/lib/inventory/warehouse-mapping-contract";
import {
  buildWarehouseMappingWorkbook,
  parseWarehouseMappingWorkbook,
} from "@/lib/inventory/warehouse-mapping-workbook";

type WarehouseMappingEntry = Omit<WarehouseMappingRow, "warehouse">;

export type WarehouseMappingFilters = {
  query: string;
  category: "all" | WarehouseMappingEntry["category"];
  inventory: "all" | "included" | "excluded";
  confirmation: "all" | "pending" | "confirmed";
};

type WarehouseMappingSettingsProps = {
  canEdit: boolean;
};

const PAGE_SIZE = 20;
const apiPath = "/api/settings/warehouse-mappings";

export const warehouseMappingRows: readonly WarehouseMappingRow[] = Object.entries(
  warehouseMappingData.warehouses as Record<string, Omit<WarehouseMappingEntry, "pendingConfirmation">>,
).map(([warehouse, entry]) => ({ warehouse, ...entry, pendingConfirmation: false }));

export function filterWarehouseMappings(
  rows: readonly WarehouseMappingRow[],
  filters: WarehouseMappingFilters,
) {
  const query = filters.query.trim().toLocaleLowerCase("zh-CN");
  return rows.filter((row) => {
    if (filters.category !== "all" && row.category !== filters.category) return false;
    if (filters.inventory === "included" && !row.includeInInventory) return false;
    if (filters.inventory === "excluded" && row.includeInInventory) return false;
    if (filters.confirmation === "pending" && !row.pendingConfirmation) return false;
    if (filters.confirmation === "confirmed" && row.pendingConfirmation) return false;
    if (!query) return true;
    return [row.warehouse, row.label, inventoryWarehouseCategoryLabels[row.category], row.category]
      .some((value) => value.toLocaleLowerCase("zh-CN").includes(query));
  });
}

function SummaryCard({ label, value, note }: { label: string; value: number; note: string }) {
  return <article><span>{label}</span><strong>{value.toLocaleString("zh-CN")}</strong><small>{note}</small></article>;
}

function errorMessage(value: unknown, fallback: string) {
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    if (typeof record.message === "string") return record.message;
    if (typeof record.error === "string") return record.error;
  }
  return fallback;
}

function downloadBytes(bytes: Uint8Array, name: string) {
  const copy = Uint8Array.from(bytes);
  const blob = new Blob([copy.buffer], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

export default function WarehouseMappingSettings({ canEdit }: WarehouseMappingSettingsProps) {
  const [rows, setRows] = useState<WarehouseMappingRow[]>([...warehouseMappingRows]);
  const [mappingRevision, setMappingRevision] = useState("");
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const [updatedBy, setUpdatedBy] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<WarehouseMappingFilters["category"]>("all");
  const [inventory, setInventory] = useState<WarehouseMappingFilters["inventory"]>("all");
  const [confirmation, setConfirmation] = useState<WarehouseMappingFilters["confirmation"]>("all");
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<{ type: "success" | "error"; message: string } | null>(null);
  const [editingWarehouse, setEditingWarehouse] = useState("");
  const [draftCategory, setDraftCategory] = useState<WarehouseMappingEntry["category"]>("dropship");
  const [draftIncluded, setDraftIncluded] = useState(true);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const applyPayload = useCallback((payload: WarehouseMappingPayload) => {
    setRows(payload.rows);
    setMappingRevision(payload.mappingRevision);
    setUpdatedAt(payload.updatedAt);
    setUpdatedBy(payload.updatedBy);
  }, []);

  const loadMappings = useCallback(async () => {
    setLoading(true);
    try {
      const response = await fetch(apiPath, { cache: "no-store" });
      const value = await response.json().catch(() => null);
      if (!response.ok) throw new Error(errorMessage(value, "读取仓库映射失败"));
      applyPayload(parseWarehouseMappingPayload(value));
      setFeedback(null);
    } catch (reason) {
      setFeedback({ type: "error", message: reason instanceof Error ? reason.message : "读取仓库映射失败" });
    } finally {
      setLoading(false);
    }
  }, [applyPayload]);

  useEffect(() => { void loadMappings(); }, [loadMappings]);

  const categoryOptions = useMemo(() => [...inventoryWarehouseCategoryOrder], []);
  const categoryCount = new Set(rows.map((row) => row.category)).size;
  const includedCount = rows.filter((row) => row.includeInInventory).length;
  const excludedCount = rows.length - includedCount;
  const pendingConfirmationCount = rows.filter((row) => row.pendingConfirmation).length;
  const filteredRows = useMemo(
    () => filterWarehouseMappings(rows, { query, category, inventory, confirmation }),
    [category, confirmation, inventory, query, rows],
  );
  const pageCount = Math.max(1, Math.ceil(filteredRows.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount);
  const visibleRows = filteredRows.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);

  const updateQuery = (value: string) => { setQuery(value); setPage(1); };
  const updateCategory = (value: WarehouseMappingFilters["category"]) => { setCategory(value); setPage(1); };
  const updateInventory = (value: WarehouseMappingFilters["inventory"]) => { setInventory(value); setPage(1); };
  const updateConfirmation = (value: WarehouseMappingFilters["confirmation"]) => { setConfirmation(value); setPage(1); };

  const saveMappings = async (
    mappings: Array<Pick<WarehouseMappingRow, "warehouse" | "category" | "includeInInventory">>,
    successMessage: string,
  ) => {
    if (!canEdit || !mappingRevision || saving) return false;
    setSaving(true);
    setFeedback(null);
    try {
      const response = await fetch(apiPath, {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ expectedMappingRevision: mappingRevision, mappings }),
      });
      const value = await response.json().catch(() => null);
      if (!response.ok) throw new Error(errorMessage(value, "保存仓库映射失败"));
      applyPayload(parseWarehouseMappingPayload(value));
      setFeedback({ type: "success", message: successMessage });
      return true;
    } catch (reason) {
      setFeedback({ type: "error", message: reason instanceof Error ? reason.message : "保存仓库映射失败" });
      return false;
    } finally {
      setSaving(false);
    }
  };

  const beginEdit = (row: WarehouseMappingRow) => {
    setEditingWarehouse(row.warehouse);
    setDraftCategory(row.category);
    setDraftIncluded(row.includeInInventory);
    setFeedback(null);
  };

  const saveEdit = async () => {
    if (!editingWarehouse) return;
    const saved = await saveMappings([{
      warehouse: editingWarehouse,
      category: draftCategory,
      includeInInventory: draftIncluded,
    }], `已确认仓库“${editingWarehouse}”，库存管理将立即采用所选归类。`);
    if (saved) setEditingWarehouse("");
  };

  const exportMappings = () => {
    downloadBytes(buildWarehouseMappingWorkbook(rows), `仓库类型映射_${new Date().toISOString().slice(0, 10)}.xlsx`);
    setFeedback({ type: "success", message: `已导出全部 ${rows.length} 条仓库映射。` });
  };

  const importMappings = async (file: File) => {
    try {
      if (!/\.xlsx$/i.test(file.name)) throw new Error("请选择 .xlsx 格式的仓库映射文件");
      const mappings = parseWarehouseMappingWorkbook(await file.arrayBuffer());
      if (!window.confirm(`将新增或更新 ${mappings.length} 条仓库映射；文件中未列出的仓库不会删除。是否继续？`)) return;
      await saveMappings(mappings, `已导入 ${mappings.length} 条仓库映射，库存管理已同步新口径。`);
    } catch (reason) {
      setFeedback({ type: "error", message: reason instanceof Error ? reason.message : "导入仓库映射失败" });
    } finally {
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  return <div className="warehouse-mapping-workspace">
    <section className="panel warehouse-mapping-overview">
      <div>
        <span className="warehouse-mapping-eyebrow">库存统一口径</span>
        <h2>仓库映射</h2>
        <p>新仓库首次出现在库存或库龄资料后会自动加入并标记“待确认”；管理员确认的结果统一用于库存总览、库龄、滞销清理、库存导入和备货计划。</p>
      </div>
      <div className="warehouse-mapping-summary" aria-label="仓库映射汇总">
        <SummaryCard label="仓库总数" value={rows.length} note={`默认版本 ${warehouseMappingData.version}`} />
        <SummaryCard label="仓库类型" value={categoryCount} note="按当前自定义类型统计" />
        <SummaryCard label="待确认" value={pendingConfirmationCount} note="新发现仓库优先显示" />
        <SummaryCard label="计入库存" value={includedCount} note="进入库存分析与备货口径" />
        <SummaryCard label="不计入库存" value={excludedCount} note="保留明细但不计入总览" />
      </div>
    </section>

    {feedback && <section className={`inventory-feedback ${feedback.type === "error" ? "inventory-feedback-error" : "inventory-feedback-success"}`} role={feedback.type === "error" ? "alert" : "status"}>
      <span>{feedback.type === "error" ? "!" : "✓"}</span><div><strong>{feedback.type === "error" ? "处理失败" : "处理完成"}</strong><p>{feedback.message}</p></div>
    </section>}

    <section className="panel table-panel warehouse-mapping-panel" aria-busy={loading || saving}>
      <div className="table-toolbar warehouse-mapping-toolbar">
        <div><h2>仓库归类明细</h2><p>共 {filteredRows.length.toLocaleString("zh-CN")} 条符合条件{updatedAt ? ` · 更新于 ${new Date(updatedAt).toLocaleString("zh-CN")}${updatedBy ? ` · ${updatedBy}` : ""}` : ""}</p></div>
        <div className="warehouse-mapping-actions">
          <button type="button" className="secondary-button" disabled={!mappingRevision || loading || saving} onClick={exportMappings}>导出映射</button>
          <button type="button" className="secondary-button" disabled={!canEdit || !mappingRevision || loading || saving} onClick={() => fileInputRef.current?.click()}>导入映射</button>
          <input ref={fileInputRef} className="visually-hidden" type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={(event) => { const file = event.target.files?.[0]; if (file) void importMappings(file); }} />
        </div>
        <div className="warehouse-mapping-filters">
          <label><span>仓库类型</span><select aria-label="仓库类型" value={category} onChange={(event) => updateCategory(event.target.value as WarehouseMappingFilters["category"])}>
            <option value="all">全部类型</option>
            {categoryOptions.map((value) => <option key={value} value={value}>{inventoryWarehouseCategoryLabels[value]}</option>)}
          </select></label>
          <label><span>库存口径</span><select aria-label="库存口径" value={inventory} onChange={(event) => updateInventory(event.target.value as WarehouseMappingFilters["inventory"])}>
            <option value="all">全部</option>
            <option value="included">计入库存</option>
            <option value="excluded">不计入库存</option>
          </select></label>
          <label><span>确认状态</span><select aria-label="确认状态" value={confirmation} onChange={(event) => updateConfirmation(event.target.value as WarehouseMappingFilters["confirmation"])}>
            <option value="all">全部状态</option>
            <option value="pending">待确认</option>
            <option value="confirmed">已确认</option>
          </select></label>
          <label className="warehouse-mapping-search"><span>搜索</span><input aria-label="搜索仓库映射" value={query} onChange={(event) => updateQuery(event.target.value)} placeholder="仓库名或类型" /></label>
        </div>
      </div>
      {!canEdit && <p className="warehouse-mapping-readonly">当前账号可查看和导出映射；只有管理员可以编辑或导入。</p>}
      <div className="data-table-wrap">
        <table className="data-table warehouse-mapping-table">
          <thead><tr><th>仓库</th><th>自定义仓库类型</th><th>系统归类</th><th>计入库存</th><th>确认状态</th><th>操作</th></tr></thead>
          <tbody>
            {visibleRows.map((row) => {
              const editing = editingWarehouse === row.warehouse;
              return <tr key={row.warehouse}>
                <td><strong>{row.warehouse}</strong></td>
                <td>{editing
                  ? <select aria-label={`${row.warehouse}仓库类型`} value={draftCategory} onChange={(event) => setDraftCategory(event.target.value as WarehouseMappingEntry["category"])}>{categoryOptions.map((value) => <option key={value} value={value}>{inventoryWarehouseCategoryLabels[value]}</option>)}</select>
                  : <span className="warehouse-category-tag">{row.label}</span>}</td>
                <td><span>{inventoryWarehouseCategoryLabels[editing ? draftCategory : row.category]}</span><small className="cell-note">{editing ? draftCategory : row.category}</small></td>
                <td>{editing
                  ? <label className="warehouse-mapping-checkbox"><input type="checkbox" checked={draftIncluded} disabled={row.warehouse === "刷刷仓"} onChange={(event) => setDraftIncluded(event.target.checked)} /><span>{draftIncluded ? "计入库存" : "不计入库存"}</span></label>
                  : <span className={`status ${row.includeInInventory ? "status-success" : "status-danger"}`}>{row.includeInInventory ? "计入" : "不计入"}</span>}</td>
                <td>{row.pendingConfirmation
                  ? <span className="status status-warning">待确认</span>
                  : <span className="status status-success">已确认</span>}</td>
                <td><div className="warehouse-mapping-row-actions">{editing
                  ? <><button type="button" className="primary-button" disabled={saving} onClick={() => void saveEdit()}>{saving ? "保存中…" : "保存"}</button><button type="button" className="secondary-button" disabled={saving} onClick={() => setEditingWarehouse("")}>取消</button></>
                  : <button type="button" className="row-action" disabled={!canEdit || !mappingRevision || saving} onClick={() => beginEdit(row)}>编辑</button>}</div></td>
              </tr>;
            })}
            {visibleRows.length === 0 && <tr><td colSpan={6}><div className="table-state">没有符合当前条件的仓库映射。</div></td></tr>}
          </tbody>
        </table>
      </div>
      <footer className="warehouse-mapping-pagination">
        <span>第 {currentPage} / {pageCount} 页 · 每页 {PAGE_SIZE} 条</span>
        <div>
          <button type="button" className="secondary-button" disabled={currentPage <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>上一页</button>
          <button type="button" className="secondary-button" disabled={currentPage >= pageCount} onClick={() => setPage((value) => Math.min(pageCount, value + 1))}>下一页</button>
        </div>
      </footer>
    </section>
  </div>;
}
