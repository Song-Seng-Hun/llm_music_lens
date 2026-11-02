const MAX_TIME = 600;
const MAX_EVENTS = 4000;
const MAX_CHORDS = 300;
const NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
// Krumhansl-Kessler profiles; duration-weighted correlation convention:
// https://music21.org/music21docs/_modules/music21/analysis/discrete.html
// Added abstention thresholds are product heuristics, not validated accuracy gates.
const MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88];
const MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17];
const finite = x => typeof x === 'number' && Number.isFinite(x);
const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
const round = x => Math.round(x * 10000) / 10000;

function cleanEvents(stage, duration) {
  if (!Array.isArray(stage?.items)) return [];
  const durationOnly = stage.mode === 'solo-vocal';
  return stage.items.slice(0, MAX_EVENTS).filter(n => finite(n?.start) && finite(n?.end) && finite(n?.midi) && (finite(n?.confidence) || (durationOnly && n.confidence === null)) && n.end > n.start)
    .map(n => ({ start: clamp(n.start, 0, duration), end: clamp(n.end, 0, duration), pc: ((Math.round(n.midi) % 12) + 12) % 12, confidence: durationOnly ? 1 : clamp(n.confidence, 0, 1) }))
    .filter(n => n.end > n.start && n.confidence > 0);
}

function weightedPcs(events, duration) {
  const weights = Array(12).fill(0);
  let activeSeconds = 0;
  const times = [...new Set(events.flatMap(e => [e.start, e.end]))].sort((a, b) => a - b);
  for (let i = 0; i + 1 < times.length; i++) {
    const start = times[i], end = times[i + 1];
    if (end <= start) continue;
    const active = Array(12).fill(0);
    for (const e of events) if (e.start < end && e.end > start && e.confidence > 0) active[e.pc] = Math.max(active[e.pc], e.confidence);
    const dt = end - start;
    if (active.some(x => x > 0)) activeSeconds += dt;
    for (let pc = 0; pc < 12; pc++) weights[pc] += active[pc] * dt;
  }
  const total = weights.reduce((a, b) => a + b, 0);
  const pitchClasses = total ? weights.map((v, i) => ({ name: NAMES[i], share: round(v / total) })).filter(x => x.share > 0).sort((a, b) => NAMES.indexOf(a.name) - NAMES.indexOf(b.name)) : [];
  return { weights, activeSeconds: Math.min(duration, activeSeconds), pitchClasses };
}

function correlation(a, b) {
  const meanA = a.reduce((x, y) => x + y, 0) / a.length, meanB = b.reduce((x, y) => x + y, 0) / b.length;
  let numerator = 0, aa = 0, bb = 0;
  for (let i = 0; i < a.length; i++) { const x = a[i] - meanA, y = b[i] - meanB; numerator += x * y; aa += x * x; bb += y * y; }
  return aa && bb ? clamp(numerator / Math.sqrt(aa * bb), -1, 1) : 0;
}

function estimateKey(weights, activeSeconds, pcCount, status) {
  if (!weights.some(x => x > 0)) return { status: status === 'complete' ? 'insufficient' : 'ambiguous', label: null, tonic: null, mode: null, score: 0, candidates: [], margin: 0, reason: 'No positive-confidence pitch evidence is available.' };
  const candidates = [];
  for (let tonic = 0; tonic < 12; tonic++) for (const [mode, profile] of [['major', MAJOR], ['minor', MINOR]]) {
    const rotated = Array.from({ length: 12 }, (_, i) => profile[(i - tonic + 12) % 12]);
    candidates.push({ label: `${NAMES[tonic]} ${mode}`, tonic: NAMES[tonic], mode, correlation: round(correlation(weights, rotated)) });
  }
  candidates.sort((a, b) => b.correlation - a.correlation || a.label.localeCompare(b.label));
  const top = candidates[0], margin = Math.max(0, top.correlation - candidates[1].correlation);
  const returned = candidates.slice(0, 4);
  const base = { status: 'insufficient', label: null, tonic: null, mode: null, score: round(Math.max(0, top.correlation)), candidates: returned, margin: round(margin), reason: '' };
  if (status !== 'complete') { base.status = 'ambiguous'; base.reason = 'Partial or truncated note evidence cannot establish whole-song key.'; return base; }
  if (pcCount < 5 || activeSeconds < 4) { base.reason = 'Need at least five meaningful pitch classes and four seconds of active note evidence.'; return base; }
  if (top.correlation < 0.65) { base.status = 'ambiguous'; base.reason = 'Best profile correlation is below the conservative key threshold.'; return base; }
  if (margin < 0.08) { base.status = 'ambiguous'; base.reason = 'Leading key candidates are too close to distinguish.'; return base; }
  Object.assign(base, { status: 'estimated', label: top.label, tonic: top.tonic, mode: top.mode });
  base.reason = 'Estimated from duration and confidence weighted pitch classes; correlation is not probability.';
  return base;
}

const CHORDS = [
  { name: 'major', pcs: [0, 4, 7] }, { name: 'minor', pcs: [0, 3, 7] },
  { name: '7', pcs: [0, 4, 7, 10] }, { name: 'maj7', pcs: [0, 4, 7, 11] }, { name: 'm7', pcs: [0, 3, 7, 10] },
];
function chordAt(active) {
  const present = new Set(active.map(e => e.pc));
  if (present.size < 3) return null;
  const matches = [];
  for (let root = 0; root < 12; root++) for (const def of CHORDS) {
    const pcs = def.pcs.map(x => (root + x) % 12);
    if (pcs.length === present.size && pcs.every(pc => present.has(pc))) matches.push({ root, def, pcs });
  }
  matches.sort((a, b) => b.pcs.length - a.pcs.length);
  const match = matches[0];
  if (!match) return null;
  const label = match.def.name === 'major' ? NAMES[match.root] : match.def.name === 'minor' ? `${NAMES[match.root]}m` : `${NAMES[match.root]}${match.def.name}`;
  return { label, root: match.root, pcs: match.pcs, confidence: Math.min(...match.pcs.map(pc => Math.max(...active.filter(e => e.pc === pc).map(e => e.confidence)))) };
}

function inferChords(events, duration) {
  const boundaries = [...new Set(events.flatMap(e => [e.start, e.end]))].sort((a, b) => a - b), segments = [];
  for (let i = 0; i + 1 < boundaries.length; i++) {
    const start = boundaries[i], end = boundaries[i + 1];
    if (end <= start) continue;
    const active = events.filter(e => e.start < end && e.end > start), match = chordAt(active);
    if (!match) continue;
    const prev = segments.at(-1);
    if (prev && prev.label === match.label && Math.abs(prev.end - start) < 1e-8) { prev.end = end; prev.confidence = Math.min(prev.confidence, match.confidence); }
    else segments.push({ start, end, label: match.label, confidence: match.confidence, source: 'note-events', pitchClasses: match.pcs.map(pc => NAMES[pc]) });
  }
  return segments.map(c => ({ ...c, start: Math.min(duration, round(c.start)), end: Math.min(duration, round(c.end)), confidence: round(c.confidence) })).filter(c => c.end > c.start);
}

function cleanSpectralChords(chords, duration) {
  if (!Array.isArray(chords)) return [];
  return chords.slice(0, MAX_CHORDS).flatMap(chord => {
    if (!finite(chord?.start) || !finite(chord?.end) || !finite(chord?.confidence) || typeof chord?.label !== 'string') return [];
    const start = Math.min(duration, round(clamp(chord.start, 0, duration))), end = Math.min(duration, round(clamp(chord.end, 0, duration)));
    if (end <= start) return [];
    return [{ start, end, label: chord.label.slice(0, 80), confidence: round(clamp(chord.confidence, 0, 1)), source: 'spectral-only', pitchClasses: [] }];
  });
}

function unavailableKey() { return { status: 'unavailable', label: null, tonic: null, mode: null, score: 0, candidates: [], margin: 0, reason: 'Note transcription is unavailable.' }; }

export function analyzeNoteHarmony(notesStage, duration, spectralChords = []) {
  const solo = notesStage?.mode === 'solo-vocal';
  const safeDuration = finite(duration) ? clamp(duration, 0, MAX_TIME) : 0;
  const status = ['complete', 'partial', 'failed', 'skipped'].includes(notesStage?.status) ? notesStage.status : 'failed';
  const warnings = [
    'Chord labels use a restricted major, minor, dominant 7, major 7, and minor 7 vocabulary.',
    'Mixture transcription can omit or combine simultaneous sources; chord labels are tentative.',
    'Key profile correlation is not a probability or confirmed tonality.',
    'Modulation and enharmonic spelling are not established by this analysis.',
  ];
  const inputTruncated = notesStage?.truncated === true || (Array.isArray(notesStage?.items) && notesStage.items.length > MAX_EVENTS) || (finite(duration) && duration > MAX_TIME);
  const incomplete = status !== 'complete' || inputTruncated;
  if (incomplete) warnings.push('Note transcription is incomplete; inferred chords and pitch classes may omit notes.');
  let source, pitchClasses = [], key, chords = [];
  if (status === 'failed' || status === 'skipped') {
    source = 'spectral-only'; key = unavailableKey();
    chords = solo ? [] : cleanSpectralChords(spectralChords, safeDuration);
  } else {
    source = 'note-events';
    const events = cleanEvents(notesStage, safeDuration);
    const evidence = weightedPcs(events, safeDuration);
    pitchClasses = evidence.pitchClasses;
    const meaningfulCount = evidence.weights.filter(weight => weight / evidence.weights.reduce((a, b) => a + b, 0) >= 0.02).length;
    key = estimateKey(evidence.weights, evidence.activeSeconds, meaningfulCount, incomplete ? 'partial' : 'complete');
    chords = solo ? [] : inferChords(events, safeDuration);
    if (solo && key.status === 'estimated') key.reason = 'Estimated from duration-weighted melody pitch classes only; no model confidence or accompaniment evidence. Correlation is not probability.';
    if (!events.length && status === 'complete') warnings.push('No valid note events were available.');
    if (inputTruncated) key.reason = 'Truncated note evidence cannot establish whole-song key.';
  }
  const outputTruncated = inputTruncated || chords.length > MAX_CHORDS || ((status === 'failed' || status === 'skipped') && Array.isArray(spectralChords) && spectralChords.length > MAX_CHORDS);
  if (solo) warnings.push('Solo-vocal mode: duration weights only (not invented confidence); melody key is tentative; accompaniment chords cannot be inferred and are withheld.');
  const method = solo ? 'Solo melody: duration-weighted Krumhansl-Kessler pitch-class profile correlation; no confidence weighting or chord inference.' : source === 'note-events' ? 'Duration and confidence weighted Krumhansl-Kessler pitch-class profile correlation; simultaneous note-event chord templates.' : 'Spectral-only two-second mixture-chroma triads; note transcription and key unavailable.';
  return { method, source, pitchClasses: pitchClasses.slice(0, 12), key, chords: chords.slice(0, MAX_CHORDS), truncated: outputTruncated, warnings: warnings.slice(0, 16) };
}
