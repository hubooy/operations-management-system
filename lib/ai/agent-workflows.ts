import type { AppPrincipal } from "@/lib/auth/authorization";
import { requestDjangoAi } from "@/lib/django/ai-service";
const list = async (path: string, params: { page: number; pageSize: number }, principal: AppPrincipal) =>
  (await requestDjangoAi<Record<string, unknown>>(principal, { path, query: new URLSearchParams({ page: String(params.page), pageSize: String(params.pageSize) }) })).data;
export const listAiAgentJobs = (params: { page: number; pageSize: number }, principal: AppPrincipal) => list("/api/ai/agent-jobs", params, principal);
export const listAiWorkflowRuns = (params: { page: number; pageSize: number }, principal: AppPrincipal) => list("/api/ai/workflow-runs", params, principal);
