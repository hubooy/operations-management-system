import { PublicApiError } from "@/lib/http/api-error";

export function requireDjangoRecord(value: unknown, message = "Django 财务响应格式无效。") {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new PublicApiError(503, "service_unavailable", message);
  }
  return value as Record<string, unknown>;
}
