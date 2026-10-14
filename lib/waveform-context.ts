import type { TimingInput } from "./music-timing.ts";

type Region = { start: number; end: number; peakTime: number; startClipped: boolean; endClipped: boolean };

/** Link measured energy regions without moving any acoustic or supplied time. */
export function waveformContext(regions: Region[], input: TimingInput, beats: number[]) {
  const nearest = (time: number) => {
    let index = -1;
    for (let i = 0; i < beats.length; i++) {
      if (index < 0 || Math.abs(beats[i] - time) < Math.abs(beats[index] - time)) index = i;
    }
    return index < 0 ? null : { index, time: beats[index], offsetSeconds: Math.round((time - beats[index]) * 1000000) / 1000000 };
  };
  const overlap = (region: Region, items: { start: number; end: number }[]) => {
    const indices = items.flatMap((item, index) => item.start < region.end && item.end > region.start ? [index] : []);
    return { indices: indices.slice(0, 8), total: indices.length, truncated: indices.length > 8 };
  };
  // Optional links have a separate cap: MCP clients may receive both text and
  // structured JSON. Preserve every retained energy event, bound link overhead.
  const items = regions.slice(0, 16).map((region, regionIndex) => ({
    regionIndex,
    // Clip edges observe only the available excerpt, not the true entry/exit.
    startBeat: region.startClipped ? null : nearest(region.start),
    endBeat: region.endClipped ? null : nearest(region.end),
    peakBeat: nearest(region.peakTime),
    suppliedVocalUnits: overlap(region, input.timedVocal?.items ?? []),
    suppliedNotes: overlap(region, input.noteEvents?.items ?? []),
  }));
  return { items, total: regions.length, truncated: items.length < regions.length };
}
