import { z } from "zod";
import { composeContinuity } from "../analysis/music-continuity.mjs";
import { AppError } from "./validation.ts";

const hash = z.string().regex(/^[a-f0-9]{64}$/);
const time = z.number().finite().min(0).max(600);
const localTime = z.number().finite().min(0).max(30);
// Strip unused fields so callers can copy existing timing subobjects directly.
// The small projection also prevents arbitrary supplied metadata being echoed.
const sourceSchema = z.object({
  encoding: z.literal("pcm_s16le"), sampleRate: z.literal(22050), channels: z.literal(1),
  sha256: hash, offsetSeconds: time, duration: z.number().finite().min(1).max(30),
});
const provenanceSchema = z.object({
  checkpointSha256: hash, onnxSha256: hash, frameRate: z.literal(50),
  contextFrames: z.literal(250), borderFrames: z.literal(6),
  overlap: z.literal("keep_first"), decoder: z.literal("official-minimal"),
});
const pitchSchema = z.discriminatedUnion("status", [
  z.object({ status: z.literal("unavailable"), reason: z.string().min(1).max(512) }),
  z.object({
    status: z.literal("estimated"), sourceKind: z.literal("isolated-vocal"),
    method: z.object({ id: z.literal("monophonic-yin-style-central-check-v2") }),
    contour: z.object({
      start: z.literal(0), stepSeconds: z.literal(0.02), duration: z.number().finite().min(1).max(30),
      hz: z.array(z.number().finite().min(70).max(1000).nullable()).max(1500),
      midi: z.array(z.number().finite().min(0).max(127).nullable()).max(1500),
      periodicity: z.array(z.number().finite().min(0).max(1).nullable()).max(1500),
      total: z.number().int().min(50).max(1500), truncated: z.literal(false),
    }),
  }),
]);
const segmentSchema = z.object({
  source: sourceSchema,
  timing: z.object({ beats: z.array(localTime).max(1500), downbeats: z.array(localTime).max(1500), provenance: provenanceSchema }),
  pitch: pitchSchema,
});

export const continuityInputSchema = z.object({
  duration: z.number().finite().min(1).max(600), start: time, end: time,
  segments: z.array(segmentSchema).min(1).max(60),
}).strict().superRefine((input, ctx) => {
  const invalid = (message: string) => ctx.addIssue({ code: z.ZodIssueCode.custom, message });
  if (input.end <= input.start || input.end > input.duration || input.end - input.start > 30) invalid("Query must be a positive <=30s window inside the song");
  let frames = 0, events = 0;
  for (const segment of input.segments) {
    const { source, timing, pitch } = segment;
    if (source.offsetSeconds + source.duration > input.duration) invalid("Source outside song");
    const samples = Math.round(source.duration * 22050);
    if (Math.abs(samples / 22050 - source.duration) > 1e-10) invalid("Duration must describe native PCM samples");
    for (const values of [timing.beats, timing.downbeats]) {
      events += values.length;
      if (values.some((value, i) => value >= source.duration || source.offsetSeconds + value >= source.offsetSeconds + source.duration || (i > 0 && (value <= values[i - 1] || source.offsetSeconds + value <= source.offsetSeconds + values[i - 1])))) invalid("Events must increase strictly and stay inside the source on both clocks");
    }
    if (pitch.status === "estimated") {
      const c = pitch.contour, count = Math.ceil(samples / 441);
      frames += c.total;
      if (c.duration !== source.duration || c.total !== count || [c.hz, c.midi, c.periodicity].some(values => values.length !== count)) invalid("Incomplete or mismatched native pitch clock");
      if (c.hz.some((hz, i) => (hz === null) !== (c.midi[i] === null) || (hz === null) !== (c.periodicity[i] === null))) invalid("Pitch unknowns must be aligned");
    }
  }
  if (frames > 33000 || events > 6000) invalid("Too many observations; supply fewer source segments");
});

export function composeMusicTimeline(args: unknown) {
  const input = continuityInputSchema.parse(args);
  try { return composeContinuity(input); }
  catch (error) {
    if (error instanceof RangeError) throw new AppError(400, "CONTINUITY_CONFLICT", "같은 원본·위치·분석 방식의 결과가 서로 다릅니다. 구간 자료를 확인해 주세요.");
    throw error;
  }
}

const number = (minimum: number, maximum: number) => ({ type: "number", minimum, maximum });
const object = (properties: Record<string, unknown>) => ({ type: "object", properties, required: Object.keys(properties), additionalProperties: true });
const array = (items: unknown, maxItems = 1500) => ({ type: "array", items, maxItems });
const nullable = (minimum: number, maximum: number) => ({ anyOf: [number(minimum, maximum), { type: "null" }] });
const hashJson = { type: "string", pattern: "^[a-f0-9]{64}$" };
export const CONTINUITY_TOOL = {
  name: "compose_music_timeline",
  description: "Compose beats/downbeats and native solo-vocal pitch from existing analyze_music_timing results on one song clock; no new analysis, model, audio or storage. Copy source/timing/pitch into segments; unused subfields are ignored. Caller must establish common-song origin and actual offsets: hashes do not authenticate them. Song <=600s, <=60 clips of 1–30s, <=33000 pitch frames/6000 combined beat events. Query [start,end) <=30s. Exact duplicate source/placement/recipe observations collapse; inconsistent duplicates reject. Distinct overlapping views remain separate; NEVER sum them as independent beats or infer a winning pitch. Source offsets + original indices define time; preserve nulls. Per page <=1000 frames/256 combined beat/downbeat records. If window.nextStart is non-null, repeat identical segments/duration with start=nextStart and the SAME end; all layers share that cut. Coverage describes supplied audio on the returned page, not voice/silence. No global BPM, gap filling, note boundaries, key or detection-accuracy improvement.",
  inputSchema: {
    type: "object", additionalProperties: false, required: ["duration", "start", "end", "segments"],
    properties: {
      duration: number(1, 600), start: number(0, 600), end: number(0, 600),
      segments: { type: "array", minItems: 1, maxItems: 60, items: object({
        source: object({ encoding: { const: "pcm_s16le" }, sampleRate: { const: 22050 }, channels: { const: 1 }, sha256: hashJson, offsetSeconds: number(0, 600), duration: number(1, 30) }),
        timing: object({ beats: array(number(0, 30)), downbeats: array(number(0, 30)), provenance: object({
          checkpointSha256: hashJson, onnxSha256: hashJson, frameRate: { const: 50 }, contextFrames: { const: 250 }, borderFrames: { const: 6 }, overlap: { const: "keep_first" }, decoder: { const: "official-minimal" },
        }) }),
        pitch: { oneOf: [
          object({ status: { const: "unavailable" }, reason: { type: "string", minLength: 1, maxLength: 512 } }),
          object({ status: { const: "estimated" }, sourceKind: { const: "isolated-vocal" }, method: object({ id: { const: "monophonic-yin-style-central-check-v2" } }), contour: object({
            start: { const: 0 }, stepSeconds: { const: 0.02 }, duration: number(1, 30), hz: array(nullable(70, 1000)), midi: array(nullable(0, 127)), periodicity: array(nullable(0, 1)), total: { type: "integer", minimum: 50, maximum: 1500 }, truncated: { const: false },
          }) }),
        ] },
      }) },
    },
  },
  annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
};
