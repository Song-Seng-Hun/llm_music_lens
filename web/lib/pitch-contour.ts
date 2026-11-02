import { z } from "zod";

// Uniform native frames are compact in storage; readers request bounded intervals.
export const pitchContourSchema = z.object({
  status: z.enum(["complete", "partial"]),
  method: z.literal("SOME native frame MIDI/rest; centered STFT; no smoothing"),
  duration: z.number().positive().max(600), stepSeconds: z.literal(512 / 44100),
  chunks: z.array(z.object({ start: z.number().finite().nonnegative().max(600), midi: z.array(z.number().finite().min(0).max(127).nullable()).min(1).max(4000) })).max(16),
  truncated: z.boolean(),
}).superRefine((v, ctx) => {
  let count = 0, previous = -v.stepSeconds;
  for (const c of v.chunks) {
    const last = c.start + (c.midi.length - 1) * v.stepSeconds;
    if (c.start <= previous || last >= v.duration || (v.status === "complete" && c.start - previous > 2 * v.stepSeconds + 1e-9)) ctx.addIssue({ code: "custom", message: "Invalid contour timing or coverage." });
    previous = last; count += c.midi.length;
  }
  if (count > 52000 || v.truncated !== (v.status === "partial") || (v.status === "complete" && (!count || v.duration - previous > v.stepSeconds + 1e-9))) ctx.addIssue({ code: "custom", message: "Invalid contour completeness or sample limit." });
});
export type PitchContour = z.infer<typeof pitchContourSchema>;

export function contourSummary(contour?: PitchContour) {
  return contour ? { status: contour.status, method: contour.method, duration: contour.duration, stepSeconds: contour.stepSeconds,
    samples: contour.chunks.reduce((n, c) => n + c.midi.length, 0), truncated: contour.truncated,
    meaning: "Fractional MIDI estimates; null=model-unvoiced, not silence. No calibrated confidence or validated vibrato/slide labels. Missing contour=unknown." }
    : { status: "unavailable" as const };
}

export function contourInterval(contour: PitchContour | undefined, start: number, end: number, limit = 200) {
  const items: { time: number; midi: number | null }[] = [];
  let total = 0, nextStart: number | null = null;
  for (const chunk of contour?.chunks ?? []) for (let i = 0; i < chunk.midi.length; i++) {
    const time = chunk.start + i * contour!.stepSeconds;
    if (time < start || time >= end) continue;
    total++;
    if (items.length < limit) items.push({ time, midi: chunk.midi[i] });
    else if (nextStart === null) nextStart = time;
  }
  return { ...contourSummary(contour), items, total, truncated: nextStart !== null, sourceTruncated: contour?.truncated ?? false,
    nextStart, continuation: nextStart === null ? null : "Query get_music_timeline again with start=nextStart and the same end; native frames are not averaged." };
}
