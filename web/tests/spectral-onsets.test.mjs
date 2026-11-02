import test from 'node:test';
import assert from 'node:assert/strict';
import { spectralFlux, pickSpectralOnsets, BROWSER_ONSET_OPTIONS, SERVER_ONSET_OPTIONS } from '../public/analysis/spectral-onsets.mjs';
import { beatFeatures } from '../lib/server/beat-frontend.mjs';

const frames = (values, frameRate = 50) => values.map((value, index) => ({
  time: index / frameRate, rms: 0.2, fluxLow: value, fluxMid: 0, fluxHigh: 0,
}));

test('subband flux uses positive changes, scaled previous state, and exclusive 180/2000 Hz boundaries', () => {
  const magnitudes = new Float64Array(399), previous = new Float64Array(399);
  for (const [hz, magnitude] of [[170, 2], [180, 3], [2000, 4]]) magnitudes[hz / 10 - 1] = magnitude;
  const original = magnitudes.slice();
  const expected = { fluxLow: 2 / 32, fluxMid: 3 / 32, fluxHigh: 4 / 32 };
  assert.deepEqual(spectralFlux(magnitudes, previous, 8000, 800, { magnitudeScale: 1 / 32 }), expected);
  assert.deepEqual(previous, magnitudes.map(value => value / 32));
  assert.deepEqual(magnitudes, original, 'reading flux must not alter model FFT input');
  assert.deepEqual(spectralFlux(magnitudes.map(value => value / 2), previous, 8000, 800, { magnitudeScale: 1 / 32 }), { fluxLow: 0, fluxMid: 0, fluxHigh: 0 });
  assert.deepEqual(spectralFlux(magnitudes, previous, 8000, 800, { magnitudeScale: 1 / 32 }), { fluxLow: 1 / 32, fluxMid: 1.5 / 32, fluxHigh: 2 / 32 });
  assert.throws(() => spectralFlux(magnitudes, magnitudes, 8000, 800), /separate/);
});

test('browser peak extraction retains right-edge plateau ties, spacing, global abstention, and half-open timestamps', () => {
  const options = { ...BROWSER_ONSET_OPTIONS, frameRate: 10 };
  assert.deepEqual(pickSpectralOnsets(frames([0, 1, 1, 0, 0], 10), 0.5, options), [
    { time: 0.2, strength: 0.75, band: 'low', fluxLow: 1, fluxMid: 0, fluxHigh: 0 },
  ]);
  const close = frames([0, 0, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0]);
  assert.deepEqual(pickSpectralOnsets(close, 0.24, { ...BROWSER_ONSET_OPTIONS, frameRate: 50 }).map(event => event.time), [0.1]);
  assert.deepEqual(pickSpectralOnsets(close, 0.24, { ...BROWSER_ONSET_OPTIONS, active: false }), []);
  assert.deepEqual(pickSpectralOnsets(frames([0, 0, 0]), 1), []);
  assert.deepEqual(pickSpectralOnsets(frames([0, 1, 1, 0, 0], 10), 0.2, options), [], 'an event exactly at duration is outside the excerpt');
});

test('server reflected edges cannot poison thresholds and unsupported context or quiet frames produce no candidate', () => {
  const values = Array(51).fill(0); values[1] = 100; values[25] = 1; values[49] = 100;
  const input = frames(values);
  const events = pickSpectralOnsets(input, 1, SERVER_ONSET_OPTIONS);
  assert.deepEqual(events.map(event => [event.time, event.strength]), [[0.5, 0.5]]);
  input[25].rms = 1e-7;
  assert.deepEqual(pickSpectralOnsets(input, 1, SERVER_ONSET_OPTIONS), []);
  for (const [index, expectedCount] of [[3, 0], [4, 1], [47, 1], [48, 0]]) {
    const impulse = Array(51).fill(0); impulse[index] = 1;
    assert.equal(pickSpectralOnsets(frames(impulse), 1, SERVER_ONSET_OPTIONS).length, expectedCount, `frame ${index}`);
  }
});

test('server frontend establishes first-frame reference and gates actual quiet RMS without changing PCM', () => {
  const samples = Float32Array.from({ length: 22050 }, (_, i) => 0.2 * Math.sin(2 * Math.PI * 110 * i / 22050));
  const original = samples.slice();
  const result = beatFeatures(samples);
  assert.deepEqual(samples, original);
  assert.equal(result.frameCount, 51); assert.equal(result.features.length, 51 * 128);
  assert.ok(result.features.every(Number.isFinite));
  const first = result.spectralFrames[0];
  assert.ok(first.rms > 0.0001);
  assert.deepEqual([first.fluxLow, first.fluxMid, first.fluxHigh], [0, 0, 0]);
  const quiet = beatFeatures(samples.map(value => value * 1e-6));
  assert.ok(quiet.spectralFrames.every(frame => frame.rms > 0 && frame.rms < 0.0001 && frame.fluxLow === 0 && frame.fluxMid === 0 && frame.fluxHigh === 0));
});
