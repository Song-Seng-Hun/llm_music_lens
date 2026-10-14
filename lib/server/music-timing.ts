import { decodeTimingAudio, TIMING_SAMPLE_RATE } from "../music-timing.ts";
import { AppError } from "../validation.ts";
import { beatContext } from "../beat-context.ts";
import { linkVocalTiming } from "../vocal-timing.ts";
import { analyzeWaveformEnvelope } from "../../analysis/waveform-envelope.mjs";
import { waveformContext } from "../waveform-context.ts";
import { attackContext } from "../attack-context.ts";
import { SERVER_ONSET_OPTIONS } from "../../public/analysis/spectral-onsets.mjs";
import { analyzeVocalPitch } from "../../analysis/vocal-pitch.mjs";
import { vocalPitchContext } from "../vocal-pitch-context.ts";

// The production MCP route calls this function. Loading the model is lazy so
// saved-report reads do not initialize the inference runtime.
export async function analyzeMusicTiming(args: unknown) {
  const { input, bytes, samples, duration } = decodeTimingAudio(args);
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  const sha256 = Array.from(new Uint8Array(hash), n => n.toString(16).padStart(2, "0")).join("");
  try {
    const { analyzeBeatPcm } = await import("./beat-analysis.mjs");
    const { acoustics, attackCandidates, ...timing } = await analyzeBeatPcm(samples, { sampleRate: TIMING_SAMPLE_RATE });
    const pitch = vocalPitchContext(input.sourceKind === "isolated-vocal" ? analyzeVocalPitch(samples, TIMING_SAMPLE_RATE) : undefined);
    const envelope = analyzeWaveformEnvelope(samples, TIMING_SAMPLE_RATE, { detail: input.waveformDetail });
    const context = waveformContext(envelope.activity.regions, input, timing.beats);
    return {
      schema: "music-lens-timing/1",
      source: { encoding: "pcm_s16le", sampleRate: TIMING_SAMPLE_RATE, channels: 1, sha256, duration, offsetSeconds: input.offsetSeconds },
      // Times stay excerpt-relative: offsetSeconds is explicit instead of
      // silently mixing global timestamps with excerpt-local provenance.
      clock: "excerpt-relative-seconds; add source.offsetSeconds for song time",
      timing: { ...timing, ...beatContext(timing.beats, timing.downbeats) },
      vocalTiming: linkVocalTiming(input, timing.beats, timing.downbeats, duration),
      pitch,
      attacks: {
        ...attackContext(attackCandidates, timing.beats, duration),
        method: { ...SERVER_ONSET_OPTIONS,
          fftSize: 1024, sampleRate: TIMING_SAMPLE_RATE, hopSeconds: 0.02,
          input: "positive changes in interior FFT magnitude / fftSize, summed <180/180-2000/>=2000 Hz",
          firstFrame: "reference only; no observed preceding audio",
          edgeSupport: "Candidates withheld before frameContextSeconds + 2 hops and after duration - frameContextSeconds - 1 hop; missing candidates there are unknown, not absence of an attack",
          clock: "excerpt-relative-seconds; add source.offsetSeconds for song time",
          strength: "smoothed total flux / excerpt maximum raw flux; uncalibrated and crop-dependent",
          fluxShares: "unsmoothed positive magnitude changes at candidate frame; not power shares",
          beatRelation: "inside bracketing detected beats only; offset=candidate time minus nearest beat time, midpoint ties earlier",
        },
        sourceKind: input.sourceKind,
        sourceAttribution: input.sourceKind === "isolated-vocal" ? "caller-declared isolated vocal; not verified or separated by this tool" : "mixture changes; cannot attribute a candidate to a voice or instrument",
        warnings: [
          "Candidates are spectral changes, not identified drums, syllables, breaths, notes or rap articulation. Density counts detected candidates, not actual performed hits.",
          "Twenty-millisecond frame spacing is not timing accuracy. Centered windows, smoothing, thresholding and crop edges can shift, merge or miss attacks.",
          "Offsets and phase depend on estimated beats and attack timing. They do not certify syncopation, swing or intentional ahead/behind playing. No beat grid is extrapolated outside observed intervals.",
          "Only the first 64 events are retained; total and one-second density include every detected candidate. Use a shorter excerpt when truncated.",
        ],
      },
      acoustics: {
        ...acoustics,
        sourceKind: input.sourceKind,
        sourceAttribution: input.sourceKind === "isolated-vocal" ? "caller-declared isolated vocal; not verified or separated by this tool" : "mixture spectrum; cannot attribute changes to a voice or instrument",
      },
      waveform: {
        ...envelope,
        sourceKind: input.sourceKind,
        sourceAttribution: input.sourceKind === "isolated-vocal" ? "caller-declared isolated vocal; not verified or separated by this tool" : "mixture energy; cannot attribute changes to a voice or instrument",
        context,
        warnings: [...envelope.warnings, "Energy regions are not phonemes, syllables, breaths or automatic voice detection. RMS is not perceived loudness or vocal effort. Overlap links never establish lyric-to-pitch identity."],
      },
      unavailable: ["automatic lyrics", "server acoustic syllable alignment", "note transcription"],
    };
  } catch (error) {
    if (error instanceof AppError) throw error;
    if (error instanceof Error && /busy/i.test(error.message)) throw new AppError(503, "ANALYSIS_BUSY", "현재 분석 중입니다. 잠시 후 같은 요청을 다시 보내 주세요.");
    throw new AppError(503, "ANALYSIS_UNAVAILABLE", "분석 모델을 실행하지 못했습니다. 모델 설치와 서버 실행 기록을 확인해 주세요. 추정 결과로 대체하지 않았습니다.");
  }
}
