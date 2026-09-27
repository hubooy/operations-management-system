"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { netshopOutletFilterKey } from "./module-view-shared";
import { appendPromotionRelations, linksForPromotionTarget, type RelationLink } from "@/lib/jd/promotion-diagnostic-relations";
import { promotionSystemSourceReady } from "@/lib/jd/promotion-diagnostic-identity";
import { attachPromotionInterpretation, preparePromotionInterpretation, type PromotionInterpretationReply } from "@/lib/jd/promotion-diagnostic-interpret";
import {
  buildPromotionDiagnosticReport,
  promotionDiagnosticHtml,
  promotionDiagnosticXlsx,
  type DiagnosticPeriod,
  type PromotionDiagnosticReport,
  type ReportColumn,
} from "@/lib/jd/promotion-diagnostic-report";

function previousPeriod(startDate: string, endDate: string) {
  const start = Date.parse(`${startDate}T00:00:00Z`);
  const end = Date.parse(`${endDate}T00:00:00Z`);
  const days = Math.round((end - start) / 86_400_000) + 1;
  if (!Number.isInteger(days) || days < 1 || days > 7) throw new Error("首版推广诊断请选择1—7个完整自然日");
  const date = (time: number) => new Date(time).toISOString().slice(0, 10);
  return { startDate: date(start - days * 86_400_000), endDate: date(start - 86_400_000) };
}

function formatCell(value: string | number | null, kind: ReportColumn["kind"]) {
  if (value === null) return "—";
  if (kind === "money") return `¥${Number(value).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  if (kind === "percent") return `${Number(value).toFixed(2)}%`;
  if (kind === "ratio") return Number(value).toFixed(2);
  if (kind === "number") return Number(value).toLocaleString("zh-CN");
  return String(value);
}

function download(name: string, bytes: BlobPart, type: string) {
  const url = URL.createObjectURL(new Blob([bytes], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
}

export default function PromotionDiagnosticPanel({ shopName, startDate, endDate }: {
  shopName: string;
  startDate: string;
  endDate: string;
}) {
  const [report, setReport] = useState<PromotionDiagnosticReport | null>(null);
  const [source, setSource] = useState<DiagnosticPeriod | null>(null);
  const [baselineSource, setBaselineSource] = useState<DiagnosticPeriod | null>(null);
  const [loading, setLoading] = useState(false);
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState("");
  const [error, setError] = useState("");
  const [selected, setSelected] = useState("summary");
  const [search, setSearch] = useState("");
  const [sortColumn, setSortColumn] = useState("");
  const [descending, setDescending] = useState(true);
  const [page, setPage] = useState(0);
  const [focus, setFocus] = useState<{ tableKey: string; groupKey: string } | null>(null);
  const [relationFilter, setRelationFilter] = useState<RelationLink | null>(null);
  const requestRef = useRef<AbortController | null>(null);
  const aiRequestRef = useRef<AbortController | null>(null);

  useEffect(() => () => { requestRef.current?.abort(); requestRef.current = null; aiRequestRef.current?.abort(); aiRequestRef.current = null; }, []);
  const table = report?.tables.find((item) => item.key === selected) ?? report?.tables[0];
  const rows = useMemo(() => {
    if (!table) return [];
    const query = search.trim().toLocaleLowerCase();
    const groupKeyColumn = table.columns.findIndex((column) => column.key === "groupKey");
    const relationColumn = table.columns.findIndex((column) => column.key === relationFilter?.columnKey);
    const filtered = table.rows.filter((row) => {
      if (focus?.tableKey === table.key && groupKeyColumn >= 0 && row[groupKeyColumn] !== focus.groupKey) return false;
      if (relationFilter?.tableKey === table.key && relationColumn >= 0 && row[relationColumn] !== relationFilter.value) return false;
      return !query || row.some((value) => value !== null && String(value).toLocaleLowerCase().includes(query));
    });
    if (sortColumn !== "") {
      const column = Number(sortColumn);
      filtered.sort((a, b) => {
        const x = a[column], y = b[column];
        if (x === null || y === null) return x === y ? 0 : x === null ? 1 : -1;
        const result = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), "zh-CN");
        return descending ? -result : result;
      });
    }
    return filtered;
  }, [table, search, sortColumn, descending, focus, relationFilter]);
  const pages = Math.max(1, Math.ceil(rows.length / 50));
  const visible = rows.slice(Math.min(page, pages - 1) * 50, (Math.min(page, pages - 1) + 1) * 50);
  const links = focus && source ? linksForPromotionTarget(source, focus.tableKey, focus.groupKey) : [];
  const systemSourceReady = Boolean(source && baselineSource && report?.comparisonAvailable
    && promotionSystemSourceReady(source, baselineSource));
  const aiBlockReason = !baselineSource ? "前等长周期未读取，AI解读暂不可用。"
    : !report?.comparisonAvailable ? "前等长周期覆盖或来源修订不可比，AI解读暂不可用。"
    : "系统导入批次归属不完整，暂不发送给模型。";

  function openTable(tableKey: string) {
    setSelected(tableKey); setFocus(null); setRelationFilter(null); setSearch(""); setSortColumn(""); setPage(0);
  }
  function openTarget(target: { tableKey: string; groupKey: string } | undefined, fallbackTable: string) {
    openTable(target?.tableKey ?? fallbackTable);
    if (target) setFocus(target);
  }
  function openRelation(link: RelationLink) {
    openTable(link.tableKey);
    setRelationFilter(link);
  }

  async function load() {
    requestRef.current?.abort();
    aiRequestRef.current?.abort();
    aiRequestRef.current = null;
    const controller = new AbortController();
    requestRef.current = controller;
    setLoading(true); setAiLoading(false); setError(""); setAiError(""); setReport(null); setSource(null); setBaselineSource(null);
    try {
      const previous = previousPeriod(startDate, endDate);
      async function read(from: string, to: string) {
        const query = new URLSearchParams({ platform: "京东", outlet: netshopOutletFilterKey("京东", shopName), startDate: from, endDate: to });
        const response = await fetch(`/api/netshop/promotion-diagnostic?${query.toString()}`, { cache: "no-store", signal: controller.signal });
        const payload = await response.json().catch(() => null) as (DiagnosticPeriod & { error?: string }) | null;
        if (!response.ok || payload?.schemaVersion !== "jd-promotion-diagnostic-v1") {
          throw new Error(payload?.error || `推广诊断数据读取失败（${response.status}）`);
        }
        return payload;
      }
      const current = await read(startDate, endDate);
      let baseline: DiagnosticPeriod | null = null;
      let baselineIssue = "";
      try { baseline = await read(previous.startDate, previous.endDate); }
      catch (caught) {
        if (controller.signal.aborted) throw caught;
        baselineIssue = caught instanceof Error ? caught.message : "前等长周期不可用";
      }
      const built = appendPromotionRelations(buildPromotionDiagnosticReport(current, baseline), current);
      if (baselineIssue) built.limitations.push(`前等长周期读取失败：${baselineIssue}；环比留空。`);
      if (requestRef.current !== controller || controller.signal.aborted) return;
      setReport(built);
      setSource(current);
      setBaselineSource(baseline);
      openTable("summary");
    } catch (caught) {
      if (requestRef.current === controller && !controller.signal.aborted) {
        setError(caught instanceof Error ? caught.message : "无法生成推广诊断草稿");
      }
    } finally {
      if (requestRef.current === controller) {
        requestRef.current = null;
        setLoading(false);
      }
    }
  }

  async function interpretFocused() {
    if (!report || !focus || !systemSourceReady || aiLoading || report.tables.some((item) => item.key === "modelInterpretation")) return;
    const controller = new AbortController();
    aiRequestRef.current = controller;
    setAiLoading(true); setAiError("");
    try {
      const prepared = preparePromotionInterpretation(report, focus);
      const response = await fetch("/api/netshop/promotion-diagnostic/interpret", {
        method: "POST", cache: "no-store", headers: { "content-type": "application/json" },
        body: JSON.stringify({ shopName, startDate, endDate, sourceRevision: report.sourceRevision, target: focus }),
        signal: controller.signal,
      });
      const payload = await response.json().catch(() => null) as { error?: string; interpretation?: PromotionInterpretationReply; sourceRevision?: string } | null;
      if (!response.ok || !payload?.interpretation || payload.sourceRevision !== report.sourceRevision) {
        throw new Error(payload?.error || `AI 解读失败（${response.status}）`);
      }
      if (aiRequestRef.current === controller && !controller.signal.aborted) {
        setReport((active) => active && active.sourceRevision === report.sourceRevision
          && active.period.startDate === report.period.startDate && active.period.endDate === report.period.endDate
          ? attachPromotionInterpretation(active, prepared, payload.interpretation!) : active);
        openTable("modelInterpretation");
      }
    } catch (caught) {
      if (aiRequestRef.current === controller && !controller.signal.aborted) {
        setAiError(caught instanceof Error ? caught.message : "AI 解读未通过来源与引用校验");
      }
    } finally {
      if (aiRequestRef.current === controller) { aiRequestRef.current = null; setAiLoading(false); }
    }
  }

  const fileBase = `${shopName.replace(/[<>:"/\\|?*]/g, "_")}_${startDate}_${endDate}_推广诊断`;
  return <section className="panel" aria-label="京东推广深度诊断首版">
    <div className="table-toolbar">
      <div><h2>推广深度诊断 · {report?.tables.some((item) => item.key === "modelInterpretation") ? "规则与单模型解释" : "规则草稿"}</h2><p>一店一周期；数值由京准通已导入事实汇总。AI 只解释已核对的对象证据，所有建议待运营复核，不自动调整投放。</p></div>
      <button type="button" className="primary-button" disabled={loading || aiLoading} onClick={() => void load()}>{loading ? "正在核对来源…" : "生成当前周期诊断"}</button>
    </div>
    {error && <div className="inventory-feedback inventory-feedback-error" role="alert"><span>!</span><div><strong>诊断未生成</strong><p>{error}</p></div></div>}
    {aiError && <div className="inventory-feedback inventory-feedback-error" role="alert"><span>!</span><div><strong>AI 解读未生成</strong><p>{aiError}</p></div></div>}
    {report && <>
      <p className={report.complete ? "muted" : "inventory-feedback inventory-feedback-error"} role={report.complete ? undefined : "alert"}>
        {report.complete ? `来源已覆盖 ${report.coverage.requestedDates.length} 天，${report.coverage.rowCount.toLocaleString()} 行；修订 ${report.sourceRevision}`
          : `来源尚缺 ${report.coverage.missingDates.join("、")}，不能作为完整周期报告。`}
      </p>
      <p className="muted">本期 {report.period.startDate} 至 {report.period.endDate}；{report.comparisonAvailable && report.previousPeriod
        ? `前等长周期 ${report.previousPeriod.startDate} 至 ${report.previousPeriod.endDate}`
        : "前等长周期不可比，环比留空"}。平台归因订单金额不是 ERP 净销售或利润。</p>
      <div className="table-toolbar"><div><strong>可查看的表与建议</strong><p>各分组是同一来源的不同视角，不跨表累加。</p></div>
        <div>
          <button className="secondary-button" disabled={!report.complete} onClick={() => download(`${fileBase}.html`, promotionDiagnosticHtml(report), "text/html;charset=utf-8")}>导出 HTML</button>{" "}
          <button className="secondary-button" disabled={!report.complete} onClick={() => download(`${fileBase}.xlsx`, Uint8Array.from(promotionDiagnosticXlsx(report)), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}>导出 XLSX</button>
        </div>
      </div>
      <div className="store-metrics-grid">{[
        ["推广花费", report.metrics.spendCents === null ? "—" : formatCell(report.metrics.spendCents / 100, "money")],
        ["展现", formatCell(report.metrics.impressions, "number")],
        ["点击", formatCell(report.metrics.clicks, "number")],
        ["归因订单行", formatCell(report.metrics.reportedOrderLines, "number")],
        ["归因总订单金额", report.metrics.reportedGmvCents === null ? "—" : formatCell(report.metrics.reportedGmvCents / 100, "money")],
      ].map(([label, value]) => <div className="panel" key={label}><small>{label}</small><strong>{value}</strong></div>)}</div>
      <div className="grid">{report.findings.map((finding) => <article className="panel" key={finding.title}><h3>{finding.title}</h3><p>{finding.text}</p><button className="row-action" onClick={() => openTarget(finding.target, finding.tableKey)}>查看证据</button></article>)}</div>
      <div className="subnav" role="tablist" aria-label="推广诊断表">{report.tables.map((item) => <button key={item.key} type="button" role="tab" aria-selected={table?.key === item.key} className={table?.key === item.key ? "active" : ""} onClick={() => openTable(item.key)}>{item.title}</button>)}</div>
      {table && <><p className="muted">{table.note}</p>
        {(focus || relationFilter) && <div className="table-toolbar"><strong>{focus ? `已定位来源键 ${focus.groupKey}` : `已筛选 ${relationFilter?.label}`}</strong><button className="row-action" onClick={() => openTable(table.key)}>查看当前表全部</button></div>}
        {links.length > 0 && <div className="table-toolbar"><span>沿同一来源行查看：</span>{links.map((link) => <button key={`${link.tableKey}:${link.columnKey}`} type="button" className="row-action" onClick={() => openRelation(link)}>{link.label}</button>)}</div>}
        {focus && ["plans", "products", "keywords", "searchTerms", "keywordSku"].includes(focus.tableKey) && <div className="table-toolbar"><button type="button" className="secondary-button" disabled={!systemSourceReady || aiLoading || report.tables.some((item) => item.key === "modelInterpretation")} onClick={() => void interpretFocused()}>{aiLoading ? "正在生成 AI 解读…" : "用现有模型解释此对象"}</button><span className="muted">{systemSourceReady ? "手动单次调用，沿用模型最大输出 65,536 Token；按服务商实际计费。" : aiBlockReason}</span></div>}
        <div className="table-toolbar"><label>搜索当前表 <input value={search} onChange={(event) => { setSearch(event.target.value); setPage(0); }} /></label><label>排序列 <select value={sortColumn} onChange={(event) => { setSortColumn(event.target.value); setPage(0); }}><option value="">原始顺序</option>{table.columns.map((column, index) => <option key={column.key} value={index}>{column.label}</option>)}</select></label><button className="row-action" onClick={() => setDescending((value) => !value)}>{descending ? "降序" : "升序"}</button></div>
        <div className="data-table-wrap"><table className="data-table"><thead><tr>{table.columns.map((column) => <th key={column.key}>{column.label}</th>)}{table.key === "actions" && <th>证据</th>}</tr></thead><tbody>{visible.map((row, index) => {
          const action = table.key === "actions" ? report.actions[table.rows.indexOf(row)] : undefined;
          return <tr key={`${table.key}-${page}-${index}`}>{row.map((cell, column) => <td key={table.columns[column]!.key}>{formatCell(cell, table.columns[column]!.kind)}</td>)}{action && <td><button type="button" className="row-action" onClick={() => openTarget(action.target, action.tableKey)}>定位</button></td>}</tr>;
        })}</tbody></table></div>
        <footer className="promotion-pagination"><span>{rows.length.toLocaleString()} / {table.rows.length.toLocaleString()} 行 · 第 {Math.min(page, pages - 1) + 1} / {pages} 页</span><button className="row-action" disabled={page <= 0} onClick={() => setPage((value) => value - 1)}>上一页</button><button className="row-action" disabled={page + 1 >= pages} onClick={() => setPage((value) => value + 1)}>下一页</button></footer>
      </>}
      <details><summary>数据口径与限制</summary><ul>{report.limitations.map((item, index) => <li key={index}>{item}</li>)}</ul></details>
    </>}
  </section>;
}
