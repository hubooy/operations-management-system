import type { AppPrincipal } from "../lib/auth/authorization";
import { createDjangoErpReferenceConsumerReader } from "../lib/django/erp-reference-consumer-reader";
import {
  createDjangoErpReferenceService,
  ERP_REFERENCE_IMPORTS_PATH,
} from "../lib/django/erp-reference-service";

const principal: AppPrincipal = {
  email: "local-admin@teruisi.local",
  displayName: "本地管理员",
  role: "admin",
  scope: null,
};

const consumer = await createDjangoErpReferenceConsumerReader().read(principal, {
  operation: "product_search",
  query: "ERP",
  offset: 0,
  limit: 1,
});
const ai = await createDjangoErpReferenceService().requestJson<{
  items: unknown[];
  pagination: { total: number };
}>(principal, {
  method: "GET",
  path: ERP_REFERENCE_IMPORTS_PATH,
  service: "reader",
  rawQuery: "source=products&page=1&pageSize=1",
});

if (!consumer.revision || !ai.revision || !Array.isArray(ai.data.items)
    || consumer.data.items.length > 1 || ai.data.items.length > 1
    || !Number.isSafeInteger(ai.data.pagination?.total)) {
  throw new Error("ERP reference consumer smoke returned an invalid bounded result");
}

process.stdout.write(JSON.stringify({
  status: "passed",
  consumerRevision: consumer.revision,
  consumerReturned: consumer.data.items.length,
  aiRevision: ai.revision,
  aiReturned: ai.data.items.length,
  aiTotalMatched: ai.data.pagination.total,
}));
