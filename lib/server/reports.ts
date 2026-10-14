import { env } from "cloudflare:workers";
import { AppError } from "../validation.ts";
import { MAX_REPORT_BYTES, reportSchema, type MusicReport } from "../report.ts";

export function database(): D1Database {
  if (!env.DB) throw new AppError(503, "STORAGE_NOT_CONFIGURED", "결과 저장소가 연결되지 않았습니다. JSON으로 내보내면 결과를 보관할 수 있습니다.");
  return env.DB;
}
export async function listReports(owner: string, limit = 30) {
  const rows = await database().prepare("SELECT id, title, source_type, created_at FROM reports WHERE owner_id = ? ORDER BY created_at DESC LIMIT ?").bind(owner, Math.min(50, limit)).all();
  return rows.results;
}
export async function getReport(owner: string, id: string): Promise<MusicReport> {
  if (!/^[a-f0-9-]{36}$/i.test(id)) throw new AppError(400, "INVALID_ID", "결과 ID 형식이 올바르지 않습니다.");
  const row = await database().prepare("SELECT body FROM reports WHERE owner_id = ? AND id = ?").bind(owner, id).first<{ body: string }>();
  if (!row) throw new AppError(404, "NOT_FOUND", "이 계정에서 해당 결과를 찾을 수 없습니다.");
  return reportSchema.parse(JSON.parse(row.body));
}
export function reportInsert(owner: string, report: MusicReport) {
  const body = JSON.stringify(report);
  if (new TextEncoder().encode(body).byteLength > MAX_REPORT_BYTES) throw new AppError(413, "REPORT_TOO_LARGE", "저장 결과는 1.9 MB 이하여야 합니다. JSON 내보내기를 사용해 주세요.");
  return database().prepare("INSERT INTO reports (id, owner_id, title, source_type, created_at, body) VALUES (?, ?, ?, ?, ?, ?)")
    .bind(report.id, owner, report.source.title, report.source.kind, report.createdAt, body);
}
export async function saveClientReport(owner: string, input: unknown) {
  const parsed = reportSchema.safeParse(input);
  if (!parsed.success) throw new AppError(400, "INVALID_REPORT", "지원하는 Music Lens 결과 형식이 아닙니다.");
  // Client/imported data is useful evidence, but cannot claim server validation.
  const report: MusicReport = { ...parsed.data, id: crypto.randomUUID(), createdAt: new Date().toISOString(),
    provenance: { ...parsed.data.provenance, mode: "imported", inputStatus: "client-supplied; not independently validated", accuracy: "unvalidated" } };
  await reportInsert(owner, report).run();
  return report;
}
