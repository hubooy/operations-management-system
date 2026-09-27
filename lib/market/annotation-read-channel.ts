export class AnnotationReadError extends Error {}

/** One read scope: only its latest request may publish data or change its error. */
export function createAnnotationReadChannel(options: {
  label: string;
  onError: (message: string) => void;
  timeoutMs?: number;
  fetchImpl?: typeof fetch;
}) {
  let generation = 0;
  let active: AbortController | null = null;
  let failed = false;
  const cancel = () => {
    generation += 1;
    active?.abort();
    active = null;
  };
  return {
    cancel,
    hasError: () => failed,
    async read<T>(url: string, validate: (value: unknown) => value is T, publish: (value: T) => void, signal?: AbortSignal): Promise<T | undefined> {
      cancel();
      const requestGeneration = generation;
      const controller = new AbortController();
      active = controller;
      let timedOut = false;
      const abort = () => controller.abort();
      signal?.addEventListener("abort", abort, { once: true });
      if (signal?.aborted) controller.abort();
      const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, options.timeoutMs ?? 30_000);
      const isCurrent = () => requestGeneration === generation && !signal?.aborted;
      let rejectAborted: () => void = () => undefined;
      try {
        if (controller.signal.aborted) return;
        const aborted = new Promise<never>((_resolve, reject) => {
          rejectAborted = () => reject(new Error("aborted"));
          controller.signal.addEventListener("abort", rejectAborted, { once: true });
        });
        const request = (async () => {
          const response = await (options.fetchImpl ?? fetch)(url, { cache: "no-store", signal: controller.signal });
          return { response, value: await response.json() as unknown };
        })();
        const { response, value } = await Promise.race([request, aborted]);
        if (!isCurrent()) return;
        if (controller.signal.aborted) throw new Error("aborted");
        if (!response.ok || !validate(value)) throw new Error("invalid_response");
        publish(value);
        failed = false;
        options.onError("");
        return value;
      } catch {
        if (!isCurrent() || (controller.signal.aborted && !timedOut)) return;
        const message = `${options.label}${timedOut ? "超时" : "失败"}，当前显示上次成功读取的数据，请重试。`;
        failed = true;
        options.onError(message);
        throw new AnnotationReadError(message);
      } finally {
        clearTimeout(timeout);
        controller.signal.removeEventListener("abort", rejectAborted);
        signal?.removeEventListener("abort", abort);
        if (active === controller) active = null;
      }
    },
  };
}
