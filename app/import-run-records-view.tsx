"use client";

import { Fragment, useMemo, useState } from "react";
import { importRunDuration, importRunStatus } from "@/lib/imports/run-presentation";
import { formatCount, formatDateTime, formatFileSize, type UnifiedHistoryItem } from "./module-view-shared";

type Props = {
  history: UnifiedHistoryItem[];
  loading: boolean;
  loaded: boolean;
  errors: string[];
  onRefresh: () => void;
  onShowChains: () => void;
};

export default function ImportRunRecordsView({ history, loading, loaded, errors, onRefresh, onShowChains }: Props) {
  const [source, setSource] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [expanded, setExpanded] = useState<string | null>(null);
  const sources = useMemo(() => [...new Map(history.map((r) => [r.sourceKey, r.sourceLabel])).entries()], [history]);
  const rows = history.filter((r) => (!source || r.sourceKey === source) && (!status || importRunStatus(r.status, r.warningCount).group === status));
  const pages = Math.max(1, Math.ceil(rows.length / pageSize));
  const currentPage = Math.min(page, pages);
  const visible = rows.slice((currentPage - 1) * pageSize, currentPage * pageSize);

  return <div className="import-monitor">
    <div className="import-monitor-note" role="note"><strong>导入批次与完整工作流分别核验</strong><p>下表汇总各业务接口最近返回的批次；筛选和分页仅作用于已读取记录。下载、登录或导入前失败的执行，请从链路规则进入 n8n 查看。</p></div>
    <div className="import-monitor-filters"><label>数据类型<select aria-label="运行记录数据类型" value={source} onChange={(e) => { setSource(e.target.value); setPage(1); setExpanded(null); }}><option value="">全部已读取类型</option>{sources.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      <label>状态<select aria-label="运行记录状态" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); setExpanded(null); }}><option value="">全部状态</option>{[["completed", "已完成"], ["warning", "完成有告警 / 部分完成"], ["failed", "失败"], ["running", "进行中"], ["pending", "待处理"], ["duplicate", "重复跳过"], ["other", "其他 / 未知"]].map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      {(source || status) && <button type="button" className="text-button" onClick={() => { setSource(""); setStatus(""); setPage(1); setExpanded(null); }}>清除筛选</button>}
      <button type="button" className="text-button import-filter-tail" onClick={onShowChains}>链路规则与 n8n 日志 →</button>
    </div>
    <section className="panel table-panel import-history-panel data-refresh-region" aria-busy={loading}>
      <div className="import-monitor-toolbar"><div><h2>运行记录</h2><span className="import-monitor-muted">最近已读取 {history.length} 条导入批次 · 匹配 {rows.length} 条{errors.length > 0 ? " · 来源不完整" : ""}</span></div><button type="button" className="text-button" disabled={loading} onClick={onRefresh}>{loading ? "刷新中…" : "刷新记录 ↻"}</button></div>
      {errors.length > 0 && <div className="import-monitor-error" role="alert"><strong>{loaded ? "部分来源读取失败" : "导入记录读取失败"}</strong><p>{errors.join("；")}</p><button type="button" className="row-action" disabled={loading} onClick={onRefresh}>重新读取</button></div>}
      <div className="data-table-wrap"><table className="data-table import-run-table" data-column-filter-scope="none"><thead><tr><th aria-label="展开详情" /><th>创建时间</th><th>类型</th><th>来源</th><th>链路 / 主体</th><th>状态</th><th>结束时间</th><th>失败原因</th><th>发起人</th></tr></thead><tbody>
        {loading && !loaded && <tr><td colSpan={9}><div className="table-state" role="status"><span className="state-spinner" />正在读取运行记录…</div></td></tr>}
        {!loading && loaded && visible.length === 0 && <tr><td colSpan={9}><div className="table-state">{source || status ? "已读取记录中没有符合筛选的批次。" : "暂无导入批次记录。"}</div></td></tr>}
        {visible.map((row) => {
          const key = `${row.sourceKey}-${row.id}`;
          const state = importRunStatus(row.status, row.warningCount);
          const open = key === expanded;
          const detailId = `import-run-${encodeURIComponent(key)}`;
          const issueMessage = row.errors?.map((i) => i.message).filter(Boolean).join("；");
          const failure = state.group === "failed" ? issueMessage || "接口未提供原因，请核查原始执行记录" : "—";
          return <Fragment key={key}><tr className={open ? "import-run-expanded" : ""}>
            <td><button type="button" className="import-expand-button" aria-label={`${open ? "收起" : "展开"} ${row.fileName}`} aria-expanded={open} aria-controls={detailId} onClick={() => setExpanded(open ? null : key)}>{open ? "−" : "+"}</button></td>
            <td className="import-time">{formatDateTime(row.createdAt)}</td><td>数据导入</td><td>业务接口</td>
            <td className="import-run-subject"><strong>{row.sourceLabel}</strong>{row.shopName && <small>{row.shopName}</small>}<small title={row.fileName}>{row.fileName}</small><small>{row.snapshotDate ? `快照 ${row.snapshotDate} · ` : ""}文件 {formatCount(row.rowCount)} 行{state.group === "completed" || state.group === "warning" ? ` · 新增 ${formatCount(row.insertedCount)}` : ""}</small></td>
            <td><span className={`import-run-badge is-${state.tone}`} title={`原始状态：${row.status}`}>{state.label}</span></td><td className="import-time">{row.completedAt ? formatDateTime(row.completedAt) : "—"}</td><td className={state.group === "failed" ? "import-run-failure" : "import-monitor-muted"}>{failure}</td><td className="import-monitor-muted">未提供</td>
          </tr>{open && <tr id={detailId}><td colSpan={9} className="import-run-detail"><div>
            <h3>批次详情</h3><dl className="import-run-facts"><div><dt>批次 ID</dt><dd>{row.id}</dd></div><div><dt>文件</dt><dd>{row.fileName}</dd></div><div><dt>文件大小</dt><dd>{row.fileSizeBytes === undefined ? "未提供" : formatFileSize(row.fileSizeBytes)}</dd></div><div><dt>工作表</dt><dd>{row.sheetName || "未提供"}</dd></div><div><dt>批次处理耗时</dt><dd>{importRunDuration(row.createdAt, row.completedAt)}</dd></div><div><dt>原始状态</dt><dd>{row.status || "未提供"}</dd></div></dl>
            <div className="import-detail-counts"><span>文件行数 <b>{formatCount(row.rowCount)}</b></span><span>新增 <b>{formatCount(row.insertedCount)}</b></span><span>更新 <b>{row.updatedCount === undefined ? "—" : formatCount(row.updatedCount)}</b></span><span>重复 <b>{row.duplicateCount === undefined ? "—" : formatCount(row.duplicateCount)}</b></span><span>剔除 <b>{row.excludedCount === undefined ? "—" : formatCount(row.excludedCount)}</b></span></div>
            {row.warnings && row.warnings.length > 0 && <div className="import-detail-warnings"><strong>批次告警</strong><ul>{row.warnings.map((w, i) => <li key={i}>{w.message}</li>)}</ul></div>}
            {state.group === "failed" && <p className="import-run-failure">{failure}</p>}
            <p className="import-monitor-muted">本条只证明该导入批次的状态；未返回工作流分步记录、发起人或执行编号。批次耗时不包含下载和登录。</p>
          </div></td></tr>}</Fragment>;
        })}
      </tbody></table></div>
      <footer className="import-monitor-footer"><span>已读取记录中的第 {rows.length ? (currentPage - 1) * pageSize + 1 : 0}–{Math.min(currentPage * pageSize, rows.length)} 条</span><div className="import-pagination"><label>每页<select aria-label="运行记录每页条数" value={pageSize} onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1); setExpanded(null); }}>{[20, 50, 100].map((n) => <option key={n} value={n}>{n} 条</option>)}</select></label><button type="button" className="secondary-button" disabled={currentPage === 1} onClick={() => { setPage(currentPage - 1); setExpanded(null); }}>上一页</button><span>{currentPage} / {pages}</span><button type="button" className="secondary-button" disabled={currentPage === pages} onClick={() => { setPage(currentPage + 1); setExpanded(null); }}>下一页</button></div></footer>
    </section>
  </div>;
}
