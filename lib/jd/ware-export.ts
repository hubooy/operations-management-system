export type JdWareExportTaskStatus = "completed" | "pending" | "failed" | "unknown";

export type JdWareExportTask = {
  taskId: string;
  createdAt: string;
  status: JdWareExportTaskStatus;
  resultText: string | null;
  successRows: number | null;
  rowText: string;
};

function cleanLine(value: string) {
  return value.replace(/\u00a0/g, " ").replace(/\s+/g, " ").trim();
}

function taskStatus(lines: readonly string[]): JdWareExportTaskStatus {
  const text = lines.join(" ");
  if (/失败|异常|已取消/.test(text)) return "failed";
  if (/已完成/.test(text)) return "completed";
  if (/处理中|执行中|导出中|排队|创建中|等待/.test(text)) return "pending";
  return "unknown";
}

/**
 * Parses only rows from JD's export-record table.  Product-table rows are
 * intentionally ignored because they do not include an export-task status.
 */
export function parseJdWareExportTaskRows(rows: readonly string[]): JdWareExportTask[] {
  const tasks = new Map<string, JdWareExportTask>();

  for (const row of rows) {
    const lines = row.split(/\r?\n/).map(cleanLine).filter(Boolean);
    const createdAt = lines.find((line) => /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(line));
    const status = taskStatus(lines);
    if (!createdAt || status === "unknown") continue;

    const taskId = lines.find((line) => /^\d{6,}$/.test(line));
    if (!taskId) continue;

    const resultText = lines.find((line) => /^(成功|失败|已导出|导出失败)[：:]/.test(line)) ?? null;
    const successRowsMatch = resultText?.match(/成功[：:]\s*(\d+)/);
    const task: JdWareExportTask = {
      taskId,
      createdAt,
      status,
      resultText,
      successRows: successRowsMatch ? Number(successRowsMatch[1]) : null,
      rowText: lines.join(" | "),
    };

    const existing = tasks.get(task.taskId);
    if (!existing || existing.createdAt < task.createdAt) tasks.set(task.taskId, task);
  }

  return [...tasks.values()].sort((left, right) => right.createdAt.localeCompare(left.createdAt));
}

export function newestUnseenJdWareExportTask(
  tasks: readonly JdWareExportTask[],
  previouslySeenTaskIds: ReadonlySet<string>,
) {
  return unseenJdWareExportTasks(tasks, previouslySeenTaskIds)[0] ?? null;
}

export function unseenJdWareExportTasks(
  tasks: readonly JdWareExportTask[],
  previouslySeenTaskIds: ReadonlySet<string>,
) {
  return tasks.filter((task) => !previouslySeenTaskIds.has(task.taskId));
}

export function newestCompletedJdWareExportTask(tasks: readonly JdWareExportTask[]) {
  return tasks.find((task) => task.status === "completed") ?? null;
}

export type ExistingJdWareExportTaskSelection =
  | { kind: "none" }
  | { kind: "unowned_pending"; tasks: readonly JdWareExportTask[] }
  | { kind: "completed"; task: JdWareExportTask }
  | { kind: "ambiguous_pending"; tasks: readonly JdWareExportTask[] };

export type JdWareExportRecovery = {
  version: 1 | 2;
  baselineTaskIds: string[];
  taskId?: string;
  createdAt: string;
  storeKey?: string;
  shopId?: string;
  shopName?: string;
};

export type JdWareExportRecoverySelection =
  | { kind: "task"; task: JdWareExportTask }
  | { kind: "missing" }
  | { kind: "ambiguous"; tasks: readonly JdWareExportTask[] };

export type JdWareExportRecoveryAbandonDecision = { kind: "abandon" } | { kind: "keep"; reason: string };

export function assertJdWareExportRecoveryIdentity(
  recovery: JdWareExportRecovery,
  expected: { storeKey: string; shopId: string; shopName: string },
) {
  if (recovery.version !== 2 || recovery.storeKey !== expected.storeKey
    || recovery.shopId !== expected.shopId || recovery.shopName !== expected.shopName
    || !Array.isArray(recovery.baselineTaskIds) || recovery.baselineTaskIds.some((taskId) => !/^\d+$/.test(taskId))
    || (recovery.taskId !== undefined && !/^\d+$/.test(recovery.taskId))
    || !Number.isFinite(Date.parse(recovery.createdAt))) {
    throw new Error("京东 SKU 活动任务清单缺少当前受控店铺的完整身份，拒绝跨店接管。");
  }
  return recovery;
}

/** JD displays export-record timestamps in Shanghai time while recovery manifests use ISO UTC. */
export function isJdWareExportTaskCreatedNear(
  manifestCreatedAt: string,
  taskCreatedAt: string,
  windowMs = 2 * 60_000,
) {
  if (!Number.isFinite(Date.parse(manifestCreatedAt))) return false;
  const match = /^(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2}):(\d{2})$/.exec(taskCreatedAt);
  if (!match) return false;
  const taskUtc = Date.UTC(
    Number(match[1]),
    Number(match[2]) - 1,
    Number(match[3]),
    Number(match[4]) - 8,
    Number(match[5]),
    Number(match[6]),
  );
  return Math.abs(taskUtc - Date.parse(manifestCreatedAt)) <= windowMs;
}

/** Resolve only the task durably associated with an interrupted submission. */
export function selectRecoverableJdWareExportTask(
  tasks: readonly JdWareExportTask[],
  recovery: JdWareExportRecovery,
): JdWareExportRecoverySelection {
  const matches = recovery.taskId
    ? tasks.filter((task) => task.taskId === recovery.taskId)
    : tasks.filter((task) => !recovery.baselineTaskIds.includes(task.taskId)
      && isJdWareExportTaskCreatedNear(recovery.createdAt, task.createdAt));
  if (matches.length > 1) return { kind: "ambiguous", tasks: matches };
  return matches[0] ? { kind: "task", task: matches[0] } : { kind: "missing" };
}

/** A baseline-only submission may be retired only after JD had ample time to publish a uniquely attributable row. */
export function decideJdWareExportBaselineRecoveryAbandonment(
  recovery: JdWareExportRecovery,
  tasks: readonly JdWareExportTask[],
  snapshotConfirmed: boolean,
  nowMs = Date.now(),
): JdWareExportRecoveryAbandonDecision {
  if (recovery.taskId) return { kind: "keep", reason: "task_id_present" };
  const createdAt = Date.parse(recovery.createdAt);
  if (!Number.isFinite(createdAt)) return { kind: "keep", reason: "invalid_created_at" };
  if (nowMs - createdAt < 30 * 60_000) return { kind: "keep", reason: "grace_period" };
  if (!snapshotConfirmed) return { kind: "keep", reason: "snapshot_unconfirmed" };
  const candidates = tasks.filter((task) => !recovery.baselineTaskIds.includes(task.taskId)
    && isJdWareExportTaskCreatedNear(recovery.createdAt, task.createdAt));
  if (candidates.length !== 0) return { kind: "keep", reason: candidates.length === 1 ? "nearby_candidate" : "ambiguous_candidates" };
  return { kind: "abandon" };
}

/**
 * Selects an existing task without ever silently ignoring an in-progress one.
 * A pending task is the only safe continuation target after a prior process
 * timed out: it has a stable JD task id, while a completed task may be older.
 */
export function selectExistingJdWareExportTask(
  tasks: readonly JdWareExportTask[],
  reuseLatest: boolean,
): ExistingJdWareExportTaskSelection {
  const pending = tasks.filter((task) => task.status === "pending");
  if (pending.length > 1) return { kind: "ambiguous_pending", tasks: pending };
  // Without a persisted post-baseline manifest even one pending task may have
  // been created manually or by another interrupted process. Never adopt it.
  if (pending.length === 1) return { kind: "unowned_pending", tasks: pending };

  if (!reuseLatest) return { kind: "none" };
  const completed = newestCompletedJdWareExportTask(tasks);
  return completed ? { kind: "completed", task: completed } : { kind: "none" };
}
