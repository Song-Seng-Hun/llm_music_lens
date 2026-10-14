import { z } from "zod";
import { AppError } from "./validation.ts";

export const TIMING_SAMPLE_RATE = 22050;
export const TIMING_MAX_SECONDS = 30;
export const TIMING_MAX_SAMPLES = TIMING_SAMPLE_RATE * TIMING_MAX_SECONDS;
export const TIMING_MAX_BASE64 = Math.ceil(TIMING_MAX_SAMPLES * 2 / 3) * 4;
// PCM plus a small JSON envelope; validated before decoding or loading a model.
export const MCP_MAX_REQUEST_BYTES = TIMING_MAX_BASE64 + 160000;

const localTime = z.number().finite().min(0).max(TIMING_MAX_SECONDS);
const vocalItem = z.object({ start: localTime, end: localTime, text: z.string().min(1).max(64) }).strict().refine(x => x.end > x.start);
const noteItem = z.object({ start: localTime, end: localTime, midi: z.number().finite().min(21).max(108) }).strict().refine(x => x.end > x.start);

export const timingInputSchema = z.object({
  audio: z.object({
    encoding: z.literal("pcm_s16le"), sampleRate: z.literal(TIMING_SAMPLE_RATE),
    channels: z.literal(1), data: z.string().min(4).max(TIMING_MAX_BASE64),
  }).strict(),
  offsetSeconds: z.number().finite().min(0).max(600).default(0),
  sourceKind: z.enum(["mixture", "isolated-vocal"]).default("mixture"),
  waveformDetail: z.enum(["summary", "fine"]).default("summary"),
  timedVocal: z.object({
    unit: z.enum(["phone", "mora", "syllable", "word"]), source: z.string().min(1).max(300),
    items: z.array(vocalItem).max(256),
  }).strict().optional(),
  noteEvents: z.object({ source: z.string().min(1).max(300), mode: z.enum(["solo-vocal", "polyphonic"]), items: z.array(noteItem).max(512) }).strict().optional(),
}).strict();
export type TimingInput = z.infer<typeof timingInputSchema>;

export function decodeTimingAudio(value: unknown) {
  const input = timingInputSchema.parse(value);
  const encoded = input.audio.data;
  // Reject ambiguous/URL-safe/whitespace encodings; atob alone accepts these
  // differently across hosts. Reject before allocating the float buffer.
  if (encoded.length % 4 !== 0 || !/^[A-Za-z0-9+/]*={0,2}$/.test(encoded)) {
    throw new AppError(400, "AUDIO_ENCODING", "오디오는 표준 Base64 PCM16 형식이어야 합니다.");
  }
  const binary = atob(encoded);
  const count = binary.length / 2;
  if (!Number.isInteger(count) || count < TIMING_SAMPLE_RATE || count > TIMING_MAX_SAMPLES) {
    throw new AppError(400, "AUDIO_LENGTH", "22,050 Hz 모노 PCM16 오디오 1~30초를 보내 주세요.");
  }
  const duration = count / TIMING_SAMPLE_RATE;
  if (input.offsetSeconds + duration > 600) throw new AppError(400, "AUDIO_RANGE", "전체 곡의 600초 범위 안에서 분석해 주세요.");
  if ([...(input.timedVocal?.items ?? []), ...(input.noteEvents?.items ?? [])].some(x => x.end > duration)) {
    throw new AppError(400, "ALIGNMENT_CLOCK", "제공된 발음·음표 시각은 이번 오디오 구간 내부의 상대 시각이어야 합니다.");
  }
  const bytes = new Uint8Array(binary.length);
  const samples = new Float32Array(count);
  for (let i = 0; i < count; i++) {
    const low = binary.charCodeAt(i * 2), high = binary.charCodeAt(i * 2 + 1);
    bytes[i * 2] = low; bytes[i * 2 + 1] = high;
    const n = low | (high << 8);
    samples[i] = (n >= 32768 ? n - 65536 : n) / 32768;
  }
  return { input, bytes, samples, duration };
}

export const TIMING_TOOL = {
  name: "analyze_music_timing",
  description: "Analyze supplied audio on the server (no browser, API key or external inference). Send 1–30s mono22050Hz signed16 little-endian PCM base64. Returns estimated beats, downbeats, BPM, waveform RMS/energy regions/rises/falls, 1s spectral texture, attack candidates with signed beat offsets/phase/density and provenance; downbeats are not certified meter. sourceKind defaults to mixture (vocal pitch unavailable); isolated-vocal enables a bounded monophonic F0/MIDI contour and observed note names, with null unknowns and uncalibrated periodicity. It is caller attribution, not voice detection or separation; this is not note transcription. waveformDetail summary returns a100ms curve; fine a10ms curve (same20ms/10ms analysis). Cropped edges are not true onsets/offsets. Attacks are not identified drums or rap syllables; offsets do not certify groove. At most64events; density includes all candidates. Spectral texture is not instrument/emotion identity; below -80dBFS it is unknown. Optional timedVocal/noteEvents (excerpt-relative times) get beat phase/offset and overlap links; supplied timestamps are NOT new acoustic alignment. offsetSeconds places the excerpt in a <=600s song. Independent excerpts may disagree at boundaries. No URL downloads, automatic lyrics/notes, audio/result storage.",
  inputSchema: {
    type: "object", properties: {
      audio: { type: "object", properties: { encoding: { const: "pcm_s16le", type: "string" }, sampleRate: { const: 22050, type: "integer" }, channels: { const: 1, type: "integer" }, data: { type: "string", minLength: 4, maxLength: TIMING_MAX_BASE64 } }, required: ["encoding", "sampleRate", "channels", "data"], additionalProperties: false },
      offsetSeconds: { type: "number", minimum: 0, maximum: 600, default: 0 },
      sourceKind: { type: "string", enum: ["mixture", "isolated-vocal"], default: "mixture", description: "Caller-declared signal content; no source separation or voice identification is performed." },
      waveformDetail: { type: "string", enum: ["summary", "fine"], default: "summary", description: "Output curve at100ms or10ms; both modes use20ms windows every10ms, with sustained changes derived from100ms power groups." },
      timedVocal: { type: "object", properties: {
        unit: { type: "string", enum: ["phone", "mora", "syllable", "word"] }, source: { type: "string", minLength: 1, maxLength: 300 },
        items: { type: "array", maxItems: 256, items: { type: "object", properties: { start: { type: "number", minimum: 0, maximum: 30 }, end: { type: "number", minimum: 0, maximum: 30 }, text: { type: "string", minLength: 1, maxLength: 64 } }, required: ["start", "end", "text"], additionalProperties: false } },
      }, required: ["unit", "source", "items"], additionalProperties: false },
      noteEvents: { type: "object", properties: {
        source: { type: "string", minLength: 1, maxLength: 300 }, mode: { type: "string", enum: ["solo-vocal", "polyphonic"] },
        items: { type: "array", maxItems: 512, items: { type: "object", properties: { start: { type: "number", minimum: 0, maximum: 30 }, end: { type: "number", minimum: 0, maximum: 30 }, midi: { type: "number", minimum: 21, maximum: 108 } }, required: ["start", "end", "midi"], additionalProperties: false } },
      }, required: ["source", "mode", "items"], additionalProperties: false },
    }, required: ["audio"], additionalProperties: false,
  },
  annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
};
