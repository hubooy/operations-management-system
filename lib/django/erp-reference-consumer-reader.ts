import type { AppPrincipal } from "@/lib/auth/authorization";
import {
  createDjangoErpReferenceService,
  ERP_REFERENCE_CONSUMER_QUERY_PATH,
  type DjangoErpReferenceConfig,
  type DjangoErpReferenceOptions,
} from "@/lib/django/erp-reference-service";

type SearchItem = {
  resultId: string;
  title: string;
  subtitle: string;
  detail: string;
  updatedAt: string;
  amountCents: null;
};

type SearchPage = { items: SearchItem[]; total: number; truncated: boolean };
type ImportPage = {
  items: Array<{
    id: string; source: string; fileName: string; status: string;
    rowCount: number; createdAt: string; completedAt: string | null;
  }>;
  total: number;
  truncated: boolean;
};

export type ErpReferenceConsumerResponseMap = {
  product_search: SearchPage;
  combo_search: SearchPage;
  import_batch_search: ImportPage;
  product_codes: {
    items: Array<{
      productCode: string; productName: string; brand: string; specification: string;
      barcode: string; category: string; supplier: string; productStatus: string; updatedAt: string;
    }>;
  };
};

export type ErpReferenceConsumerRequest =
  | { operation: "product_search"; query: string; offset: number; limit: number }
  | { operation: "combo_search"; query: string; offset: number; limit: number }
  | { operation: "import_batch_search"; query: string; offset: number; limit: number }
  | { operation: "product_codes"; codes: string[] };

export type ErpReferenceConsumerReader = {
  read<K extends keyof ErpReferenceConsumerResponseMap>(
    principal: AppPrincipal,
    request: Extract<ErpReferenceConsumerRequest, { operation: K }>,
    options?: Omit<DjangoErpReferenceOptions, "config">,
  ): Promise<{ data: ErpReferenceConsumerResponseMap[K]; revision: string }>;
};

export function createDjangoErpReferenceConsumerReader(
  config?: DjangoErpReferenceConfig,
): ErpReferenceConsumerReader {
  const service = createDjangoErpReferenceService(config);
  return {
    async read(principal, request, options = {}) {
      const { operation, ...params } = request;
      const response = await service.requestJson<{
        operation: typeof operation;
        data: ErpReferenceConsumerResponseMap[typeof operation];
      }>(principal, {
        method: "POST", path: ERP_REFERENCE_CONSUMER_QUERY_PATH,
        service: "reader", payload: { operation, params },
      }, options);
      if (response.data.operation !== operation) throw new Error("Django ERP consumer 返回 operation 不匹配");
      return { data: response.data.data, revision: response.revision };
    },
  };
}
