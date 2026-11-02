import { getChatGPTUser } from "@/app/chatgpt-auth";
import { AppError } from "../validation.ts";

export async function requireOwner(): Promise<string> {
  const user = await getChatGPTUser();
  if (!user) throw new AppError(401, "SIGN_IN_REQUIRED", "서버 분석·결과 저장·저장된 팀 자료 조회는 로그인 후 사용할 수 있어요. 로컬 분석과 내보내기는 로그인 없이 가능합니다.");
  return user.userId;
}
export const json = (value: unknown, status = 200) => Response.json(value, { status, headers: { "cache-control": "private, no-store", "x-content-type-options": "nosniff" } });
export function errorResponse(error: unknown): Response {
  if (error instanceof AppError) return json({ error: { code: error.code, message: error.message } }, error.status);
  return json({ error: { code: "UNAVAILABLE", message: "처리를 완료하지 못했습니다. 분석 결과나 저장 상태를 확인한 뒤 다시 시도해 주세요." } }, 500);
}
