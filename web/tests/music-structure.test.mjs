import test from 'node:test';
import assert from 'node:assert/strict';
import { beatFeatures, BEAT_SAMPLE_RATE as RATE } from '../lib/server/beat-frontend.mjs';
import { extractStructureFrames } from '../lib/server/structure-features.mjs';
import { analyzeStructure } from '../analysis/music-structure.mjs';

const vector = band => Array.from({ length: 16 }, (_, i) => i === band ? 1 : 0);
const row = (start, band, rmsDbfs = -16) => ({ start, end: start + 1, activeFraction: 1, rmsDbfs, lowShare: band === 0 ? 0.8 : 0.1, mel: vector(band) });
const packet = (frames, offsetSeconds = 0) => ({ schema: 'music-structure-packet/1', recipe: 'logmel16-1s-sequence-v1', source: { sha256: 'a'.repeat(64), offsetSeconds, duration: frames.at(-1).end }, frames });
const phrase = (length = 8) => Array.from({ length }, (_, i) => row(i, i % 4));

// New packet aggregation could corrupt old model input or borrow a tone into
// silence; existing FFT tests do not exercise this new serialization boundary.
test('reused frontend stays unchanged, silence is unknown and exact partial clocks survive', () => {
  const pcm = new Float32Array(RATE * 2 + 1);
  for (let i = 0; i < RATE; i++) pcm[i] = 0.3 * Math.sin(2 * Math.PI * 440 * i / RATE);
  const frontend = beatFeatures(pcm), before = frontend.features.slice();
  const frames = extractStructureFrames({ ...frontend, duration: pcm.length / RATE });
  assert.deepEqual(frontend.features, before);
  assert.equal(frames.length, 3);
  assert.equal(frames[0].mel.length, 16);
  assert.ok(Math.abs(Math.hypot(...frames[0].mel) - 1) < 0.001);
  assert.equal(frames[1].mel, null);
  assert.equal(frames[2].mel, null);
  assert.equal(frames.at(-1).end, pcm.length / RATE);
  assert.ok(frames.at(-1).end > frames.at(-1).start);
});

// A single-bin similarity would mistake reordered content and sustained tone
// for a phrase return; only a temporally varying aligned sequence is evidence.
test('returns varying sequences, abstains on steady content and altered ordering', () => {
  const p = phrase();
  const frames = [...p, ...Array.from({ length: 8 }, (_, i) => row(i + 8, 8 + i % 4)), ...p.map(r => ({ ...r, start: r.start + 16, end: r.end + 16 }))];
  const result = analyzeStructure({ duration: 24, packets: [packet(frames)] });
  assert.ok(result.repetitions.some(p => p.first.start === 0 && p.second.start === 16 && p.durationSeconds >= 8));
  assert.deepEqual(result, analyzeStructure({ duration: 24, packets: [packet(frames)] }));
  assert.equal(analyzeStructure({ duration: 24, packets: [packet(Array.from({ length: 24 }, (_, i) => row(i, 2)))] }).repetitions.length, 0);
  const changed = [...phrase(), ...Array.from({ length: 8 }, (_, i) => row(i + 8, 8 + i % 4))];
  assert.equal(analyzeStructure({ duration: 16, packets: [packet(changed)] }).repetitions.some(p => p.first.start < 8 && p.second.start >= 8), false);
  // Similar timbre alone must not turn reversed content into ordered recurrence.
  const common = band => { const mel = vector(band).map(value => value * 0.2); mel[0] = Math.sqrt(0.96); return mel; };
  const similar = Array.from({ length: 16 }, (_, i) => ({ ...row(i, 1), mel: common(i < 8 ? i + 1 : 16 - i) }));
  assert.equal(analyzeStructure({ duration: 16, packets: [packet(similar)] }).repetitions.some(p => p.first.start < 8 && p.second.start >= 8), false);
});

// Gap coverage must stay explicit: known occurrences may be compared across
// an unknown interval, but no reported occurrence or novelty support spans it.
test('gap-aware clocks allow known returns without inventing continuous evidence', () => {
  const result = analyzeStructure({ duration: 24, packets: [packet(phrase(), 0), packet(phrase(), 16)] });
  assert.deepEqual(result.coverage.gaps, [{ start: 8, end: 16 }]);
  assert.equal(result.coverage.coveredSeconds, 16);
  assert.equal(result.coverage.complete, false);
  assert.ok(result.repetitions.some(p => p.first.start === 0 && p.second.start === 16));
  for (const p of result.repetitions) {
    for (const occurrence of [p.first, p.second]) assert.ok(occurrence.end <= 8 || occurrence.start >= 16);
  }
  for (const t of result.transitions) assert.ok(t.support.after.end <= 8 || t.support.before.start >= 16);
  const change = [...Array.from({ length: 8 }, (_, i) => row(i, 1)), ...Array.from({ length: 8 }, (_, i) => row(i + 8, 12))];
  const transition = analyzeStructure({ duration: 16, packets: [packet(change)] }).transitions.find(t => t.time === 8);
  assert.ok(transition);
  assert.equal(transition.contrast.rmsDbChange, 0);
  assert.deepEqual(transition.support, { before: { start: 5, end: 8 }, after: { start: 8, end: 11 } });
});

// The stateless public boundary must reject malformed/mixed clocks rather
// than turning untrusted caller packets into plausible analysis evidence.
test('rejects incompatible packets, overlaps, clocks, vectors and excessive input', () => {
  const p = packet(phrase());
  const malformed = [
    { ...p, recipe: 'different' },
    { ...p, source: { ...p.source, offsetSeconds: NaN } },
    { ...p, frames: p.frames.slice(1) },
    { ...p, frames: p.frames.map((r, i) => i ? r : { ...r, end: 0.5 }) },
    { ...p, frames: p.frames.map((r, i) => i ? r : { ...r, mel: [1] }) },
    { ...p, frames: p.frames.map((r, i) => i ? r : { ...r, mel: Array(16).fill(NaN) }) },
    { ...p, frames: p.frames.map((r, i) => i ? r : { ...r, mel: Array(16).fill(0) }) },
    { ...p, frames: p.frames.map((r, i) => i ? r : { ...r, rmsDbfs: null, mel: null, lowShare: null }) },
  ];
  for (const bad of malformed) assert.throws(() => analyzeStructure({ duration: 24, packets: [bad] }));
  assert.throws(() => analyzeStructure({ duration: 24, packets: [p, packet(phrase(), 4)] }));
  assert.throws(() => analyzeStructure({ duration: 601, packets: [p] }));
  assert.throws(() => analyzeStructure({ duration: 600, packets: Array(61).fill(p) }));
});

// Output caps must disclose omitted candidates, and 600s must stay bounded;
// current chunk tests cannot catch quadratic packet-combination output growth.
test('600-second recurrence output is bounded with explicit truncation', () => {
  const packets = Array.from({ length: 20 }, (_, j) => packet(phrase(30), j * 30));
  const result = analyzeStructure({ duration: 600, packets });
  assert.ok(result.repetitions.length <= 16);
  assert.ok(result.transitions.length <= 24);
  assert.ok(result.repetitionsTotal >= result.repetitions.length);
  assert.equal(result.repetitionsTruncated, result.repetitionsTotal > result.repetitions.length);
  assert.ok(result.diagnostics.comparisonCells <= 600 * 600);
  assert.ok(Buffer.byteLength(JSON.stringify(result)) < 24000);
});
