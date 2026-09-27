import { fetchBoundedJson } from "./bounded-fetch";

export type BusinessFileRun = {
  id: string; reportId: string; draft: boolean; status: string; version: number; attempt: number;
  bindingDigest: string; storedBytes: number; errorCode: string; rendererVersion?: number;
  progress: { stage?: string; sourcePage?: number; table?: number; rows?: number; chunks?: number; bytes?: number; volumeIndex?: number; volumeCount?: number; publicationFenceDigest?: string; manifestFileSha256?: string; attempt?: number; bindingDigest?: string; attestationId?: string; attestationSha256?: string; fullManifestDigest?: string; owningVerificationDigest?: string; publishRequestDigest?: string };
  manifest: { schemaVersion: "business-file-delivery-v1"; attempt: number; bindingDigest: string; draft: boolean; files: Record<"html" | "xlsx", BusinessFile> } | BusinessVolumeManifest | null;
};
export type BusinessVolumeFile = { volumeIndex: number; format: "html" | "xlsx" | "json"; bytes: number; sha256: string; chunkCount: number };
export type BusinessVolumeManifest = { schemaVersion: "business-file-delivery-v2"; rendererVersion: 4 | 6 | 7 | 9 | 10; bindingDigest: string; attempt: number; draft: boolean; volumeCount: number; files: BusinessVolumeFile[]; manifestFile: BusinessVolumeFile };
type BusinessFile = { bytes: number; chunkCount: number; chunkBytes: number; sha256: string; fileName: string; mimeType: string };
const digestPattern = /^[a-f0-9]{64}$/;
const idPattern = /^[A-Za-z0-9_-]{1,160}$/;
const fail = () => new Error("报告文件未通过完整性校验，请刷新任务后重试。");

export async function businessFileJson<T>(url: string, init: RequestInit = {}, options: { fetcher?: typeof fetch; signal?: AbortSignal } = {}): Promise<T> {
  options.signal?.throwIfAborted();
  const { response, data } = await fetchBoundedJson({ url, init: { cache: "no-store", ...init }, timeoutMs: 30_000, maxBytes: 1024*1024, ...options });
  if (!response.ok) throw new Error(data && typeof data === "object" && "error" in data && typeof data.error === "string" ? data.error : "报告文件请求失败");
  if (!data || typeof data !== "object" || Array.isArray(data)) throw fail();
  return data as T;
}

async function sha256(bytes: Uint8Array) {
  const result = await crypto.subtle.digest("SHA-256", new Uint8Array(bytes).buffer);
  return Array.from(new Uint8Array(result), value => value.toString(16).padStart(2, "0")).join("");
}

export async function downloadBusinessFile(runId: string, format: "html" | "xlsx", options: { fetcher?: typeof fetch; signal?: AbortSignal; onProgress?: (received: number, total: number) => void } = {}) {
  if (!idPattern.test(runId) || !["html", "xlsx"].includes(format)) throw fail();
  const base = "/api/ai/business-files/"+runId;
  const { item } = await businessFileJson<{ item: BusinessFileRun }>(base, {}, options);
  const manifest = item?.manifest, file = manifest?.schemaVersion === "business-file-delivery-v1" ? manifest.files?.[format] : undefined;
  const mime = format === "html" ? "text/html; charset=utf-8" : "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
  if (item?.id !== runId || item.status !== "ready" || manifest?.schemaVersion !== "business-file-delivery-v1" || manifest.attempt !== item.attempt || manifest.bindingDigest !== item.bindingDigest || !digestPattern.test(item.bindingDigest) || !Number.isInteger(item.attempt) || item.attempt < 1 || item.attempt > 5 || !file || !Number.isInteger(file.bytes) || file.bytes < 1 || file.bytes > 256*1024*1024 || file.chunkBytes !== 512*1024 || file.chunkCount !== Math.ceil(file.bytes/file.chunkBytes) || !digestPattern.test(file.sha256) || file.mimeType !== mime || typeof file.fileName !== "string" || !file.fileName.endsWith("."+format) || file.fileName.length > 240 || /[\\/\x00-\x1f]/.test(file.fileName)) throw fail();
  const bytes = new Uint8Array(file.bytes);
  let received = 0;
  options.onProgress?.(0, file.bytes);
  for (let sequence=1; sequence<=file.chunkCount; sequence++) {
    options.signal?.throwIfAborted();
    const part = await businessFileJson<{ schemaVersion: string; runId: string; attempt: number; sequence: number; format: string; bytes: number; sha256: string; fileSha256: string; base64: string }>(`${base}/chunks/${format}?sequence=${sequence}`, {}, options);
    if (part.schemaVersion !== "business-file-chunk-v1" || part.runId !== runId || part.attempt !== item.attempt || part.sequence !== sequence || part.format !== format || part.fileSha256 !== file.sha256 || !digestPattern.test(part.sha256) || typeof part.base64 !== "string" || !/^[A-Za-z0-9+/]*={0,2}$/.test(part.base64)) throw fail();
    let chunk: Uint8Array;
    try { chunk = Uint8Array.from(atob(part.base64), character => character.charCodeAt(0)); } catch { throw fail(); }
    if (chunk.length !== Math.min(file.chunkBytes, file.bytes-received) || part.bytes !== chunk.length || await sha256(chunk) !== part.sha256) throw fail();
    bytes.set(chunk, received);
    received += chunk.length;
    options.onProgress?.(received, file.bytes);
  }
  if (received !== file.bytes || await sha256(bytes) !== file.sha256) throw fail();
  // Recheck live permission and immutable delivery identity before exposing a Blob.
  const fresh = await businessFileJson<{ item: BusinessFileRun }>(base, {}, options);
  if (fresh.item?.id !== runId || fresh.item.status !== "ready" || fresh.item.attempt !== item.attempt || fresh.item.bindingDigest !== item.bindingDigest || fresh.item.manifest?.schemaVersion !== "business-file-delivery-v1" || fresh.item.manifest.files?.[format]?.sha256 !== file.sha256) throw fail();
  options.signal?.throwIfAborted();
  return { blob: new Blob([bytes.buffer], { type: mime }), fileName: file.fileName, sha256: file.sha256 };
}

type ReadOptions = { fetcher?: typeof fetch; signal?: AbortSignal };
export async function businessFilePrincipal(options: ReadOptions = {}) {
  const value = await businessFileJson<{ principalKey: string }>("/api/ai/business-evidence?page=1&pageSize=1", {}, options);
  if (typeof value.principalKey !== "string" || !digestPattern.test(value.principalKey)) throw fail();
  return value.principalKey;
}

function exactKeys(value: object, keys: string[]) {
  if (!value || typeof value !== "object" || Array.isArray(value) || Object.keys(value).length !== keys.length || keys.some(key => !Object.hasOwn(value, key))) throw fail();
}

/** Validate and detach the complete compact manifest before listing/downloading. */
export function businessVolumeManifest(item: BusinessFileRun, runId = item?.id): BusinessVolumeManifest {
  const value = item?.manifest;
  if (!idPattern.test(runId) || item?.id !== runId || !idPattern.test(item.reportId) || item.status !== "ready" || (item.rendererVersion !== 4 && item.rendererVersion !== 6 && item.rendererVersion !== 7 && item.rendererVersion !== 9 && item.rendererVersion !== 10) || !Number.isSafeInteger(item.version) || item.version < 1 || !Number.isInteger(item.attempt) || item.attempt < 1 || item.attempt > 5 || typeof item.draft !== "boolean" || !digestPattern.test(item.bindingDigest) || value?.schemaVersion !== "business-file-delivery-v2") throw fail();
  exactKeys(value, ["schemaVersion", "rendererVersion", "bindingDigest", "attempt", "draft", "volumeCount", "files", "manifestFile"]);
  if (value.rendererVersion !== item.rendererVersion || value.bindingDigest !== item.bindingDigest || value.attempt !== item.attempt || value.draft !== item.draft || !Number.isInteger(value.volumeCount) || value.volumeCount < 1 || value.volumeCount > 100 || !Array.isArray(value.files) || value.files.length !== 2*value.volumeCount) throw fail();
  function descriptor(file: BusinessVolumeFile, index: number, format: BusinessVolumeFile["format"]): BusinessVolumeFile {
    exactKeys(file, ["volumeIndex", "format", "bytes", "sha256", "chunkCount"]);
    if (file.volumeIndex !== index || file.format !== format || !Number.isSafeInteger(file.bytes) || file.bytes < 1 || file.bytes > (index === 0 ? 16 : 256)*1024*1024 || file.chunkCount !== Math.ceil(file.bytes/524288) || typeof file.sha256 !== "string" || !digestPattern.test(file.sha256)) throw fail();
    return { volumeIndex: index, format, bytes: file.bytes, sha256: file.sha256, chunkCount: file.chunkCount };
  }
  const files = value.files.map((file, i) => descriptor(file, Math.floor(i/2)+1, i%2 ? "xlsx" : "html"));
  const manifestFile = descriptor(value.manifestFile, 0, "json");
  if ((item.rendererVersion === 7 || item.rendererVersion === 9) && (item.draft || item.progress?.stage !== "ready" || !digestPattern.test(item.progress.publicationFenceDigest ?? "") || item.progress.manifestFileSha256 !== manifestFile.sha256)) throw fail();
  if (item.rendererVersion === 10) {
    const progress = item.progress;
    exactKeys(progress, ["stage", "attempt", "bindingDigest", "attestationId", "attestationSha256", "fullManifestDigest", "manifestFileSha256", "owningVerificationDigest", "publicationFenceDigest", "publishRequestDigest"]);
    if (item.draft || progress.stage !== "ready" || progress.attempt !== item.attempt || progress.bindingDigest !== item.bindingDigest || progress.manifestFileSha256 !== manifestFile.sha256 || [progress.attestationId, progress.attestationSha256, progress.fullManifestDigest, progress.owningVerificationDigest, progress.publicationFenceDigest, progress.publishRequestDigest].some(value => !digestPattern.test(value ?? ""))) throw fail();
  }
  if (files.reduce((sum, file) => sum+file.bytes, manifestFile.bytes) > 1024*1024*1024) throw fail();
  return { schemaVersion: "business-file-delivery-v2", rendererVersion: item.rendererVersion, bindingDigest: item.bindingDigest, attempt: item.attempt, draft: item.draft, volumeCount: value.volumeCount, files, manifestFile };
}

export async function downloadBusinessVolume(runId: string, volumeIndex: number, format: BusinessVolumeFile["format"], options: ReadOptions & { expectedPrincipalKey: string; onProgress?: (received: number, total: number) => void }) {
  if (!idPattern.test(runId) || !Number.isInteger(volumeIndex) || volumeIndex < 0 || volumeIndex > 100 || !(volumeIndex === 0 ? format === "json" : format === "html" || format === "xlsx") || !digestPattern.test(options.expectedPrincipalKey)) throw fail();
  const identity = async () => {
    if (await businessFilePrincipal(options) !== options.expectedPrincipalKey) throw new Error("账号已变化，下载已停止；请刷新文件列表。");
    options.signal?.throwIfAborted();
  };
  await identity();
  const base = "/api/ai/business-files/"+runId;
  const { item } = await businessFileJson<{ item: BusinessFileRun }>(base, {}, options);
  const manifest = businessVolumeManifest(item, runId), signature = JSON.stringify(manifest);
  if (item.rendererVersion === 10) throw fail();
  const publicationFence = item.rendererVersion === 7 || item.rendererVersion === 9 ? item.progress.publicationFenceDigest : null;
  const file = [...manifest.files, manifest.manifestFile].find(file => file.volumeIndex === volumeIndex && file.format === format);
  if (!file) throw fail();
  const bytes = new Uint8Array(file.bytes);
  let received = 0;
  options.onProgress?.(0, file.bytes);
  for (let sequence = 1; sequence <= file.chunkCount; sequence++) {
    options.signal?.throwIfAborted();
    const part = await businessFileJson<{ schemaVersion: string; runId: string; volumeIndex: number; format: string; attempt: number; sequence: number; bytes: number; sha256: string; fileSha256: string; bindingDigest: string; base64: string }>(`${base}/volumes/${volumeIndex}/chunks/${format}?sequence=${sequence}`, {}, options);
    if (part.schemaVersion !== "business-volume-chunk-v1" || part.runId !== runId || part.volumeIndex !== volumeIndex || part.format !== format || part.attempt !== item.attempt || part.sequence !== sequence || part.bindingDigest !== item.bindingDigest || part.fileSha256 !== file.sha256 || typeof part.sha256 !== "string" || !digestPattern.test(part.sha256) || typeof part.base64 !== "string" || !/^[A-Za-z0-9+/]*={0,2}$/.test(part.base64)) throw fail();
    let chunk: Uint8Array;
    try { chunk = Uint8Array.from(atob(part.base64), character => character.charCodeAt(0)); } catch { throw fail(); }
    if (chunk.length !== Math.min(524288, file.bytes-received) || part.bytes !== chunk.length || await sha256(chunk) !== part.sha256) throw fail();
    bytes.set(chunk, received); received += chunk.length;
    options.onProgress?.(received, file.bytes);
  }
  if (received !== file.bytes || await sha256(bytes) !== file.sha256) throw fail();
  const fresh = await businessFileJson<{ item: BusinessFileRun }>(base, {}, options);
  if (JSON.stringify(businessVolumeManifest(fresh.item, runId)) !== signature || fresh.item.version !== item.version || fresh.item.reportId !== item.reportId || (publicationFence !== null && fresh.item.progress.publicationFenceDigest !== publicationFence)) throw fail();
  await identity();
  const mime = format === "json" ? "application/json;charset=utf-8" : format === "html" ? "text/html;charset=utf-8" : "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
  const fileName = volumeIndex === 0 ? `${item.reportId}-完整交付清单.json` : `${item.reportId}-volume-${String(volumeIndex).padStart(3, "0")}-of-${String(manifest.volumeCount).padStart(3, "0")}.${format}`;
  return { blob: new Blob([bytes.buffer], { type: mime }), fileName, sha256: file.sha256 };
}

/** Explicit renderer-10 candidate. The page does not expose this until release. */
export async function downloadBudgetV10Volume(runId: string, volumeIndex: number, format: BusinessVolumeFile["format"], options: ReadOptions & { expectedPrincipalKey: string; onProgress?: (received: number, total: number) => void }) {
  if (!idPattern.test(runId) || !Number.isInteger(volumeIndex) || volumeIndex < 0 || volumeIndex > 100 || !(volumeIndex === 0 ? format === "json" : format === "html" || format === "xlsx") || !digestPattern.test(options.expectedPrincipalKey)) throw fail();
  const identity = async () => {
    if (await businessFilePrincipal(options) !== options.expectedPrincipalKey) throw new Error("账号已变化，下载已停止；请刷新文件列表。");
    options.signal?.throwIfAborted();
  };
  await identity();
  const base = "/api/ai/business-files/" + runId;
  const { item } = await businessFileJson<{ item: BusinessFileRun }>(base, {}, options);
  const manifest = businessVolumeManifest(item, runId);
  if (item.rendererVersion !== 10) throw fail();
  const manifestSignature = JSON.stringify(manifest), progressSignature = JSON.stringify(item.progress);
  const file = [...manifest.files, manifest.manifestFile].find(value => value.volumeIndex === volumeIndex && value.format === format);
  if (!file) throw fail();
  const bytes = new Uint8Array(file.bytes);
  let received = 0, receiptDigest: string | null = null;
  options.onProgress?.(0, file.bytes);
  for (let sequence = 1; sequence <= file.chunkCount; sequence++) {
    options.signal?.throwIfAborted();
    await identity();
    const part = await businessFileJson<{ schemaVersion: string; runId: string; volumeIndex: number; format: string; attempt: number; readyVersion: number; sequence: number; bytes: number; sha256: string; fileSha256: string; bindingDigest: string; manifestFileSha256: string; receiptDigest: string; base64: string }>(`${base}/volumes/${volumeIndex}/chunks/${format}?sequence=${sequence}`, {}, options);
    if (part.schemaVersion !== "business-volume-chunk-v10-v1" || part.runId !== runId || part.volumeIndex !== volumeIndex || part.format !== format || part.attempt !== item.attempt || part.readyVersion !== item.version || part.sequence !== sequence || part.bindingDigest !== item.bindingDigest || part.fileSha256 !== file.sha256 || part.manifestFileSha256 !== manifest.manifestFile.sha256 || !digestPattern.test(part.sha256) || !digestPattern.test(part.receiptDigest) || receiptDigest !== null && part.receiptDigest !== receiptDigest || typeof part.base64 !== "string" || !/^[A-Za-z0-9+/]*={0,2}$/.test(part.base64)) throw fail();
    let content: Uint8Array;
    try { content = Uint8Array.from(atob(part.base64), character => character.charCodeAt(0)); } catch { throw fail(); }
    if (content.length !== Math.min(524288, file.bytes-received) || part.bytes !== content.length || await sha256(content) !== part.sha256) throw fail();
    bytes.set(content, received); received += content.length;
    receiptDigest = part.receiptDigest;
    options.onProgress?.(received, file.bytes);
  }
  if (received !== file.bytes || await sha256(bytes) !== file.sha256) throw fail();
  const fresh = await businessFileJson<{ item: BusinessFileRun }>(base, {}, options);
  if (fresh.item?.id !== runId || fresh.item.reportId !== item.reportId || fresh.item.version !== item.version || fresh.item.attempt !== item.attempt || fresh.item.bindingDigest !== item.bindingDigest || JSON.stringify(businessVolumeManifest(fresh.item, runId)) !== manifestSignature || JSON.stringify(fresh.item.progress) !== progressSignature) throw fail();
  await identity();
  const mime = format === "json" ? "application/json;charset=utf-8" : format === "html" ? "text/html;charset=utf-8" : "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
  const fileName = volumeIndex === 0 ? `${item.reportId}-完整交付清单.json` : `${item.reportId}-volume-${String(volumeIndex).padStart(3, "0")}-of-${String(manifest.volumeCount).padStart(3, "0")}.${format}`;
  return { blob: new Blob([bytes.buffer], { type: mime }), fileName, sha256: file.sha256 };
}
