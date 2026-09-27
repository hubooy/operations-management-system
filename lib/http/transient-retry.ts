const retryableStatuses = new Set([500, 502, 503, 504]);

export type TransientFetchRetryOptions = {
  delaysMs?: readonly number[];
  fetchImpl?: typeof fetch;
  wait?: (delayMs: number, signal?: AbortSignal | null) => Promise<boolean>;
};

function isAbortError(error: unknown) {
  return (error instanceof DOMException || error instanceof Error) && error.name === "AbortError";
}

function waitForRetry(delayMs: number, signal?: AbortSignal | null) {
  return new Promise<boolean>((resolve) => {
    if (signal?.aborted) {
      resolve(false);
      return;
    }
    let settled = false;
    const finish = (completed: boolean) => {
      if (settled) return;
      settled = true;
      globalThis.clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
      resolve(completed);
    };
    const onAbort = () => finish(false);
    const timer = globalThis.setTimeout(() => finish(true), Math.max(0, delayMs));
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export async function fetchWithTransientRetry(
  input: RequestInfo | URL,
  init: RequestInit = {},
  options: TransientFetchRetryOptions = {},
) {
  const method = (init.method ?? "GET").toUpperCase();
  if (method !== "GET" && method !== "HEAD") {
    throw new Error("瞬时故障自动重试仅允许 GET 或 HEAD 请求");
  }

  const fetchImpl = options.fetchImpl ?? fetch;
  const wait = options.wait ?? waitForRetry;
  const delaysMs = options.delaysMs ?? [1_000, 2_000, 4_000];

  for (let attempt = 0; ; attempt += 1) {
    try {
      const response = await fetchImpl(input, init);
      if (!retryableStatuses.has(response.status) || attempt >= delaysMs.length) return response;
      await response.arrayBuffer().catch(() => undefined);
    } catch (error) {
      if (isAbortError(error) || init.signal?.aborted || attempt >= delaysMs.length) throw error;
    }

    if ((await wait(delaysMs[attempt] ?? 0, init.signal)) === false) {
      throw new DOMException("The operation was aborted", "AbortError");
    }
  }
}
