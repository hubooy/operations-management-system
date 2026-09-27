"use client";
import { useEffect, useRef, useState } from "react";
import { fetchBoundedJson } from "@/lib/ai/bounded-fetch";

type Target = { sourceKey: string; dimension: string; rowIndex: number; rowId: string; weight: number; minBudgetCents: number; maxBudgetCents: number; ownerRole: string; minimumRoasBps: number };
type Assumption = { name: string; cpcFactorBps: number; orderRateFactorBps: number; orderValueFactorBps: number; contributionMarginBps: number | null };
export type BudgetPlan = { totalBudgetCents: number; reserveCents: number; horizonDays: number; observationDays: number; reviewAfterSpendBps: number; minimumClicks: number; minimumOrderLines: number; targets: Target[]; scenarios: Assumption[] };
export type BudgetResult = { schemaVersion: string; plan: BudgetPlan; planDigest: string; evidenceRunId: string;
  allocation: { totalBudgetCents: number; reservedCents: number; allocatedCents: number; unallocatedCents: number };
  scenarios: { assumptions: Assumption; summary: { projectedAttributedGmvCents: number | null; assumedContributionAfterAdCents: number | null; unavailableTargets: number; mixedReportingBases: boolean; byReportingBasis: { source: string; knownAttributedGmvCents: number; unavailableTargets: number }[] };
    rows: { entity: Record<string, unknown>; budgetCents: number; status: string; reviewAfterSpendCents: number; ownerRole: string }[] }[] };

const money = (value: number | null) => value === null ? "不可合计或不可测算" : (value/100).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
function units(data: FormData, name: string, scale = 1, nullable = false) {
  const raw = String(data.get(name) ?? "").trim();
  if (nullable && raw === "") return null;
  const digits = scale === 10000 ? 4 : scale === 100 ? 2 : 0;
  if (!(digits ? new RegExp(`^\\d{1,13}(?:\\.\\d{1,${digits}})?$`) : /^\d{1,13}$/).test(raw)) throw new Error("请填写有效数字，金额和百分比最多两位小数，产出比最多四位小数。");
  const [whole, fraction = ""] = raw.split(".");
  const value = Number(whole)*scale + (digits ? Number(fraction.padEnd(digits, "0")) : 0);
  if (!Number.isSafeInteger(value)) throw new Error("输入超出计算范围。");
  return value;
}
export function budgetPlanFromForm(form: HTMLFormElement, original: BudgetPlan): BudgetPlan {
  const data = new FormData(form), plan = structuredClone(original);
  for (const key of ["totalBudgetCents", "reserveCents", "reviewAfterSpendBps"] as const) plan[key] = units(data, key, 100)!;
  for (const key of ["horizonDays", "observationDays", "minimumClicks", "minimumOrderLines"] as const) plan[key] = units(data, key)!;
  plan.targets.forEach((target, i) => {
    target.weight = units(data, `target-${i}-weight`)!;
    for (const key of ["minBudgetCents", "maxBudgetCents", "minimumRoasBps"] as const) target[key] = units(data, `target-${i}-${key}`, key === "minimumRoasBps" ? 10000 : 100)!;
  });
  plan.scenarios.forEach((scenario, i) => {
    for (const key of ["cpcFactorBps", "orderRateFactorBps", "orderValueFactorBps"] as const) scenario[key] = units(data, `scenario-${i}-${key}`, 100)!;
    scenario.contributionMarginBps = units(data, `scenario-${i}-contributionMarginBps`, 100, true);
  });
  return plan;
}

async function request<T>(url: string, payload: unknown, signal: AbortSignal): Promise<T> {
  const { response, data } = await fetchBoundedJson({ url, init: { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(payload), cache: "no-store" }, signal, timeoutMs: 30000, maxBytes: 2*1024*1024 });
  if (!response.ok) throw Object.assign(new Error(data && typeof data === "object" && "error" in data ? String(data.error) : "预算请求失败"), { status: response.status });
  return data as T;
}

export default function AiBusinessBudget({ reportId, question, budget, onCreated }: { reportId: string; question: string; budget: BudgetResult; onCreated: (id: string) => void }) {
  const [preview, setPreview] = useState<BudgetResult | null>(null), [error, setError] = useState("");
  const [busy, setBusy] = useState(false), [pending, setPending] = useState(false);
  const controller = useRef<AbortController | null>(null), alive = useRef(true);
  const submission = useRef<{ clientRequestId: string; evidenceRunId: string; question: string; dryRun: false; budgetPlan: BudgetPlan; previousReportId: string } | null>(null);
  useEffect(() => { alive.current = true; return () => { alive.current = false; controller.current?.abort(); }; }, []);
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const save = (event.nativeEvent as SubmitEvent).submitter?.getAttribute("value") === "save";
    try {
      const plan = submission.current?.budgetPlan ?? budgetPlanFromForm(event.currentTarget, budget.plan);
      setBusy(true); setError("");
      const current = new AbortController(); controller.current = current;
      if (save) {
        submission.current ??= { clientRequestId: "budget-"+crypto.randomUUID(), evidenceRunId: budget.evidenceRunId, question, dryRun: false, budgetPlan: plan, previousReportId: reportId };
        setPending(true);
        const result = await request<{ item: { id: string } }>("/api/ai/business-reports", submission.current, current.signal);
        if (!result?.item?.id) throw new Error("提交回执无效，请重试确认同一提交。");
        if (alive.current) { submission.current = null; setPending(false); onCreated(result.item.id); }
      } else {
        const result = await request<{ previewOnly: boolean; reportId: string; originalPlanDigest: string; budget: BudgetResult }>(`/api/ai/reports/${reportId}/budget-preview`, { budgetPlan: plan }, current.signal);
        if (result.reportId !== reportId || result.originalPlanDigest !== budget.planDigest || result.previewOnly !== true || result.budget?.schemaVersion !== "business-budget-v1") throw new Error("试算版本不一致，请刷新报告。");
        if (alive.current) setPreview(result.budget);
      }
    } catch (caught) {
      if (alive.current) {
        const status = caught && typeof caught === "object" && "status" in caught ? Number(caught.status) : 0;
        if (save && status >= 400 && status < 500 && status !== 409) { submission.current = null; setPending(false); }
        setError((caught instanceof Error ? caught.message : "预算请求失败") + (save && submission.current ? " 提交结果尚未确认；重试会使用同一请求，避免重复创建。" : ""));
      }
    } finally { if (alive.current) setBusy(false); }
  }
  const result = preview ?? budget;
  const input = (label: string, name: string, value: number | null, scale = 1, min = 0, max?: number) => <label key={name}>{label}<input name={name} type="number" min={min} max={max} step={1/scale} defaultValue={value === null ? "" : value/scale} required={!name.endsWith("-contributionMarginBps")} /></label>;
  return <section className="report-review" aria-label="预算情景与调整规划"><h4>预算情景与调整规划</h4><p className="report-note">按新参数试算不会修改当前报告。保存会创建新的分析任务，调用已配置模型并重新复核；原报告保留。归因金额情景不是实际利润或收益承诺。</p>
    <form className="report-budget-controls" onSubmit={event => void submit(event)} onChange={() => setPreview(null)}><fieldset disabled={busy || pending}><legend>预算与复盘条件</legend><div className="report-form">
      {input("预算上限（元）", "totalBudgetCents", budget.plan.totalBudgetCents, 100)}{input("预留预算（元）", "reserveCents", budget.plan.reserveCents, 100)}
      {input("规划天数", "horizonDays", budget.plan.horizonDays, 1, 1, 93)}{input("观察天数", "observationDays", budget.plan.observationDays, 1, 1, 93)}
      {input("提前复盘花费占比（%）", "reviewAfterSpendBps", budget.plan.reviewAfterSpendBps, 100, .01, 100)}{input("样本点击门槛", "minimumClicks", budget.plan.minimumClicks, 1, 1)}{input("样本订单口径门槛", "minimumOrderLines", budget.plan.minimumOrderLines, 1, 1)}
    </div><p>样本门槛是规划规则，不代表统计置信度。</p>
      <details><summary>调整对象权重和预算边界（{budget.plan.targets.length} 项）</summary>{budget.plan.targets.map((target, i) => <div className="report-review" key={target.rowId}><strong>{Object.values(budget.scenarios[0].rows[i].entity).filter(v => v !== null).join(" · ")}</strong><div className="report-form">{input("分配权重", `target-${i}-weight`, target.weight, 1, 1, 10000)}{input("最低预算（元）", `target-${i}-minBudgetCents`, target.minBudgetCents, 100)}{input("最高预算（元）", `target-${i}-maxBudgetCents`, target.maxBudgetCents, 100)}{input("最低归因产出比（倍）", `target-${i}-minimumRoasBps`, target.minimumRoasBps, 10000)}</div></div>)}</details>
      {budget.plan.scenarios.map((scenario, i) => <div className="report-review" key={scenario.name}><strong>{scenario.name}</strong><div className="report-form">{input("点击成本相对基期（%）", `scenario-${i}-cpcFactorBps`, scenario.cpcFactorBps, 100, 10, 300)}{input("订单效率相对基期（%）", `scenario-${i}-orderRateFactorBps`, scenario.orderRateFactorBps, 100, 10, 300)}{input("订单金额相对基期（%）", `scenario-${i}-orderValueFactorBps`, scenario.orderValueFactorBps, 100, 10, 300)}{input("假设贡献率（%，未知留空）", `scenario-${i}-contributionMarginBps`, scenario.contributionMarginBps, 100, 0, 100)}</div></div>)}
    </fieldset>{error && <p role="alert">{error}</p>}<div className="report-actions"><button name="action" value="preview" disabled={busy || pending}>按新参数试算</button><button name="action" value="save" className="primary-button" disabled={busy}>{pending ? "重试确认同一提交" : "保存新版本并重新分析"}</button></div></form>
    <h4>{preview ? "新参数试算（尚未保存）" : "当前报告固定预算"}</h4><p>已分配 {money(result.allocation.allocatedCents)} 元；预留 {money(result.allocation.reservedCents)} 元；未分配 {money(result.allocation.unallocatedCents)} 元。</p>
    {result.scenarios.map(scenario => <div className="report-review" key={scenario.assumptions.name}><strong>{scenario.assumptions.name}</strong><p>情景归因金额：{money(scenario.summary.projectedAttributedGmvCents)} 元；假设贡献扣推广余额：{money(scenario.summary.assumedContributionAfterAdCents)} 元。不可测算对象 {scenario.summary.unavailableTargets} 项。</p>{scenario.summary.mixedReportingBases && <p>不同平台口径分别展示：{scenario.summary.byReportingBasis.map(b => `${b.source} 已知对象 ${money(b.knownAttributedGmvCents)} 元`).join("；")}</p>}</div>)}
  </section>;
}
