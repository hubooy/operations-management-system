export type AnnotationReviewEligibility = {
  status: string;
  reviewJobReady: boolean;
  reviewSegments: string[];
  snapshotValid?: boolean;
};

export function canSelectAnnotationReviewItem(item: AnnotationReviewEligibility, segment: string): boolean {
  return ["review_pending", "approved", "rejected"].includes(item.status)
    && item.reviewJobReady
    && item.snapshotValid !== false
    && Array.isArray(item.reviewSegments)
    && item.reviewSegments.includes(segment);
}
