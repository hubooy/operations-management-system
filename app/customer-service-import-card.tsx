"use client";

import { useRef, useState } from "react";

import { SearchableSelect } from "./ui/searchable-select";

const formatCount = (value = 0) => new Intl.NumberFormat("zh-CN").format(value);
const formatFileSize = (bytes = 0) => {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
};

export default function CustomerServiceImportCard({ canImport, onCompleted }: { canImport: boolean; onCompleted: () => Promise<void> }) {
  const sessionFileRef = useRef<HTMLInputElement>(null);
  const chatFileRef = useRef<HTMLInputElement>(null);
  const [sessionFile, setSessionFile] = useState<File | null>(null);
  const [chatFile, setChatFile] = useState<File | null>(null);
  const [shopName, setShopName] = useState("志高商用设备");
  const [uploading, setUploading] = useState(false);
  const [feedback, setFeedback] = useState("");
  const acceptDroppedFiles = (files: FileList) => {
    if (!canImport) return;
    const candidates = Array.from(files);
    const nextSession = candidates.find((file) => /\.xlsx$/i.test(file.name));
    const nextChat = candidates.find((file) => /\.(log|txt)$/i.test(file.name));
    if (nextSession) setSessionFile(nextSession);
    if (nextChat) setChatFile(nextChat);
    setFeedback(nextSession && nextChat ? "已识别 Excel 会话记录和 LOG 聊天记录，请确认店铺后开始导入。" : "请同时拖入一份 .xlsx 会话记录和一份 .log/.txt 聊天记录。");
  };
  const uploadFile = async (file: File, kind: "session" | "chat") => {
    const chunkSize = 1024 * 1024;
    const chunkCount = Math.ceil(file.size / chunkSize);
    const init = await fetch("/api/customer-service/import/chunks", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ action: "init", kind, fileName: file.name, fileSizeBytes: file.size, chunkCount, fingerprint: `${kind}:${file.name}:${file.size}:${file.lastModified}` }) });
    const initPayload = await init.json().catch(() => null) as { ok?: boolean; message?: string; upload?: { id: string; receivedChunkIndexes?: number[] } } | null;
    if (!init.ok || !initPayload?.ok || !initPayload.upload) throw new Error(initPayload?.message || "无法创建分片上传任务");
    const uploaded = new Set(initPayload.upload.receivedChunkIndexes ?? []);
    for (let index = 0; index < chunkCount; index += 1) {
      if (uploaded.has(index)) continue;
      const part = file.slice(index * chunkSize, Math.min((index + 1) * chunkSize, file.size));
      const response = await fetch("/api/customer-service/import/chunks", { method: "PUT", headers: { "x-upload-id": initPayload.upload.id, "x-chunk-index": String(index), "content-type": "application/octet-stream" }, body: part });
      const payload = await response.json().catch(() => null) as { ok?: boolean; message?: string } | null;
      if (!response.ok || !payload?.ok) throw new Error(payload?.message || `第 ${index + 1} 个分片上传失败`);
    }
    return initPayload.upload.id;
  };
  const submit = async () => {
    if (!sessionFile || !chatFile || uploading || !canImport) return;
    setUploading(true); setFeedback("");
    try {
      const [sessionUploadId, chatUploadId] = await Promise.all([uploadFile(sessionFile, "session"), uploadFile(chatFile, "chat")]);
      const response = await fetch("/api/customer-service/import/chunks", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ action: "complete", shopName, sessionUploadId, chatUploadId, sessionFileName: sessionFile.name, chatFileName: chatFile.name }) });
      const payload = await response.json().catch(() => null) as { ok?: boolean; status?: string; message?: string; summary?: { matchedCount: number; timeOnlyMatchedCount: number; sessionOnlyCount: number; chatOnlyCount: number } } | null;
      if (!response.ok || !payload?.ok) throw new Error(payload?.message || "客服会话导入失败");
      const summary = payload.summary;
      setFeedback(`${payload.message || "导入完成"}${summary ? ` 已关联 ${formatCount(summary.matchedCount + summary.timeOnlyMatchedCount)} 条，待核对 ${formatCount(summary.sessionOnlyCount + summary.chatOnlyCount)} 条。` : ""}`);
      setSessionFile(null); setChatFile(null); await onCompleted();
    } catch (error) { setFeedback(error instanceof Error ? error.message : "客服会话导入失败"); }
    finally { setUploading(false); }
  };
  return <section className="customer-service-import-in-data" onDragOver={(event) => { if (canImport) event.preventDefault(); }} onDrop={(event) => { event.preventDefault(); acceptDroppedFiles(event.dataTransfer.files); }}><div className="customer-service-import-copy"><span className="eyebrow">双文件关联导入</span><h3>客服会话与聊天记录</h3><p>可同时拖入一份 Excel 和一份 LOG；系统按咨询时间、顾客脱敏标识和会话顺序关联，补充日志会替换同一聊天的旧记录。</p></div><label className="customer-service-import-shop"><span>所属店铺</span><SearchableSelect value={shopName} onChange={setShopName} ariaLabel="客服导入店铺" searchPlaceholder="搜索客服店铺" options={[{ value: "志高商用设备", label: "志高商用设备" }, { value: "志高厨电", label: "志高厨电" }]} /></label><input className="file-input-hidden" ref={sessionFileRef} type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={(event) => { const file = event.currentTarget.files?.[0]; if (file && /\.xlsx$/i.test(file.name)) setSessionFile(file); else if (file) setFeedback("会话记录请上传 .xlsx 文件。"); event.currentTarget.value = ""; }} /><input className="file-input-hidden" ref={chatFileRef} type="file" accept=".log,.txt,text/plain" onChange={(event) => { const file = event.currentTarget.files?.[0]; if (file && /\.(log|txt)$/i.test(file.name)) setChatFile(file); else if (file) setFeedback("聊天记录请上传 .log 或 .txt 文件。"); event.currentTarget.value = ""; }} /><div className="customer-service-import-files"><button type="button" className={`customer-file-field ${sessionFile ? "selected" : ""}`} onClick={() => sessionFileRef.current?.click()} disabled={!canImport}><span>①</span><strong>{sessionFile?.name || "选择会话记录 Excel"}</strong><small>{sessionFile ? formatFileSize(sessionFile.size) : "咨询时间、顾客、客服、商品等字段"}</small></button><button type="button" className={`customer-file-field ${chatFile ? "selected" : ""}`} onClick={() => chatFileRef.current?.click()} disabled={!canImport}><span>②</span><strong>{chatFile?.name || "选择聊天记录 LOG"}</strong><small>{chatFile ? formatFileSize(chatFile.size) : "以“以下为一通会话”为分隔符"}</small></button></div><div className="customer-service-import-actions"><small>支持整组拖入；单个文件最大 25MB，仅管理员可导入。</small><button type="button" className="primary-button" disabled={!sessionFile || !chatFile || uploading || !canImport} onClick={() => void submit()}>{uploading ? "导入匹配中…" : canImport ? "开始导入并匹配" : "仅管理员可导入"}</button></div>{feedback && <p className={`customer-service-feedback ${feedback.includes("失败") || feedback.includes("请同时") || feedback.includes("请上传") ? "error" : ""}`}>{feedback}</p>}</section>;
}
