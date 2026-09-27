import type { AppPrincipal } from "@/lib/auth/authorization";
import { annotationCommand, annotationQuery, runClaimedDjangoMarketVisionTask } from "@/lib/market/django-annotation-runner";
import { dispatchAnnotationJobs, type AnnotationDispatchJob } from "@/lib/market/annotation-dispatch";

const INTERNAL_ANNOTATION_PRINCIPAL: AppPrincipal = {
  email: "market-annotation-runner@teruisi.internal",
  displayName: "市场云端标注执行器",
  role: "operator",
  scope: null,
};

export async function runScheduledDjangoMarketAnnotation() {
  const response = await annotationQuery<{ jobs: AnnotationDispatchJob[] }>(
    INTERNAL_ANNOTATION_PRINCIPAL, "dispatch", { limit: 5 },
  );
  if (!Array.isArray(response.data.jobs) || response.data.jobs.length > 5) throw new Error("Django 市场派发列表无效");
  const { resolveAiBackgroundPrincipal } = await import("@/lib/ai/background-principal");
  return dispatchAnnotationJobs<{ principal: AppPrincipal; coordinatorToken: string }>({
    jobs: response.data.jobs,
    prepare: async (job) => {
      // The scheduler identity may coordinate the market queue, but may not
      // impersonate a user when requesting a paid model runtime from AI.
      const owner = await resolveAiBackgroundPrincipal(job.ownerEmail, "null");
      if (!owner.ok || !["operator", "admin"].includes(owner.principal.role) || owner.principal.scope !== null) {
        await annotationCommand(INTERNAL_ANNOTATION_PRINCIPAL, {
          action: "set_cloud_run_state", jobId: job.jobId, state: "paused",
          failureCode: "authorization_revoked", failureMessage: "标注任务发起账号或数据权限已变化",
        }, AbortSignal.timeout(5_000));
        return null;
      }
      const begun = await annotationCommand<{ coordinatorToken?: string }>(owner.principal, { action: "begin_dispatch", jobId: job.jobId }, AbortSignal.timeout(5_000));
      const coordinatorToken = begun.data.result.coordinatorToken;
      if (!coordinatorToken) return null;
      return { principal: owner.principal, coordinatorToken };
    },
    run: async (job, handle) => {
      const result = await runClaimedDjangoMarketVisionTask({ ...handle, jobId: job.jobId });
      return result.data.result;
    },
    release: async (job, handle) => {
      await annotationCommand(handle.principal, { action: "end_dispatch", jobId: job.jobId, coordinatorToken: handle.coordinatorToken }, AbortSignal.timeout(5_000));
    },
  });
}
