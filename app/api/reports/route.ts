import { requireOwner, json, errorResponse } from "@/lib/server/http";
import { boundedJson, sameOrigin } from "@/lib/validation";
import { listReports, saveClientReport } from "@/lib/server/reports";
import { MAX_REPORT_BYTES } from "@/lib/report";
export async function GET() {
  try { return json({ reports: await listReports(await requireOwner()) }); } catch (e) { return errorResponse(e); }
}
export async function POST(request: Request) {
  try { sameOrigin(request); const owner = await requireOwner(); return json({ report: await saveClientReport(owner, await boundedJson(request, MAX_REPORT_BYTES)) }, 201); } catch (e) { return errorResponse(e); }
}
