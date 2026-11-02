/** Summaries of detected intervals, not a metrical certification. */
export function beatContext(beats: number[], downbeats: number[]) {
  const intervals = beats.slice(1).map((t, i) => t - beats[i]).filter(n => n > 0);
  const sorted = [...intervals].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  const median = sorted.length ? sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2 : null;
  const bpm = median === null ? null : 60 / median;
  return {
    tempo: {
      bpm: bpm !== null && bpm >= 20 && bpm <= 400 ? Math.round(bpm * 100) / 100 : null,
      method: "median of detected consecutive beat intervals; tempo octave/half-time remains uncertain",
      intervalCount: intervals.length,
      minimumIntervalSeconds: sorted[0] ?? null, maximumIntervalSeconds: sorted.at(-1) ?? null,
    },
    // An omitted downbeat may span multiple measures. Expose the count rather
    // than silently claiming 3/4, 4/4, a measure number or an actual first bar.
    downbeatSpans: downbeats.slice(1).map((end, i) => ({
      start: downbeats[i], end,
      detectedBeatCount: beats.filter(time => time >= downbeats[i] && time < end).length,
    })),
    meter: null,
    meterStatus: "unverified; intervals between downbeat candidates are not guaranteed complete bars",
  };
}
