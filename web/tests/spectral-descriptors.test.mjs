import test from 'node:test';
import assert from 'node:assert/strict';
import { spectralDescriptors } from '../public/analysis/spectral-descriptors.mjs';
import { aggregateAcoustics } from '../public/analysis/acoustics.mjs';

test('interior bin indexing preserves band boundaries, magnitude centroid, and first 85% power crossing', () => {
  const magnitudes = new Float64Array(399); // 8000 Hz / 800 samples = 10 Hz per bin.
  for (const [hz, magnitude] of [[170, 1], [180, 2], [1990, 3], [2000, 4]]) magnitudes[hz / 10 - 1] = magnitude;
  const result = spectralDescriptors(magnitudes, 8000, 800);
  assert.equal(result.powerLow, 1);
  assert.equal(result.powerMid, 13);
  assert.equal(result.powerHigh, 16);
  assert.equal(result.centroidHz, 1450);
  assert.equal(result.rolloffHz, 2000);
  // Total power=20; exactly 85%=17 is reached at bin 2, not bin 3.
  assert.equal(spectralDescriptors(new Float64Array([1, 4, Math.sqrt(3)]), 8000, 8).rolloffHz, 2000);
  assert.throws(() => spectralDescriptors(new Float64Array(400), 8000, 800), /excluding DC and Nyquist/);
});

test('zero spectra stay unknown, power-flatness floors and FFT normalization remain explicit', () => {
  assert.deepEqual(spectralDescriptors(new Float64Array(3), 8000, 8), {
    centroidHz: null, flatness: null, rolloffHz: null, powerLow: 0, powerMid: 0, powerHigh: 0,
  });
  const tiny = spectralDescriptors(new Float64Array([1e-12, 0, 0]), 8000, 8);
  assert.equal(tiny.centroidHz, null); assert.equal(tiny.flatness, null); assert.equal(tiny.rolloffHz, null);
  assert.equal(tiny.powerMid, 1e-24, 'positive sub-floor power must not be recoded as zero');
  assert.equal(spectralDescriptors(new Float64Array([1, 1, 1]), 8000, 8).flatness, 1);
  const sparse = spectralDescriptors(new Float64Array([1, 0, 0]), 8000, 8);
  assert.equal(sparse.flatness, Math.exp((Math.log(1e-20) + Math.log(1e-20)) / 3) / (1 / 3));
  const input = new Float64Array([0.7, 1e-9, 2]), original = input.slice();
  const scaled = spectralDescriptors(input, 8000, 8, { magnitudeScale: 1 / 32 });
  assert.deepEqual(scaled, spectralDescriptors(input.map(value => value / 32), 8000, 8));
  assert.deepEqual(input, original, 'descriptor extraction must not mutate the beat frontend spectrum');
  for (const magnitudeScale of [0, -1, Infinity, NaN]) assert.throws(() => spectralDescriptors(input, 8000, 8, { magnitudeScale }), /positive/);
});

test('absent onset measurements stay unknown, while explicit observed-empty and detected arrays retain rates', () => {
  const samples = new Float32Array(16000); samples.fill(0.2, 0, 8000);
  const frame = { time: 0, rms: 0.2, centroidHz: 1000, flatness: 0.01, rolloffHz: 1100, powerLow: 0, powerMid: 1, powerHigh: 0 };
  const unknown = aggregateAcoustics([frame], samples, 8000);
  assert.deepEqual(unknown.windows.map(window => window.onsetRate), [null, null]);
  assert.deepEqual(aggregateAcoustics([frame], samples, 8000, null), unknown);
  assert.match(unknown.method, /onset rate unavailable/);
  assert.match(unknown.warnings.join(' '), /onset detection was not supplied/);
  const observedEmpty = aggregateAcoustics([frame], samples, 8000, []);
  assert.deepEqual(observedEmpty.windows.map(window => window.onsetRate), [0, 0]);
  assert.deepEqual(aggregateAcoustics([frame], samples, 8000, [{ time: 0.25 }, { time: 0.75 }, { time: 1.5 }]).windows.map(window => window.onsetRate), [2, 0]);
  const withoutOnsets = result => result.windows.map(window => { const copy = { ...window }; delete copy.onsetRate; return copy; });
  assert.deepEqual(withoutOnsets(unknown), withoutOnsets(observedEmpty));
});
