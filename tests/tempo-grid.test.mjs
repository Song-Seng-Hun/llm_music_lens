import test from 'node:test';
import assert from 'node:assert/strict';
import { estimateTempo } from '../public/analysis/tempo-grid.mjs';

const RATE = 22050 / 1024;

function clickEvidence(bpm, duration, phase = 0) {
  const step = 60 / bpm, onsets = [];
  for (let time = phase; time < duration; time += step) onsets.push({ time, strength: 0.8, band: 'low' });
  const onsetSeries = new Float32Array(Math.ceil(duration * RATE));
  for (const onset of onsets) onsetSeries[Math.min(onsetSeries.length - 1, Math.round(onset.time * RATE))] = onset.strength;
  return { onsetSeries, onsets };
}

for (const bpm of [40, 90, 120, 180, 240]) test(`fixed ${bpm} BPM pulse is supported without a wrong subharmonic`, () => {
  const duration = 60, input = clickEvidence(bpm, duration);
  const result = estimateTempo(input.onsetSeries, RATE, input.onsets, duration);
  assert.equal(result.evidence.status, 'supported');
  assert.ok(Math.abs(result.bpm - bpm) <= 1.5, `estimated ${result.bpm} BPM`);
  assert.ok(result.beats.length > 0 && result.beats.length <= 4000);
  assert.ok(result.candidates.some(x => Math.abs(x.bpm - bpm) <= 1.5));
});

test('120 BPM phase remains within 50 ms at the end of a 60-second grid', () => {
  const input = clickEvidence(120, 60, 0.02);
  const result = estimateTempo(input.onsetSeries, RATE, input.onsets, 60);
  assert.equal(result.evidence.status, 'supported');
  const target = 59.52;
  const nearest = result.beats.reduce((best, time) => Math.abs(time - target) < Math.abs(best - target) ? time : best, result.beats[0]);
  assert.ok(Math.abs(nearest - target) <= 0.05, `final beat error ${Math.abs(nearest - target)}s`);
  assert.ok(result.evidence.medianOffsetMs <= 50);
});

test('silence, sparse and irregular onsets withhold a global grid', () => {
  const silent = estimateTempo(new Float32Array(RATE * 10), RATE, [], 10);
  assert.equal(silent.evidence.status, 'insufficient');
  assert.equal(silent.bpm, null);
  assert.deepEqual(silent.beats, []);
  const sparseOnsets = Array.from({ length: 3 }, (_, i) => ({ time: i * 3, strength: 0.8, band: 'mid' }));
  const sparse = estimateTempo(new Float32Array(RATE * 10), RATE, sparseOnsets, 10);
  assert.equal(sparse.evidence.status, 'insufficient');
  assert.equal(sparse.bpm, null);
  const irregular = Array.from({ length: 36 }, (_, i) => ({ time: i * 0.7 + (i % 3) * 0.18, strength: 0.7, band: 'high' }));
  const result = estimateTempo(new Float32Array(RATE * 30), RATE, irregular, 30);
  assert.ok(['insufficient', 'ambiguous'].includes(result.evidence.status));
  assert.equal(result.bpm, null);
  assert.deepEqual(result.beats, []);
});

test('competing simultaneous pulse hypotheses are reported as ambiguous', () => {
  const onsets = [];
  for (const bpm of [90, 120]) {
    for (let time = 0; time < 30; time += 60 / bpm) {
      if (!onsets.some(x => Math.abs(x.time - time) < 0.01)) onsets.push({ time, strength: 0.8, band: 'mid' });
    }
  }
  onsets.sort((a, b) => a.time - b.time);
  const result = estimateTempo(new Float32Array(Math.ceil(30 * RATE)), RATE, onsets, 30);
  assert.equal(result.evidence.status, 'ambiguous');
  assert.equal(result.bpm, null);
  assert.deepEqual(result.beats, []);
  assert.ok(result.candidates.length >= 2 && result.candidates.every(x => x.score >= 0 && x.score <= 1));
});

test('clear 90 to 140 BPM change reports variable segments and no continuous grid', () => {
  const onsets = [];
  for (let time = 0; time < 15; time += 60 / 90) onsets.push({ time, strength: 0.8, band: 'low' });
  for (let time = 15; time < 30; time += 60 / 140) onsets.push({ time, strength: 0.8, band: 'low' });
  onsets.sort((a, b) => a.time - b.time);
  const result = estimateTempo(new Float32Array(RATE * 30), RATE, onsets, 30);
  assert.equal(result.evidence.status, 'variable');
  assert.equal(result.bpm, null);
  assert.deepEqual(result.beats, []);
  assert.ok(result.evidence.segments.some(x => Math.abs(x.bpm - 90) <= 3));
  assert.ok(result.evidence.segments.some(x => Math.abs(x.bpm - 140) <= 3));
});

test('supported pulse does not extrapolate through a long silent gap but bridges one missed beat', () => {
  const duration = 14, step = 0.5, onsets = [];
  for (let time = 0; time < 5; time += step) {
    if (Math.abs(time - 2) > 1e-6) onsets.push({ time, strength: 0.8, band: 'low' });
  }
  for (let time = 9; time < duration; time += step) onsets.push({ time, strength: 0.8, band: 'low' });
  const onsetSeries = new Float32Array(Math.ceil(duration * RATE));
  for (const onset of onsets) onsetSeries[Math.round(onset.time * RATE)] = onset.strength;
  const result = estimateTempo(onsetSeries, RATE, onsets, duration);
  assert.equal(result.evidence.status, 'supported');
  assert.ok(Math.abs(result.bpm - 120) <= 1.5, `estimated ${result.bpm} BPM`);
  assert.ok(result.beats.some(time => Math.abs(time - 2) <= 0.05), 'one isolated missed beat may be bridged');
  assert.ok(result.beats.some(time => time < 5));
  assert.ok(result.beats.some(time => time >= 9));
  assert.ok(result.beats.every(time => time < 5.5 || time >= 8.5), `unexpected grid in silence: ${result.beats.filter(time => time >= 5.5 && time < 11.5)}`);
});


