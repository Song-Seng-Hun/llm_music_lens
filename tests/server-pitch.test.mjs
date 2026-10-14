import test from 'node:test';
import assert from 'node:assert/strict';
import { analyzeVocalPitch } from '../analysis/vocal-pitch.mjs';

const rate = 22050;
const tone = (hz, amplitude = 0.2, dc = 0) => Float32Array.from({ length: rate }, (_, i) => dc + amplitude * Math.sin(2 * Math.PI * hz * i / rate));
const cents = (actual, expected) => 1200 * Math.log2(actual / expected);

test('pitch inputs and uniform null arrays are bounded, including fractional tails and 30 second silence', () => {
  for (const samples of [[], new Float32Array(), new Float32Array(rate - 1), new Float32Array(rate * 30 + 1)]) assert.throws(() => analyzeVocalPitch(samples));
  for (const sampleRate of [0, 16000, 44100, NaN]) assert.throws(() => analyzeVocalPitch(new Float32Array(rate), sampleRate));
  for (const invalid of [NaN, Infinity, -1.01, 1.01]) { const samples = new Float32Array(rate); samples[500] = invalid; assert.throws(() => analyzeVocalPitch(samples)); }
  for (const length of [rate + 1, rate * 30]) {
    const result = analyzeVocalPitch(new Float32Array(length));
    assert.equal(result.start, 0); assert.equal(result.stepSeconds, 0.02); assert.equal(result.duration, length / rate);
    assert.equal(result.hz.length, Math.ceil(length / 441)); assert.equal(result.periodicity.length, result.hz.length);
    assert.ok(result.hz.length <= 1500 && (result.hz.length - 1) * result.stepSeconds < result.duration);
    assert.ok(result.hz.every(value => value === null)); assert.ok(result.periodicity.every(value => value === null));
  }
});

test('known periodic pitch preserves PCM and retains unknown crop support without a shifted contour clock', () => {
  const samples = tone(440, 0.2, 0.15), original = samples.slice(), result = analyzeVocalPitch(samples);
  assert.deepEqual(samples, original);
  assert.equal(result.method.edgeSupportSeconds, 1069 / rate);
  assert.equal(result.hz.length, 50);
  for (let i = 0; i < result.hz.length; i++) {
    const center = i * 441, missingSupport = center < 1069 || center + 1069 >= samples.length;
    if (missingSupport) { assert.equal(result.hz[i], null); assert.equal(result.periodicity[i], null); }
    else { assert.ok(Math.abs(cents(result.hz[i], 440)) <= 30); assert.ok(result.periodicity[i] >= 0 && result.periodicity[i] <= 1); }
  }
  assert.match(result.method.periodicity, /not a voicing probability/);
});

test('harmonic-rich missing fundamentals do not become their louder overtone and above-range tones abstain', () => {
  const samples = Float32Array.from({ length: rate }, (_, i) => 0.2 * Math.sin(2 * Math.PI * 280 * i / rate) + 0.12 * Math.sin(2 * Math.PI * 420 * i / rate) + 0.04 * Math.sin(2 * Math.PI * 560 * i / rate));
  const result = analyzeVocalPitch(samples);
  const observed = result.hz.filter(value => value !== null);
  assert.ok(observed.length >= 40);
  assert.ok(observed.every(value => Math.abs(cents(value, 140)) <= 30));
  assert.ok(analyzeVocalPitch(tone(2000)).hz.every(value => value === null), 'an above-range first trough must not fall through to an in-range subharmonic');
});

test('DC, quiet periodic input, and deterministic noise are not fabricated voiced pitch', () => {
  let seed = 114;
  const noise = Float32Array.from({ length: rate }, () => { seed = (1664525 * seed + 1013904223) >>> 0; return 0.4 * (seed / 2 ** 32 - 0.5); });
  for (const samples of [new Float32Array(rate).fill(0.5), tone(220, 1e-5), noise]) {
    const result = analyzeVocalPitch(samples);
    assert.ok(result.hz.every(value => value === null));
    assert.ok(result.periodicity.every(value => value === null));
  }
});
