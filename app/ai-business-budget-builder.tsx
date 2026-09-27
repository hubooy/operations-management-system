"use client";
import { useEffect, useRef, useState } from "react";
import { fetchBoundedJson } from "@/lib/ai/bounded-fetch";
import { canonical, dimensions, eligible, makePlan, validateBinding, validateTargetPage, type BudgetPlan, type BudgetResult, type BudgetRun, type EvidenceBinding, type SelectedTarget, type Source, type TargetPage } from "@/lib/ai/business-budget-builder";

export type BusinessBudgetBuilderProps = { run: BudgetRun; principalKey: string; disabled?: boolean; allowDryRun?: boolean; onSubmit: (plan: BudgetPlan, dryRun: boolean) => void | Promise<void> };
type Directory = { schemaVersion: string; runId: string; evidenceVersion: number; catalogDigest: string; offset: number; total: number; returned: number; nextOffset: number | null; items: Source[] };
const message = (error: unknown) => error instanceof Error ? error.message : "预算读取失败，请重新核验。";
const money = (value: number | null | undefined) => value == null ? "不可测算" : (value/100).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const entity = (value: Record<string, unknown>) => Object.entries(value).map(([key, item]) => `${key}：${item == null || item === "" ? "缺失" : String(item)}`).join(" · ");
const topFields = { totalBudgetCents: "总预算（元）", reserveCents: "预留预算（元）", horizonDays: "规划天数（1–93）", observationDays: "观察天数", reviewAfterSpendBps: "消耗复核比例（%）", minimumClicks: "最低点击数", minimumOrderLines: "最低订单行数" };
const statuses: Record<string, string> = { unavailable: "基数不足，无法测算", low_sample_scenario: "低样本假设测算，须谨慎复核", assumption_scenario: "按输入假设测算，不是业绩承诺" };
const targetFields = { weight: "分配权重（1–10000）", minBudgetCents: "最低预算（元）", maxBudgetCents: "最高预算（元）", ownerRole: "负责人角色", minimumRoasBps: "最低产出比" };
const scenarioFields = { name: "情景名称", cpcFactorBps: "点击成本系数（10–300%）", orderRateFactorBps: "订单行率系数（10–300%）", orderValueFactorBps: "订单行价值系数（10–300%）", contributionMarginBps: "贡献毛利率（%，留空为未知）" };
async function api<T>(url: string, signal: AbortSignal, payload?: unknown): Promise<T> {
  const { response, data } = await fetchBoundedJson({ url, signal, timeoutMs: 30000, maxBytes: 2*1024*1024, init: { method: payload === undefined ? "GET" : "POST", cache: "no-store", ...(payload === undefined ? {} : { headers: { "content-type": "application/json" }, body: JSON.stringify(payload) }) } });
  if (!response.ok) throw new Error(data && typeof data === "object" && "error" in data ? String(data.error) : `请求失败（${response.status}）`);
  if (!data || typeof data !== "object") throw new Error("预算回执格式无效。");
  return data as T;
}
function Inputs({ names, value, change, prefix }: { names: Record<string, string>; value: Record<string, string>; change: (key: string, value: string) => void; prefix: string }) {
  return <div className="bw-grid">{Object.entries(names).map(([key, name]) => <label key={key}>{name}<input aria-label={`${prefix}${name}`} value={value[key] ?? ""} maxLength={key === "name" || key === "ownerRole" ? 80 : 18} inputMode={key === "name" || key === "ownerRole" ? "text" : "decimal"} onChange={event => change(key, event.target.value)} /></label>)}</div>;
}
export default function AiBusinessBudgetBuilder(props: BusinessBudgetBuilderProps) {
  const { run, principalKey } = props;
  if (!principalKey || run.status !== "sealed" || run.plan.schemaVersion !== "business-evidence-v2" || !Number.isSafeInteger(run.version) || run.version < 1 || !Number.isSafeInteger(run.plan.sourceCount) || run.plan.sourceCount! < 1 || run.plan.sourceCount! > 48 || !/^[a-f0-9]{64}$/.test(run.plan.catalogDigest ?? "")) return <p>固定预算需要当前账号下已封存且目录完整的 v2 证据。</p>;
  return <Builder key={`${principalKey}:${run.id}:${run.version}:${run.plan.catalogDigest}`} {...props} />;
}
function Builder({ run, disabled = false, allowDryRun = true, onSubmit }: BusinessBudgetBuilderProps) {
  const [directory, setDirectory] = useState<Directory | null>(null), [directoryOffset, setDirectoryOffset] = useState(0), [directoryHistory, setDirectoryHistory] = useState<number[]>([]), [directoryError, setDirectoryError] = useState("");
  const [source, setSource] = useState<Source | null>(null), [dimension, setDimension] = useState(""), [offset, setOffset] = useState(0), [history, setHistory] = useState<number[]>([]), [page, setPage] = useState<TargetPage | null>(null), [targetError, setTargetError] = useState("");
  const [selected, setSelected] = useState<SelectedTarget[]>([]), [fields, setFields] = useState<Record<string, string>>({}), [scenarios, setScenarios] = useState<Record<string, string>[]>([{}]);
  const [preview, setPreview] = useState<{ plan: BudgetPlan; budget: BudgetResult } | null>(null), [error, setError] = useState(""), [busy, setBusy] = useState(false), [submitting, setSubmitting] = useState(false), [retry, setRetry] = useState(0);
  const binding = useRef<EvidenceBinding | null>(null), revision = useRef(0), controller = useRef<AbortController | null>(null), alive = useRef(true);
  const base = `/api/ai/business-evidence/${encodeURIComponent(run.id)}`, sourceKeys = JSON.stringify(Object.keys(run.sources).sort());
  useEffect(() => { alive.current = true; return () => { alive.current = false; controller.current?.abort(); controller.current = null; }; }, []);
  useEffect(() => {
    const ctl = new AbortController(); let active = true;
    setDirectory(null); setDirectoryError("");
    void api<Directory>(`${base}/sources?offset=${directoryOffset}&limit=20`, ctl.signal).then(value => {
      if (!active || ctl.signal.aborted) return;
      const known = new Set<string>(JSON.parse(sourceKeys));
      if (value.schemaVersion !== "business-evidence-directory-page-v2" || value.runId !== run.id || value.evidenceVersion !== run.version || value.catalogDigest !== run.plan.catalogDigest || value.total !== run.plan.sourceCount || value.offset !== directoryOffset || !Array.isArray(value.items) || value.items.length < 1 || value.items.length > 20 || value.returned !== value.items.length || directoryOffset+value.returned > value.total || value.nextOffset !== (directoryOffset+value.returned < value.total ? directoryOffset+value.returned : null) || new Set(value.items.map(item => item.key)).size !== value.items.length || value.items.some((item, i) => !item || !known.has(item.key) || item.ordinal !== directoryOffset+i+1 || typeof item.domain !== "string" || !item.query || Object.values(item.query).some(v => typeof v !== "string"))) throw new Error("来源目录身份或分页不匹配，请刷新证据任务。");
      setDirectory(value);
    }).catch(caught => { if (active && !ctl.signal.aborted) {
      revision.current++; controller.current?.abort(); controller.current = null; setBusy(false); setPreview(null); setDirectoryError(message(caught));
    } });
    return () => { active = false; ctl.abort(); };
  }, [base, run.id, run.version, run.plan.catalogDigest, run.plan.sourceCount, sourceKeys, directoryOffset, retry]);
  useEffect(() => {
    const ctl = new AbortController(); let active = true;
    setPage(null); setTargetError("");
    if (source && dimension) void api<TargetPage>(`${base}/budget-targets?sourceKey=${encodeURIComponent(source.key)}&dimension=${encodeURIComponent(dimension)}&offset=${offset}`, ctl.signal).then(value => {
      if (!active || ctl.signal.aborted) return;
      const trusted = { id: run.id, version: run.version, plan: { catalogDigest: run.plan.catalogDigest } } as BudgetRun;
      validateTargetPage(value, trusted, source.key, dimension, offset, binding.current);
      binding.current ??= structuredClone(value.evidenceBinding); setPage(value);
    }).catch(caught => { if (active && !ctl.signal.aborted) {
      revision.current++; controller.current?.abort(); controller.current = null; setBusy(false); setPreview(null); setTargetError(message(caught));
    } });
    return () => { active = false; ctl.abort(); };
  }, [base, source, dimension, offset, run.id, run.version, run.plan.catalogDigest, retry]);
  function invalidate() { revision.current++; controller.current?.abort(); controller.current = null; setBusy(false); setPreview(null); setError(""); }
  function add(row: TargetPage["rows"][number]) {
    if (!source || !page || row.dimensionMissing || selected.length >= 100) return;
    const candidate = { source, dimension, row, fields: {} };
    const scope = (s: Source) => canonical([s.query.platform, s.query.shop]);
    if (selected.some(item => scope(item.source) === scope(source) && (item.source.key !== source.key || item.dimension !== dimension))) { setError("同一店铺不能混选不同来源或聚合维度。"); return; }
    if (selected.some(item => item.source.key === source.key && item.dimension === dimension && item.row.id === row.id)) return;
    if (selected.some(item => item.source.query.startDate !== source.query.startDate || item.source.query.endDate !== source.query.endDate)) { setError("预算目标须使用相同本期日期区间。"); return; }
    invalidate(); setSelected(items => [...items, structuredClone(candidate)]);
  }
  async function calculate() {
    invalidate();
    const ctl = new AbortController(), current = revision.current; controller.current = ctl; setBusy(true);
    try {
      const plan = makePlan(fields, selected, scenarios), fixed = binding.current;
      if (!fixed) throw new Error("请先选择已验证的预算目标。");
      const result = await api<{ schemaVersion: string; previewOnly: boolean; evidenceBinding: EvidenceBinding; budget: BudgetResult & { evidenceVersion: number; evidencePlanDigest: string } }>(`${base}/budget-preview`, ctl.signal, { evidenceBinding: fixed, budgetPlan: plan });
      if (!alive.current || ctl.signal.aborted || current !== revision.current || controller.current !== ctl) return;
      validateBinding(result.evidenceBinding, run, fixed);
      if (result.schemaVersion !== "business-budget-preview-v1" || result.previewOnly !== true || result.budget?.schemaVersion !== "business-budget-v1" || canonical(result.budget.plan) !== canonical(plan) || result.budget.evidenceRunId !== run.id || result.budget.evidenceVersion !== run.version || result.budget.evidencePlanDigest !== fixed.evidencePlanDigest || !/^[a-f0-9]{64}$/.test(result.budget.planDigest) || !result.budget.allocation || !Array.isArray(result.budget.scenarios) || result.budget.scenarios.length !== scenarios.length || result.budget.scenarios.some(s => !s.summary || !Array.isArray(s.rows) || s.rows.length !== selected.length)) throw new Error("试算回执与当前完整参数不一致。");
      const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonical(plan)));
      if (Array.from(new Uint8Array(hash), b => b.toString(16).padStart(2, "0")).join("") !== result.budget.planDigest) throw new Error("试算参数摘要不匹配。");
      if (alive.current && !ctl.signal.aborted && current === revision.current && controller.current === ctl) setPreview({ plan: structuredClone(plan), budget: result.budget });
    } catch (caught) { if (alive.current && !ctl.signal.aborted && current === revision.current && controller.current === ctl) setError(message(caught)); }
    finally { if (alive.current && controller.current === ctl) { setBusy(false); controller.current = null; } }
  }
  async function submit(dryRun: boolean) {
    if (!preview || disabled || submitting || busy || dryRun && !allowDryRun) return;
    const current = revision.current; setSubmitting(true);
    try {
      if (canonical(makePlan(fields, selected, scenarios)) !== canonical(preview.plan)) throw new Error("参数已变化，请重新试算。");
      await onSubmit(structuredClone(preview.plan), dryRun);
    } catch (caught) { if (alive.current && current === revision.current) setError(message(caught)); }
    finally { if (alive.current) setSubmitting(false); }
  }
  const visibleDirectory = directory?.offset === directoryOffset ? directory : null;
  const visiblePage = page?.sourceKey === source?.key && page?.dimension === dimension && page?.pagination.offset === offset ? page : null;
  const locked = disabled || submitting;
  return <section aria-label="初次固定预算配置"><h4>从封存证据配置固定预算</h4><p>试算不调用模型。预算金额和业绩假设须由你明确填写；日期有记录不代表结算完成，也不能证明投放因果效果。</p>
    <fieldset disabled={locked}><legend>选择本期推广目标</legend>
      {directoryError && <p role="alert">{directoryError}<button onClick={() => setRetry(n => n+1)}>重读预算来源</button></p>}
      {!visibleDirectory && !directoryError && <p role="status">正在读取精确来源目录…</p>}
      {visibleDirectory && <><p>目录 {directoryOffset+1}–{directoryOffset+visibleDirectory.returned} / {visibleDirectory.total}；其他来源仍保留在分析范围中。</p>{visibleDirectory.items.map(item => <div key={item.key}><p>{entity(item.query)} · {eligible(item) || "可选择本期推广目标"}</p><button disabled={Boolean(eligible(item)) || !run.sources[item.key]?.complete} onClick={() => { setSource(item); setDimension(""); setOffset(0); setHistory([]); }}>选择来源 {item.key}</button></div>)}</>}
      <div className="bw-actions"><button disabled={!directoryHistory.length || !visibleDirectory} onClick={() => { setDirectoryOffset(directoryHistory[directoryHistory.length-1]); setDirectoryHistory(items => items.slice(0, -1)); }}>预算来源上一页</button><button disabled={!visibleDirectory || visibleDirectory.nextOffset === null} onClick={() => { if (visibleDirectory?.nextOffset != null) { setDirectoryHistory(items => [...items, directoryOffset]); setDirectoryOffset(visibleDirectory.nextOffset); } }}>预算来源下一页</button></div>
      {source && <><p>当前来源：{entity(source.query)}</p><label>预算聚合维度<select aria-label="预算聚合维度" value={dimension} onChange={event => { setDimension(event.target.value); setOffset(0); setHistory([]); }}><option value="">请选择维度</option>{Object.entries(dimensions).map(([key, text]) => <option key={key} value={key}>{text}</option>)}</select></label></>}
      {targetError && <p role="alert">{targetError}<button onClick={() => setRetry(n => n+1)}>重读预算目标</button></p>}
      {source && dimension && !visiblePage && !targetError && <p role="status">正在核验完整来源并读取目标…</p>}
      {visiblePage && <><p>分析行 {offset} 起，共 {visiblePage.pagination.total} 行。日期覆盖：{visiblePage.sourceMetadata.coverage?.status === "dates_present" ? "所选日期有记录" : "缺日或日期覆盖尚不足，测算可能不可用"}。</p><div className="bw-scroll"><table><thead><tr><th>目标</th><th>推广费（元）</th><th>点击</th><th>上报订单行</th><th>上报成交（元）</th><th>选择</th></tr></thead><tbody>{visiblePage.rows.map(row => { const metric = (name: string) => row.metrics[name] && !row.metrics[name]?.missingRows ? row.metrics[name]?.value : null; const chosen = selected.some(s => s.source.key === source?.key && s.dimension === dimension && s.row.id === row.id); return <tr key={row.id}><td>{entity(row.entity)}</td><td>{money(metric("spendCents"))}</td><td>{metric("clicks") ?? "缺失"}</td><td>{metric("reportedOrderLines") ?? "缺失"}</td><td>{money(metric("reportedGmvCents"))}</td><td><button disabled={row.dimensionMissing || chosen || selected.length >= 100} onClick={() => add(row)}>{row.dimensionMissing ? "身份缺失" : chosen ? "已选" : `选择目标 ${row.rowIndex+1}`}</button></td></tr>; })}</tbody></table></div><div className="bw-actions"><button disabled={!history.length} onClick={() => { setOffset(history[history.length-1]); setHistory(items => items.slice(0, -1)); }}>预算目标上一页</button><button disabled={visiblePage.pagination.nextOffset === null} onClick={() => { if (visiblePage.pagination.nextOffset != null) { setHistory(items => [...items, offset]); setOffset(visiblePage.pagination.nextOffset); } }}>预算目标下一页</button></div></>}
    </fieldset>
    <fieldset disabled={locked}><legend>明确填写预算与观察条件</legend><Inputs names={topFields} value={fields} prefix="" change={(key, value) => { invalidate(); setFields(old => ({ ...old, [key]: value })); }} />
      <p>已选择 {selected.length} / 100 个目标；权重仅用于分配，不代表系统预测。</p>{selected.map((target, i) => <fieldset key={`${target.source.key}:${target.dimension}:${target.row.id}`}><legend>目标 {i+1} · {entity(target.row.entity)}</legend><Inputs names={targetFields} value={target.fields} prefix={`目标${i+1} `} change={(key, value) => { invalidate(); setSelected(old => old.map((t, j) => j === i ? { ...t, fields: { ...t.fields, [key]: value } } : t)); }} /><button onClick={() => { invalidate(); setSelected(old => old.filter((_, j) => j !== i)); }}>移除目标 {i+1}</button></fieldset>)}
      {scenarios.map((scenario, i) => <fieldset key={i}><legend>情景 {i+1}</legend><p>系数相对于已验证历史基数；100% 表示保持历史水平，仍需你明确填写。</p><Inputs names={scenarioFields} value={scenario} prefix={`情景${i+1} `} change={(key, value) => { invalidate(); setScenarios(old => old.map((s, j) => j === i ? { ...s, [key]: value } : s)); }} /><button disabled={scenarios.length === 1} onClick={() => { invalidate(); setScenarios(old => old.filter((_, j) => j !== i)); }}>移除情景 {i+1}</button></fieldset>)}
      <button disabled={scenarios.length >= 5} onClick={() => { invalidate(); setScenarios(old => [...old, {}]); }}>添加空白情景</button><button disabled={busy || !selected.length} onClick={() => void calculate()}>{busy ? "正在免费试算…" : "免费试算预算"}</button>
    </fieldset>
    {error && <p role="alert">{error}</p>}
    {preview && <section aria-label="当前预算试算"><h4>当前参数试算</h4><p>总预算 {money(preview.budget.allocation.totalBudgetCents)} 元，预留 {money(preview.budget.allocation.reservedCents)} 元，已分配 {money(preview.budget.allocation.allocatedCents)} 元，未分配 {money(preview.budget.allocation.unallocatedCents)} 元。</p>{preview.budget.scenarios.map((scenario, i) => <div key={i}><h5>{scenario.assumptions.name}</h5><p>假设归因成交 {money(scenario.summary.projectedAttributedGmvCents)} 元；假设贡献扣推广 {money(scenario.summary.assumedContributionAfterAdCents)} 元；不可测算目标 {scenario.summary.unavailableTargets} 个。</p>{scenario.summary.mixedReportingBases && <p>上报口径不同，不能合计预测成交。</p>}<ul>{scenario.rows.map((row, j) => <li key={j}>{entity(row.entity)}：分配 {money(row.budgetCents)} 元；{statuses[row.status] ?? "未知测算状态，请核验"}；消耗 {money(row.reviewAfterSpendCents)} 元后复核，负责人：{row.ownerRole}</li>)}</ul></div>)}</section>}
    <p>试算通过后仍须后端检查模型容量与权限。正式分析会调用模型，可能产生费用；不会自动启动。</p>{!allowDryRun && <p>完整规则筛查模式不支持模拟分析；上方免费预算试算仍可使用。</p>}<div className="bw-actions">{allowDryRun && <button disabled={locked || busy || !preview} onClick={() => void submit(true)}>创建预算模拟分析（不调用模型）</button>}<button disabled={locked || busy || !preview} onClick={() => void submit(false)}>创建预算正式分析（调用模型）</button></div>
  </section>;
}
