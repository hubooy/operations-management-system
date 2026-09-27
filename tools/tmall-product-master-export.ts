import { createHash, randomUUID } from "node:crypto";
import { mkdir, mkdtemp, readFile, rename, rm, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import type { Browser, Frame, Locator, Page } from "playwright-core";

import { launchDedicatedChrome } from "../lib/jackyun/cdp-client";
import { writeJsonAtomic } from "../lib/jackyun/json-file";
import { connectPlaywrightBrowser } from "../lib/jackyun/playwright-client";
import { inspectTmallImportBytes } from "../lib/netshop/normalized-import";
import {
  getRegisteredTmallStore,
  getTmallStore,
  resolveTmallBrowserLaunchTarget,
  type TmallStore,
} from "../lib/netshop/tmall-store-registry";
import {
  autoLoginTmallWithSavedBrowserCredentials,
  autoLoginTmallWithWindowsDpapiCredential,
  inspectTmallLoginPageState,
} from "./tmall-saved-login";
import {
  hasExactTmallImportVerification,
  type TmallImportVerificationProof,
} from "./tmall-import-verification";

export const TMALL_SELLER_ON_SALE_URL = "https://myseller.taobao.com/home.htm/SellManage/on_sale?current=1&pageSize=20";
export const TMALL_MASTER_EXPORT_PROMPT = "导出全部商品";
export const TMALL_LILI_MASTER_EXPORT_PROMPT = "查询[商品状态:出售中]的商品，并批量导出到excel";

export function resolveTmallMasterExportPrompt(storeKey: string) {
  return storeKey === "tmall-lili" ? TMALL_LILI_MASTER_EXPORT_PROMPT : TMALL_MASTER_EXPORT_PROMPT;
}
export const TMALL_PRODUCT_MANAGER_LABEL = "商品管家";
export const TMALL_IMPORTANT_NOTICE_LABEL = "重要通知";
export const TMALL_IMPORTANT_MESSAGE_LABEL = "重要消息";
export const TMALL_PRODUCT_INSPECTION_NOTICE_LABEL = "商品巡检";
export const TMALL_SHIPPING_EXCEPTION_NOTICE_LABEL = "发货异常提醒";
export const TMALL_CHANNEL_PROMOTION_NOTICE_LABEL = "渠道活动快速报名";

const tmallExportConfirmationLabels = ["确认导出", "确认任务", "确认执行", "确认执行任务", "确定", "立即导出", "确认"] as const;
const tmallNoticeActionSelector = 'button,a,[role="button"],[aria-label],[title],[class*="close" i],:text-is("×"),:text-is("✕")';

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const tmallAutomationProjectRoot = projectRoot;
const artifactDirectory = path.join(projectRoot, "outputs", "tmall-product-master-export");
const directMtopArtifactDirectory = path.join(projectRoot, "outputs", "tmall-direct-product-master-export");
const defaultChromeExecutable = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const maximumWorkbookBytes = 25 * 1024 * 1024;
const exportResultTimeoutMs = 10 * 60 * 1000;
const exportRecordTimeoutMs = 3 * 60 * 1000;
const exportRecordRefreshIntervalMs = 8_000;
export const productManagerChatOpenTimeoutMs = 60_000;
export const tmallSellerLoginRedirectGraceMs = 15_000;
let retainedTmallSessionBrowser: Browser | null = null;

type MasterImportBatch = {
  id?: string;
  source?: string;
  dataset?: string;
  platform?: string;
  shopName?: string;
  snapshotDate?: string | null;
  status?: string;
  rowCount?: number;
  warningCount?: number;
};

type MasterImportPayload = {
  ok?: boolean;
  status?: string;
  message?: string;
  batch?: MasterImportBatch | null;
  warnings?: Array<{ code?: string; message?: string }>;
  verification?: TmallImportVerificationProof;
};

type MasterFileEvidence = {
  fileName: string;
  filePath: string;
  fileSizeBytes: number;
  sha256: string;
  rowCount: number;
  uniqueProductCount: number;
  uniqueSkuCount: number;
};

type MasterExportAuditStage =
  | "planned"
  | "browser_ready"
  | "export_submitting"
  | "export_submitted"
  | "export_confirmed"
  | "downloaded"
  | "completed";

type MasterExportAudit = {
  version: 1;
  runId: string;
  storeKey: string;
  shopName: string;
  snapshotDate: string;
  targetUrl: string;
  prompt: string;
  startedAt: string;
  updatedAt: string;
  stage: MasterExportAuditStage;
  entryMode?: "product_manager_opened" | "product_manager_floating_icon" | "product_manager_already_open" | "bulk_export_entry" | "assistant_direct";
  noticeState?: "dismissed" | "not_present";
  exportSubmittedAt?: string;
  exportTaskCreatedAt?: string;
  file?: MasterFileEvidence;
  importResult?: {
    status: "imported" | "duplicate";
    batchId: string;
    rowCount: number;
    warningCount: number;
  };
  lastError?: string;
  abandonment?: {
    abandonedAt: string;
    previousStage: "export_submitted" | "export_confirmed";
    reason: string;
  };
};

export type TmallProductMasterStageResult = {
  ok: true;
  stage: "product_master";
  status: "imported" | "duplicate";
  storeKey: string;
  shopName: string;
  snapshotDate: string;
  batchId: string;
  rowCount: number;
  warningCount: number;
  auditPath?: string;
  filePath?: string;
};

type TextCandidate = {
  frame: Frame;
  locator: Locator;
  score: number;
  signature: string;
  top?: number;
};

type DownloadCandidate = {
  frame: Frame;
  locator: Locator;
  signature: string;
  frameUrl: string;
  href: string;
  left: number;
  top: number;
  width: number;
  height: number;
  contextText: string;
};

type TmallDownloadChoice = Omit<DownloadCandidate, "frame" | "locator">;

export type ExportRecordDownloadCandidate = {
  locator?: Locator;
  recordPage: Page;
  signature: string;
  recordIdentity?: string;
  taskCreatedAt: string;
  status: string;
  downloadReady?: boolean;
};

type TmallExportRecordChoice = Omit<ExportRecordDownloadCandidate, "locator" | "recordPage">;

type PositionedUiElement = {
  text: string;
  attributes: string;
  tag: string;
  role: string;
  left: number;
  top: number;
  width: number;
  height: number;
  viewportWidth: number;
  viewportHeight: number;
  position?: string;
  cursor?: string;
};

export function scoreProductManagerCandidate(detail: PositionedUiElement) {
  const label = `${detail.text} ${detail.attributes}`.replace(/\s+/g, "").trim();
  const recognized = label.includes(TMALL_PRODUCT_MANAGER_LABEL) || /product[-_ ]?(manager|assistant)/i.test(label);
  if (!recognized || detail.viewportWidth <= 0 || detail.viewportHeight <= 0) return -1;
  const right = detail.left + detail.width;
  if (right < detail.viewportWidth * 0.9 || detail.top < detail.viewportHeight * 0.6) return -1;
  if (detail.width < 8 || detail.height < 8 || detail.width > 240 || detail.height > 180) return -1;
  let score = 10;
  if (["button", "a"].includes(detail.tag) || ["button", "link", "menuitem"].includes(detail.role)) score += 6;
  score += Math.min(6, Math.round((detail.top / detail.viewportHeight) * 6));
  score += Math.min(4, Math.round((detail.left / detail.viewportWidth) * 4));
  return score;
}

export function scoreProductManagerFloatingCandidate(detail: PositionedUiElement) {
  if (detail.viewportWidth <= 0 || detail.viewportHeight <= 0) return -1;
  const right = detail.left + detail.width;
  const bottom = detail.top + detail.height;
  if (right < detail.viewportWidth * 0.97 || bottom < detail.viewportHeight * 0.78 || detail.top > detail.viewportHeight * 0.96) return -1;
  if (detail.width < 16 || detail.height < 16 || detail.width > 120 || detail.height > 120) return -1;
  if (!["fixed", "sticky", "ancestor-fixed", "ancestor-sticky"].includes(detail.position ?? "")) return -1;
  const label = `${detail.text} ${detail.attributes}`.replace(/\s+/g, "").trim();
  if (/重要通知|商品巡检|关闭|返回顶部|回到顶部|客服|帮助|意见反馈|忽略|去优化|下载|翻译/.test(label)) return -1;
  const actionable = ["button", "a"].includes(detail.tag)
    || ["button", "link", "menuitem"].includes(detail.role)
    || detail.cursor === "pointer";
  if (!actionable) return -1;
  let score = 10;
  if (["button", "a"].includes(detail.tag) || ["button", "link"].includes(detail.role)) score += 6;
  if (detail.cursor === "pointer") score += 4;
  if (right >= detail.viewportWidth * 0.98) score += 5;
  if (detail.top <= detail.viewportHeight * 0.85) score += 3;
  return score;
}

export function productManagerFloatingClusterKey(detail: PositionedUiElement) {
  const centerX = detail.left + detail.width / 2;
  const centerY = detail.top + detail.height / 2;
  return `${Math.round(centerX / 12)}|${Math.round(centerY / 12)}`;
}

export function scoreChatSendCandidate(
  detail: { label: string; left: number; top: number; width: number; height: number },
  inputRect: { left: number; right: number; top: number; bottom: number },
) {
  if (detail.width < 10 || detail.height < 10 || detail.width > 96 || detail.height > 96) return -1;
  const centerX = detail.left + detail.width / 2;
  const centerY = detail.top + detail.height / 2;
  const besideInput = centerX >= inputRect.right - 96
    && centerX <= inputRect.right + 80
    && centerY >= inputRect.top - 15
    && centerY <= inputRect.bottom + 15;
  if (!besideInput) return -1;
  let score = 10;
  if (/发送|send|submit|arrow-up/i.test(detail.label)) score += 10;
  if (centerX >= inputRect.right - 64) score += 4;
  return score;
}

export function isTmallExportConfirmationLabel(text: string) {
  const normalized = text.replace(/\s+/g, "").trim();
  return tmallExportConfirmationLabels.includes(normalized as typeof tmallExportConfirmationLabels[number]);
}

export function hasAcceptedTmallExportTask(text: string) {
  const normalized = text.replace(/\s+/g, "");
  return /导出(?:\d+个)?商品到Excel/.test(normalized)
    && /任务\d*[:：]|待执行|任务已执行|执行结果|所有任务已完成|成功导出/.test(normalized);
}

export function countAcceptedTmallExportTasks(text: string) {
  return text.replace(/\s+/g, "").match(/导出(?:\d+个)?商品到Excel/g)?.length ?? 0;
}

export function hasCompletedTmallExportResult(text: string) {
  return /成功导出\s*\d+\s*个商品到Excel文件|所有任务已完成/.test(text.replace(/\s+/g, " "));
}

export function summarizeTmallExportAcknowledgement(text: string, promptStillInInput: boolean) {
  // Persist only classifications/counts, never chat text, item IDs, URLs or credentials.
  const normalized = text.replace(/\s+/g, "");
  return {
    promptStillInInput,
    searchResultPresent: /商品查询结果[（(]共\d+个[）)]/.test(normalized),
    acceptedTaskCount: countAcceptedTmallExportTasks(text),
    completedResultPresent: hasCompletedTmallExportResult(text),
  };
}

export async function waitForTmallExportAcknowledgement(
  probe: () => Promise<{ ready: boolean; diagnostic: ReturnType<typeof summarizeTmallExportAcknowledgement> }>,
  options: { timeoutMs?: number; now?: () => number; pause?: () => Promise<void> } = {},
) {
  const now = options.now ?? Date.now;
  const pause = options.pause ?? (() => new Promise<void>((resolve) => setTimeout(resolve, 1_000)));
  const deadline = now() + (options.timeoutMs ?? 90_000);
  let diagnostic: ReturnType<typeof summarizeTmallExportAcknowledgement> | undefined;
  while (now() < deadline) {
    const observation = await probe();
    diagnostic = observation.diagnostic;
    if (observation.ready) return;
    await pause();
  }
  throw new Error(`商品管家未出现导出确认、任务受理或下载结果；响应诊断=${JSON.stringify(diagnostic ?? null)}；请人工核对原会话，禁止重复提交`);
}

export function chooseTmallResumeSellerPageIndex(pages: readonly { hasCompletedResult: boolean }[]) {
  const completedIndexes = pages.flatMap((page, index) => page.hasCompletedResult ? [index] : []);
  if (completedIndexes.length > 1) {
    throw new Error("多个千牛页面都显示已完成商品导出，无法唯一接管原任务");
  }
  return completedIndexes[0] ?? 0;
}

export function isResumableTmallExportStage(stage: string | undefined) {
  return stage === "export_submitted" || stage === "export_confirmed";
}

export function decideTmallMasterAuditRecovery(
  requestedSnapshotDate: string,
  audit: Pick<MasterExportAudit, "snapshotDate" | "stage">,
) {
  if (audit.snapshotDate === requestedSnapshotDate) {
    return { action: "continue", snapshotDate: requestedSnapshotDate } as const;
  }
  if (audit.stage === "downloaded" || isResumableTmallExportStage(audit.stage)) {
    return { action: "resume_previous", snapshotDate: audit.snapshotDate } as const;
  }
  if (audit.stage === "export_submitting") {
    return { action: "block", snapshotDate: audit.snapshotDate } as const;
  }
  return { action: "discard", snapshotDate: requestedSnapshotDate } as const;
}

export function isTmallProductWorkbookFilename(fileName: string) {
  return fileName.length > 5 && fileName.length <= 240 && /\.xlsx$/i.test(fileName) && !/[\u0000-\u001f<>:"/\\|?*]/.test(fileName);
}

export function chooseLatestTmallDownloadSignature(candidates: readonly TmallDownloadChoice[]) {
  const distinct = clusterTmallDownloadChoices(candidates);
  if (distinct.length === 0) return null;
  if (distinct.length === 1) return distinct[0]!.signature;
  if (new Set(distinct.map((candidate) => candidate.frameUrl)).size !== 1) {
    throw new Error("多个下载链接分布在不同页面，无法确认当前商品管家任务");
  }
  const completed = distinct.filter((candidate) => hasCompletedTmallExportResult(candidate.contextText));
  if (completed.length === 0) return null;
  const ordered = completed.sort((left, right) => left.top - right.top || left.left - right.left);
  if (ordered.length > 1 && ordered.at(-1)!.top - ordered.at(-2)!.top < 16) {
    throw new Error("多个成功下载链接位置并列，无法唯一确认最新商品管家任务");
  }
  return ordered.at(-1)!.signature;
}

export function chooseTmallResumedDownloadSignature(candidates: readonly TmallDownloadChoice[]) {
  // A resumed task is already submitted: existing completed cards must remain
  // visible to recovery, even after page reflow. The export-record timestamp
  // check still binds the eventual download to the original submission.
  return chooseLatestTmallDownloadSignature(candidates.filter((candidate) => hasCompletedTmallExportResult(candidate.contextText)));
}

export async function findTmallResumedCompletedDownload<T extends TmallDownloadChoice>(
  readScoped: () => Promise<readonly T[]>,
  readSamePage: () => Promise<readonly T[]>,
): Promise<T | null> {
  // Read-only recovery: the completed card can live outside the input overlay.
  // This does not establish file ownership; the original export-record time
  // and completion checks remain mandatory before accepting any download.
  for (const read of [readScoped, readSamePage]) {
    const candidates = await read();
    const signature = chooseTmallResumedDownloadSignature(candidates);
    if (signature) return candidates.find((candidate) => candidate.signature === signature) ?? null;
  }
  return null;
}

function clusterTmallDownloadChoices(candidates: readonly TmallDownloadChoice[]) {
  const visualClusters = new Map<string, TmallDownloadChoice>();
  for (const candidate of candidates) {
    const centerX = candidate.left + candidate.width / 2;
    const centerY = candidate.top + candidate.height / 2;
    const key = `${candidate.frameUrl}|${Math.round(centerX / 16)}|${Math.round(centerY / 16)}`;
    const previous = visualClusters.get(key);
    if (!previous) {
      visualClusters.set(key, candidate);
      continue;
    }
    const representative = !previous.href && candidate.href ? candidate : previous;
    visualClusters.set(key, {
      ...representative,
      contextText: representative.contextText || previous.contextText || candidate.contextText,
    });
  }
  return [...visualClusters.values()];
}

export function countTmallCompletedDownloadCards(candidates: readonly TmallDownloadChoice[]) {
  return clusterTmallDownloadChoices(candidates)
    .filter((candidate) => hasCompletedTmallExportResult(candidate.contextText)).length;
}

export function chooseFreshTmallDownloadSignature(
  candidates: readonly TmallDownloadChoice[],
  baselineCompletedCount: number,
) {
  if (!Number.isInteger(baselineCompletedCount) || baselineCompletedCount < 0) {
    throw new Error("商品管家下载基线数量无效");
  }
  if (countTmallCompletedDownloadCards(candidates) <= baselineCompletedCount) return null;
  return chooseLatestTmallDownloadSignature(candidates);
}

export function parseTmallShanghaiTaskTime(value: string) {
  const match = value.replace(/\s+/g, "").match(/(20\d{2})-(\d{2})-(\d{2})(\d{2}):(\d{2}):(\d{2})/);
  if (!match) return null;
  const text = `${match[1]}-${match[2]}-${match[3]} ${match[4]}:${match[5]}:${match[6]}`;
  const epochMs = Date.parse(`${match[1]}-${match[2]}-${match[3]}T${match[4]}:${match[5]}:${match[6]}+08:00`);
  if (!Number.isFinite(epochMs)) return null;
  const roundTrip = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).format(new Date(epochMs)).replace(",", "");
  if (roundTrip !== text) return null;
  return { text, epochMs };
}

export function parseTmallExportRecordStatus(value: string) {
  const normalized = value.replace(/\s+/g, "");
  if (normalized.includes("任务失败") || normalized.includes("生成失败")) return "任务失败" as const;
  if (normalized.includes("已完成") || normalized.includes("生成成功")) return "已完成" as const;
  if (normalized.includes("处理中") || normalized.includes("生成中")) return "处理中" as const;
  if (normalized.includes("待执行")) return "待执行" as const;
  return "未完成" as const;
}

export function chooseTmallExportRecordSignature(
  candidates: readonly TmallExportRecordChoice[],
  expectedRunStartedAt: string,
) {
  const matched = matchTmallExportRecordChoice(candidates, expectedRunStartedAt);
  if (!matched || matched.status.replace(/\s+/g, "") !== "已完成" || matched.downloadReady === false) return null;
  return matched.signature;
}

export function dedupeTmallExportRecordChoices(candidates: readonly TmallExportRecordChoice[]) {
  const unique = new Map<string, TmallExportRecordChoice>();
  const score = (candidate: TmallExportRecordChoice) => (
    (candidate.status.replace(/\s+/g, "") === "已完成" ? 10 : 0)
    + (candidate.downloadReady ? 5 : 0)
  );
  for (const candidate of candidates) {
    const key = candidate.recordIdentity ? `record:${candidate.recordIdentity}` : `candidate:${candidate.signature}`;
    const previous = unique.get(key);
    if (!previous || score(candidate) > score(previous)) unique.set(key, candidate);
  }
  return [...unique.values()];
}

export function matchTmallExportRecordChoice(
  candidates: readonly TmallExportRecordChoice[],
  expectedRunStartedAt: string,
) {
  const expectedMs = Date.parse(expectedRunStartedAt);
  if (!Number.isFinite(expectedMs)) throw new Error("天猫货品活动清单开始时间无效");
  const uniqueCandidates = dedupeTmallExportRecordChoices(candidates);
  const eligible = uniqueCandidates.flatMap((candidate) => {
    const parsed = parseTmallShanghaiTaskTime(candidate.taskCreatedAt);
    if (!parsed) return [];
    const deltaMs = parsed.epochMs - expectedMs;
    if (deltaMs < -5_000 || deltaMs > 20 * 60_000) return [];
    return [{ candidate, distanceMs: Math.abs(deltaMs) }];
  }).sort((left, right) => left.distanceMs - right.distanceMs);
  if (eligible.length === 0) return null;
  if (eligible[1] && eligible[1].distanceMs - eligible[0]!.distanceMs < 60_000) {
    throw new Error("导出记录中有多个创建时间同样接近的任务，无法唯一确认本轮文件");
  }
  const matched = eligible[0]!.candidate;
  if (matched.status.replace(/\s+/g, "") === "任务失败") {
    throw new Error(`导出记录 ${matched.taskCreatedAt} 明确显示任务失败，拒绝下载失败文件或重复发送导出指令`);
  }
  return matched;
}

export function scoreImportantNoticeCloseCandidate(detail: PositionedUiElement, notice: PositionedUiElement) {
  if (detail.viewportWidth <= 0 || detail.viewportHeight <= 0) return -1;
  if (notice.left < notice.viewportWidth * 0.45 || notice.top < notice.viewportHeight * 0.4) return -1;
  const centerX = detail.left + detail.width / 2;
  const centerY = detail.top + detail.height / 2;
  const text = detail.text.replace(/\s+/g, "").trim();
  if (/去优化|立即优化|去处理|立即处理|查看详情|去查看|去解决|处理异常|去发货|立即发货|去补货|立即补货/.test(text)) return -1;
  const explicitClose = isExplicitTmallNoticeDismissAction(detail);
  const compact = detail.width >= 8 && detail.width <= 72 && detail.height >= 8 && detail.height <= 72;
  const nearby = centerX >= notice.left - 40
    && centerX <= notice.viewportWidth
    && centerY >= notice.top - 180
    && centerY <= notice.top + 260;
  if (!nearby || (!explicitClose && !compact)) return -1;
  let score = explicitClose ? 16 : 4;
  if (["button", "a"].includes(detail.tag) || ["button", "link"].includes(detail.role)) score += 5;
  if (compact) score += 4;
  if (centerX >= notice.left) score += 3;
  if (centerY <= notice.top + 80) score += 2;
  return score;
}

export function isExplicitTmallNoticeDismissAction(detail: PositionedUiElement) {
  const text = detail.text.replace(/\s+/g, "").trim();
  if (/去优化|立即优化|去处理|立即处理|查看详情|去查看|去解决|处理异常|去发货|立即发货|去补货|立即补货/.test(text)) {
    return false;
  }
  const label = `${detail.text} ${detail.attributes}`.replace(/\s+/g, " ").trim();
  return text === "忽略" || /关闭|close|dismiss|我知道了|知道了|^[×✕x]$/i.test(label);
}

export function isTmallNoticePointerInterceptionError(error: unknown) {
  return error instanceof Error
    && /intercepts pointer events|another element.*receives pointer events/i.test(error.message);
}

export function sameTmallNoticeActionTarget(left: PositionedUiElement, right: PositionedUiElement) {
  const intersectionWidth = Math.max(
    0,
    Math.min(left.left + left.width, right.left + right.width) - Math.max(left.left, right.left),
  );
  const intersectionHeight = Math.max(
    0,
    Math.min(left.top + left.height, right.top + right.height) - Math.max(left.top, right.top),
  );
  const smallerArea = Math.min(left.width * left.height, right.width * right.height);
  if (smallerArea <= 0) return false;
  const overlapOfSmaller = (intersectionWidth * intersectionHeight) / smallerArea;
  if (overlapOfSmaller >= 0.8) return true;
  const leftCenterX = left.left + left.width / 2;
  const leftCenterY = left.top + left.height / 2;
  const rightCenterX = right.left + right.width / 2;
  const rightCenterY = right.top + right.height / 2;
  return overlapOfSmaller >= 0.5
    && Math.hypot(leftCenterX - rightCenterX, leftCenterY - rightCenterY) <= 8;
}

export function compareTmallNoticeActionCandidates(
  left: { score: number; explicitDismiss: boolean },
  right: { score: number; explicitDismiss: boolean },
) {
  return Number(right.explicitDismiss) - Number(left.explicitDismiss) || right.score - left.score;
}

export function shouldRejectEqualTmallNoticeActions(
  first: { score: number; signature: string; explicitDismiss: boolean },
  second?: { score: number; signature: string; explicitDismiss: boolean },
) {
  return Boolean(
    second
    && second.score === first.score
    && second.signature !== first.signature
    && !first.explicitDismiss
    && !second.explicitDismiss,
  );
}

export function scoreTmallBlockingNoticeCandidate(detail: PositionedUiElement, contextText = detail.text) {
  if (detail.viewportWidth <= 0 || detail.viewportHeight <= 0) return -1;
  if (detail.left < detail.viewportWidth * 0.45 || detail.top < detail.viewportHeight * 0.35) return -1;
  if (detail.width < 2 || detail.height < 2 || detail.width > 800 || detail.height > 700) return -1;
  const text = detail.text.replace(/\s+/g, "").trim();
  const context = contextText.replace(/\s+/g, "").trim();
  const closableOverlay = isTmallClosableOverlayNotice(detail);
  const structural = /notify[_-]?body/i.test(detail.attributes) || closableOverlay;
  const importantNotice = text.includes(TMALL_IMPORTANT_NOTICE_LABEL);
  const importantMessage = text.includes(TMALL_IMPORTANT_MESSAGE_LABEL);
  const productInspection = text.includes(TMALL_PRODUCT_INSPECTION_NOTICE_LABEL);
  const shippingException = text.includes(TMALL_SHIPPING_EXCEPTION_NOTICE_LABEL);
  const channelPromotion = text.includes(TMALL_CHANNEL_PROMOTION_NOTICE_LABEL);
  if (
    productInspection
    && !structural
    && !(
      /商品当前存在以下问题|影响成交转化|质量分问题|及时关注/.test(context)
      && context.includes("忽略")
      && /去优化|立即优化/.test(context)
    )
  ) {
    return -1;
  }
  const labeled = importantNotice || importantMessage || productInspection || shippingException || channelPromotion;
  if (!structural && !labeled) return -1;
  let score = 10;
  if (
    text === TMALL_IMPORTANT_NOTICE_LABEL
    || text === TMALL_IMPORTANT_MESSAGE_LABEL
    || text === TMALL_PRODUCT_INSPECTION_NOTICE_LABEL
    || text.startsWith(TMALL_SHIPPING_EXCEPTION_NOTICE_LABEL)
    || text.startsWith(TMALL_CHANNEL_PROMOTION_NOTICE_LABEL)
  ) score += 10;
  if (channelPromotion) score += 12;
  if (importantMessage) score += 8;
  if (shippingException) score += 6;
  if (structural) score += 8;
  if (closableOverlay) score += 20;
  score += Math.round((detail.left / detail.viewportWidth) * 5);
  score += Math.round((detail.top / detail.viewportHeight) * 5);
  return score;
}

export function isTmallClosableOverlayNotice(detail: PositionedUiElement) {
  const attributes = detail.attributes.replace(/\s+/g, " ").trim();
  return detail.role === "tooltip"
    && /next-balloon/i.test(attributes)
    && /closable/i.test(attributes);
}

function shanghaiToday(now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const read = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value ?? "";
  return `${read("year")}-${read("month")}-${read("day")}`;
}

function normalizeLocalBaseUrl(value: string) {
  const url = new URL(value);
  if (url.protocol !== "http:" || !["localhost", "127.0.0.1"].includes(url.hostname)) {
    throw new Error("天猫货品自动导入只允许连接本机运营系统");
  }
  return url.toString().replace(/\/$/, "");
}

function inside(directory: string, filePath: string) {
  const relative = path.relative(path.resolve(directory), path.resolve(filePath));
  return relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative);
}

function safeSegment(value: string) {
  return value.replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_").replace(/\s+/g, "-").slice(0, 80);
}

function safeError(error: unknown) {
  const message = error instanceof Error ? error.message : String(error);
  return message.replace(/[\r\n]+/g, " ").slice(0, 500);
}

function activeAuditPath(storeKey: string, auditDirectory = artifactDirectory) {
  return path.join(auditDirectory, `active-${safeSegment(storeKey)}.json`);
}

async function readActiveAudit(storeKey: string, auditDirectory = artifactDirectory) {
  const filePath = activeAuditPath(storeKey, auditDirectory);
  try {
    const parsed = JSON.parse(await readFile(filePath, "utf8")) as MasterExportAudit;
    if (parsed.version !== 1 || parsed.storeKey !== storeKey || !parsed.runId || !parsed.snapshotDate || !parsed.stage) {
      throw new Error("活动清单结构无效");
    }
    return { filePath, audit: parsed };
  } catch (error) {
    const code = (error as NodeJS.ErrnoException).code;
    if (code === "ENOENT") return null;
    throw error;
  }
}

async function writeActiveAudit(audit: MasterExportAudit, auditDirectory = artifactDirectory) {
  const updated = { ...audit, updatedAt: new Date().toISOString() };
  await writeJsonAtomic(activeAuditPath(audit.storeKey, auditDirectory), updated);
  return updated;
}

export async function abandonActiveTmallProductMasterAudit(options: {
  storeKey: string;
  reason: string;
  operatorConfirmed: boolean;
  auditDirectory?: string;
  now?: Date;
}) {
  if (options.operatorConfirmed !== true) {
    throw new Error("作废已提交的天猫货品任务必须取得操作者明确确认");
  }
  const reason = options.reason.replace(/\s+/g, " ").trim();
  if (reason.length < 4 || reason.length > 300) {
    throw new Error("天猫货品任务作废原因必须为 4 至 300 个字符");
  }
  const store = await getTmallStore(options.storeKey);
  const auditDirectory = path.resolve(options.auditDirectory ?? artifactDirectory);
  const existing = await readActiveAudit(store.storeKey, auditDirectory);
  if (!existing) throw new Error("当前店铺不存在可作废的天猫货品活动清单");
  const audit = existing.audit;
  if (audit.shopName !== store.shopName) {
    throw new Error("天猫货品活动清单店铺身份不一致，拒绝作废");
  }
  if (!isResumableTmallExportStage(audit.stage)) {
    throw new Error(`天猫货品活动清单阶段 ${audit.stage} 不允许按已提交任务作废`);
  }
  const previousStage = audit.stage;
  const abandonedAt = (options.now ?? new Date()).toISOString();
  const archiveFileName = `abandoned-${safeSegment(store.storeKey)}-${audit.snapshotDate}-${safeSegment(audit.runId)}.json`;
  const archivePath = path.join(auditDirectory, archiveFileName);
  if (await stat(archivePath).then(() => true).catch(() => false)) {
    throw new Error("天猫货品任务作废归档已存在，拒绝覆盖");
  }

  // Renaming first removes the manifest from the active slot atomically while
  // preserving its complete evidence. This entry point is intentionally not
  // called by n8n or automatic recovery; it is only for an explicitly confirmed
  // operator decision after the original platform task has been reviewed.
  await rename(existing.filePath, archivePath);
  const archivedAudit: MasterExportAudit = {
    ...audit,
    updatedAt: abandonedAt,
    abandonment: {
      abandonedAt,
      previousStage,
      reason,
    },
  };
  await writeJsonAtomic(archivePath, archivedAudit);
  return {
    ok: true as const,
    stage: "abandoned" as const,
    storeKey: store.storeKey,
    shopName: store.shopName,
    snapshotDate: audit.snapshotDate,
    previousStage,
    archiveFileName,
  };
}

export async function inspectTmallMasterFile(
  filePath: string,
  store: Pick<TmallStore, "shopName" | "browser">,
  snapshotDate: string,
): Promise<MasterFileEvidence> {
  const resolved = path.resolve(filePath);
  if (!inside(store.browser.downloadDir, resolved) || !/\.xlsx$/i.test(resolved)) {
    throw new Error("天猫货品文件必须位于当前店铺独立下载目录且扩展名为 .xlsx");
  }
  const info = await stat(resolved);
  if (!info.isFile() || info.size <= 0 || info.size > maximumWorkbookBytes) {
    throw new Error("天猫货品文件为空、不是文件或超过 25MB");
  }
  const bytes = new Uint8Array(await readFile(resolved));
  const inspection = await inspectTmallImportBytes({
    source: "tmall_product_master",
    bytes,
    fileName: path.basename(resolved),
    fileSizeBytes: bytes.byteLength,
    platform: "天猫",
    shopName: store.shopName,
    snapshotDate,
  });
  if (inspection.errors.length > 0 || inspection.dataset !== "product_master"
    || inspection.platform !== "天猫" || inspection.shopName !== store.shopName
    || inspection.totals.rowCount <= 0) {
    const message = inspection.errors.map((issue) => issue.message).join("；") || "货品工作簿身份或行数不符合预期";
    throw new Error(`天猫货品工作簿校验失败：${message}`);
  }
  return {
    fileName: path.basename(resolved),
    filePath: resolved,
    fileSizeBytes: bytes.byteLength,
    sha256: createHash("sha256").update(bytes).digest("hex"),
    rowCount: inspection.totals.rowCount,
    uniqueProductCount: inspection.totals.uniqueProductCount,
    uniqueSkuCount: inspection.totals.uniqueSkuCount,
  };
}

async function assertEvidenceUnchanged(evidence: MasterFileEvidence, store: Pick<TmallStore, "browser">) {
  if (!inside(store.browser.downloadDir, evidence.filePath)
    || path.basename(evidence.filePath) !== evidence.fileName
    || !/^[a-f0-9]{64}$/.test(evidence.sha256)) {
    throw new Error("活动清单中的货品文件身份无效");
  }
  const info = await stat(evidence.filePath);
  if (!info.isFile() || info.size !== evidence.fileSizeBytes) throw new Error("活动清单中的货品文件缺失或大小变化");
  const bytes = await readFile(evidence.filePath);
  if (createHash("sha256").update(bytes).digest("hex") !== evidence.sha256) {
    throw new Error("活动清单中的货品文件哈希变化");
  }
}

export async function importTmallProductMasterFile(options: {
  baseUrl: string;
  store: Pick<TmallStore, "shopName">;
  snapshotDate: string;
  evidence: MasterFileEvidence;
  request?: typeof fetch;
}) {
  const request = options.request ?? fetch;
  const bytes = await readFile(options.evidence.filePath);
  const form = new FormData();
  form.append("source", "tmall_product_master");
  form.append("platform", "天猫");
  form.append("shop_name", options.store.shopName);
  form.append("snapshot_date", options.snapshotDate);
  form.append("note", "n8n 千牛出售中全部商品自动导出后导入");
  form.append("file", new File([bytes], options.evidence.fileName, {
    type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  }));

  const response = await request(`${options.baseUrl}/api/netshop/import`, {
    method: "POST",
    body: form,
    signal: AbortSignal.timeout(120_000),
  });
  const payload = await response.json().catch(() => null) as MasterImportPayload | null;
  const batch = payload?.batch;
  const importStatus = payload?.status === "imported" || payload?.status === "duplicate" ? payload.status : null;
  const expectedStatus = importStatus === "imported" ? 201 : importStatus === "duplicate" ? 200 : 0;
  const verification = payload?.verification;
  if (response.status !== expectedStatus || !payload?.ok || importStatus === null
    || !batch?.id || batch.source !== "tmall_product_master" || batch.dataset !== "product_master"
    || batch.platform !== "天猫" || batch.shopName !== options.store.shopName || batch.snapshotDate !== options.snapshotDate
    || batch.status !== "completed" || batch.rowCount !== options.evidence.rowCount
    || !hasExactTmallImportVerification(verification, {
      status: importStatus,
      rowCount: options.evidence.rowCount,
      dataset: "product_master",
      platform: "天猫",
      shopName: options.store.shopName,
    })) {
    throw new Error(payload?.message ?? `天猫货品主数据导入或落库回查失败（HTTP ${response.status}）`);
  }
  return {
    status: importStatus,
    batchId: batch.id,
    rowCount: batch.rowCount,
    warningCount: Number(batch.warningCount ?? 0),
    warnings: (payload.warnings ?? []).map((warning) => ({
      code: String(warning.code ?? "UNKNOWN"),
      message: String(warning.message ?? ""),
    })),
  } as const;
}

async function frameText(frame: Frame) {
  return await frame.locator("body").innerText({ timeout: 5_000 }).catch(() => "");
}

async function combinedPageText(page: Page) {
  const texts = await Promise.all(page.frames().map((frame) => frameText(frame)));
  return texts.map((text) => text.slice(0, 30_000)).join("\n");
}

async function textCandidates(page: Page, labels: readonly string[], scopeFrame?: Frame, scopeLocator?: Locator) {
  const candidates: TextCandidate[] = [];
  for (const frame of scopeFrame ? [scopeFrame] : page.frames()) {
    for (const label of labels) {
      const matches = scopeLocator && frame === scopeFrame
        ? scopeLocator.getByText(label, { exact: true })
        : frame.getByText(label, { exact: true });
      const count = Math.min(await matches.count().catch(() => 0), 20);
      for (let index = 0; index < count; index += 1) {
        const locator = matches.nth(index);
        if (!await locator.isVisible().catch(() => false)) continue;
        const detail = await locator.evaluate((element) => {
          const rect = element.getBoundingClientRect();
          const tag = element.tagName.toLowerCase();
          const role = element.getAttribute("role") ?? "";
          return { tag, role, left: Math.round(rect.left), top: Math.round(rect.top), width: Math.round(rect.width), height: Math.round(rect.height) };
        }).catch(() => null);
        if (!detail || detail.width < 2 || detail.height < 2) continue;
        const score = ["button", "a"].includes(detail.tag) ? 10 : ["button", "menuitem", "link"].includes(detail.role) ? 8 : 1;
        candidates.push({
          frame,
          locator,
          score,
          signature: `${frame.url()}|${label}|${detail.left}|${detail.top}|${detail.width}|${detail.height}`,
          top: detail.top,
        });
      }
    }
  }
  const unique = new Map<string, TextCandidate>();
  for (const candidate of candidates) {
    const previous = unique.get(candidate.signature);
    if (!previous || candidate.score > previous.score) unique.set(candidate.signature, candidate);
  }
  return [...unique.values()].sort((left, right) => right.score - left.score);
}

async function clickText(page: Page, labels: readonly string[], optional = false, scopeFrame?: Frame, scopeLocator?: Locator) {
  for (const label of labels) {
    const candidates = await textCandidates(page, [label], scopeFrame, scopeLocator);
    if (candidates.length === 0) continue;
    const best = candidates[0]!;
    if (candidates.length > 1 && candidates[1]!.score === best.score && candidates[1]!.signature !== best.signature) {
      throw new Error(`页面存在多个同等候选“${label}”，为防止误点已停止`);
    }
    await best.locator.click({ timeout: 10_000 });
    return true;
  }
  if (optional) return false;
  throw new Error(`页面未找到可点击的“${labels.join("/ ")}”`);
}

async function waitUntil(timeoutMs: number, probe: () => Promise<boolean>, errorMessage: string, intervalMs = 1_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await probe()) return;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error(errorMessage);
}

async function chatInputCandidates(page: Page) {
  const candidates: Array<TextCandidate & { frame: Frame }> = [];
  for (const frame of page.frames()) {
    const inputs = frame.locator('textarea,input[type="text"],input:not([type]),[contenteditable="true"]');
    const count = Math.min(await inputs.count().catch(() => 0), 30);
    for (let index = 0; index < count; index += 1) {
      const locator = inputs.nth(index);
      if (!await locator.isVisible().catch(() => false)) continue;
      const detail = await locator.evaluate((element) => {
        const rect = element.getBoundingClientRect();
        const attributes = [element.getAttribute("placeholder"), element.getAttribute("aria-label"), element.getAttribute("title")].filter(Boolean).join(" ");
        const nearby = (element.closest('[role="dialog"],aside,section,form')?.textContent ?? element.parentElement?.textContent ?? "").slice(0, 2_000);
        const viewportWidth = element.ownerDocument.defaultView?.innerWidth ?? 0;
        return {
          tag: element.tagName.toLowerCase(),
          left: Math.round(rect.left),
          top: Math.round(rect.top),
          width: Math.round(rect.width),
          height: Math.round(rect.height),
          attributes,
          nearby,
          viewportWidth,
          contentEditable: element.getAttribute("contenteditable") === "true",
        };
      }).catch(() => null);
      if (!detail || detail.width < 80 || detail.height < 20) continue;
      let score = 0;
      if (detail.tag === "textarea") score += 5;
      if (detail.contentEditable) score += 5;
      if (/输入|消息|提问|问问|chat/i.test(detail.attributes)) score += 6;
      if (/新会话|执行结果|商品搜索|商品巡检|商品上架|商品下架/.test(detail.nearby)) score += 6;
      if (detail.viewportWidth > 0 && detail.left > detail.viewportWidth * 0.5) score += 4;
      if (/商品标题|商品ID|商家编码|搜索/.test(detail.attributes)) score -= 12;
      candidates.push({
        frame,
        locator,
        score,
        signature: `${frame.url()}|${detail.left}|${detail.top}|${detail.width}|${detail.height}`,
      });
    }
  }
  candidates.sort((left, right) => right.score - left.score);
  return candidates;
}

async function maybeFindChatInput(page: Page) {
  const candidates = await chatInputCandidates(page);
  if (!candidates[0] || candidates[0].score < 5) return null;
  if (candidates[1] && candidates[1].score === candidates[0].score && candidates[1].signature !== candidates[0].signature) {
    throw new Error("检测到多个同等聊天输入框，为防止把指令填入商品搜索框已停止");
  }
  return candidates[0];
}

async function findChatInput(page: Page) {
  const input = await maybeFindChatInput(page);
  if (!input) throw new Error("未找到右侧千牛聊天输入框");
  return input;
}

async function positionedDetail(locator: Locator) {
  return await locator.evaluate((element): PositionedUiElement => {
    const rect = element.getBoundingClientRect();
    const view = element.ownerDocument.defaultView;
    const style = view?.getComputedStyle(element);
    return {
      text: element.textContent ?? "",
      attributes: [
        element.getAttribute("aria-label"),
        element.getAttribute("title"),
        element.getAttribute("class"),
        element.getAttribute("id"),
        element.getAttribute("name"),
        element.getAttribute("data-title"),
        element.getAttribute("data-tip"),
        element.getAttribute("data-tooltip"),
        ...Array.from(element.querySelectorAll('[aria-label],[title],img[alt],img[title]')).slice(0, 8).flatMap((child) => [
          child.getAttribute("aria-label"),
          child.getAttribute("title"),
          child.getAttribute("alt"),
        ]),
      ].filter(Boolean).join(" "),
      tag: element.tagName.toLowerCase(),
      role: element.getAttribute("role") ?? "",
      left: Math.round(rect.left),
      top: Math.round(rect.top),
      width: Math.round(rect.width),
      height: Math.round(rect.height),
      viewportWidth: view?.innerWidth ?? 0,
      viewportHeight: view?.innerHeight ?? 0,
      position: style?.position ?? "",
      cursor: style?.cursor ?? "",
    };
  }).catch(() => null);
}

async function importantNoticeCandidates(page: Page) {
  const candidates: Array<TextCandidate & { detail: PositionedUiElement; actionScope?: Locator }> = [];
  for (const frame of page.frames()) {
    const sources = [
      frame.getByText(/重要通知|重要消息|商品巡检|发货异常提醒|渠道活动快速报名/),
      frame.locator('[class*="notify_body" i],[class*="notify-body" i]'),
      frame.locator('[role="tooltip"][class*="next-balloon" i][class*="closable" i]'),
    ];
    for (const matches of sources) {
      const count = Math.min(await matches.count().catch(() => 0), 30);
      for (let index = 0; index < count; index += 1) {
        const locator = matches.nth(index);
        if (!await locator.isVisible().catch(() => false)) continue;
        const detail = await positionedDetail(locator);
        if (!detail) continue;
        const container = locator.locator(
          "xpath=ancestor-or-self::*[.//*[self::button or self::a or @role='button' or @aria-label or @title or contains(translate(@class, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'close') or normalize-space(.)='×' or normalize-space(.)='✕']][1]",
        );
        const actionScope = await container.count().catch(() => 0) > 0 ? container : undefined;
        const contextText = actionScope
          ? await actionScope.innerText({ timeout: 2_000 }).catch(() => detail.text)
          : detail.text;
        const score = scoreTmallBlockingNoticeCandidate(detail, contextText.slice(0, 3_000));
        if (score < 0) continue;
        candidates.push({
          frame,
          locator,
          score,
          signature: `${frame.url()}|${detail.left}|${detail.top}|${detail.width}|${detail.height}|${detail.text.replace(/\s+/g, "").slice(0, 120)}`,
          detail,
          actionScope,
        });
      }
    }
  }
  const unique = new Map<string, typeof candidates[number]>();
  for (const candidate of candidates.sort((left, right) => right.score - left.score)) {
    if (!unique.has(candidate.signature)) unique.set(candidate.signature, candidate);
  }
  return [...unique.values()];
}

async function receivesPointerAtCenter(locator: Locator) {
  return await locator.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return false;
    const target = element.ownerDocument.elementFromPoint(
      rect.left + rect.width / 2,
      rect.top + rect.height / 2,
    );
    return Boolean(target && (target === element || element.contains(target)));
  }).catch(() => false);
}

export async function dismissImportantNotice(page: Page) {
  const initialDeadline = Date.now() + 4_000;
  let dismissedCount = 0;
  let pointerInterceptionRetries = 0;
  while (dismissedCount < 4) {
    const notices = await importantNoticeCandidates(page);
    if (notices.length === 0) {
      if (dismissedCount > 0) return "dismissed" as const;
      if (Date.now() >= initialDeadline) return "not_present" as const;
      await new Promise((resolve) => setTimeout(resolve, 400));
      continue;
    }

    const notice = notices[0]!;
    const actions = notice.actionScope
      ? notice.actionScope.locator(tmallNoticeActionSelector)
      : notice.frame.locator(tmallNoticeActionSelector);
    const count = Math.min(await actions.count().catch(() => 0), 120);
    const candidates: Array<{
      locator: Locator;
      score: number;
      signature: string;
      detail: PositionedUiElement;
      explicitDismiss: boolean;
    }> = [];
    for (let index = 0; index < count; index += 1) {
      const locator = actions.nth(index);
      if (!await locator.isVisible().catch(() => false)) continue;
      const detail = await positionedDetail(locator);
      if (!detail) continue;
      const explicitDismiss = isExplicitTmallNoticeDismissAction(detail);
      const score = scoreImportantNoticeCloseCandidate(detail, notice.detail);
      if (score < 0
        || isTmallClosableOverlayNotice(notice.detail) && !explicitDismiss
        || !await receivesPointerAtCenter(locator)) continue;
      candidates.push({
        locator,
        score,
        signature: `${detail.left}|${detail.top}|${detail.width}|${detail.height}|${detail.attributes}`,
        detail,
        explicitDismiss,
      });
    }
    candidates.sort(compareTmallNoticeActionCandidates);
    const distinctCandidates: typeof candidates = [];
    for (const candidate of candidates) {
      if (!distinctCandidates.some((existing) => sameTmallNoticeActionTarget(existing.detail, candidate.detail))) {
        distinctCandidates.push(candidate);
      }
    }
    if (!distinctCandidates[0]) throw new Error("检测到右下角通知，但未找到当前可点击且安全的“忽略/关闭”按钮");
    if (shouldRejectEqualTmallNoticeActions(distinctCandidates[0], distinctCandidates[1])) {
      throw new Error("右下角通知存在多个同等“忽略/关闭”候选，为防止误点已停止");
    }
    const noticeSignaturesBeforeClick = new Set(notices.map((candidate) => candidate.signature));
    try {
      await distinctCandidates[0].locator.click({ timeout: 10_000 });
      pointerInterceptionRetries = 0;
    } catch (error) {
      if (!isTmallNoticePointerInterceptionError(error) || pointerInterceptionRetries >= 2) throw error;
      pointerInterceptionRetries += 1;
      // Some QianNiu balloons are created only after the pointer moves toward an
      // underlying close icon. Never force-click through them: wait for the new
      // top layer to become inspectable, then rebuild the safe candidate set.
      await new Promise((resolve) => setTimeout(resolve, 250));
      continue;
    }
    await waitUntil(
      10_000,
      async () => {
        const currentSignatures = new Set((await importantNoticeCandidates(page)).map((candidate) => candidate.signature));
        return [...noticeSignaturesBeforeClick].some((signature) => !currentSignatures.has(signature));
      },
      "右下角通知点击“忽略/关闭”后仍然可见",
    );
    dismissedCount += 1;
  }

  if ((await importantNoticeCandidates(page)).length > 0) {
    throw new Error("右下角连续通知超过 4 个安全处理上限，已停止");
  }
  return "dismissed" as const;
}

async function productManagerCandidates(page: Page) {
  const candidates: Array<TextCandidate & { detail: PositionedUiElement }> = [];
  for (const frame of page.frames()) {
    const matches = frame.locator([
      `:text-is("${TMALL_PRODUCT_MANAGER_LABEL}")`,
      `button:has-text("${TMALL_PRODUCT_MANAGER_LABEL}")`,
      `a:has-text("${TMALL_PRODUCT_MANAGER_LABEL}")`,
      `[role="button"]:has-text("${TMALL_PRODUCT_MANAGER_LABEL}")`,
      `[aria-label*="${TMALL_PRODUCT_MANAGER_LABEL}"]`,
      `[title*="${TMALL_PRODUCT_MANAGER_LABEL}"]`,
      `[data-title*="${TMALL_PRODUCT_MANAGER_LABEL}"]`,
      `[data-tip*="${TMALL_PRODUCT_MANAGER_LABEL}"]`,
      `[data-tooltip*="${TMALL_PRODUCT_MANAGER_LABEL}"]`,
      `img[alt*="${TMALL_PRODUCT_MANAGER_LABEL}"]`,
      `img[title*="${TMALL_PRODUCT_MANAGER_LABEL}"]`,
      '[class*="product-manager" i]',
      '[class*="productmanager" i]',
      '[class*="product-assistant" i]',
    ].join(","));
    const count = Math.min(await matches.count().catch(() => 0), 50);
    for (let index = 0; index < count; index += 1) {
      const locator = matches.nth(index);
      if (!await locator.isVisible().catch(() => false)) continue;
      const detail = await positionedDetail(locator);
      if (!detail) continue;
      const score = scoreProductManagerCandidate(detail);
      if (score < 0) continue;
      candidates.push({
        frame,
        locator,
        score,
        signature: `${frame.url()}|${detail.left}|${detail.top}|${detail.width}|${detail.height}`,
        detail,
      });
    }
  }
  return candidates.sort((left, right) => right.score - left.score);
}

async function productManagerFloatingCandidates(page: Page) {
  const candidates: Array<TextCandidate & { detail: PositionedUiElement }> = [];
  for (const frame of page.frames()) {
    const elements = frame.locator("body *");
    const raw = await elements.evaluateAll((items) => {
      const results: Array<{ index: number; detail: PositionedUiElement }> = [];
      for (let index = 0; index < items.length; index += 1) {
        const element = items[index]!;
        const view = element.ownerDocument.defaultView;
        if (!view) continue;
        const rect = element.getBoundingClientRect();
        if (rect.width < 8 || rect.height < 8 || rect.right < view.innerWidth * 0.9 || rect.top < view.innerHeight * 0.35) continue;
        const style = view.getComputedStyle(element);
        if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity || "1") <= 0) continue;
        let positioned: Element | null = element;
        let position = style.position;
        for (let depth = 0; depth < 5 && !["fixed", "sticky"].includes(position); depth += 1) {
          positioned = positioned.parentElement;
          if (!positioned) break;
          position = view.getComputedStyle(positioned).position;
        }
        if (!["fixed", "sticky"].includes(position)) continue;
        const attributes = [
          element.getAttribute("aria-label"),
          element.getAttribute("title"),
          element.getAttribute("class"),
          element.getAttribute("id"),
          element.getAttribute("name"),
          element.getAttribute("data-title"),
          element.getAttribute("data-tip"),
          element.getAttribute("data-tooltip"),
          ...Array.from(element.querySelectorAll('[aria-label],[title],img[alt],img[title]')).slice(0, 8).flatMap((child) => [
            child.getAttribute("aria-label"),
            child.getAttribute("title"),
            child.getAttribute("alt"),
          ]),
        ].filter(Boolean).join(" ");
        results.push({
          index,
          detail: {
            text: element.textContent ?? "",
            attributes,
            tag: element.tagName.toLowerCase(),
            role: element.getAttribute("role") ?? "",
            left: Math.round(rect.left),
            top: Math.round(rect.top),
            width: Math.round(rect.width),
            height: Math.round(rect.height),
            viewportWidth: view.innerWidth,
            viewportHeight: view.innerHeight,
            position: positioned === element ? position : `ancestor-${position}`,
            cursor: style.cursor,
          },
        });
        if (results.length >= 50) break;
      }
      return results;
    }).catch(() => []);
    for (const item of raw) {
      const score = scoreProductManagerFloatingCandidate(item.detail);
      if (score < 0) continue;
      candidates.push({
        frame,
        locator: elements.nth(item.index),
        score,
        signature: `${frame.url()}|${item.detail.left}|${item.detail.top}|${item.detail.width}|${item.detail.height}|${item.detail.attributes}`,
        detail: item.detail,
      });
    }
  }
  const unique = new Map<string, TextCandidate & { detail: PositionedUiElement }>();
  for (const candidate of candidates.sort((left, right) => right.score - left.score)) {
    const key = `${candidate.frame.url()}|${productManagerFloatingClusterKey(candidate.detail)}`;
    if (!unique.has(key)) unique.set(key, candidate);
  }
  return [...unique.values()];
}

async function openProductManagerChat(page: Page) {
  const existing = await maybeFindChatInput(page);
  if (existing) return { input: existing, entryMode: "product_manager_already_open" as const };

  let entryMode: "product_manager_opened" | "product_manager_floating_icon" = "product_manager_opened";
  let candidates = await productManagerCandidates(page);
  if (!candidates[0]) {
    entryMode = "product_manager_floating_icon";
    candidates = await productManagerFloatingCandidates(page);
  }
  if (!candidates[0]) throw new Error("未找到右下角“商品管家”入口（包括唯一无标签悬浮图标）");
  if (entryMode === "product_manager_floating_icon" && candidates.length > 1) {
    throw new Error("右下角存在多个无标签悬浮图标，无法唯一确认“商品管家”，为防止误点已停止");
  }
  if (candidates[1] && candidates[1].score === candidates[0].score && candidates[1].signature !== candidates[0].signature) {
    throw new Error("右下角存在多个同等“商品管家”入口，为防止误点已停止");
  }
  await candidates[0].locator.click({ timeout: 10_000 });
  let input: Awaited<ReturnType<typeof maybeFindChatInput>> = null;
  await waitUntil(productManagerChatOpenTimeoutMs, async () => {
    input = await maybeFindChatInput(page);
    return input !== null;
  }, "点击右下角“商品管家”后未出现右侧聊天输入框", 500);
  return { input: input!, entryMode };
}

async function chatOverlayScope(input: TextCandidate & { frame: Frame }) {
  const overlay = input.locator.locator(
    "xpath=ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' next-overlay-wrapper ')][1]",
  );
  return await overlay.count().catch(() => 0) > 0 ? overlay : null;
}

async function clickSendOrPressEnter(input: TextCandidate & { frame: Frame }) {
  const inputRect = await input.locator.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    return { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom };
  });
  const senderScope = input.locator.locator(
    "xpath=ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' ant-sender ')][1]",
  );
  const overlayScope = await chatOverlayScope(input);
  const scope = await senderScope.count().catch(() => 0) > 0
    ? senderScope
    : overlayScope
      ? overlayScope
      : null;
  if (!scope) {
    await input.locator.press("Enter", { timeout: 10_000 });
    return;
  }
  const buttons = scope.locator('button,[role="button"]');
  const count = Math.min(await buttons.count().catch(() => 0), 80);
  const candidates: Array<{ locator: Locator; score: number; signature: string }> = [];
  for (let index = 0; index < count; index += 1) {
    const locator = buttons.nth(index);
    if (!await locator.isVisible().catch(() => false)) continue;
    const detail = await locator.evaluate((element) => {
      const rect = element.getBoundingClientRect();
      return {
        left: rect.left,
        top: rect.top,
        width: rect.width,
        height: rect.height,
        label: [
          element.textContent,
          element.getAttribute("aria-label"),
          element.getAttribute("title"),
          element.getAttribute("class"),
        ].filter(Boolean).join(" "),
      };
    }).catch(() => null);
    if (!detail) continue;
    const score = scoreChatSendCandidate(detail, inputRect);
    if (score > 0) candidates.push({ locator, score, signature: `${Math.round(detail.left)}|${Math.round(detail.top)}` });
  }
  candidates.sort((left, right) => right.score - left.score);
  if (candidates[0] && (!candidates[1] || candidates[0].score > candidates[1].score || candidates[0].signature === candidates[1].signature)) {
    try {
      await candidates[0].locator.click({ timeout: 10_000 });
      return;
    } catch {
      // The prompt is still in the textarea when a covered send control rejects the click.
      // Pressing Enter is the Sender component's scoped, non-global fallback.
    }
  }
  await input.locator.press("Enter", { timeout: 10_000 });
}

async function downloadCandidates(page: Page, scopeFrame?: Frame, scopeLocator?: Locator) {
  const candidates: DownloadCandidate[] = [];
  for (const frame of scopeFrame ? [scopeFrame] : page.frames()) {
    const root = scopeLocator && frame === scopeFrame ? scopeLocator : frame.locator("body");
    const links = root.locator('a,button,[role="button"]').filter({ hasText: "前往下载" });
    const count = Math.min(await links.count().catch(() => 0), 20);
    for (let index = 0; index < count; index += 1) {
      const locator = links.nth(index);
      if (!await locator.isVisible().catch(() => false)) continue;
      const detail = await locator.evaluate((element) => {
        const rect = element.getBoundingClientRect();
        let contextText = "";
        let ancestor: Element | null = element;
        // 商品管家会把任务结果渲染在独立右侧栏；其成功卡片的可滚动父容器
        // 可能高于视口。继续向上查找，但仍限制文本长度，避免把整页历史误作结果卡片。
        for (let depth = 0; depth < 12 && ancestor; depth += 1, ancestor = ancestor.parentElement) {
          const text = (ancestor.textContent ?? "").replace(/\s+/g, " ").trim();
          if (
            /成功导出\s*\d+\s*个商品到Excel文件|所有任务已完成/.test(text)
            && text.length <= 3_000
          ) {
            contextText = text;
            break;
          }
        }
        return {
          href: element instanceof HTMLAnchorElement ? element.href : "",
          text: element.textContent?.replace(/\s+/g, "").trim() ?? "",
          left: Math.round(rect.left),
          top: Math.round(rect.top),
          width: Math.round(rect.width),
          height: Math.round(rect.height),
          contextText,
        };
      }).catch(() => null);
      if (!detail || !detail.text.includes("前往下载")) continue;
      const frameUrl = frame.url();
      candidates.push({
        frame,
        locator,
        signature: `${frameUrl}|${detail.href}|${detail.left}|${detail.top}|${detail.width}|${detail.height}`,
        frameUrl,
        href: detail.href,
        left: detail.left,
        top: detail.top,
        width: detail.width,
        height: detail.height,
        contextText: detail.contextText,
      });
    }
  }
  return [...new Map(candidates.map((candidate) => [candidate.signature, candidate])).values()];
}

export async function exportRecordDownloadCandidates(page: Page) {
  const candidates: ExportRecordDownloadCandidate[] = [];
  const recordPages = new Set<Page>();
  const pages = page.context().pages();
  for (let pageIndex = 0; pageIndex < pages.length; pageIndex += 1) {
    const candidatePage = pages[pageIndex]!;
    const frames = candidatePage.frames();
    for (let frameIndex = 0; frameIndex < frames.length; frameIndex += 1) {
      const frame = frames[frameIndex]!;
      const hasTaskTimeHeader = await frame.getByText("任务创建时间", { exact: true }).first().isVisible().catch(() => false);
      const hasFileNameHeader = await frame.getByText("文件名称", { exact: true }).first().isVisible().catch(() => false);
      if (!hasTaskTimeHeader || !hasFileNameHeader) continue;
      recordPages.add(candidatePage);
      const rows = frame.locator('tr,[role="row"]');
      const count = Math.min(await rows.count().catch(() => 0), 200);
      for (let index = 0; index < count; index += 1) {
        const row = rows.nth(index);
        if (!await row.isVisible().catch(() => false)) continue;
        const rowText = await row.innerText({ timeout: 2_000 }).catch(() => "");
        const taskTime = parseTmallShanghaiTaskTime(rowText);
        if (!taskTime) continue;
        const normalizedRowText = rowText.replace(/\s+/g, "");
        const stableRecordText = await row.evaluate((element) => {
          const fields = Array.from(element.children)
            .filter((child) => child.matches("td,[role='cell']"))
            .map((child) => (child.textContent ?? "").replace(/\s+/g, "").trim());
          return fields.length >= 8 ? fields.slice(0, -2).join("|") : "";
        }).catch(() => "");
        const status = parseTmallExportRecordStatus(normalizedRowText);
        const actions = row.locator('a,button,[role="button"]').filter({ hasText: /下载/ });
        const actionCount = Math.min(await actions.count().catch(() => 0), 10);
        const clickable: Array<{ locator: Locator; score: number; signature: string }> = [];
        for (let actionIndex = 0; actionIndex < actionCount; actionIndex += 1) {
          const locator = actions.nth(actionIndex);
          if (!await locator.isVisible().catch(() => false)) continue;
          const detail = await locator.evaluate((element) => ({
            text: (element.textContent ?? "").replace(/\s+/g, "").trim(),
            tag: element.tagName.toLowerCase(),
            role: element.getAttribute("role") ?? "",
            href: element instanceof HTMLAnchorElement ? element.href : "",
          })).catch(() => null);
          if (!detail || detail.text !== "下载") continue;
          const score = detail.tag === "a" && detail.href
            ? 20
            : detail.tag === "button"
              ? 15
              : detail.role === "button"
                ? 10
                : 0;
          if (score <= 0) continue;
          clickable.push({
            locator,
            score,
            signature: `${detail.tag}|${detail.role}|${detail.href}`,
          });
        }
        if (clickable.length === 0) {
          const rawDownloads = row.getByText("下载", { exact: true });
          const rawCount = Math.min(await rawDownloads.count().catch(() => 0), 10);
          for (let actionIndex = 0; actionIndex < rawCount; actionIndex += 1) {
            const locator = rawDownloads.nth(actionIndex);
            if (!await locator.isVisible().catch(() => false)) continue;
            const detail = await locator.evaluate((element) => ({
              text: (element.textContent ?? "").replace(/\s+/g, "").trim(),
              tag: element.tagName.toLowerCase(),
              role: element.getAttribute("role") ?? "",
            })).catch(() => null);
            if (!detail || detail.text !== "下载") continue;
            clickable.push({
              locator,
              score: 5,
              signature: `text|${detail.tag}|${detail.role}|${actionIndex}`,
            });
          }
        }
        clickable.sort((left, right) => right.score - left.score);
        const firstClickable = clickable[0];
        if (firstClickable && clickable[1] && clickable[1].score === firstClickable.score && clickable[1].signature !== firstClickable.signature) {
          throw new Error(`导出记录 ${taskTime.text} 存在多个同等下载操作，已停止`);
        }
        const signature = `${pageIndex}|${frameIndex}|${frame.url()}|${taskTime.text}|${index}`;
        candidates.push({
          locator: firstClickable?.locator,
          recordPage: candidatePage,
          signature,
          recordIdentity: createHash("sha256").update(stableRecordText || normalizedRowText).digest("hex"),
          taskCreatedAt: taskTime.text,
          status,
          downloadReady: Boolean(firstClickable),
        });
      }
    }
  }
  return {
    candidates: [...new Map(candidates.map((candidate) => [candidate.signature, candidate])).values()],
    recordPages: [...recordPages],
  };
}

async function withDeadline<T>(promise: Promise<T>, timeoutMs: number, message: string) {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      promise,
      new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new Error(message)), timeoutMs);
      }),
    ]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

export async function createTmallBrowserDownloadSession(page: Page) {
  const browser = page.context().browser();
  if (!browser) throw new Error("无法取得 Chrome 浏览器根会话，已停止商品管家下载");
  return browser.newBrowserCDPSession();
}

export function tmallBrowserDownloadOutcome(
  event: { guid: string; state?: string; filePath?: string },
  activeGuid: string | undefined,
) {
  if (!activeGuid || event.guid !== activeGuid) return null;
  if (event.state === "completed") {
    return { ok: true as const, guid: event.guid, filePath: event.filePath };
  }
  if (event.state === "canceled") {
    return { ok: false as const, guid: event.guid, error: "Chrome 已取消商品管家 XLSX 下载" };
  }
  return null;
}

export async function resolveTmallStagedDownloadPath(options: {
  stagingDirectory: string;
  guid: string;
  suggestedFilename: string;
  reportedFilePath?: string;
}) {
  const stagingDirectory = path.resolve(options.stagingDirectory);
  const reported = options.reportedFilePath?.trim();
  const candidates = [
    reported
      ? path.isAbsolute(reported)
        ? path.resolve(reported)
        : path.resolve(stagingDirectory, reported)
      : undefined,
    path.resolve(stagingDirectory, options.guid),
    path.resolve(stagingDirectory, options.suggestedFilename),
  ].filter((candidate): candidate is string => Boolean(candidate));
  for (const candidate of new Set(candidates)) {
    if (!inside(stagingDirectory, candidate)) continue;
    const candidateStat = await stat(candidate).catch(() => null);
    if (candidateStat?.isFile()) return candidate;
  }
  throw new Error("Chrome 下载结果未落入本轮受控暂存目录");
}

async function downloadWithBrowserEvents(options: {
  page: Page;
  locator: Locator;
  downloadDirectory: string;
  targetPath: string;
  expectedRunStartedAt: string;
}) {
  await mkdir(options.downloadDirectory, { recursive: true });
  const stagingDirectory = await mkdtemp(path.join(options.downloadDirectory, ".tmall-product-master-"));
  if (!inside(options.downloadDirectory, stagingDirectory)) throw new Error("浏览器下载暂存目录越过店铺独立目录");
  const session = await createTmallBrowserDownloadSession(options.page);
  let activeGuid: string | undefined;
  let resolveStarted!: (value: { guid: string; suggestedFilename: string }) => void;
  let resolveCompleted!: (value:
    | { ok: true; guid: string; filePath?: string }
    | { ok: false; guid: string; error: string }
  ) => void;
  const started = new Promise<{ guid: string; suggestedFilename: string }>((resolve) => {
    resolveStarted = resolve;
  });
  const completed = new Promise<
    | { ok: true; guid: string; filePath?: string }
    | { ok: false; guid: string; error: string }
  >((resolve) => {
    resolveCompleted = resolve;
  });
  session.on("Browser.downloadWillBegin", (event) => {
    if (activeGuid) return;
    activeGuid = event.guid;
    resolveStarted({ guid: event.guid, suggestedFilename: event.suggestedFilename });
  });
  session.on("Browser.downloadProgress", (event) => {
    const outcome = tmallBrowserDownloadOutcome(event, activeGuid);
    if (outcome) resolveCompleted(outcome);
  });
  try {
    await session.send("Browser.setDownloadBehavior", {
      behavior: "allowAndName",
      downloadPath: stagingDirectory,
      eventsEnabled: true,
    });
    await options.locator.click({ timeout: 15_000 });
    let selectedRecord: ExportRecordDownloadCandidate | undefined;
    let lastMatchedRecord: TmallExportRecordChoice | null = null;
    let nextRecordRefreshAt = Date.now() + exportRecordRefreshIntervalMs;
    const recordDeadline = Date.now() + exportRecordTimeoutMs;
    while (!selectedRecord && Date.now() < recordDeadline) {
      if (activeGuid) throw new Error("前往下载未经过导出记录时间和完成状态核验就触发了文件下载，已停止");
      const scan = await exportRecordDownloadCandidates(options.page);
      const records = scan.candidates;
      const matchedRecord = matchTmallExportRecordChoice(records, options.expectedRunStartedAt);
      if (matchedRecord) lastMatchedRecord = matchedRecord;
      const selectedSignature = chooseTmallExportRecordSignature(records, options.expectedRunStartedAt);
      const selected = selectedSignature
        ? records.find((candidate) => candidate.signature === selectedSignature)
        : undefined;
      if (selected?.locator) {
        await selected.locator.click({ timeout: 15_000 });
        selectedRecord = selected;
        break;
      }
      if (Date.now() >= nextRecordRefreshAt && scan.recordPages.length > 0) {
        for (const recordPage of scan.recordPages) {
          if (recordPage.isClosed()) continue;
          await recordPage.reload({ waitUntil: "domcontentloaded", timeout: 30_000 }).catch(() => undefined);
        }
        nextRecordRefreshAt = Date.now() + exportRecordRefreshIntervalMs;
      } else {
        await new Promise((resolve) => setTimeout(resolve, 500));
      }
    }
    if (!selectedRecord) {
      const observation = lastMatchedRecord
        ? `最近匹配任务 ${lastMatchedRecord.taskCreatedAt}，状态“${lastMatchedRecord.status}”，${lastMatchedRecord.downloadReady ? "已出现下载动作" : "尚未出现唯一下载动作"}`
        : "未出现创建时间属于本轮的任务";
      throw new Error(`导出记录页刷新等待三分钟后仍无法安全下载：${observation}`);
    }
    const start = await withDeadline(
      started,
      60_000,
      `点击导出记录 ${selectedRecord.taskCreatedAt} 的“下载”后 Chrome 未开始浏览器级下载`,
    );
    if (!isTmallProductWorkbookFilename(start.suggestedFilename)) {
      throw new Error(`千牛返回的货品文件不是安全的 .xlsx：${safeSegment(start.suggestedFilename)}`);
    }
    const finish = await withDeadline(completed, 120_000, "Chrome 商品管家 XLSX 下载未在两分钟内完成");
    if (!finish.ok) throw new Error(finish.error);
    const stagedPath = await resolveTmallStagedDownloadPath({
      stagingDirectory,
      guid: finish.guid,
      suggestedFilename: start.suggestedFilename,
      reportedFilePath: finish.filePath,
    });
    const targetExists = await stat(options.targetPath).then(() => true).catch(() => false);
    if (targetExists) throw new Error("本轮商品管家规范文件已存在，为防止覆盖已停止");
    await rename(stagedPath, options.targetPath);
    return { taskCreatedAt: selectedRecord.taskCreatedAt };
  } finally {
    await session.send("Browser.setDownloadBehavior", { behavior: "default" }).catch(() => undefined);
    await session.detach().catch(() => undefined);
    if (inside(options.downloadDirectory, stagingDirectory)) {
      await rm(stagingDirectory, { recursive: true, force: true }).catch(() => undefined);
    }
  }
}

export function isTmallSellerLoginUrl(url: string) {
  return /(?:loginmyseller|login)\.taobao\.com|passport|member\/login/i.test(url);
}

export function isTmallSellerBusinessUrl(url: string) {
  try {
    return new URL(url).hostname.toLowerCase() === "myseller.taobao.com";
  } catch {
    return false;
  }
}

export async function waitForTmallSellerSessionUrl(
  readUrl: () => string,
  wait: (milliseconds: number) => Promise<void> = async (milliseconds) => {
    await new Promise((resolve) => setTimeout(resolve, milliseconds));
  },
  timeoutMs = tmallSellerLoginRedirectGraceMs,
  pollIntervalMs = 500,
) {
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0 || !Number.isFinite(pollIntervalMs) || pollIntervalMs <= 0) {
    throw new Error("千牛登录跳转等待参数无效");
  }
  const maximumPolls = Math.max(1, Math.ceil(timeoutMs / pollIntervalMs));
  let url = readUrl();
  for (let poll = 0; isTmallSellerLoginUrl(url) && poll < maximumPolls; poll += 1) {
    await wait(pollIntervalMs);
    url = readUrl();
  }
  if (isTmallSellerLoginUrl(url)) {
    throw new Error("waiting_login：天猫店铺独立浏览器尚未登录千牛，请先在该浏览器完成登录后重试");
  }
  return url;
}

async function assertSellerIdentity(page: Page, store: TmallStore) {
  const url = page.url();
  const text = await combinedPageText(page);
  if (isTmallSellerLoginUrl(url) || /扫码登录|密码登录|账户登录/.test(text)) {
    throw new Error(`waiting_login：${store.shopName} 独立浏览器尚未登录千牛，请先在该浏览器完成登录后重试`);
  }
  const expected = store.shopName.replace(/^天猫-/, "");
  const shorter = expected.replace(/专卖店$/, "");
  if (!text.includes(expected) && !text.includes(shorter)) {
    throw new Error(`shop_identity_mismatch：页面未显示受控店铺“${expected}”，已停止导出`);
  }
  if (!text.includes("出售中")) throw new Error("千牛页面未进入“商品 > 出售中”列表");
}

export async function launchStoreChrome(store: TmallStore, interactiveLogin = false) {
  const launchTarget = resolveTmallBrowserLaunchTarget(
    store,
    process.env.CHROME_EXECUTABLE_PATH?.trim() || defaultChromeExecutable,
  );
  if (!path.isAbsolute(launchTarget.executablePath)) throw new Error("天猫 Chromium 可执行文件必须是绝对路径");
  await mkdir(store.browser.downloadDir, { recursive: true });
  await launchDedicatedChrome({
    executablePath: launchTarget.executablePath,
    profileDirectory: launchTarget.profileDirectory,
    profileName: launchTarget.profileName,
    port: store.browser.debugPort,
    startUrl: TMALL_SELLER_ON_SALE_URL,
    headless: false,
    visible: interactiveLogin,
    startMinimized: !interactiveLogin,
    keepWindowHidden: !interactiveLogin,
  });
  return {
    profileDirectory: launchTarget.profileDirectory,
    profileName: launchTarget.profileName,
    debugPort: store.browser.debugPort,
  };
}

export async function ensureTmallSellerSession(page: Page, store: TmallStore) {
  let authentication: "existing_session" | "saved_browser_credentials" | "windows_dpapi_credentials" = "existing_session";
  await waitUntil(tmallSellerLoginRedirectGraceMs, async () => {
    const text = await combinedPageText(page);
    return text.includes("出售中") || isTmallSellerLoginUrl(page.url())
      || /扫码登录|密码登录|账户登录|安全验证|人机验证|短信验证码|滑块验证/.test(text);
  }, "等待千牛登录状态加载超时");
  const initialText = await combinedPageText(page);
  const loginRequired = isTmallSellerLoginUrl(page.url())
    || /扫码登录|密码登录|账户登录|安全验证|人机验证|短信验证码|滑块验证/.test(initialText)
      && !initialText.includes("出售中");
  if (loginRequired) {
    if (store.loginMode !== "saved_browser_credentials" && store.loginMode !== "windows_dpapi_credentials") {
      if (isTmallSellerLoginUrl(page.url())) await waitForTmallSellerSessionUrl(() => page.url());
      else throw new Error(`waiting_login：${store.shopName} 独立浏览器尚未登录千牛，请先人工登录后重试`);
    } else {
      const login = store.loginMode === "windows_dpapi_credentials"
        ? await autoLoginTmallWithWindowsDpapiCredential(page, store.storeKey)
        : await autoLoginTmallWithSavedBrowserCredentials(page);
      const currentText = await combinedPageText(page);
      const stillRequiresLogin = isTmallSellerLoginUrl(page.url())
        || /扫码登录|密码登录|账户登录/.test(currentText) && !currentText.includes("出售中");
      if (!stillRequiresLogin && !login.submitted) {
        authentication = "existing_session";
      } else if (login.reason === "challenge_present") {
        throw new Error(`waiting_login：${store.shopName} 出现验证码或安全验证，需要人工处理`);
      } else if (!login.submitted) {
        const reason = login.reason === "saved_credentials_missing"
          ? "未检测到 Chromium 已保存并自动填充的账号密码"
          : login.reason === "login_control_ambiguous"
            ? "登录页出现多个提交按钮"
            : login.reason === "login_control_missing"
              ? "登录按钮缺失或不可用"
              : "登录表单尚未就绪";
        throw new Error(`waiting_login：${store.shopName} ${reason}，请人工登录并选择保存密码`);
      } else {
        authentication = store.loginMode;
      }
    }
  }
  try {
    await waitUntil(60_000, async () => {
      const text = await combinedPageText(page);
      if (authentication !== "existing_session") {
        const loginState = await inspectTmallLoginPageState(page);
        if (loginState.temporarilyLocked) {
          throw new Error(`waiting_login：${store.shopName} 登录操作受限或过于频繁，需要稍后人工检查`);
        }
        if (loginState.credentialRejected) {
          throw new Error(`waiting_login：${store.shopName} 本机加密凭据未被平台接受，请重新配置凭据并人工核验`);
        }
        if (loginState.challengePresent
          || /安全验证|人机验证|短信验证码|动态验证码|滑块验证/.test(text)) {
          throw new Error(`waiting_login：${store.shopName} 自动登录后出现验证码或安全验证，需要人工处理`);
        }
      }
      return text.includes("出售中");
    }, "等待千牛出售中页面加载超时");
  } catch (error) {
    if (error instanceof Error && error.message.startsWith("waiting_login")) throw error;
    if (authentication !== "existing_session") {
      throw new Error(`waiting_login：${store.shopName} 自动提交已保存密码后仍未进入千牛，可能需要验证码或安全验证`);
    }
    throw error;
  }
  await assertSellerIdentity(page, store);
  return { status: "authenticated" as const, authentication };
}

export async function ensureTmallStoreAuthenticatedSession(storeKey = "tmall-yijiu") {
  const store = await getTmallStore(storeKey);
  await launchStoreChrome(store);
  const browser = retainedTmallSessionBrowser?.isConnected()
    ? retainedTmallSessionBrowser
    : await connectPlaywrightBrowser(store.browser.debugPort);
  retainedTmallSessionBrowser = browser;
  const context = browser.contexts()[0];
  if (!context) throw new Error(`${store.shopName} 独立 Chromium 没有可用上下文`);
  const pages = context.pages();
  const page = pages.find((candidate) => isTmallSellerBusinessUrl(candidate.url()))
    ?? pages.find((candidate) => isTmallSellerLoginUrl(candidate.url()))
    ?? await context.newPage();
  page.setDefaultTimeout(15_000);
  if (!isTmallSellerBusinessUrl(page.url()) && !isTmallSellerLoginUrl(page.url())) {
    await page.goto(TMALL_SELLER_ON_SALE_URL, { waitUntil: "domcontentloaded", timeout: 60_000 });
  }
  const session = await ensureTmallSellerSession(page, store);
  return {
    ok: true,
    status: session.status,
    authentication: session.authentication,
    storeKey: store.storeKey,
    shopName: store.shopName,
  };
}

export async function launchTmallProductMasterLogin(storeKey = "tmall-yijiu") {
  const store = await getRegisteredTmallStore(storeKey);
  const browser = await launchStoreChrome(store, true);
  return {
    ok: true,
    status: "browser_ready" as const,
    storeKey: store.storeKey,
    shopName: store.shopName,
    targetUrl: TMALL_SELLER_ON_SALE_URL,
    debugPort: browser.debugPort,
    instruction: "请完成登录，并在 Chromium 提示时为当前独立店铺 Profile 保存密码；程序不会读取或保存明文凭证。",
  };
}

async function browserExport(options: {
  store: TmallStore;
  snapshotDate: string;
  runId: string;
  taskStartedAt: string;
  prompt: string;
  exportSubmittedAt?: string;
  resumeStage?: "export_submitted" | "export_confirmed";
  entryMode?: MasterExportAudit["entryMode"];
  noticeState?: MasterExportAudit["noticeState"];
  onStage: (stage: MasterExportAuditStage, patch?: Partial<MasterExportAudit>) => Promise<void>;
}) {
  await launchStoreChrome(options.store);
  const browser = await connectPlaywrightBrowser(options.store.browser.debugPort);
  const context = browser.contexts()[0];
  if (!context) throw new Error(`${options.store.shopName} 独立 Chromium 没有可用上下文`);
  const sellerPages = context.pages().filter((candidate) => isTmallSellerBusinessUrl(candidate.url()));
  let page = sellerPages[0];
  if (options.resumeStage && sellerPages.length > 1) {
    const observations = await Promise.all(sellerPages.map(async (candidate) => ({
      hasCompletedResult: hasCompletedTmallExportResult(await combinedPageText(candidate)),
    })));
    page = sellerPages[chooseTmallResumeSellerPageIndex(observations)];
  }
  if (!page) page = await context.newPage();
  page.setDefaultTimeout(15_000);
  try {
    if (!page.url().startsWith("https://myseller.taobao.com/home.htm/SellManage/on_sale")) {
      await page.goto(TMALL_SELLER_ON_SALE_URL, { waitUntil: "domcontentloaded", timeout: 60_000 });
    }
    await ensureTmallSellerSession(page, options.store);
    if (!options.resumeStage) await options.onStage("browser_ready");

    const currentNoticeState = await dismissImportantNotice(page);
    const productManager = await openProductManagerChat(page);
    const entryMode = options.entryMode ?? productManager.entryMode;
    const noticeState = currentNoticeState === "dismissed"
      ? currentNoticeState
      : options.noticeState ?? currentNoticeState;
    let input = productManager.input;
    let chatScope = await chatOverlayScope(input);
    const chatText = async () => chatScope
      ? await chatScope.innerText({ timeout: 5_000 }).catch(() => "")
      : await frameText(input.frame);
    let baselineAcceptedTaskCount = 0;
    let baselineCompletedDownloadCount = 0;
    let baselineConfirmationCount = 0;
    if (!options.resumeStage) {
      await clickText(page, ["新会话"], true, input.frame, chatScope ?? undefined);
      input = await findChatInput(page);
      chatScope = await chatOverlayScope(input);
      await new Promise((resolve) => setTimeout(resolve, 500));
      baselineAcceptedTaskCount = countAcceptedTmallExportTasks(await chatText());
      baselineCompletedDownloadCount = countTmallCompletedDownloadCards(
        await downloadCandidates(page, input.frame, chatScope ?? undefined),
      );
      baselineConfirmationCount = (await textCandidates(
        page,
        tmallExportConfirmationLabels,
        input.frame,
        chatScope ?? undefined,
      )).length;
      await input.locator.fill(options.prompt, { timeout: 10_000 });
      await options.onStage("export_submitting", { entryMode, noticeState });
      await clickSendOrPressEnter(input);
      options.exportSubmittedAt = new Date().toISOString();
      await options.onStage("export_submitted", {
        entryMode,
        noticeState,
        exportSubmittedAt: options.exportSubmittedAt,
      });
    }

    if (options.resumeStage !== "export_confirmed") {
      await waitForTmallExportAcknowledgement(async () => {
        const promptStillInInput = await input.locator.evaluate((element, prompt) => {
          const value = element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement
            ? element.value : element.textContent ?? "";
          return value.trim() === prompt;
        }, options.prompt);
        const diagnostic = summarizeTmallExportAcknowledgement(await chatText(), promptStillInInput);
        if (options.resumeStage === "export_submitted") {
          const completed = await findTmallResumedCompletedDownload(
            () => downloadCandidates(page!, input.frame, chatScope ?? undefined),
            () => downloadCandidates(page!),
          );
          if (completed) {
            return { ready: true, diagnostic: summarizeTmallExportAcknowledgement(completed.contextText, promptStillInInput) };
          }
        }
        const confirmations = await textCandidates(
          page!,
          tmallExportConfirmationLabels,
          input.frame,
          chatScope ?? undefined,
        );
        if (confirmations.length > baselineConfirmationCount) {
          const ordered = [...confirmations].sort((left, right) => (right.top ?? -1) - (left.top ?? -1) || right.score - left.score);
          const best = ordered[0]!;
          if (ordered[1] && ordered[1].top === best.top && ordered[1].score === best.score && ordered[1].signature !== best.signature) {
            throw new Error("商品管家存在多个同等导出确认候选，为防止误点已停止");
          }
          await best.locator.click({ timeout: 10_000 });
          return { ready: true, diagnostic };
        }
        const downloads = await downloadCandidates(page!, input.frame, chatScope ?? undefined);
        return {
          ready: Boolean(chooseFreshTmallDownloadSignature(downloads, baselineCompletedDownloadCount))
            || diagnostic.acceptedTaskCount > baselineAcceptedTaskCount,
          diagnostic,
        };
      });
      await options.onStage("export_confirmed", { entryMode, noticeState });
    }

    let newDownload: DownloadCandidate | null = null;
    await waitUntil(exportResultTimeoutMs, async () => {
      const current = await downloadCandidates(page!, input.frame, chatScope ?? undefined);
      const selectedSignature = options.resumeStage
        ? chooseLatestTmallDownloadSignature(current)
        : chooseFreshTmallDownloadSignature(current, baselineCompletedDownloadCount);
      let selected = selectedSignature
        ? current.find((candidate) => candidate.signature === selectedSignature)
        : undefined;
      // A resumed task must not send another chat prompt. The product-manager side panel
      // can preserve its completed card outside the current input overlay, so scan the
      // same page as a fallback and still require the eventual export-record time match.
      if (options.resumeStage && (!selected || !hasCompletedTmallExportResult(selected.contextText))) {
        const pageWide = (await downloadCandidates(page!))
          .filter((candidate) => hasCompletedTmallExportResult(candidate.contextText));
        const pageWideSignature = chooseTmallResumedDownloadSignature(pageWide);
        selected = pageWideSignature
          ? pageWide.find((candidate) => candidate.signature === pageWideSignature)
          : selected;
      }
      if (selected) {
        const text = await chatText();
        if (
          hasCompletedTmallExportResult(selected.contextText)
          || (!options.resumeStage
            && countTmallCompletedDownloadCards(current) > baselineCompletedDownloadCount
            && hasCompletedTmallExportResult(text))
        ) {
          newDownload = selected;
        }
      }
      return newDownload !== null;
    }, "等待千牛生成全部商品 Excel 超时", 2_000);

    const canonicalName = `${safeSegment(options.store.shopName)}-出售中全部商品-${options.snapshotDate}-${options.runId}.xlsx`;
    const targetPath = path.resolve(options.store.browser.downloadDir, canonicalName);
    if (!inside(options.store.browser.downloadDir, targetPath)) throw new Error("下载目标越过店铺独立目录");
    const downloadedFile = await downloadWithBrowserEvents({
      page,
      locator: newDownload!.locator,
      downloadDirectory: options.store.browser.downloadDir,
      targetPath,
      expectedRunStartedAt: options.exportSubmittedAt ?? options.taskStartedAt,
    });
    return {
      targetPath,
      entryMode,
      noticeState,
      exportTaskCreatedAt: downloadedFile.taskCreatedAt,
    };
  } finally {
    await browser.close().catch(() => undefined);
  }
}

export async function runTmallProductMasterStage(options: {
  storeKey?: string;
  baseUrl?: string;
  request?: typeof fetch;
  snapshotDate?: string;
  auditDirectory?: string;
} = {}): Promise<TmallProductMasterStageResult> {
  const store = await getTmallStore(options.storeKey ?? "tmall-yijiu");
  const baseUrl = normalizeLocalBaseUrl(options.baseUrl ?? process.env.OPERATIONS_SYSTEM_URL ?? "http://localhost:3000");
  const requestedSnapshotDate = options.snapshotDate ?? shanghaiToday();
  let snapshotDate = requestedSnapshotDate;
  const request = options.request ?? fetch;
  const runAuditDirectory = path.resolve(options.auditDirectory ?? artifactDirectory);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(requestedSnapshotDate)) throw new Error("天猫货品快照日期必须是 YYYY-MM-DD");

  await mkdir(runAuditDirectory, { recursive: true });
  const directMtopActivePath = path.join(directMtopArtifactDirectory, `active-${safeSegment(store.storeKey)}.json`);
  if (await stat(directMtopActivePath).then(() => true).catch(() => false)) {
    throw new Error("检测到 MTOP 直连 M 节点仍有活动清单；必须先人工核对原任务，不能切换为商品管家导出");
  }
  const existing = await readActiveAudit(store.storeKey, runAuditDirectory);
  let audit: MasterExportAudit | undefined = existing?.audit;
  let evidence: MasterFileEvidence | undefined;
  if (existing && audit) {
    if (audit.shopName !== store.shopName) {
      throw new Error(`存在未完成的天猫货品导出清单 ${existing.filePath}，其店铺与本轮不一致，已停止以避免跨店任务`);
    }
    if (!/^\d{4}-\d{2}-\d{2}$/.test(audit.snapshotDate)) {
      throw new Error(`未完成的天猫货品导出清单 ${existing.filePath} 快照日期无效`);
    }
    const recovery = decideTmallMasterAuditRecovery(requestedSnapshotDate, audit);
    if (recovery.action === "block") {
      throw new Error(`检测到跨日未决千牛导出任务（${audit.stage}，快照日 ${audit.snapshotDate}，清单 ${existing.filePath}），为防止重复发送已停止，请先人工核对右侧聊天任务`);
    }
    if (recovery.action === "discard") {
      await rm(existing.filePath, { force: true });
      audit = undefined;
    } else {
      snapshotDate = recovery.snapshotDate;
    }
    if (audit?.stage === "downloaded" && audit.file) {
      await assertEvidenceUnchanged(audit.file, store);
      evidence = await inspectTmallMasterFile(audit.file.filePath, store, snapshotDate);
      if (evidence.sha256 !== audit.file.sha256 || evidence.rowCount !== audit.file.rowCount) {
        throw new Error("恢复文件重新校验后与活动清单不一致");
      }
    } else if (audit?.stage === "export_submitting") {
      throw new Error(`检测到未决千牛导出任务（${audit.stage}，清单 ${existing.filePath}），为防止重复发送已停止，请先人工核对右侧聊天任务`);
    } else if (audit && isResumableTmallExportStage(audit.stage)) {
      // Resume the isolated current chat without starting a new conversation or sending the prompt again.
    } else if (audit) {
      await rm(existing.filePath, { force: true });
      audit = undefined;
    }
  }

  if (!audit) {
    const now = new Date().toISOString();
    audit = await writeActiveAudit({
      version: 1,
      runId: randomUUID(),
      storeKey: store.storeKey,
      shopName: store.shopName,
      snapshotDate,
      targetUrl: TMALL_SELLER_ON_SALE_URL,
      prompt: resolveTmallMasterExportPrompt(store.storeKey),
      startedAt: now,
      updatedAt: now,
      stage: "planned",
    }, runAuditDirectory);
  }
  let activeAudit: MasterExportAudit = audit;

  try {
    if (!evidence) {
      const downloaded = await browserExport({
        store,
        snapshotDate,
        runId: activeAudit.runId,
        taskStartedAt: activeAudit.startedAt,
        prompt: activeAudit.prompt,
        exportSubmittedAt: activeAudit.exportSubmittedAt,
        resumeStage: isResumableTmallExportStage(activeAudit.stage)
          ? activeAudit.stage as "export_submitted" | "export_confirmed"
          : undefined,
        entryMode: activeAudit.entryMode,
        noticeState: activeAudit.noticeState,
        onStage: async (stage, patch = {}) => {
          activeAudit = await writeActiveAudit({ ...activeAudit, ...patch, stage }, runAuditDirectory);
        },
      });
      evidence = await inspectTmallMasterFile(downloaded.targetPath, store, snapshotDate);
      activeAudit = await writeActiveAudit({
        ...activeAudit,
        stage: "downloaded",
        entryMode: downloaded.entryMode,
        noticeState: downloaded.noticeState,
        exportTaskCreatedAt: downloaded.exportTaskCreatedAt,
        file: evidence,
      }, runAuditDirectory);
    }

    const imported = await importTmallProductMasterFile({ baseUrl, store, snapshotDate, evidence, request });
    const completedAudit = { ...activeAudit };
    delete completedAudit.lastError;
    activeAudit = await writeActiveAudit({
      ...completedAudit,
      stage: "completed",
      importResult: {
        status: imported.status,
        batchId: imported.batchId,
        rowCount: imported.rowCount,
        warningCount: imported.warningCount,
      },
    }, runAuditDirectory);
    const finalAuditPath = path.join(runAuditDirectory, `run-${activeAudit.runId}.json`);
    await writeJsonAtomic(finalAuditPath, activeAudit);
    await rm(activeAuditPath(store.storeKey, runAuditDirectory), { force: true });
    return {
      ok: true,
      stage: "product_master",
      status: imported.status,
      storeKey: store.storeKey,
      shopName: store.shopName,
      snapshotDate,
      batchId: imported.batchId,
      rowCount: imported.rowCount,
      warningCount: imported.warningCount,
      auditPath: finalAuditPath,
      filePath: evidence.filePath,
    };
  } catch (error) {
    const lastError = safeError(error);
    await writeActiveAudit({ ...activeAudit, lastError }, runAuditDirectory).catch(() => undefined);
    if (["planned", "browser_ready"].includes(activeAudit.stage)) {
      await rm(activeAuditPath(store.storeKey, runAuditDirectory), { force: true }).catch(() => undefined);
    }
    throw error;
  }
}

async function main() {
  const args = process.argv.slice(2);
  const value = (name: string) => {
    const index = args.indexOf(name);
    return index >= 0 ? args[index + 1] : undefined;
  };
  const result = args.includes("--launch-only")
    ? await launchTmallProductMasterLogin(value("--store-key") ?? "tmall-yijiu")
    : await runTmallProductMasterStage({
        storeKey: value("--store-key"),
        baseUrl: value("--base-url"),
        snapshotDate: value("--snapshot-date"),
      });
  console.log(JSON.stringify(result));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  void main().catch((error: unknown) => {
    console.error(safeError(error));
    process.exitCode = 1;
  });
}
