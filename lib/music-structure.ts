import { z } from "zod";
import { analyzeStructure } from "../analysis/music-structure.mjs";

const ratio = z.number().finite().min(0).max(1);
const time = z.number().finite().min(0).max(30);
const rowSchema = z.object({
  start: time, end: time, activeFraction: ratio,
  rmsDbfs: z.number().finite().min(-160).max(0).nullable(),
  lowShare: ratio.nullable(),
  mel: z.array(ratio).length(16).nullable(),
}).strict();

export const structurePacketSchema = z.object({
  schema: z.literal("music-structure-packet/1"),
  recipe: z.literal("logmel16-1s-sequence-v1"),
  source: z.object({
    sha256: z.string().regex(/^[a-f0-9]{64}$/),
    offsetSeconds: z.number().finite().min(0).max(600),
    duration: z.number().finite().min(1).max(30),
  }).strict(),
  frames: z.array(rowSchema).min(1).max(30),
}).strict().superRefine((packet, ctx) => {
  const invalid = (message: string) => ctx.addIssue({ code: z.ZodIssueCode.custom, message });
  if (packet.frames.length !== Math.ceil(packet.source.duration)) invalid("Incomplete packet clock");
  packet.frames.forEach((row, i) => {
    if (row.start !== i || row.end !== Math.min(i + 1, packet.source.duration) || row.end <= row.start) invalid("Invalid excerpt-relative frame clock");
    const active = row.activeFraction >= 0.5 && row.rmsDbfs !== null && row.rmsDbfs >= -80;
    if (row.rmsDbfs === null && row.activeFraction !== 0) invalid("Null RMS represents measured digital zero, not missing data");
    if (!active && (row.mel !== null || row.lowShare !== null)) invalid("Inactive features must be unknown");
    if ((row.mel === null) !== (row.lowShare === null)) invalid("Partially missing packet features");
    if (row.mel && Math.abs(Math.hypot(...row.mel) - 1) > 0.0001) invalid("Invalid feature normalization");
  });
});

export const structureInputSchema = z.object({
  duration: z.number().finite().min(1).max(600),
  packets: z.array(structurePacketSchema).min(1).max(60),
  includeExperimentalRepetitions: z.boolean().default(false),
}).strict().superRefine((value, ctx) => {
  const invalid = (message: string) => ctx.addIssue({ code: z.ZodIssueCode.custom, message });
  if (value.packets.reduce((n, p) => n + p.frames.length, 0) > 660) invalid("Too many frames");
  let end = 0;
  for (const p of [...value.packets].sort((a, b) => a.source.offsetSeconds - b.source.offsetSeconds)) {
    if (p.source.offsetSeconds < end) invalid("Overlapping packets");
    end = p.source.offsetSeconds + p.source.duration;
    if (end > value.duration) invalid("Packet outside declared song");
  }
});

export function analyzeMusicStructure(args: unknown) {
  const input = structureInputSchema.parse(args);
  const { repetitions, repetitionsTotal, repetitionsTruncated, ...observations } = analyzeStructure({ ...input, packets: [...input.packets].sort((a, b) => a.source.offsetSeconds - b.source.offsetSeconds) });
  return {
    ...observations,
    ...(input.includeExperimentalRepetitions ? { repetitions, repetitionsTotal, repetitionsTruncated } : {}),
    repetitionEvidence: {
      status: input.includeExperimentalRepetitions ? "experimental" : "withheld",
      reason: "Frozen synthetic full-phrase retrieval F1 0.555556 did not meet 0.80. Short motifs and merged long returns differ from section labels; no real-song section accuracy established. Withheld never means no repetition.",
      optIn: "includeExperimentalRepetitions: true",
    },
    transitionEvidence: "Heuristic candidates: frozen synthetic confirmation found 7/7 boundaries plus 2 extras (macro F1 0.885714). Not validated real-song verse/chorus boundaries.",
  };
}

const nullableRatio = { anyOf: [{ type: "number", minimum: 0, maximum: 1 }, { type: "null" }] };
export const STRUCTURE_TOOL = {
  name: "analyze_music_structure",
  description: "Compare compact structurePacket values returned by analyze_music_timing for excerpts of one <=600s song. Supply duration and 1–60 nonoverlapping packets; keep each packet unchanged with its actual source.offsetSeconds. No audio, browser, model call or stored state required. Default returns transition candidates, spectral/energy contrasts and coverage/gaps. Recurrence failed the frozen full-phrase quality gate: withheld by default, never interpreted as no repetition. Set includeExperimentalRepetitions:true only for experimental recurring-sequence candidates with adjacent failed-gate metadata. Requires contiguous full1s rows; partial tails count coverage only. About1s resolution, not certified boundaries; similarity is not probability. No chorus/verse, instrument, emotion or intent labels. Packet hashes do NOT authenticate features or common song origin. At most16 experimental pairs/24 transitions; inspect total/truncated.",
  inputSchema: {
    type: "object", additionalProperties: false, required: ["duration", "packets"],
    properties: {
      duration: { type: "number", minimum: 1, maximum: 600 },
      includeExperimentalRepetitions: { type: "boolean", default: false, description: "Explicitly request experimental recurrence candidates despite failed full-phrase confirmation. False withholds arrays/totals, not evidence of absence." },
      packets: { type: "array", minItems: 1, maxItems: 60, items: {
        type: "object", additionalProperties: false, required: ["schema", "recipe", "source", "frames"], properties: {
          schema: { type: "string", const: "music-structure-packet/1" },
          recipe: { type: "string", const: "logmel16-1s-sequence-v1" },
          source: { type: "object", additionalProperties: false, required: ["sha256", "offsetSeconds", "duration"], properties: {
            sha256: { type: "string", pattern: "^[a-f0-9]{64}$" }, offsetSeconds: { type: "number", minimum: 0, maximum: 600 }, duration: { type: "number", minimum: 1, maximum: 30 },
          } },
          frames: { type: "array", minItems: 1, maxItems: 30, items: {
            type: "object", additionalProperties: false, required: ["start", "end", "activeFraction", "rmsDbfs", "lowShare", "mel"], properties: {
              start: { type: "number", minimum: 0, maximum: 30 }, end: { type: "number", minimum: 0, maximum: 30 },
              activeFraction: { type: "number", minimum: 0, maximum: 1 }, lowShare: nullableRatio,
              rmsDbfs: { description: "Centered-frame RMS proxy in dBFS. Null means measured digital zero with activeFraction0, never an omitted measurement.", anyOf: [{ type: "number", minimum: -160, maximum: 0 }, { type: "null" }] },
              mel: { anyOf: [{ type: "array", minItems: 16, maxItems: 16, items: { type: "number", minimum: 0, maximum: 1 } }, { type: "null" }] },
            },
          } },
        },
      } },
    },
  },
  annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
};
