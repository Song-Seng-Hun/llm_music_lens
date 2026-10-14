import { decodeTimingAudio, TIMING_SAMPLE_RATE } from "../music-timing.ts";
import { AppError } from "../validation.ts";
import { beatContext } from "../beat-context.ts";
import { linkVocalTiming } from "../vocal-timing.ts";

// The production MCP route calls this function. Loading the model is lazy so
// saved-report reads do not initialize the inference runtime.
export async function analyzeMusicTiming(args: unknown) {
  const { input, bytes, samples, duration } = decodeTimingAudio(args);
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  const sha256 = Array.from(new Uint8Array(hash), n => n.toString(16).padStart(2, "0")).join("");
  try {
    const { analyzeBeatPcm } = await import("./beat-analysis.mjs");
    const timing = await analyzeBeatPcm(samples, { sampleRate: TIMING_SAMPLE_RATE });
    return {
      schema: "music-lens-timing/1",
      source: { encoding: "pcm_s16le", sampleRate: TIMING_SAMPLE_RATE, channels: 1, sha256, duration, offsetSeconds: input.offsetSeconds },
      // Times stay excerpt-relative: offsetSeconds is explicit instead of
      // silently mixing global timestamps with excerpt-local provenance.
      clock: "excerpt-relative-seconds; add source.offsetSeconds for song time",
      timing: { ...timing, ...beatContext(timing.beats, timing.downbeats) },
      vocalTiming: linkVocalTiming(input, timing.beats, timing.downbeats, duration),
      unavailable: ["automatic lyrics", "server acoustic syllable alignment", "note transcription"],
    };
  } catch (error) {
    if (error instanceof AppError) throw error;
    if (error instanceof Error && /busy/i.test(error.message)) throw new AppError(503, "ANALYSIS_BUSY", "현재 분석 중입니다. 잠시 후 같은 요청을 다시 보내 주세요.");
    throw new AppError(503, "ANALYSIS_UNAVAILABLE", "분석 모델을 실행하지 못했습니다. 모델 설치와 서버 실행 기록을 확인해 주세요. 추정 결과로 대체하지 않았습니다.");
  }
}
