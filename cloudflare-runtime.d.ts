interface Fetcher {
  fetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response>;
}

interface D1Result<T = Record<string, unknown>> {
  results: T[];
  success: boolean;
  meta: {
    changes?: number;
    [key: string]: unknown;
  };
}

interface D1PreparedStatement {
  bind(...values: unknown[]): D1PreparedStatement;
  first<T = Record<string, unknown>>(): Promise<T | null>;
  all<T = Record<string, unknown>>(): Promise<D1Result<T>>;
  run<T = Record<string, unknown>>(): Promise<D1Result<T>>;
}

interface D1Database {
  prepare(query: string): D1PreparedStatement;
  batch<T = Record<string, unknown>>(
    statements: D1PreparedStatement[],
  ): Promise<Array<D1Result<T>>>;
}

interface R2HttpMetadata {
  contentType?: string;
  cacheControl?: string;
}

interface R2Object {
  key: string;
  size: number;
  httpMetadata?: R2HttpMetadata;
  customMetadata?: Record<string, string>;
}

interface R2ObjectBody extends R2Object {
  body: ReadableStream;
  httpEtag: string;
  arrayBuffer(): Promise<ArrayBuffer>;
}

interface R2Bucket {
  put(key: string, value: ArrayBuffer | Uint8Array, options?: {
    onlyIf?: { etagDoesNotMatch: string };
    httpMetadata?: R2HttpMetadata;
    customMetadata?: Record<string, string>;
  }): Promise<unknown>;
  head(key: string): Promise<R2Object | null>;
  get(key: string): Promise<R2ObjectBody | null>;
  delete(keys: string | string[]): Promise<void>;
  list(options?: { prefix?: string; limit?: number; cursor?: string }): Promise<{
    objects: R2Object[];
    truncated: boolean;
    cursor?: string;
  }>;
}

interface ImagesBinding {
  info(stream: ReadableStream): Promise<{ width?: number; height?: number }>;
  input(stream: ReadableStream): {
    transform(options: Record<string, unknown>): {
      output(options: { format: string; quality: number; anim?: boolean }): Promise<{ response(): Response }>;
    };
  };
}

declare module "cloudflare:workers" {
  export const env: {
    DB?: D1Database;
    SALES_IMPORT_FILES?: R2Bucket;
    IMAGES?: ImagesBinding;
    AI_SECRET_ENCRYPTION_KEY?: string;
    AI_MODEL_ENDPOINT_ORIGIN_ALLOWLIST?: string;
    AI_ALLOW_LOCAL_MODEL_ENDPOINTS?: string;
    TERUISI_LOCAL_DIRECT_ACCESS?: string;
    TERUISI_RUNTIME_ENV?: string;
    [binding: string]: unknown;
  };
}
