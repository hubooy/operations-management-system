import { authorizationErrorResponse, requireAppPrincipal } from "@/lib/auth/authorization";
import { requestSalesAnalysisOptions } from "@/lib/django/sales-gateway";
import { requireAnalysisPrincipal } from "@/lib/netshop/analysis-tool";
import { PublicApiError } from "@/lib/http/api-error";

export async function GET(request: Request) {
  try {
    const principal=await requireAppPrincipal(); requireAnalysisPrincipal(principal);
    const result=await requestSalesAnalysisOptions(principal,new URL(request.url).searchParams,{signal:request.signal});
    return Response.json(result,{headers:{"cache-control":"no-store"}});
  } catch(error) {
    const auth=authorizationErrorResponse(error);
    if(auth){auth.headers.set("cache-control","no-store");return auth;}
    return Response.json({code:error instanceof PublicApiError?error.code:"service_unavailable",
      error:error instanceof PublicApiError?error.message:"ERP来源选项读取失败"},
    {status:error instanceof PublicApiError?error.status:503,headers:{"cache-control":"no-store"}});
  }
}
