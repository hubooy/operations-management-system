export type AnnotationRetryKind = "waiting" | "transient" | "rate_limit";

const TRANSIENT_RETRY_BASE_MS = 5_000;
const TRANSIENT_RETRY_MAX_MS = 30_000;
const RATE_LIMIT_RETRY_BASE_MS = 60_000;
const WAITING_RETRY_MS = 2_000;
const AUTO_RETRY_MAX_MS = 5 * 60_000;

export function annotationRetryDelayMs(kind: AnnotationRetryKind, failureCount: number, providerRetryAfterMs = 0) {
  const providerDelay = Number.isFinite(providerRetryAfterMs) ? Math.max(0, Math.trunc(providerRetryAfterMs)) : 0;
  if (kind === "waiting") return Math.max(WAITING_RETRY_MS, Math.min(providerDelay, AUTO_RETRY_MAX_MS));
  const base = kind === "rate_limit" ? RATE_LIMIT_RETRY_BASE_MS : TRANSIENT_RETRY_BASE_MS;
  const normalizedFailureCount = Number.isFinite(failureCount) ? Math.max(1, Math.trunc(failureCount)) : 1;
  const exponent = Math.min(4, normalizedFailureCount - 1);
  const computedDelay = base * (2 ** exponent);
  const boundedDelay = kind === "transient" ? Math.min(computedDelay, TRANSIENT_RETRY_MAX_MS) : computedDelay;
  return Math.min(AUTO_RETRY_MAX_MS, Math.max(providerDelay, boundedDelay));
}

export function annotationRetryConcurrency(
  kind: AnnotationRetryKind,
  currentConcurrency: number,
  configuredConcurrency: number,
  failureCount: number,
) {
  const configured = Math.max(1, Math.trunc(configuredConcurrency));
  const current = Math.max(1, Math.min(configured, Math.trunc(currentConcurrency)));
  if (kind === "waiting") return current;
  if (kind === "rate_limit") return Math.max(1, Math.floor(current / 2));
  const normalizedFailureCount = Number.isFinite(failureCount) ? Math.max(1, Math.trunc(failureCount)) : 1;
  if (normalizedFailureCount === 1) {
    const minimum = configured >= 4 ? 2 : 1;
    return Math.max(minimum, Math.ceil(current * 0.75));
  }
  return Math.max(1, Math.ceil(current / 2));
}

export function annotationRecoveredConcurrency(
  currentConcurrency: number,
  configuredConcurrency: number,
  successfulImages: number,
) {
  const configured = Math.max(1, Math.trunc(configuredConcurrency));
  const current = Math.max(1, Math.min(configured, Math.trunc(currentConcurrency)));
  const recoverySteps = Math.floor(Math.max(0, Math.trunc(successfulImages)) / 3);
  return Math.min(configured, current + recoverySteps);
}

export type AnnotationRunRetryDecision = {
  kind: Exclude<AnnotationRetryKind, "waiting">;
  scope: "worker" | "global";
  delayMs: number;
  blockedUntil: number;
  previousConcurrency: number;
  concurrency: number;
  countedIncident: boolean;
  suppressedByGlobalRateLimit: boolean;
  floorFailureCount: number;
  shouldPause: boolean;
};

export const ANNOTATION_RETRY_FLOOR_FAILURE_LIMIT = 3;

export type AnnotationRunRetrySnapshot = {
  configuredConcurrency: number;
  currentConcurrency: number;
  transientFailureCount: number;
  rateLimitFailureCount: number;
  successfulImagesSinceFailure: number;
  transientIncidentUntil: number;
  globalRateLimitUntil: number;
  floorFailureCount: number;
  workerRetryUntil: Array<[number, number]>;
};

/**
 * Coordinates one interactive cloud run. Ordinary transient failures lower the
 * shared launch ceiling but cool down only the worker that observed the error.
 * Provider rate limits retain a global launch pause so no worker amplifies a
 * 429 response.
 */
export class AnnotationRunRetryController {
  private configuredConcurrency: number;
  private currentConcurrency: number;
  private transientFailureCount = 0;
  private rateLimitFailureCount = 0;
  private successfulImagesSinceFailure = 0;
  private transientIncidentUntil = 0;
  private globalRateLimitUntil = 0;
  private floorFailureCount = 0;
  private readonly workerRetryUntil = new Map<number, number>();

  constructor(configuredConcurrency: number, snapshot?: Partial<AnnotationRunRetrySnapshot> | null) {
    const normalized = Math.max(1, Math.trunc(configuredConcurrency));
    this.configuredConcurrency = normalized;
    this.currentConcurrency = boundedInteger(snapshot?.currentConcurrency, normalized, 1, normalized);
    this.transientFailureCount = boundedInteger(snapshot?.transientFailureCount, 0, 0, 1_000);
    this.rateLimitFailureCount = boundedInteger(snapshot?.rateLimitFailureCount, 0, 0, 1_000);
    this.successfulImagesSinceFailure = boundedInteger(snapshot?.successfulImagesSinceFailure, 0, 0, 1_000_000);
    this.transientIncidentUntil = boundedTimestamp(snapshot?.transientIncidentUntil);
    this.globalRateLimitUntil = boundedTimestamp(snapshot?.globalRateLimitUntil);
    this.floorFailureCount = boundedInteger(snapshot?.floorFailureCount, 0, 0, ANNOTATION_RETRY_FLOOR_FAILURE_LIMIT);
    for (const entry of Array.isArray(snapshot?.workerRetryUntil) ? snapshot.workerRetryUntil.slice(0, 50) : []) {
      if (!Array.isArray(entry) || entry.length !== 2) continue;
      const worker = boundedInteger(entry[0], -1, 0, 49);
      const until = boundedTimestamp(entry[1]);
      if (worker >= 0 && until > 0) this.workerRetryUntil.set(worker, until);
    }
  }

  get targetConcurrency() {
    return this.configuredConcurrency;
  }

  get workerLimit() {
    return this.currentConcurrency;
  }

  get recovering() {
    return this.currentConcurrency < this.configuredConcurrency;
  }

  snapshot(): AnnotationRunRetrySnapshot {
    return {
      configuredConcurrency: this.configuredConcurrency,
      currentConcurrency: this.currentConcurrency,
      transientFailureCount: this.transientFailureCount,
      rateLimitFailureCount: this.rateLimitFailureCount,
      successfulImagesSinceFailure: this.successfulImagesSinceFailure,
      transientIncidentUntil: this.transientIncidentUntil,
      globalRateLimitUntil: this.globalRateLimitUntil,
      floorFailureCount: this.floorFailureCount,
      workerRetryUntil: [...this.workerRetryUntil.entries()].slice(0, 50),
    };
  }

  updateTarget(configuredConcurrency: number) {
    const normalized = Math.max(1, Math.trunc(configuredConcurrency));
    const wasRecovering = this.recovering;
    this.configuredConcurrency = normalized;
    this.currentConcurrency = wasRecovering
      ? Math.min(this.currentConcurrency, normalized)
      : normalized;
    this.successfulImagesSinceFailure = 0;
    if (!this.recovering) this.resetFailureCounts();
    if (this.currentConcurrency > 1) this.floorFailureCount = 0;
  }

  schedule(
    kind: Exclude<AnnotationRetryKind, "waiting">,
    workerIndex: number,
    providerRetryAfterMs = 0,
    now = Date.now(),
  ): AnnotationRunRetryDecision {
    const previousConcurrency = this.currentConcurrency;
    const globalRateLimitActive = now < this.globalRateLimitUntil;

    if (kind === "rate_limit") {
      const countedIncident = !globalRateLimitActive;
      if (countedIncident) {
        this.rateLimitFailureCount += 1;
        this.currentConcurrency = annotationRetryConcurrency(
          kind,
          this.currentConcurrency,
          this.configuredConcurrency,
          this.rateLimitFailureCount,
        );
        this.successfulImagesSinceFailure = 0;
        this.recordFloorFailure(previousConcurrency);
      }
      const delayMs = annotationRetryDelayMs(kind, Math.max(1, this.rateLimitFailureCount), providerRetryAfterMs);
      this.globalRateLimitUntil = Math.max(this.globalRateLimitUntil, now + delayMs);
      return {
        kind,
        scope: "global",
        delayMs,
        blockedUntil: this.globalRateLimitUntil,
        previousConcurrency,
        concurrency: this.currentConcurrency,
        countedIncident,
        suppressedByGlobalRateLimit: false,
        floorFailureCount: this.floorFailureCount,
        shouldPause: this.floorFailureCount >= ANNOTATION_RETRY_FLOOR_FAILURE_LIMIT,
      };
    }

    const countedIncident = !globalRateLimitActive && now >= this.transientIncidentUntil;
    if (countedIncident) {
      this.transientFailureCount += 1;
      this.currentConcurrency = annotationRetryConcurrency(
        kind,
        this.currentConcurrency,
        this.configuredConcurrency,
        this.transientFailureCount,
      );
      this.successfulImagesSinceFailure = 0;
      this.recordFloorFailure(previousConcurrency);
    }
    const delayMs = annotationRetryDelayMs(kind, Math.max(1, this.transientFailureCount), providerRetryAfterMs);
    const localRetryUntil = Math.max(this.workerRetryUntil.get(workerIndex) ?? 0, now + delayMs);
    this.workerRetryUntil.set(workerIndex, localRetryUntil);
    this.transientIncidentUntil = Math.max(this.transientIncidentUntil, localRetryUntil);
    return {
      kind,
      scope: "worker",
      delayMs,
      blockedUntil: Math.max(localRetryUntil, this.globalRateLimitUntil),
      previousConcurrency,
      concurrency: this.currentConcurrency,
      countedIncident,
      suppressedByGlobalRateLimit: globalRateLimitActive,
      floorFailureCount: this.floorFailureCount,
      shouldPause: this.floorFailureCount >= ANNOTATION_RETRY_FLOOR_FAILURE_LIMIT,
    };
  }

  blockedUntil(workerIndex: number) {
    return Math.max(this.globalRateLimitUntil, this.workerRetryUntil.get(workerIndex) ?? 0);
  }

  recordSuccess(successfulImages: number) {
    const previousConcurrency = this.currentConcurrency;
    const normalizedSuccesses = Math.max(0, Math.trunc(successfulImages));
    this.successfulImagesSinceFailure += normalizedSuccesses;
    if (normalizedSuccesses > 0) this.floorFailureCount = 0;
    const recoveredConcurrency = annotationRecoveredConcurrency(
      this.currentConcurrency,
      this.configuredConcurrency,
      this.successfulImagesSinceFailure,
    );
    if (recoveredConcurrency > this.currentConcurrency) {
      this.successfulImagesSinceFailure = Math.max(
        0,
        this.successfulImagesSinceFailure - ((recoveredConcurrency - this.currentConcurrency) * 3),
      );
      this.currentConcurrency = recoveredConcurrency;
      this.resetFailureCounts();
    }
    return {
      previousConcurrency,
      concurrency: this.currentConcurrency,
      recovered: this.currentConcurrency > previousConcurrency,
    };
  }

  private resetFailureCounts() {
    this.transientFailureCount = 0;
    this.rateLimitFailureCount = 0;
  }

  private recordFloorFailure(previousConcurrency: number) {
    if (previousConcurrency === 1 && this.currentConcurrency === 1) {
      this.floorFailureCount += 1;
    } else if (this.currentConcurrency > 1) {
      this.floorFailureCount = 0;
    }
  }
}

function boundedInteger(value: unknown, fallback: number, minimum: number, maximum: number) {
  const normalized = Number(value);
  return Number.isFinite(normalized)
    ? Math.max(minimum, Math.min(maximum, Math.trunc(normalized)))
    : fallback;
}

function boundedTimestamp(value: unknown) {
  const normalized = Number(value);
  return Number.isFinite(normalized) && normalized > 0 ? Math.trunc(normalized) : 0;
}

export function annotationRequestRetryKind(error: unknown): Exclude<AnnotationRetryKind, "waiting"> | null {
  const status = typeof error === "object" && error !== null && "status" in error ? Number(error.status) : 0;
  const message = error instanceof Error
    ? `${error.name} ${error.message}`
    : typeof error === "object" && error !== null && "message" in error
      ? String(error.message)
      : String(error ?? "");
  if (status === 429 || /(?:HTTP|状态码)\s*429|rate limit|限流|额度不足/i.test(message)) return "rate_limit";
  if ([408, 425, 500, 502, 503, 504].includes(status)) return "transient";
  if (/abort|超时|timeout|network|fetch|failed to fetch|load failed/i.test(message)) return "transient";
  return null;
}

export function isRetryableAnnotationRequestError(error: unknown) {
  return annotationRequestRetryKind(error) !== null;
}
