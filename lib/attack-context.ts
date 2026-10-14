type Attack = { time: number; strength: number; band: string; fluxLow: number; fluxMid: number; fluxHigh: number };
const round = (value: number) => Math.round(value * 1e6) / 1e6;

/** Relate candidates to observed beat intervals, never extrapolate a grid. */
export function attackContext(candidates: Attack[], beats: number[], duration: number) {
  const events = candidates.slice(0, 64).map(candidate => {
    const sum = candidate.fluxLow + candidate.fluxMid + candidate.fluxHigh;
    let preceding = -1;
    if (beats.length >= 2 && candidate.time >= beats[0] && candidate.time <= beats[beats.length - 1]) {
      for (let i = 0; i < beats.length - 1; i++) {
        if (beats[i] <= candidate.time) preceding = i;
      }
    }
    const period = preceding >= 0 ? beats[preceding + 1] - beats[preceding] : 0;
    const nearest = preceding < 0 || period <= 0 ? -1
      : candidate.time <= (beats[preceding] + beats[preceding + 1]) / 2 ? preceding : preceding + 1;
    return {
      time: candidate.time, strength: candidate.strength, band: candidate.band,
      fluxShares: sum > 0 ? { low: round(candidate.fluxLow / sum), mid: round(candidate.fluxMid / sum), high: round(candidate.fluxHigh / sum) } : null,
      nearestBeat: nearest < 0 ? null : { index: nearest, time: beats[nearest], offsetSeconds: round(candidate.time - beats[nearest]) },
      beatPhase: preceding < 0 || period <= 0 ? null : { precedingBeatIndex: preceding, fraction: round((candidate.time - beats[preceding]) / period) },
    };
  });
  const density = Array.from({ length: Math.ceil(duration) }, (_, second) => {
    const end = Math.min(duration, second + 1);
    const inWindow = candidates.filter(candidate => candidate.time >= second && candidate.time < end);
    return { start: second, end, count: inWindow.length, candidatesPerSecond: round(inWindow.length / (end - second)),
      dominantBands: { low: inWindow.filter(x => x.band === 'low').length, mid: inWindow.filter(x => x.band === 'mid').length, high: inWindow.filter(x => x.band === 'high').length } };
  });
  return { events, total: candidates.length, truncated: events.length < candidates.length, density };
}
