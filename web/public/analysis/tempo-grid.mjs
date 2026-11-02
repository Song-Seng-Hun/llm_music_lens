const clamp = (x, lo = 0, hi = 1) => Math.max(lo, Math.min(hi, x));
const finite = Number.isFinite;
const round = x => Math.round(x * 1000) / 1000;

function addCandidate(list, bpm, source) {
  if (!finite(bpm) || bpm < 40 || bpm > 240) return;
  const existing = list.find(x => Math.abs(x.bpm - bpm) <= Math.max(1, bpm * 0.025));
  if (existing) {
    if (source === 'observed-interval') existing.direct = true;
    return;
  }
  list.push({ bpm, direct: source === 'observed-interval' });
}

function candidatesFromFlux(series, frameRate) {
  if (series.length < frameRate * 2) return [];
  const mean = series.reduce((a, b) => a + b, 0) / series.length;
  const centered = series.map(x => x - mean);
  const minLag = frameRate * 60 / 240, maxLag = frameRate * 60 / 40, step = 0.1;
  const curve = [];
  for (let lag = minLag - 0.2; lag <= maxLag + 0.2; lag += step) {
    let xy = 0, xx = 0, yy = 0;
    for (let i = Math.ceil(lag); i < centered.length - 1; i++) {
      const p = i - lag, left = Math.floor(p), frac = p - left;
      const a = centered[i], b = centered[left] * (1 - frac) + centered[left + 1] * frac;
      xy += a * b; xx += a * a; yy += b * b;
    }
    curve.push({ lag, score: xx && yy ? Math.max(0, xy / Math.sqrt(xx * yy)) : 0 });
  }
  const peaks = [];
  for (let i = 1; i < curve.length - 1; i++) {
    if (curve[i].score < curve[i - 1].score || curve[i].score <= curve[i + 1].score || curve[i].score < 0.05) continue;
    const a = curve[i - 1].score, b = curve[i].score, c = curve[i + 1].score;
    const den = a - 2 * b + c, delta = den ? clamp(0.5 * (a - c) / den, -1, 1) : 0;
    let lag = curve[i].lag + delta * step;
    if (lag < minLag && lag >= minLag * 0.95) lag = minLag;
    if (lag > maxLag && lag <= maxLag * 1.05) lag = maxLag;
    if (lag >= minLag && lag <= maxLag) peaks.push({ bpm: 60 * frameRate / lag, score: b });
  }
  peaks.sort((a, b) => b.score - a.score);
  return peaks.slice(0, 6).map(x => x.bpm);
}

function supportAt(bpm, onsets, duration, frameRate, phaseCount = 48) {
  const period = 60 / bpm;
  const tolerance = Math.min(period * 0.28, Math.max(1.5 / frameRate, period * 0.12));
  const totalWeight = onsets.reduce((s, x) => s + x.strength, 0) || onsets.length;
  let best = { support: 0, eventCoverage: 0, slotCoverage: 0, phase: 0, medianOffsetMs: null };
  for (let p = 0; p < phaseCount; p++) {
    const phase = period * p / phaseCount;
    const occupied = new Set(), offsets = [];
    let hitWeight = 0;
    for (const onset of onsets) {
      const beatIndex = Math.round((onset.time - phase) / period);
      const beatTime = phase + beatIndex * period;
      const offset = Math.abs(onset.time - beatTime);
      if (offset <= tolerance) {
        occupied.add(beatIndex);
        hitWeight += onset.strength || 1;
        offsets.push(offset * 1000);
      }
    }
    const firstIndex = Math.ceil((0 - phase) / period), lastIndex = Math.floor((duration - phase) / period);
    const expected = Math.max(1, lastIndex - firstIndex + 1);
    const eventCoverage = clamp(hitWeight / totalWeight);
    const slotCoverage = clamp(occupied.size / expected);
    const score = Math.sqrt(eventCoverage * slotCoverage);
    if (score > best.support + 1e-12 || (Math.abs(score - best.support) <= 1e-12
      && offsets.length && (best.medianOffsetMs === null || offsets.sort((a, b) => a - b)[Math.floor(offsets.length / 2)] < best.medianOffsetMs))) {
      offsets.sort((a, b) => a - b);
      best = { support: score, eventCoverage, slotCoverage, phase, medianOffsetMs: offsets.length ? offsets[Math.floor(offsets.length / 2)] : null };
    }
  }
  return best;
}

function makeSupportedBeats(phase, bpm, duration, onsets, frameRate) {
  const period = 60 / bpm, tolerance = Math.min(period * 0.28, Math.max(1.5 / frameRate, period * 0.12));
  const beats = [];
  let onsetIndex = 0;
  for (let time = phase; time < duration && beats.length < 4000; time += period) {
    while (onsetIndex < onsets.length && onsets[onsetIndex].time < time - tolerance) onsetIndex++;
    const nearby = onsetIndex < onsets.length && Math.abs(onsets[onsetIndex].time - time) <= tolerance;
    const prev = onsetIndex > 0 ? onsets[onsetIndex - 1] : null;
    const next = onsetIndex < onsets.length ? onsets[onsetIndex] : null;
    const oneMissedBeat = prev && next && next.time - prev.time <= period * 2.5 && time >= prev.time && time <= next.time;
    if (nearby || oneMissedBeat) beats.push(round(time));
  }
  return beats;
}

function localWindows(onsets, duration, frameRate) {
  if (duration < 12 || onsets.length < 10) return [];
  const width = Math.min(10, duration), step = Math.max(width / 2, duration / 79), windows = [];
  for (let start = 0; start < duration; start += step) {
    const end = Math.min(duration, start + width);
    const local = onsets.filter(x => x.time >= start && x.time < end);
    if (local.length < 5) continue;
    const span = local.at(-1).time - local[0].time;
    if (span <= 0) continue;
    const bpm = 60 * (local.length - 1) / span;
    if (bpm < 40 || bpm > 240) continue;
    const gaps = local.slice(1).map((x, i) => x.time - local[i].time), mean = gaps.reduce((a, b) => a + b, 0) / gaps.length;
    const variance = gaps.reduce((s, x) => s + (x - mean) ** 2, 0) / gaps.length;
    const regularity = clamp(1 - Math.sqrt(variance) / Math.max(mean, 1e-6) * 2.5);
    const shifted = local.map(x => ({ ...x, time: x.time - start }));
    const fit = supportAt(bpm, shifted, end - start, frameRate, 32).support;
    const score = Math.sqrt(regularity * fit);
    if (score >= 0.5) windows.push({ start, end: Math.min(duration, start + step), bpm, score });
  }
  return windows;
}

function findVariableSegments(windows) {
  if (windows.length < 4) return [];
  const clusters = [];
  for (const window of windows) {
    let cluster = clusters.find(x => Math.abs(x.bpm - window.bpm) / x.bpm <= 0.08);
    if (!cluster) { cluster = { values: [], bpm: window.bpm }; clusters.push(cluster); }
    cluster.values.push(window);
    cluster.bpm = cluster.values.reduce((s, x) => s + x.bpm, 0) / cluster.values.length;
  }
  const strong = clusters.filter(x => x.values.length >= 2);
  if (!strong.some((a, i) => strong.slice(i + 1).some(b => Math.abs(a.bpm - b.bpm) / Math.min(a.bpm, b.bpm) >= 0.12))) return [];
  const segments = [];
  for (const window of windows) {
    const prior = segments.at(-1);
    if (prior && Math.abs(prior.bpm - window.bpm) / prior.bpm <= 0.08 && window.start <= prior.end + 0.001) {
      prior.end = window.end; prior.bpm = round((prior.bpm + window.bpm) / 2); prior.score = round(Math.min(prior.score, window.score));
    } else segments.push({ start: round(window.start), end: round(window.end), bpm: round(window.bpm), score: round(window.score) });
  }
  return segments.slice(0, 80);
}

function insufficient(reason, candidates = [], segments = []) {
  return { bpm: null, candidates: candidates.slice(0, 8), beats: [], evidence: {
    status: 'insufficient', onsetCoverage: 0, gridSupport: 0, medianOffsetMs: null, segments: segments.slice(0, 80), reason,
  } };
}

export function estimateTempo(onsetSeries, frameRate, inputOnsets, duration) {
  if (!(onsetSeries instanceof Float32Array || onsetSeries instanceof Float64Array || Array.isArray(onsetSeries))
    || !finite(frameRate) || frameRate <= 0 || !finite(duration) || duration <= 0 || duration > 600 || !Array.isArray(inputOnsets)) {
    return insufficient('Tempo input is invalid or outside the 600-second analysis bound.');
  }
  const onsets = inputOnsets.filter(x => finite(x?.time) && x.time >= 0 && x.time < duration && finite(x?.strength) && x.strength >= 0)
    .map(x => ({ time: x.time, strength: clamp(x.strength), band: x.band })).sort((a, b) => a.time - b.time);
  if (onsets.length < 4) return insufficient('At least four onset candidates are required for a stable local pulse estimate.');

  const rawCandidates = [];
  const observedBpm = 60 * (onsets.length - 1) / (onsets.at(-1).time - onsets[0].time);
  if (observedBpm >= 38 && observedBpm <= 252) addCandidate(rawCandidates, clamp(observedBpm, 40, 240), 'observed-interval');
  for (const bpm of candidatesFromFlux(onsetSeries, frameRate)) addCandidate(rawCandidates, bpm, 'flux-periodicity');
  for (const base of [...rawCandidates]) for (const factor of [0.5, 2]) addCandidate(rawCandidates, clamp(base.bpm * factor, 40, 240), 'octave-ambiguity');
  if (!rawCandidates.length) return insufficient('No tempo candidate fell within the supported 40 to 240 BPM range.');

  const evaluated = rawCandidates.map(candidate => {
    const fit = supportAt(candidate.bpm, onsets, duration, frameRate);
    const intervalAffinity = Math.exp(-Math.abs(Math.log(candidate.bpm / observedBpm)) * 2.5);
    const score = clamp(fit.support * (0.82 + 0.18 * intervalAffinity));
    return { bpm: candidate.bpm, score, direct: candidate.direct, fit };
  }).sort((a, b) => b.score - a.score);
  const publicCandidates = evaluated.slice(0, 8).map(x => ({ bpm: round(x.bpm), score: round(x.score) }));
  const variable = findVariableSegments(localWindows(onsets, duration, frameRate));
  if (variable.length) {
    const best = evaluated[0];
    return { bpm: null, candidates: publicCandidates, beats: [], evidence: {
      status: 'variable', onsetCoverage: round(best?.fit.eventCoverage ?? 0), gridSupport: round(best?.fit.slotCoverage ?? 0), medianOffsetMs: null, segments: variable,
      reason: 'Local tempo segments vary; global coverage values describe the strongest candidate only, and no single beat grid is emitted.',
    } };
  }
  const best = evaluated[0], second = evaluated[1];
  if (!best || best.score < 0.48) {
    return insufficient('Onset timing does not support a stable beat grid; candidate pulse evidence is too weak.', publicCandidates);
  }
  if (second && ((best.score < 0.72 && second.score >= 0.5) || (!best.direct && Math.abs(best.score - second.score) < 0.035))) {
    return { bpm: null, candidates: publicCandidates, beats: [], evidence: {
      status: 'ambiguous', onsetCoverage: round(best.fit.eventCoverage), gridSupport: round(best.fit.slotCoverage), medianOffsetMs: best.fit.medianOffsetMs === null ? null : round(best.fit.medianOffsetMs),
      segments: [], reason: 'Multiple tempo candidates fit the onset evidence similarly; no single grid is selected.',
    } };
  }
  const beats = makeSupportedBeats(best.fit.phase, best.bpm, duration, onsets, frameRate);
  return { bpm: round(best.bpm), candidates: publicCandidates, beats, evidence: {
    status: 'supported', onsetCoverage: round(best.fit.eventCoverage), gridSupport: round(best.fit.slotCoverage),
    medianOffsetMs: best.fit.medianOffsetMs === null ? null : round(best.fit.medianOffsetMs), segments: [],
    reason: 'A stable local pulse grid is supported by the measured onset timing; this does not establish musical tactus or meter.',
  } };
}
