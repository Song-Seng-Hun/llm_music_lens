import { requireOwner, json, errorResponse } from "@/lib/server/http";
import { getReport } from "@/lib/server/reports";
export async function GET(_request: Request, context: { params: Promise<{ id: string }> }) {
  try { const owner = await requireOwner(); return json({ report: await getReport(owner, (await context.params).id) }); } catch (e) { return errorResponse(e); }
}
