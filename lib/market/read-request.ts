/** A bounded GET, including response-body reading. Never retries a mutation. */
export async function readMarketJson<T>(url: string, signal?: AbortSignal, timeoutMs = 30_000): Promise<T> {
  const controller = new AbortController();
  let timedOut = false;
  const abort = () => controller.abort();
  if (signal?.aborted) controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
  try {
    const response = await fetch(url, { cache: "no-store", signal: controller.signal });
    const payload = await response.json() as T & { error?: string };
    if (controller.signal.aborted) throw new DOMException("请求已取消", "AbortError");
    if (!response.ok) throw new Error(payload?.error || "市场数据读取失败");
    if (!payload) throw new Error("市场数据返回为空");
    return payload;
  } catch (error) {
    if (timedOut) throw new Error("市场数据读取超时，请缩小范围或重新加载");
    throw error;
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener("abort", abort);
  }
}
