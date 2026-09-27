/** Passive projection of the already owner-bound promotion report detail. */
export const PROMOTION_PROFILE = "business-agent-screening-promotion-reference-v1";
const SCREENING_SCHEMA = "business-promotion-completed-screening-v1";
const roles = ["commerce", "promotion", "market_b2b", "independent_review", "report"] as const;
const names: Record<string, string> = { commerce: "店铺与商品", promotion: "推广与搜索", market_b2b: "市场与 B 端",
  independent_review: "独立复核", report: "报告整合", human_review: "人工复核" };
const statuses: Record<string, string> = { queued: "排队中", pending: "等待前序", running: "运行中", completed: "已完成",
  waiting_review: "待人工复核", failed: "失败", paused: "已暂停", cancelled: "已取消", rejected: "已拒绝", skipped: "跳过" };
type Obj = Record<string, unknown>;
function object(value: unknown): Obj { if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("词货报告详情结构无效。"); return value as Obj; }
function str(value: unknown, maximum = 10000): string { if (typeof value !== "string" || !value || [...value].length > maximum) throw new Error("词货报告详情文本无效。"); return value; }
function list(value: unknown, maximum: number): unknown[] { if (!Array.isArray(value) || value.length > maximum) throw new Error("词货报告详情列表无效。"); return value; }
export type PromotionView = { reportStatus: string; nodes: { key: string; label: string; status: string }[];
  scan: "verified" | "unverified"; coverageCount: number | null; limitations: string[];
  professionals: { role: string; label: string; summary: string }[]; humanReview: string };

export function allowPromotionReportDetailBytes(data: unknown, bytes: number, ok: boolean, maxBytes: number): boolean {
  if (!ok || !Number.isSafeInteger(bytes) || bytes <= 0 || bytes > maxBytes || bytes <= 2 * 1024 * 1024) return false;
  if (!data || typeof data !== "object" || Array.isArray(data)) return false;
  const snapshot = (data as Obj).snapshot;
  return Boolean(snapshot && typeof snapshot === "object" && !Array.isArray(snapshot)
    && (snapshot as Obj).schemaVersion === "business-report-v1"
    && (snapshot as Obj).executionProfile === PROMOTION_PROFILE);
}

export function projectPromotionReport(raw: unknown): PromotionView {
  const detail = object(raw), snapshot = object(detail.snapshot);
  if (snapshot.schemaVersion !== "business-report-v1" || snapshot.executionProfile !== PROMOTION_PROFILE)
    throw new Error("此详情不是词货专项报告。");
  const item = object(detail.item), workflow = object(detail.workflow);
  str(item.id, 160);
  const status = str(workflow.status, 40);
  if (!Object.hasOwn(statuses, status)) throw new Error("词货报告工作流状态无效。");
  const nodes = list(workflow.nodes, 8).map(value => {
    const node = object(value), key = str(node.key, 40), type = str(node.type, 30), state = str(node.status, 40);
    if (!Object.hasOwn(names, key) || type !== (key === "human_review" ? "human_review" : "agent")
      || !Object.hasOwn(statuses, state)) throw new Error("词货报告节点状态无效。");
    return { key, label: names[key], status: statuses[state] };
  });
  if (nodes.length !== 6 || new Set(nodes.map(node => node.key)).size !== 6) throw new Error("词货报告节点缺失或重复。");
  const human = nodes.find(node => node.key === "human_review")!;
  if (detail.screening === undefined) return { reportStatus: statuses[status], nodes,
    scan: "unverified", coverageCount: null, limitations: [], professionals: [], humanReview: human.status };
  const screening = object(detail.screening), disclosure = object(screening.candidateDisclosure);
  if (screening.schemaVersion !== SCREENING_SCHEMA || disclosure.fullCandidatesIncluded !== false
    || disclosure.crossPartitionAmountsAdditive !== false) throw new Error("词货筛查内容版本或候选口径无效。");
  const coverage = list(screening.coverage, 20000), proofs = object(screening.readProofs);
  if (Object.keys(proofs).sort().join(",") !== [...roles].sort().join(",")) throw new Error("词货五角色读取证明不完整。");
  for (const role of roles) if (object(proofs[role]).role !== role) throw new Error("词货读取证明角色不匹配。");
  const limitations = list(screening.limitations, 24).map(value => str(value, 1000));
  const analyses = object(detail.professionalAnalyses);
  const professionals = roles.slice(0, 3).map(role => {
    const analysis = object(analyses[role]);
    if (analysis.role !== role) throw new Error("词货专业分析角色不匹配。");
    return { role, label: names[role], summary: str(analysis.summary) };
  });
  return { reportStatus: statuses[status], nodes, scan: "verified", coverageCount: coverage.length,
    limitations, professionals, humanReview: human.status };
}
