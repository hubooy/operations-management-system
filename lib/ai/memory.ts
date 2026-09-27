import type { AppPrincipal } from "@/lib/auth/authorization";
import { aiConsumer } from "@/lib/django/ai-service";
export const retrieveAiMemoriesForContext = (query: unknown, principal: AppPrincipal) => aiConsumer<Record<string, unknown>>(principal, { operation: "memory-recall", query });
