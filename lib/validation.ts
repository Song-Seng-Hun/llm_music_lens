export class AppError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) { super(message); this.status = status; this.code = code; }
}

export function parseYouTube(value: string): { id: string; url: string } {
  let u: URL;
  try { u = new URL(value.trim()); } catch { throw new AppError(400, "INVALID_URL", "유효한 YouTube 주소를 입력해 주세요."); }
  if (u.protocol !== "https:" || u.username || u.password || u.port) throw new AppError(400, "INVALID_URL", "공개 HTTPS YouTube 영상 주소만 지원합니다.");
  let id: string | null = null;
  if (u.hostname === "youtu.be" && u.pathname.split("/").length === 2) id = u.pathname.slice(1);
  if (["youtube.com", "www.youtube.com", "m.youtube.com"].includes(u.hostname)) {
    if (u.pathname === "/watch") id = u.searchParams.get("v");
    else if (/^\/(shorts|embed)\/[^/]+\/?$/.test(u.pathname)) id = u.pathname.split("/")[2];
  }
  if (!id || !/^[A-Za-z0-9_-]{11}$/.test(id)) throw new AppError(400, "INVALID_URL", "단일 YouTube 영상의 주소를 입력해 주세요.");
  return { id, url: `https://www.youtube.com/watch?v=${id}` };
}

export function sameOrigin(request: Request) {
  const origin = request.headers.get("origin");
  if (origin && origin !== new URL(request.url).origin) throw new AppError(403, "ORIGIN", "다른 사이트에서 보낸 요청은 허용하지 않습니다.");
  if (request.headers.get("sec-fetch-site") === "cross-site") throw new AppError(403, "ORIGIN", "다른 사이트에서 보낸 요청은 허용하지 않습니다.");
}

export async function boundedJson(request: Request, limit: number): Promise<unknown> {
  if (!request.headers.get("content-type")?.toLowerCase().startsWith("application/json")) throw new AppError(415, "CONTENT_TYPE", "JSON 요청이 필요합니다.");
  const length = Number(request.headers.get("content-length"));
  if (length > limit) throw new AppError(413, "TOO_LARGE", "요청이 너무 큽니다.");
  if (!request.body) throw new AppError(400, "EMPTY", "요청 내용이 없습니다.");
  const reader = request.body.getReader();
  const decoder = new TextDecoder();
  let total = 0; let value = "";
  try {
    while (true) {
      const { done, value: chunk } = await reader.read(); if (done) break;
      total += chunk.byteLength;
      if (total > limit) { await reader.cancel(); throw new AppError(413, "TOO_LARGE", "요청이 너무 큽니다."); }
      value += decoder.decode(chunk, { stream: true });
    }
    value += decoder.decode();
  } finally { reader.releaseLock(); }
  try { return JSON.parse(value); } catch { throw new AppError(400, "INVALID_JSON", "JSON을 읽을 수 없습니다."); }
}
