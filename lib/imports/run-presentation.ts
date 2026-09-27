export type RunTone = "success" | "warning" | "danger" | "pending" | "neutral";

export function importRunStatus(status: string, warnings = 0): { label: string; tone: RunTone; group: string } {
  if (["failed", "rejected", "error"].includes(status)) return { label: "失败", tone: "danger", group: "failed" };
  if (["processing", "running", "staging", "committing"].includes(status)) return { label: "进行中", tone: "pending", group: "running" };
  if (["pending", "queued", "created"].includes(status)) return { label: "待处理", tone: "neutral", group: "pending" };
  if (["cancelled", "canceled", "expired"].includes(status)) return { label: status === "expired" ? "已过期" : "已取消", tone: "neutral", group: "other" };
  if (status === "duplicate") return { label: "重复跳过", tone: "neutral", group: "duplicate" };
  if (status === "partial") return { label: "部分完成", tone: "warning", group: "warning" };
  if (["imported", "completed", "success", "done"].includes(status)) {
    return warnings > 0 ? { label: `完成 · ${warnings} 条告警`, tone: "warning", group: "warning" }
      : { label: "已完成", tone: "success", group: "completed" };
  }
  return { label: "未知状态", tone: "neutral", group: "other" };
}

export function importRunDuration(createdAt: string, completedAt?: string | null) {
  if (!completedAt) return "—";
  const ms = Date.parse(completedAt) - Date.parse(createdAt);
  if (!Number.isFinite(ms) || ms < 0) return "—";
  if (ms < 1000) return "不足 1 秒";
  const seconds = Math.floor(ms / 1000);
  return seconds < 60 ? `${seconds} 秒` : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}

export function describeSchedule(expression: string) {
  const daily = /^(\d{1,2}) (\d{1,2}) \* \* \*$/.exec(expression);
  if (daily && Number(daily[1]) < 60 && Number(daily[2]) < 24) return `每天 ${daily[2].padStart(2, "0")}:${daily[1].padStart(2, "0")}`;
  return expression;
}
