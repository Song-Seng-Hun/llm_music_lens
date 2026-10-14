import test from 'node:test';
import assert from 'node:assert/strict';
import { analyzeVocalPitch } from '../analysis/vocal-pitch.mjs';

const rate = 22050;
const tone = (hz, amplitude = 0.2, dc = 0) => Float32Array.from({ length: rate }, (_, i) => dc + amplitude * Math.sin(2 * Math.PI * hz * i / rate));
const cents = (actual, expected) => 1200 * Math.log2(actual / expected);
const leapFrequency = time => time < 1.5 ? 220 : time < 3 ? 440 : time < 4.5 ? 329.6275569 : time < 6 ? 659.2551138 : 196;
function harmonicSignal(seconds, frequency, amplitudes) {
  let cumulativeFrequency = 0;
  return Float32Array.from({ length: rate * seconds }, (_, i) => {
    cumulativeFrequency += frequency(i / rate);
    const phase = 2 * Math.PI * cumulativeFrequency / rate;
    const signal = amplitudes.reduce((sum, amplitude, harmonic) => sum + amplitude * Math.sin((harmonic + 1) * phase), 0);
    return Math.max(-32768, Math.min(32767, Math.round(signal * 32768))) / 32768;
  });
}

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

// The old long frame accepted a ~110Hz subharmonic at a known 440->329.63Hz
// leap. This analytic fixture catches accepting weak unsupported periodicity;
// the existing steady-tone tests cannot expose transition-context mixtures.
test('weak wrong periodic estimates around a known leap remain unknown', () => {
  const result = analyzeVocalPitch(harmonicSignal(8, leapFrequency, [0.18, 0.06, 0.03]));
  for (const index of [149, 150, 151]) {
    assert.equal(result.hz[index], null);
    assert.equal(result.periodicity[index], null);
  }
  for (const [index, expected] of [[147, 440], [153, 329.6275569]]) {
    assert.ok(result.hz[index] !== null && Math.abs(cents(result.hz[index], expected)) <= 50);
  }
  assert.equal(result.method.id, 'monophonic-yin-style-central-check-v2');
});

// Selective abstention must preserve real changes, not flatten the contour.
// Frozen independent synthetic guard: each leap by120ms, >=90% supported
// plateau centers correct; vibrato >=90% baseline span, no >1pp coverage loss,
// <=5c extra median error, signed correlation >=0.9. Scalar baseline evidence
// is 295/300 voiced, 108.310640036c span, 16.183212233c median error; no old
// tracker implementation or research audio is shipped with this test.
test('genuine leaps and vibrato retain timing, coverage and signed variation', () => {
  const leaps = analyzeVocalPitch(harmonicSignal(8, leapFrequency, [0.18, 0.06, 0.03]));
  const boundaries = [1.5, 3, 4.5, 6];
  let supported = 0, correct = 0;
  for (let i = 0; i < leaps.hz.length; i++) {
    const time = i * 0.02;
    if (i * 441 < 1069 || i * 441 + 1069 >= rate * 8 || boundaries.some(boundary => Math.abs(time - boundary) < 0.08)) continue;
    supported++;
    if (leaps.hz[i] !== null && Math.abs(cents(leaps.hz[i], leapFrequency(time))) <= 50) correct++;
  }
  assert.ok(correct / supported >= 0.9);
  for (const boundary of boundaries) {
    const start = Math.ceil(boundary / 0.02), end = Math.floor((boundary + 0.12) / 0.02) + 1;
    assert.ok(leaps.hz.slice(start, end).some(hz => hz !== null && Math.abs(cents(hz, leapFrequency(boundary))) <= 50));
  }
  const frequency = time => 440 * 2 ** (0.8 * Math.sin(2 * Math.PI * 5 * time) / 12);
  const vibrato = analyzeVocalPitch(harmonicSignal(6, frequency, [0.18, 0.04, 0.02]));
  const observed = [], expected = [], errors = [];
  vibrato.hz.forEach((hz, i) => {
    if (hz === null) return;
    observed.push(cents(hz, 440)); expected.push(80 * Math.sin(2 * Math.PI * 5 * i * 0.02));
    errors.push(Math.abs(observed.at(-1) - expected.at(-1)));
  });
  assert.ok(observed.length >= 292);
  const ordered = [...observed].sort((a, b) => a - b), sortedErrors = errors.sort((a, b) => a - b);
  const quantile = (values, q) => values[Math.floor((values.length - 1) * q)];
  assert.ok(quantile(ordered, 0.95) - quantile(ordered, 0.05) >= 0.9 * 108.310640036);
  assert.ok(quantile(sortedErrors, 0.5) <= 16.183212233 + 5);
  const aMean = observed.reduce((sum, value) => sum + value, 0) / observed.length;
  const bMean = expected.reduce((sum, value) => sum + value, 0) / expected.length;
  let ab = 0, aa = 0, bb = 0;
  observed.forEach((value, i) => { const a = value - aMean, b = expected[i] - bMean; ab += a * b; aa += a * a; bb += b * b; });
  assert.ok(ab / Math.sqrt(aa * bb) >= 0.9);
});
