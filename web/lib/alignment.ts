import type { DetailResult } from "./detail.ts";

// Temporal overlap only: mixture notes cannot establish the pitch of a word.
export function wordContexts(detail: DetailResult, start: number, end: number) {
  const round = (n: number) => Math.round(n * 10000) / 10000;
  const supported = !detail.rhythm.tempoEvidence || detail.rhythm.tempoEvidence.status === "supported";
  const beats = (supported ? detail.rhythm.beats : []).map((time, index) => ({ time, index })).sort((a, b) => a.time - b.time);
  const matching = detail.lyrics.words.map((word, index) => ({ ...word, wordIndex: index })).filter(w => w.start < end && w.end > start);
  return { relation: "temporal-overlap-only; not vocal note identity or forced alignment", total: matching.length, truncated: matching.length > 200,
    items: matching.slice(0, 200).map(word => {
      let nearest = null as { time: number; index: number } | null;
      for (const beat of beats) if (!nearest || Math.abs(beat.time - word.start) < Math.abs(nearest.time - word.start)) nearest = beat;
      if (nearest && detail.rhythm.bpm && Math.abs(nearest.time - word.start) > 60 / detail.rhythm.bpm * 0.55) nearest = null;
      const notes = detail.notes.items.map((n, index) => ({ ...n, noteIndex: index })).filter(n => n.start < word.end && n.end > word.start);
      return { ...word, nearestBeat: nearest ? { beatIndex: nearest.index, time: nearest.time, offsetSeconds: round(word.start - nearest.time), status: "estimated grid, not downbeat" } : null,
        simultaneousNotes: { items: notes.slice(0, 16), total: notes.length, truncated: notes.length > 16 },
        chordCandidates: detail.rhythm.chords.filter(c => c.start < word.end && c.end > word.start).slice(0, 4) };
    }) };
}
