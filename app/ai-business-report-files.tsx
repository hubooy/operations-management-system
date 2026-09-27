"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { businessFileJson as api, businessFilePrincipal, businessVolumeManifest, downloadBudgetV10Volume, downloadBusinessFile, downloadBusinessVolume, type BusinessFileRun, type BusinessVolumeManifest, type BusinessVolumeFile } from "@/lib/ai/business-file-download";

const states: Record<string, string> = { queued: "等待生成", building: "生成中", paused: "已暂停", ready: "可下载", cancelled: "已取消" };
const stages: Record<string, string> = { preparing: "整理完整明细", preparing_volume: "准备分卷", rendering: "生成文件", rendering_volume: "生成分卷", saving: "保存文件", verifying: "校验完整性", verifying_volume_file: "校验分卷文件", verifying_complete_delivery: "校验完整多卷交付", ready: "文件已就绪" };

export function budgetV10UiEnabled() {
  return process.env.NEXT_PUBLIC_AI_PROMOTION_BUDGET_V10_DOWNLOAD_ENABLED === "true";
}

export function budgetV10PageAvailable(item: BusinessFileRun, enabled = budgetV10UiEnabled()) {
  return enabled && item.rendererVersion === 10 && item.status === "ready" && item.draft === false;
}

export function BudgetV10Directory({ item, manifest, enabled, disabled, onSave }: {
  item: BusinessFileRun; manifest: BusinessVolumeManifest; enabled: boolean; disabled: boolean;
  onSave: (format: BusinessVolumeFile["format"], volumeIndex: number) => void;
}) {
  if (!budgetV10PageAvailable(item, enabled) || manifest.rendererVersion !== 10 ||
      manifest.bindingDigest !== item.bindingDigest || manifest.attempt !== item.attempt || manifest.draft) return null;
  return <div aria-label="预算候选分卷文件列表"><p className="report-note">内部验收候选；Office 原生打开、公式复算和真实规模仍待核验。</p>
    <p>共 {manifest.volumeCount} 卷；每卷须与完整交付清单一起核对，卷内表片与行区间以该清单为准。</p>
    <button disabled={disabled} onClick={() => onSave("json", 0)}>下载完整交付清单 JSON</button>
    <p className="report-note">交付清单 SHA-256：<code>{manifest.manifestFile.sha256}</code></p>
    <ul>{manifest.files.map(file => <li key={`${file.volumeIndex}:${file.format}`}><button disabled={disabled} onClick={() => onSave(file.format, file.volumeIndex)}>第 {file.volumeIndex} 卷 · {file.format === "html" ? "HTML" : "Excel"}（{(file.bytes/1024/1024).toFixed(2)} MiB）</button><small> SHA-256：<code>{file.sha256}</code></small></li>)}</ul>
  </div>;
}

export function FileDownloadProgress({ percent, onCancel }: { percent: number; onCancel: () => void }) {
  return <p role="status">下载校验 {percent}% <button onClick={onCancel}>取消下载</button></p>;
}

export default function AiBusinessReportFiles({ reportId, allowFormal, volumeMode = false, formalOnly = false, promotionTrial = false }: { reportId: string; allowFormal: boolean; volumeMode?: boolean; formalOnly?: boolean; promotionTrial?: boolean }) {
  const [items, setItems] = useState<BusinessFileRun[]>([]);
  const [directories, setDirectories] = useState<Record<string, { version: number; binding: string; manifest: BusinessVolumeManifest; progressSignature?: string }>>({});
  const [principalKey, setPrincipalKey] = useState("");
  const [readError, setReadError] = useState("");
  const [writeError, setWriteError] = useState("");
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [notice, setNotice] = useState("");
  const live = useRef(true);
  const actor = useRef("");
  const read = useRef<AbortController | null>(null), write = useRef<AbortController | null>(null), download = useRef<AbortController | null>(null);
  const base = "/api/ai/reports/"+reportId+"/files";
  const budgetV10Enabled = budgetV10UiEnabled();
  const principal = useCallback((key: string) => {
    const changed = Boolean(actor.current && actor.current !== key);
    if (changed) {
      write.current?.abort(); write.current = null; download.current?.abort(); download.current = null;
      setItems([]); setDirectories({}); setBusy(false); setProgress(null); setNotice("");
      setWriteError("账号已变化，已停止操作并清空原账号文件，请重新查看报告。");
    }
    actor.current = key; setPrincipalKey(key);
    return !changed;
  }, []);
  const load = useCallback(async (force = false) => {
    if (read.current && !force) return;
    read.current?.abort();
    const controller = new AbortController();
    read.current = controller;
    const current = () => live.current && read.current === controller && !controller.signal.aborted;
    try {
      const key = await businessFilePrincipal({ signal: controller.signal });
      if (!current()) return;
      principal(key);
      const data = await api<{ items: BusinessFileRun[] }>(base, {}, { signal: controller.signal });
      if (!Array.isArray(data.items) || data.items.some(item => item.reportId !== reportId)) throw new Error("文件状态响应无效");
      if (current() && actor.current === key) {
        setItems(data.items); setReadError("");
        setDirectories(previous => Object.fromEntries(Object.entries(previous).filter(([id, value]) => data.items.some(item => item.id === id && item.version === value.version && item.bindingDigest === value.binding && (item.rendererVersion !== 10 || value.progressSignature === JSON.stringify(item.progress))))));
      }
    } catch (error) { if (current()) { setReadError(error instanceof Error ? error.message : "文件状态读取失败"); setItems([]); setDirectories({}); } }
    finally { if (read.current === controller) read.current = null; }
  }, [base, principal, reportId]);
  useEffect(() => {
    live.current = true;
    actor.current = ""; setPrincipalKey(""); setItems([]); setDirectories({}); setBusy(false); setProgress(null); setReadError(""); setWriteError(""); setNotice("");
    void load(true);
    const focus = () => void load(true);
    window.addEventListener("focus", focus);
    return () => { live.current = false; read.current?.abort(); read.current = null; write.current?.abort(); write.current = null; download.current?.abort(); download.current = null; window.removeEventListener("focus", focus); };
  }, [load]);
  const running = items.some(item => ["queued", "building"].includes(item.status));
  useEffect(() => {
    const timer = window.setInterval(() => void load(), running ? 5000 : 15000);
    return () => window.clearInterval(timer);
  }, [load, running]);
  async function mutate(url: string, body: unknown) {
    if (write.current || download.current || !actor.current) return;
    const key = actor.current, controller = new AbortController(); write.current = controller;
    const current = () => live.current && write.current === controller && actor.current === key && !controller.signal.aborted;
    setBusy(true); setWriteError(""); setNotice("");
    try {
      const identity = await businessFilePrincipal({ signal: controller.signal });
      if (!current() || !principal(identity)) return;
      await api(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) }, { signal: controller.signal });
      if (current()) { setNotice("任务已保存，后台会继续处理。"); await load(true); }
    } catch (error) { if (current()) setWriteError((error instanceof Error ? error.message : "文件操作失败")+"；如提交结果未知，请刷新列表确认后操作。"); }
    finally { if (write.current === controller) { write.current = null; if (live.current && actor.current === key) setBusy(false); } }
  }
  async function directory(item: BusinessFileRun) {
    if (write.current || download.current || !actor.current) return;
    if (item.rendererVersion === 10 && !budgetV10PageAvailable(item, budgetV10Enabled)) return;
    const key = actor.current, controller = new AbortController(); write.current = controller;
    const current = () => live.current && write.current === controller && actor.current === key && !controller.signal.aborted;
    setBusy(true); setWriteError("");
    try {
      const identity = await businessFilePrincipal({ signal: controller.signal });
      if (!current() || !principal(identity)) return;
      const result = await api<{ item: BusinessFileRun }>(`/api/ai/business-files/${item.id}`, {}, { signal: controller.signal });
      const manifest = businessVolumeManifest(result.item, item.id);
      if (result.item.reportId !== reportId || result.item.version !== item.version || result.item.bindingDigest !== item.bindingDigest || (item.rendererVersion === 10 && (!budgetV10PageAvailable(result.item, budgetV10Enabled) || JSON.stringify(result.item.progress) !== JSON.stringify(item.progress)))) throw new Error("文件任务已变化，请刷新列表。");
      if (current()) setDirectories(previous => ({ ...previous, [item.id]: { version: item.version, binding: item.bindingDigest, manifest, ...(item.rendererVersion === 10 ? { progressSignature: JSON.stringify(item.progress) } : {}) } }));
    } catch (error) { if (current()) setWriteError(error instanceof Error ? error.message : "分卷列表读取失败"); }
    finally { if (write.current === controller) { write.current = null; if (live.current && actor.current === key) setBusy(false); } }
  }
  async function save(item: BusinessFileRun, format: BusinessVolumeFile["format"], volumeIndex?: number) {
    if (write.current || download.current || !actor.current) return;
    const key = actor.current, controller = new AbortController(); download.current = controller;
    const current = () => live.current && download.current === controller && actor.current === key && !controller.signal.aborted;
    setProgress(0); setWriteError(""); setNotice("");
    try {
      const identity = await businessFilePrincipal({ signal: controller.signal });
      if (!current() || !principal(identity)) return;
      const options = { signal: controller.signal, onProgress: (received: number, total: number) => { if (current()) setProgress(Math.floor(received/total*100)); } };
      const result = item.rendererVersion === 10 && budgetV10PageAvailable(item, budgetV10Enabled) && volumeIndex !== undefined
        ? await downloadBudgetV10Volume(item.id, volumeIndex, format, { ...options, expectedPrincipalKey: key })
        : (item.rendererVersion === 4 || item.rendererVersion === 6 || item.rendererVersion === 7 || item.rendererVersion === 9)
        ? await downloadBusinessVolume(item.id, volumeIndex!, format, { ...options, expectedPrincipalKey: key })
        : item.rendererVersion !== 10 && format !== "json" ? await downloadBusinessFile(item.id, format, options) : null;
      if (!result || !current()) return;
      const finalIdentity = await businessFilePrincipal({ signal: controller.signal });
      if (!current() || !principal(finalIdentity)) return;
      const url = URL.createObjectURL(result.blob), anchor = document.createElement("a");
      anchor.href = url; anchor.download = result.fileName; anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setNotice("所选文件已校验，下载已开始。");
    } catch (error) { if (live.current && download.current === controller && actor.current === key) setWriteError(controller.signal.aborted ? "下载已取消，可再次下载已保存的文件。" : error instanceof Error ? error.message : "下载失败"); }
    finally { if (download.current === controller) { download.current = null; if (live.current && actor.current === key) setProgress(null); } }
  }
  const disabled = busy || progress !== null || !principalKey;
  const createBody = (draft: boolean) => volumeMode || formalOnly ? { deliveryMode: "volumes", draft, expectedPrincipalKey: principalKey } : { draft };
  const trialBody = { deliveryMode: "promotionTrialVolumes", draft: false, expectedPrincipalKey: principalKey };
  return <section className="report-review" aria-label="完整经营报告文件"><h4>完整报告文件</h4><p className="report-note">HTML 与 Excel 包含同一份封存证据、诊断及明细。生成无需保持网页打开，下载前会校验完整文件。</p>
    {readError && <p role="alert">{readError}</p>}{writeError && <p role="alert">{writeError}</p>}{notice && <p role="status">{notice}</p>}
    {(volumeMode || promotionTrial) && <p className="report-note">多卷报告须保留完整交付清单及各卷；单卷只含其中一部分表。请选择单个文件下载。</p>}
    {promotionTrial && <p className="report-note">推广专项试用版包含行动建议、来源范围与缺口清单；预算尚未接入生成链路，文件会明确标记为未交付。</p>}
    <div className="report-actions">{!formalOnly && <button disabled={disabled} onClick={() => void mutate(base, createBody(true))}>{volumeMode ? "生成多卷草稿" : "生成双文件草稿"}</button>}{allowFormal && <button className="primary-button" disabled={disabled} onClick={() => void mutate(base, createBody(false))}>{volumeMode ? "生成已复核多卷文件" : "生成已复核双文件"}</button>}{promotionTrial && allowFormal && <button disabled={disabled} onClick={() => void mutate(base, trialBody)}>生成推广专项试用版</button>}<button disabled={busy} onClick={() => void load()}>刷新文件状态</button></div>
    {progress !== null && <FileDownloadProgress percent={progress} onCancel={() => download.current?.abort()} />}
    {items.map(item => <div className="report-review" key={item.id}><p><strong>{item.draft ? "草稿" : "已复核报告"}</strong> · {item.rendererVersion === 10 && item.status === "ready" ? "内部候选已就绪" : states[item.status] ?? item.status} · {stages[item.progress.stage ?? ""] ?? ""}{item.progress.table ? ` · 第 ${item.progress.table} 张表` : ""}{item.progress.rows ? ` · ${item.progress.rows} 行` : ""}</p>
      {item.rendererVersion === 10 && <p className="report-note">{budgetV10Enabled ? "预算多卷内部验收候选；页面创建与控制仍未开放，Office 原生与真实规模尚未验收。" : "预算多卷内部候选，页面创建、控制与下载尚未开放。"}</p>}
      {item.errorCode && <p className="report-note">生成已暂停，错误标识：{item.errorCode}。恢复会优先核验已完整保存的文件；重新构建会保留旧记录。</p>}
      <div className="report-actions">{item.status === "ready" && (item.rendererVersion === 10 ? budgetV10PageAvailable(item, budgetV10Enabled) ? <button disabled={disabled} onClick={() => void directory(item)}>查看预算候选分卷文件</button> : <span className="report-note">预算多卷文件的页面下载入口尚未开放。</span> : (item.rendererVersion === 4 || item.rendererVersion === 6 || item.rendererVersion === 7 || item.rendererVersion === 9) ? <button disabled={disabled} onClick={() => void directory(item)}>查看分卷文件</button> : <><button className="primary-button" disabled={disabled} onClick={() => void save(item, "html")}>下载 HTML</button><button disabled={disabled} onClick={() => void save(item, "xlsx")}>下载 Excel</button></>)}
        {item.rendererVersion !== 10 && ["queued", "building"].includes(item.status) && <button disabled={disabled} onClick={() => void mutate(`/api/ai/business-files/${item.id}/control`, { action: "pause", expectedVersion: item.version })}>暂停生成</button>}
        {item.rendererVersion !== 10 && item.status === "paused" && <><button disabled={disabled} onClick={() => void mutate(`/api/ai/business-files/${item.id}/control`, { action: "resume", expectedVersion: item.version })}>恢复生成</button><button disabled={disabled} onClick={() => void mutate(`/api/ai/business-files/${item.id}/control`, { action: "rebuild", expectedVersion: item.version })}>重新构建</button></>}
        {item.rendererVersion !== 10 && !["ready", "cancelled"].includes(item.status) && <button disabled={disabled} onClick={() => void mutate(`/api/ai/business-files/${item.id}/control`, { action: "cancel", expectedVersion: item.version })}>取消生成任务</button>}</div>
      {directories[item.id] && (item.rendererVersion === 10 ? <BudgetV10Directory item={item} manifest={directories[item.id].manifest} enabled={budgetV10Enabled} disabled={disabled} onSave={(format, index) => void save(item, format, index)} /> : <div aria-label="分卷文件列表"><p>共 {directories[item.id].manifest.volumeCount} 卷；每卷须与完整交付清单一起核对，卷内表片与行区间以该清单为准。</p><button disabled={disabled} onClick={() => void save(item, "json", 0)}>下载完整交付清单 JSON</button><p className="report-note">交付清单 SHA-256：<code>{directories[item.id].manifest.manifestFile.sha256}</code></p><ul>{directories[item.id].manifest.files.map(file => <li key={`${file.volumeIndex}:${file.format}`}><button disabled={disabled} onClick={() => void save(item, file.format, file.volumeIndex)}>第 {file.volumeIndex} 卷 · {file.format === "html" ? "HTML" : "Excel"}（{(file.bytes/1024/1024).toFixed(2)} MiB）</button><small> SHA-256：<code>{file.sha256}</code></small></li>)}</ul></div>)}
    </div>)}
  </section>;
}
