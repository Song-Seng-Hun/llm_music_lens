export const STRUCTURE_RECIPE = 'logmel16-1s-sequence-v1';
const EPSILON = 1e-8;
const round = value => Math.round(value * 10000) / 10000;
const same = (a, b) => Math.abs(a - b) <= EPSILON;
const mean = values => values.reduce((sum, value) => sum + value, 0) / values.length;
const cosineDistance = (a, b) => {
  let dot = 0, aa = 0, bb = 0;
  for (let i = 0; i < 16; i++) { dot += a[i] * b[i]; aa += a[i] ** 2; bb += b[i] ** 2; }
  return Math.max(0, Math.min(1, 1 - dot / Math.sqrt(aa * bb)));
};
const db = frame => frame.rmsDbfs ?? -160;

function checkedFrames(duration, packets) {
  if (!Number.isFinite(duration) || duration <= 0 || duration > 600 || !Array.isArray(packets) || !packets.length || packets.length > 60) {
    throw new RangeError('Structure accepts a positive duration up to 600 seconds and 1–60 packets');
  }
  const all = [], intervals = [];
  let previousEnd = 0, group = 0, rows = 0;
  for (const packet of packets) {
    const source = packet?.source;
    if (packet?.schema !== 'music-structure-packet/1' || packet.recipe !== STRUCTURE_RECIPE ||
        !source || !/^[a-f0-9]{64}$/.test(source.sha256) || !Number.isFinite(source.offsetSeconds) || source.offsetSeconds < 0 ||
        !Number.isFinite(source.duration) || source.duration < 1 || source.duration > 30 ||
        source.offsetSeconds < previousEnd || source.offsetSeconds + source.duration > duration ||
        !Array.isArray(packet.frames) || packet.frames.length !== Math.ceil(source.duration)) {
      throw new RangeError('Incompatible, overlapping or malformed structure packet');
    }
    const end = source.offsetSeconds + source.duration;
    if (intervals.length && same(intervals.at(-1).end, source.offsetSeconds)) intervals.at(-1).end = end;
    else intervals.push({ start: source.offsetSeconds, end });
    previousEnd = end;
    for (let i = 0; i < packet.frames.length; i++) {
      const f = packet.frames[i]; rows++;
      const finiteRange = (value, min, max) => Number.isFinite(value) && value >= min && value <= max;
      if (!f || f.start !== i || f.end !== Math.min(i + 1, source.duration) || !finiteRange(f.activeFraction, 0, 1) ||
          !(f.rmsDbfs === null || finiteRange(f.rmsDbfs, -160, 0)) ||
          !(f.lowShare === null || finiteRange(f.lowShare, 0, 1)) ||
          !(f.mel === null || (Array.isArray(f.mel) && f.mel.length === 16 && f.mel.every(v => finiteRange(v, 0, 1)) && Math.abs(Math.hypot(...f.mel) - 1) <= 0.0001)) ||
          ((f.mel === null) !== (f.lowShare === null)) ||
          (f.rmsDbfs === null && f.activeFraction !== 0) ||
          (f.mel !== null && (f.activeFraction < 0.5 || f.rmsDbfs === null || f.rmsDbfs < -80))) {
        throw new RangeError('Invalid structure frame vector, gate or exact local clock');
      }
      if (!same(f.end - f.start, 1)) { group++; continue; }
      const start = source.offsetSeconds + f.start;
      if (all.length && !same(all.at(-1).end, start)) group++;
      all.push({ ...f, start, end: source.offsetSeconds + f.end, group });
    }
  }
  if (rows > 660 || all.length > 600) throw new RangeError('Excessive structure frames');
  const gaps = [];
  let cursor = 0;
  for (const interval of intervals) {
    if (interval.start > cursor) gaps.push({ start: cursor, end: interval.start });
    cursor = interval.end;
  }
  if (cursor < duration) gaps.push({ start: cursor, end: duration });
  const coveredSeconds = intervals.reduce((sum, interval) => sum + interval.end - interval.start, 0);
  return { frames: all, rows, coverage: { coveredSeconds, fraction: coveredSeconds / duration, complete: gaps.length === 0, intervals, gaps } };
}

function varying(frames) {
  const distances = [];
  for (let i = 1; i < frames.length; i++) distances.push(cosineDistance(frames[i - 1].mel, frames[i].mel));
  const levels = frames.map(db);
  return mean(distances) >= 0.004 || Math.max(...levels) - Math.min(...levels) >= 3;
}

function recurrence(frames) {
  const n = frames.length, matrix = new Float32Array(n * n).fill(1), candidates = [];
  let comparisonCells = 0;
  for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
    if (frames[i].mel === null || frames[j].mel === null) continue;
    matrix[i * n + j] = cosineDistance(frames[i].mel, frames[j].mel); comparisonCells++;
  }
  function save(start, length, lag) {
    if (length < 4) return;
    const a = frames.slice(start, start + length), b = frames.slice(start + lag, start + lag + length);
    if (!varying(a) || !varying(b)) return;
    const spectralDistance = mean(a.map((_, i) => matrix[(start + i) * n + start + lag + i]));
    let unalignedDistance = 0;
    for (let i = 0; i < length; i++) for (let j = 0; j < length; j++) {
      if (i !== j) unalignedDistance += matrix[(start + i) * n + start + lag + j];
    }
    unalignedDistance /= length * (length - 1);
    // Ordered content must improve over this window pair's unaligned timbre.
    if (spectralDistance > 0.5 * unalignedDistance) return;
    const levelA = mean(a.map(db)), levelB = mean(b.map(db));
    const envelopeError = mean(a.map((f, i) => Math.abs((db(f) - levelA) - (db(b[i]) - levelB))));
    const similarity = 1 - 0.75 * spectralDistance - 0.25 * Math.min(1, envelopeError / 12);
    if (similarity < 0.94 || envelopeError > 2.5) return;
    const lowA = a.filter(f => f.lowShare !== null), lowB = b.filter(f => f.lowShare !== null);
    candidates.push({ first: { start: a[0].start, end: a.at(-1).end }, second: { start: b[0].start, end: b.at(-1).end },
      durationSeconds: length, similarity: round(similarity), contrast: {
        rmsDbChange: round(levelB - levelA), lowShareChange: lowA.length && lowB.length ? round(mean(lowB.map(f => f.lowShare)) - mean(lowA.map(f => f.lowShare))) : null } });
  }
  for (let lag = 4; lag < n; lag++) {
    let start = 0, length = 0;
    for (let i = 0; i + lag < n; i++) {
      const j = i + lag;
      const continuous = !length || (frames[i].group === frames[i - 1].group && frames[j].group === frames[j - 1].group);
      const delta = length ? Math.abs((db(frames[i]) - db(frames[i - 1])) - (db(frames[j]) - db(frames[j - 1]))) : 0;
      const match = matrix[i * n + j] <= 0.06 && delta <= 3 && frames[j].start - frames[i].start >= 4;
      if (!match || !continuous || length >= 60 || (length && frames[i].end > frames[start + lag].start)) {
        save(start, length, lag); length = 0;
      }
      if (match) { if (!length) start = i; length++; }
    }
    save(start, length, lag);
  }
  candidates.sort((a, b) => b.durationSeconds - a.durationSeconds || b.similarity - a.similarity || a.first.start - b.first.start || a.second.start - b.second.start);
  const selected = [];
  for (const candidate of candidates) {
    const overlap = (a, b) => Math.max(0, Math.min(a.end, b.end) - Math.max(a.start, b.start));
    if (selected.some(p => {
      const shorter = Math.min(p.durationSeconds, candidate.durationSeconds);
      const samePair = overlap(p.first, candidate.first) >= 0.8 * shorter && overlap(p.second, candidate.second) >= 0.8 * shorter;
      const contained = p.durationSeconds > candidate.durationSeconds && [candidate.first, candidate.second].every(occurrence =>
        Math.max(overlap(p.first, occurrence), overlap(p.second, occurrence)) >= 0.8 * candidate.durationSeconds);
      return samePair || contained;
    })) continue;
    selected.push(candidate);
  }
  return { repetitions: selected.slice(0, 16), repetitionsTotal: selected.length, repetitionsTruncated: selected.length > 16, comparisonCells };
}

function transitions(frames) {
  const candidates = [];
  for (let i = 3; i <= frames.length - 3; i++) {
    const before = frames.slice(i - 3, i), after = frames.slice(i, i + 3);
    if (before[0].group !== after.at(-1).group) continue;
    const shape = side => side.every(f => f.mel !== null) ? Array.from({ length: 16 }, (_, band) => mean(side.map(f => f.mel[band]))) : null;
    const a = shape(before), b = shape(after), spectralDistance = a && b ? cosineDistance(a, b) : null;
    const rmsDbChange = mean(after.map(db)) - mean(before.map(db));
    const low = side => side.every(f => f.lowShare !== null) ? mean(side.map(f => f.lowShare)) : null;
    const lowA = low(before), lowB = low(after), lowShareChange = lowA !== null && lowB !== null ? lowB - lowA : null;
    const novelty = Math.max((spectralDistance ?? 0) / 0.08, Math.abs(rmsDbChange) / 6, Math.abs(lowShareChange ?? 0) / 0.2);
    if (novelty < 1) continue;
    candidates.push({ time: after[0].start, novelty: round(novelty), support: {
      before: { start: before[0].start, end: before.at(-1).end }, after: { start: after[0].start, end: after.at(-1).end } },
    contrast: { spectralDistance: spectralDistance === null ? null : round(spectralDistance), rmsDbChange: round(rmsDbChange), lowShareChange: lowShareChange === null ? null : round(lowShareChange) } });
  }
  candidates.sort((a, b) => b.novelty - a.novelty || a.time - b.time);
  const selected = [];
  for (const candidate of candidates) if (!selected.some(p => Math.abs(p.time - candidate.time) < 4)) selected.push(candidate);
  return { transitions: selected.slice(0, 24).sort((a, b) => a.time - b.time), transitionsTotal: selected.length, transitionsTruncated: selected.length > 24 };
}

/** Stateless caller-packet evidence; hashes do not authenticate the audio. */
export function analyzeStructure({ duration, packets, includeExperimentalRepetitions = true }) {
  const { frames, rows, coverage } = checkedFrames(duration, packets);
  const { comparisonCells, ...repeats } = includeExperimentalRepetitions ? recurrence(frames) : {
    repetitions: [], repetitionsTotal: 0, repetitionsTruncated: false, comparisonCells: 0,
  };
  return { schema: 'music-structure/1', recipe: STRUCTURE_RECIPE, durationSeconds: duration, coverage, ...repeats, ...transitions(frames),
    method: includeExperimentalRepetitions
      ? '16 grouped log-mel bands; aligned cosine sequences >=4s, <=60s; variation and 0.5 unaligned-distance specificity gates; maximal pairs suppress >=80% overlapping/contained subpatterns; 3s before/after novelty; 1s resolution'
      : '16 grouped log-mel bands; recurrence withheld (not computed); 3s before/after novelty; 1s resolution',
    warnings: [
      'Caller-held packets are unverified observations; source hashes and offsets do not authenticate a song, continuity or source attribution.',
      'Similarity and novelty are heuristic measurements, not probabilities or verified chorus, verse, instrument or artistic-intent labels.',
      'Longest nonredundant recurrence evidence is preferred. Short motifs within stronger occurrences and substantially overlapping phase variants may be omitted; this is not an exhaustive motif catalogue.',
      'Coverage describes supplied packet intervals. Comparisons require full one-second bins; partial tails and sparse/quiet spectral bins are unavailable for recurrence.',
      'Each occurrence and each transition support must be continuous; known occurrences may be compared on opposite sides of an unknown gap.',
      'RMS is a centered-frame energy proxy; null RMS requires zero active frames and uses the -160 dBFS comparison floor. Low-band power changes do not identify drums or instruments.',
    ], diagnostics: { rows, fullSecondRows: frames.length, comparisonCells } };
}
