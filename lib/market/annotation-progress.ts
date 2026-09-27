export type AnnotationJobProgressTarget = {
  id: string;
  remainingInferenceCount: number;
};

export type AnnotationJobProgressSnapshot = {
  job: { id: string };
  remainingInferenceUnits: number;
};

export function remainingInferenceUnitsForJob(
  currentJob: AnnotationJobProgressTarget | null | undefined,
  progress: AnnotationJobProgressSnapshot | null | undefined,
): number {
  if (currentJob && progress?.job.id === currentJob.id) {
    return progress.remainingInferenceUnits;
  }
  return currentJob?.remainingInferenceCount ?? 0;
}
