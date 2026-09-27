import {
  createDjangoCustomerService,
  CUSTOMER_SERVICE_CONVERSATIONS_PATH,
} from "../lib/django/customer-service";
import { readDjangoCustomerServiceConsumer } from "../lib/django/customer-service-consumer-reader";
import type { AppPrincipal } from "../lib/auth/authorization";

const principal: AppPrincipal = {
  email: "local-admin@teruisi.local",
  displayName: "本地管理员",
  role: "admin",
  scope: null,
};

const search = await readDjangoCustomerServiceConsumer(principal, {
  operation: "search",
  query: "客服",
  offset: 0,
  limit: 1,
  includeMessages: false,
});
const ai = await createDjangoCustomerService().requestJson<{
  items: unknown[];
  pagination: { total: number };
}>(principal, {
  method: "GET",
  path: CUSTOMER_SERVICE_CONVERSATIONS_PATH,
  service: "reader",
  rawQuery: "page=1&pageSize=1&includeOptions=false",
});

if (!search.revision || !ai.revision || !Array.isArray(ai.data.items)
    || ai.data.items.length > 1 || !Number.isSafeInteger(ai.data.pagination?.total)) {
  throw new Error("customer-service consumer smoke returned an invalid bounded result");
}

process.stdout.write(JSON.stringify({
  status: "passed",
  consumerRevision: search.revision,
  consumerReturned: search.data.items.length,
  aiRevision: ai.revision,
  aiReturned: ai.data.items.length,
  aiTotalMatched: ai.data.pagination.total,
}));
