import { mkdir, readFile, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { writeJsonAtomic } from "../lib/jackyun/json-file";
import type { TmallStore } from "../lib/netshop/tmall-store-registry";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const defaultStateDirectory = path.join(projectRoot, ".runtime", "tmall-product-master-cadence");
const productManagerAuditDirectory = path.join(projectRoot, "outputs", "tmall-product-master-export");
const pagewiseAuditDirectory = path.join(projectRoot, "outputs", "tmall-pagewise-product-master-export");
const directMtopAuditDirectory = path.join(projectRoot, "outputs", "tmall-direct-product-master-export");
const isoDatePattern = /^\d{4}-\d{2}-\d{2}$/;

export const tmallForceProductMasterHeader = "x-teruisi-tmall-force-product-master";

export type TmallProductMasterCadenceState = {
  version: 1;
  storeKey: string;
  intervalDays: number;
  lastSuccessDate: string;
  lastSnapshotDate: string;
  nextDueDate: string;
  updatedAt: string;
};

export type TmallProductMasterCadenceDecision = {
  configured: boolean;
  due: boolean;
  forced: boolean;
  reason: "daily_compatibility" | "forced" | "pending_audit" | "scheduled" | "not_due";
  operationDate: string;
  intervalDays: number;
  nextDueDate: string;
  lastSuccessDate: string | null;
  lastSnapshotDate: string | null;
};

function validIsoDate(value: unknown): value is string {
  if (typeof value !== "string" || !isoDatePattern.test(value)) return false;
  const date = new Date(`${value}T00:00:00.000Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

export function addIsoCalendarDays(value: string, days: number) {
  if (!validIsoDate(value) || !Number.isInteger(days) || days < 1) {
    throw new Error("天猫货品三日节奏日期或间隔无效");
  }
  const date = new Date(`${value}T00:00:00.000Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

export function shanghaiBusinessDate(now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

export function parseTmallForceProductMasterHeader(value: string | string[] | undefined) {
  if (value === undefined || value === "0") return false;
  if (value === "1") return true;
  throw new Error("天猫货品强制运行请求头无效");
}

function statePath(storeKey: string, stateDirectory = defaultStateDirectory) {
  if (!/^[a-z0-9][a-z0-9-]{0,63}$/.test(storeKey)) throw new Error("天猫货品节奏店铺键无效");
  return path.join(stateDirectory, `${storeKey}.json`);
}

export function validateTmallProductMasterCadenceState(
  value: unknown,
  store: Pick<TmallStore, "storeKey" | "productMasterCadence">,
): TmallProductMasterCadenceState {
  if (!value || typeof value !== "object") throw new Error("天猫货品节奏状态格式无效");
  const state = value as Partial<TmallProductMasterCadenceState>;
  const cadence = store.productMasterCadence;
  if (!cadence || state.version !== 1 || state.storeKey !== store.storeKey
    || state.intervalDays !== cadence.intervalDays
    || !validIsoDate(state.lastSuccessDate) || !validIsoDate(state.lastSnapshotDate)
    || !validIsoDate(state.nextDueDate) || typeof state.updatedAt !== "string"
    || Number.isNaN(Date.parse(state.updatedAt))) {
    throw new Error("天猫货品节奏状态与店铺配置不一致");
  }
  return state as TmallProductMasterCadenceState;
}

export async function loadTmallProductMasterCadenceState(
  store: Pick<TmallStore, "storeKey" | "productMasterCadence">,
  stateDirectory = defaultStateDirectory,
) {
  if (!store.productMasterCadence) return null;
  const file = statePath(store.storeKey, stateDirectory);
  const raw = await readFile(file, "utf8").catch((error: NodeJS.ErrnoException) => {
    if (error.code === "ENOENT") return null;
    throw error;
  });
  if (raw === null) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new Error("天猫货品节奏状态无法解析");
  }
  return validateTmallProductMasterCadenceState(parsed, store);
}

export async function hasPendingTmallProductMasterAudit(
  storeKey: string,
  auditDirectories = [productManagerAuditDirectory, pagewiseAuditDirectory, directMtopAuditDirectory],
) {
  const activeName = `active-${storeKey}.json`;
  for (const directory of auditDirectories) {
    const exists = await stat(path.join(directory, activeName)).then(() => true).catch((error: NodeJS.ErrnoException) => {
      if (error.code === "ENOENT") return false;
      throw error;
    });
    if (exists) return true;
  }
  return false;
}

export function decideTmallProductMasterCadence(input: {
  store: Pick<TmallStore, "storeKey" | "productMasterCadence">;
  operationDate: string;
  state: TmallProductMasterCadenceState | null;
  forced?: boolean;
  pendingAudit?: boolean;
}): TmallProductMasterCadenceDecision {
  if (!validIsoDate(input.operationDate)) throw new Error("天猫货品节奏业务日期无效");
  const cadence = input.store.productMasterCadence;
  if (!cadence) {
    return {
      configured: false,
      due: true,
      forced: Boolean(input.forced),
      reason: input.forced ? "forced" : "daily_compatibility",
      operationDate: input.operationDate,
      intervalDays: 1,
      nextDueDate: input.operationDate,
      lastSuccessDate: null,
      lastSnapshotDate: null,
    };
  }
  const nextDueDate = input.state?.nextDueDate ?? cadence.initialDueDate;
  const common = {
    configured: true,
    operationDate: input.operationDate,
    intervalDays: cadence.intervalDays,
    nextDueDate,
    lastSuccessDate: input.state?.lastSuccessDate ?? null,
    lastSnapshotDate: input.state?.lastSnapshotDate ?? null,
  };
  if (input.forced) return { ...common, due: true, forced: true, reason: "forced" };
  if (input.pendingAudit) return { ...common, due: true, forced: false, reason: "pending_audit" };
  if (input.operationDate >= nextDueDate) return { ...common, due: true, forced: false, reason: "scheduled" };
  return { ...common, due: false, forced: false, reason: "not_due" };
}

export async function getTmallProductMasterCadenceDecision(input: {
  store: TmallStore;
  forced?: boolean;
  now?: Date;
  stateDirectory?: string;
  auditDirectories?: string[];
}) {
  const operationDate = shanghaiBusinessDate(input.now);
  const [state, pendingAudit] = await Promise.all([
    loadTmallProductMasterCadenceState(input.store, input.stateDirectory),
    hasPendingTmallProductMasterAudit(input.store.storeKey, input.auditDirectories),
  ]);
  return decideTmallProductMasterCadence({
    store: input.store,
    operationDate,
    state,
    forced: input.forced,
    pendingAudit,
  });
}

export async function recordTmallProductMasterCadenceSuccess(input: {
  store: TmallStore;
  decision: TmallProductMasterCadenceDecision;
  snapshotDate: string;
  stateDirectory?: string;
  updatedAt?: string;
}) {
  const cadence = input.store.productMasterCadence;
  if (!cadence) return null;
  if (!input.decision.due || !validIsoDate(input.snapshotDate)) {
    throw new Error("拒绝为未到期或快照日期无效的天猫货品任务推进节奏");
  }
  const updatedAt = input.updatedAt ?? new Date().toISOString();
  if (Number.isNaN(Date.parse(updatedAt))) throw new Error("天猫货品节奏更新时间无效");
  const state: TmallProductMasterCadenceState = {
    version: 1,
    storeKey: input.store.storeKey,
    intervalDays: cadence.intervalDays,
    lastSuccessDate: input.decision.operationDate,
    lastSnapshotDate: input.snapshotDate,
    nextDueDate: addIsoCalendarDays(input.decision.operationDate, cadence.intervalDays),
    updatedAt,
  };
  const directory = input.stateDirectory ?? defaultStateDirectory;
  await mkdir(directory, { recursive: true });
  await writeJsonAtomic(statePath(input.store.storeKey, directory), state);
  return state;
}

export async function migrateTmallProductMasterCadenceInterval(input: {
  store: TmallStore;
  expectedPreviousIntervalDays: number;
  expectedLastSuccessDate: string;
  stateDirectory?: string;
  updatedAt?: string;
}) {
  const cadence = input.store.productMasterCadence;
  if (!cadence || !Number.isInteger(input.expectedPreviousIntervalDays)
    || input.expectedPreviousIntervalDays < 1
    || input.expectedPreviousIntervalDays === cadence.intervalDays
    || !validIsoDate(input.expectedLastSuccessDate)) {
    throw new Error("天猫货品节奏迁移参数无效");
  }
  const directory = input.stateDirectory ?? defaultStateDirectory;
  const file = statePath(input.store.storeKey, directory);
  const raw = await readFile(file, "utf8");
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new Error("天猫货品节奏状态无法解析");
  }
  const previous = validateTmallProductMasterCadenceState(parsed, {
    storeKey: input.store.storeKey,
    productMasterCadence: {
      ...cadence,
      intervalDays: input.expectedPreviousIntervalDays,
    },
  });
  if (previous.lastSuccessDate !== input.expectedLastSuccessDate) {
    throw new Error("天猫货品节奏迁移的最后成功日期已变化");
  }
  const updatedAt = input.updatedAt ?? new Date().toISOString();
  if (Number.isNaN(Date.parse(updatedAt))) throw new Error("天猫货品节奏迁移更新时间无效");
  const migrated: TmallProductMasterCadenceState = {
    ...previous,
    intervalDays: cadence.intervalDays,
    nextDueDate: addIsoCalendarDays(previous.lastSuccessDate, cadence.intervalDays),
    updatedAt,
  };
  await writeJsonAtomic(file, migrated);
  return validateTmallProductMasterCadenceState(migrated, input.store);
}
