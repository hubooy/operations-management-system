import { guangdongRoute } from "@/lib/inventory/guangdong-route";
type Context = { params: Promise<{ operation: string }> };
async function handle(request: Request, context: Context) {
  return guangdongRoute(request, (await context.params).operation);
}
export { handle as GET, handle as POST, handle as PATCH };
