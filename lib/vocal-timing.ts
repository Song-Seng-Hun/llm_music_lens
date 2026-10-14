import type { TimingInput } from "./music-timing.ts";

// Preserve supplied phonetic units; never divide a word into invented timings.
export function linkVocalTiming(input: TimingInput, beats: number[], downbeats: number[], duration: number) {
  const vocal = input.timedVocal;
  if (!vocal) return { status: "unavailable", reason: "No supplied timed units; server forced alignment is unavailable." };
  const notes = input.noteEvents?.items ?? [];
  return {
    status: "linked-supplied-timing", unit: vocal.unit, source: vocal.source,
    relation: "supplied timestamps linked to audio-derived beats; no new forced alignment or vocal-note identity proof",
    noteSource: input.noteEvents ? { source: input.noteEvents.source, mode: input.noteEvents.mode } : null,
    items: vocal.items.map((v, index) => {
      let nearest = -1, preceding = -1, nextDownbeat = -1;
      for (let i = 0; i < beats.length; i++) {
        if (nearest < 0 || Math.abs(beats[i] - v.start) < Math.abs(beats[nearest] - v.start)) nearest = i;
        if (beats[i] <= v.start) preceding = i;
      }
      for (let i = 0; i < downbeats.length; i++) if (nextDownbeat < 0 || Math.abs(downbeats[i] - v.start) < Math.abs(downbeats[nextDownbeat] - v.start)) nextDownbeat = i;
      const period = preceding >= 0 && preceding + 1 < beats.length ? beats[preceding + 1] - beats[preceding] : null;
      const simultaneous = notes.map((n, noteIndex) => ({ ...n, noteIndex })).filter(n => n.start < v.end && n.end > v.start);
      return { ...v, index,
        nearestBeat: nearest < 0 ? null : { index: nearest, time: beats[nearest], offsetSeconds: v.start - beats[nearest] },
        beatPhase: period && period > 0 ? { precedingBeatIndex: preceding, fraction: (v.start - beats[preceding]) / period } : null,
        nearestDownbeat: nextDownbeat < 0 ? null : { index: nextDownbeat, time: downbeats[nextDownbeat], offsetSeconds: v.start - downbeats[nextDownbeat] },
        simultaneousNotes: { items: simultaneous.slice(0, 16), total: simultaneous.length, truncated: simultaneous.length > 16 },
      };
    }),
    onsetDensity: Array.from({ length: Math.ceil(duration) }, (_, second) => ({ start: second, end: Math.min(duration, second + 1),
      unitsPerSecond: vocal.items.filter(x => x.start >= second && x.start < second + 1).length / Math.min(1, duration - second),
    })),
    warnings: ["Density counts supplied units, not automatic syllables or a rap classifier.", "Offsets depend on both supplied alignment and estimated beats. Source labels are caller attribution, not verification."],
  };
}
