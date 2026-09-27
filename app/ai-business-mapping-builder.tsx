"use client";
import { useEffect, useRef, useState } from "react";
import { fetchBoundedJson } from "@/lib/ai/bounded-fetch";
import { mappingBindingKey, mappingCanonical, mappingContext, mappingPairs, matchingMasters, readMappingDirectory,
  type BudgetRun, type MappingContext, type MappingPair, type MappingSelection, type MappingSource } from "@/lib/ai/business-mapping-builder";

export type BusinessMappingBuilderProps = { run: BudgetRun; principalKey: string; disabled?: boolean; onChange: (value: MappingSelection) => void };
const windows: Record<string, string> = { current: "本期", previous: "环比基期", yearAgo: "同比基期" };
const message = (error: unknown) => error instanceof Error ? error.message : "关联目录读取失败，请重读后重新选择。";
export default function AiBusinessMappingBuilder(props: BusinessMappingBuilderProps) {
  return <Builder key={mappingBindingKey(props.run, props.principalKey)} {...props} />;
}
function Builder({ run, principalKey, disabled = false, onChange }: BusinessMappingBuilderProps) {
  let context: MappingContext | null = null, invalid = "";
  try { context = mappingContext(run, principalKey); } catch (error) { invalid = message(error); }
  const contextText = context ? mappingCanonical(context) : "", bindingKey = mappingBindingKey(run, principalKey);
  const [state, setState] = useState<{ token: string; sources: MappingSource[]; pairs: MappingPair[]; ready: boolean; error: string }>({ token: "", sources: [], pairs: [], ready: false, error: "" });
  const [retry, setRetry] = useState(0), callback = useRef(onChange), generation = useRef(0);
  useEffect(() => { callback.current = onChange; }, [onChange]);
  useEffect(() => {
    const ctl = new AbortController(), current = ++generation.current; let active = true;
    const announce = (pairs: MappingPair[], ready: boolean, reason: MappingSelection["reason"]) => callback.current({ bindingKey, pairs, ready, reason });
    setState({ token: contextText, sources: [], pairs: [], ready: false, error: "" }); announce([], false, contextText ? "loading" : "failed");
    if (!contextText) return () => { active = false; ctl.abort(); };
    const fixed = JSON.parse(contextText) as MappingContext;
    void readMappingDirectory(fixed, async offset => {
      if (!active || ctl.signal.aborted || current !== generation.current) throw new Error("关联读取已取消。");
      const { response, data } = await fetchBoundedJson({ url: `/api/ai/business-evidence/${encodeURIComponent(fixed.runId)}/sources?offset=${offset}&limit=20`,
        init: { method: "GET", cache: "no-store" }, signal: ctl.signal, timeoutMs: 30000, maxBytes: 65536 });
      if (!response.ok) throw new Error(`关联目录读取失败（${response.status}），请重读并重新选择。`);
      return data;
    }).then(sources => {
      if (!active || ctl.signal.aborted || current !== generation.current) return;
      setState({ token: contextText, sources, pairs: [], ready: true, error: "" }); announce([], true, "verified");
    }).catch(error => {
      if (!active || ctl.signal.aborted || current !== generation.current) return;
      setState({ token: contextText, sources: [], pairs: [], ready: false, error: message(error) }); announce([], false, "failed");
    });
    return () => { active = false; ctl.abort(); };
  }, [contextText, bindingKey, retry]);
  const current = state.token === contextText, ready = !!contextText && current && state.ready;
  const sources = ready ? state.sources : [], selected = ready ? state.pairs : [], sales = sources.filter(source => source.domain === "sales");
  function select(salesKey: string, masterKey: string) {
    if (!ready || disabled) return;
    try {
      const pairs = mappingPairs(sources, [...selected.filter(pair => pair.salesKey !== salesKey), ...(masterKey ? [{ salesKey, masterKey }] : [])]);
      setState(old => ({ ...old, pairs })); callback.current({ bindingKey, pairs, ready: true, reason: "selected" });
    } catch (error) {
      setState({ token: contextText, sources: [], pairs: [], ready: false, error: message(error) }); callback.current({ bindingKey, pairs: [], ready: false, reason: "failed" });
    }
  }
  return <section className="bw-card" aria-label="商品关联选择">
    <h4>商品关联（可选）</h4>
    <p>选择 ERP 销售对应的同平台、同店铺当前商品主数据。未选择时可直接分析。</p>
    <p>当前快照关联用于汇总销售，不代表历史 SKU 归属、广告归因或推广收益。</p>
    {invalid && <p role="alert" className="bw-error">{invalid}</p>}
    {current && state.error && <p role="alert" className="bw-error">{state.error}原关联选择已失效，请重新选择。</p>}
    {contextText && !ready && !state.error && <p role="status">正在核验完整来源目录，完成前不能选择关联…</p>}
    <button type="button" disabled={disabled || !contextText} onClick={() => { generation.current++; setState({ token: "", sources: [], pairs: [], ready: false, error: "" }); callback.current({ bindingKey, pairs: [], ready: false, reason: "loading" }); setRetry(value => value+1); }}>重读关联目录（清空选择）</button>
    <button type="button" disabled={disabled} onClick={() => { setState(old => ({ ...old, pairs: [] })); callback.current({ bindingKey, pairs: [], ready, reason: "cleared" }); }}>清空关联选择</button>
    <p>重读后需重新选择；也可清空关联选择，继续普通分析。</p>
    {ready && <>
      <p role="status">已核验完整目录：{sources.length} 个来源；已明确选择 {selected.length} 组关联。</p>
      {!sales.length && <p>目录没有 ERP 销售来源，无法添加商品关联；可继续不含关联的报告。</p>}
      <fieldset disabled={disabled}><legend>逐个销售来源选择主数据</legend>
        <p>以下为原查询区间，同比、环比按此区间换算。</p>
        {sales.map(source => { const masters = matchingMasters(source, sources), value = selected.find(pair => pair.salesKey === source.key)?.masterKey ?? ""; return <div key={source.key}>
          <p>{source.query.platform} · {source.query.shop} · {source.query.channel} · {windows[source.query.window]} · {source.query.startDate} 至 {source.query.endDate}</p>
          {masters.length ? <label>关联主数据<select aria-label={`关联主数据 ${source.key}`} style={{ maxWidth: "100%", width: "100%", padding: 8 }} value={value}
            disabled={!value && selected.length >= 47} onChange={event => select(source.key, event.target.value)}>
            <option value="">不添加此关联</option>{masters.map(master => <option key={master.key} value={master.key}>{master.query.platform} · {master.query.shop} · 本期主数据{masters.length > 1 ? ` · ${master.key}` : ""}</option>)}
          </select></label> : <p>缺少同一精确平台、店铺的本期商品主数据：{source.query.platform} / {source.query.shop}；此来源不能关联。</p>}
        </div>; })}
      </fieldset>
    </>}
  </section>;
}
