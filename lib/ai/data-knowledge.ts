import type { AppPrincipal } from "@/lib/auth/authorization";
import { aiConsumer } from "@/lib/django/ai-service";
export const searchAiKnowledge = (args: Record<string, unknown>, principal: AppPrincipal) => aiConsumer<Record<string, unknown>>(principal, { operation: "knowledge", ...args });
