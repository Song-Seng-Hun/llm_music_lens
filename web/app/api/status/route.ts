import { env } from "cloudflare:workers";
import { getChatGPTUser } from "@/app/chatgpt-auth";
import { json } from "@/lib/server/http";
import { MAX_FILE_BYTES, MAX_DURATION } from "@/lib/report";
export async function GET(request: Request) {
  const user = await getChatGPTUser();
  const localPreview = import.meta.env.DEV && ["127.0.0.1", "localhost", "[::1]"].includes(new URL(request.url).hostname);
  return json({ user: user ? { name: user.displayName } : null, localPreview, analysisMode: "local", storageReady: Boolean(env.DB), limits: { fileBytes: MAX_FILE_BYTES, durationSeconds: MAX_DURATION }, schema: "music-lens/1" });
}
