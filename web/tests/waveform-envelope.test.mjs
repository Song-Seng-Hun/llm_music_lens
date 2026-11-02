import test from 'node:test';
import assert from 'node:assert/strict';
import { analyzeWaveformEnvelope } from '../analysis/waveform-envelope.mjs';

const rate = 8000;
const tone = (seconds, amplitude) => Float32Array.from({ length: Math.round(seconds * rate) }, (_, i) => {
  const level = typeof amplitude === 'function' ? amplitude(i / rate) : amplitude;
  return level * Math.sin(2 * Math.PI * 400 * i / rate);
});
const close = (actual, expected, tolerance) => assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} vs ${expected} (tolerance ${tolerance})`);

test('zero and quiet PCM stay distinguishable; malformed inputs are rejected', () => {
  const silent = analyzeWaveformEnvelope(new Float32Array(rate), rate);
  assert.ok(silent.curve.rmsDbfs.every(value => value === null));
  assert.deepEqual(silent.summary, { rmsDbfs: null, peakDbfs: null });
  assert.equal(silent.activity.total, 0); assert.equal(silent.changes.total, 0);
  const quiet = analyzeWaveformEnvelope(tone(1, 1e-7), rate);
  assert.ok(quiet.curve.rmsDbfs.every(Number.isFinite));
  assert.equal(quiet.activity.total, 0);
  assert.match(quiet.warnings.join(' '), /not proven silence/);
  for (const samples of [[], new Float32Array(), new Float32Array([NaN]), new Float32Array([Infinity]), new Float32Array([1.01])]) assert.throws(() => analyzeWaveformEnvelope(samples, rate));
  for (const invalidRate of [0, NaN, Infinity, 8000.5, 999, 192001]) assert.throws(() => analyzeWaveformEnvelope(tone(1, 0.1), invalidRate));
  assert.throws(() => analyzeWaveformEnvelope(new Float32Array(30001), 1000), /30 seconds/);
  assert.throws(() => analyzeWaveformEnvelope(tone(1, 0.1), rate, { detail: 'unknown' }), /detail/);
});

test('centered windows locate two isolated gates and preserve observed/clipped edges', () => {
  const samples = tone(3, time => (time >= 0.5 && time < 1) || (time >= 1.5 && time < 2.3) ? 0.3 : 0);
  const result = analyzeWaveformEnvelope(samples, rate, { detail: 'fine' });
  assert.equal(result.activity.total, 2);
  for (const [index, expected] of [[0, [0.5, 1]], [1, [1.5, 2.3]]]) {
    const region = result.activity.regions[index];
    close(region.start, expected[0], 0.04); close(region.end, expected[1], 0.04);
    assert.equal(region.startClipped, false); assert.equal(region.endClipped, false);
    assert.ok(region.peakTime >= region.start && region.peakTime < region.end);
  }
  assert.equal(result.curve.rmsDbfs[120], null);
  const dc = analyzeWaveformEnvelope(new Float32Array(rate).fill(0.25), rate, { detail: 'fine' });
  assert.ok(dc.curve.rmsDbfs.every(value => value === -12.04), 'clamped edge windows must not invent an attack or release');
  assert.equal(dc.activity.regions[0].startClipped, true); assert.equal(dc.activity.regions[0].endClipped, true);
  assert.equal(dc.activity.regions[0].end, 1);
  assert.equal(dc.method.dcRemoval, false);
});

test('known tone amplitude doubling gives 6.02 dB and a steady tone has no sustained changes', () => {
  const quiet = analyzeWaveformEnvelope(tone(2, 0.1), rate, { detail: 'fine' });
  const loud = analyzeWaveformEnvelope(tone(2, 0.2), rate);
  close(quiet.summary.rmsDbfs, 20 * Math.log10(0.1 / Math.sqrt(2)), 0.01);
  close(loud.summary.rmsDbfs - quiet.summary.rmsDbfs, 6.0206, 0.2);
  close(loud.summary.peakDbfs - quiet.summary.peakDbfs, 6.0206, 0.2);
  assert.equal(quiet.changes.total, 0); assert.equal(loud.changes.total, 0);
  const summary = analyzeWaveformEnvelope(tone(2, 0.1), rate);
  assert.deepEqual(summary.curve.rmsDbfs, quiet.curve.rmsDbfs.filter((_, i) => i % 10 === 0));
  assert.deepEqual(summary.activity, quiet.activity); assert.deepEqual(summary.changes, quiet.changes);
});

test('crescendo and decrescendo have sustained signed changes and the expected energy peak', () => {
  const result = analyzeWaveformEnvelope(tone(3, time => 0.02 + 0.58 * (1 - Math.abs(time - 1.5) / 1.5)), rate);
  assert.equal(result.activity.total, 1);
  close(result.activity.regions[0].peakTime, 1.5, 0.04);
  assert.ok(result.changes.items.some(event => event.kind === 'rise' && event.deltaDb >= 3 && event.end <= 1.6));
  assert.ok(result.changes.items.some(event => event.kind === 'fall' && event.deltaDb <= -3 && event.start >= 1.4));
  for (const event of result.changes.items) assert.ok(event.end - event.start >= 0.2 - 1e-9);
});

test('30 second dense fixtures bound curves/events and retain true totals and truncation', () => {
  const gates = analyzeWaveformEnvelope(tone(30, time => time % 0.12 < 0.04 ? 0.4 : 0), rate);
  assert.equal(gates.activity.total, 250); assert.equal(gates.activity.regions.length, 64); assert.equal(gates.activity.truncated, true);
  const varying = tone(30, time => 10 ** ((-28 + 24 * (1 - Math.abs((time % 0.6) - 0.3) / 0.3)) / 20));
  const changes = analyzeWaveformEnvelope(varying, rate);
  assert.equal(changes.changes.total, 100); assert.equal(changes.changes.items.length, 64); assert.equal(changes.changes.truncated, true);
  const fine = analyzeWaveformEnvelope(varying, rate, { detail: 'fine' });
  assert.equal(fine.curve.rmsDbfs.length, 3000); assert.equal(changes.curve.rmsDbfs.length, 300);
  assert.deepEqual(fine.changes, changes.changes);
  for (const result of [gates, changes, fine]) {
    for (const event of [...result.activity.regions, ...result.changes.items]) assert.ok(event.start >= 0 && event.end > event.start && event.end <= 30);
    for (const region of result.activity.regions) assert.ok(region.peakTime >= region.start && region.peakTime < region.end);
  }
  assert.ok(Buffer.byteLength(JSON.stringify(changes)) <= 50_000);
  assert.ok(Buffer.byteLength(JSON.stringify(gates)) <= 50_000);
});
