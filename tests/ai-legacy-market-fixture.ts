// Legacy D1 market tests use an isolated historical model adapter.
import { registerHooks } from "node:module";
registerHooks({ resolve(specifier, context, next) {
  if (specifier === "@/lib/market/annotation-model") return { url: new URL("./legacy/market/annotation-model.ts", import.meta.url).href, shortCircuit: true };
  const result = next(specifier, context);
  if (result.url.endsWith("/lib/market/annotation-model.ts")) return { url: new URL("./legacy/market/annotation-model.ts", import.meta.url).href, shortCircuit: true };
  return result;
} });
