import test from 'node:test';
import assert from 'node:assert/strict';
import { beatFeatures, BEAT_SAMPLE_RATE as RATE } from '../lib/server/beat-frontend.mjs';
import { analyzeBeatPcm } from '../lib/server/beat-analysis.mjs';
import { spectralTexture } from '../lib/server/spectral-texture.mjs';

const tone = (hz, seconds = 1, amplitude = 0.4) => Float32Array.from({ length: Math.round(RATE * seconds) }, (_, i) => amplitude * Math.sin(2 * Math.PI * hz * i / RATE));
function analyze(pcm) {
  return spectralTexture(beatFeatures(pcm).spectralFrames, pcm, RATE);
}

// A mel-power mixup, incorrect FFT scaling or wrong bin boundaries corrupts
// every server texture value; browser-only checks cannot catch this frontend.
test('server shared FFT exposes the known tone bands and weighted mixture', () => {
  const sequence = new Float32Array(3 * RATE);
  [100, 1000, 5000].forEach((hz, i) => sequence.set(tone(hz), i * RATE));
  const result = analyze(sequence);
  assert.equal(result.windows.length, 3);
  result.windows.forEach((window, i) => {
    assert.ok(window.bandBalance[['low', 'mid', 'high'][i]] > 0.97, JSON.stringify(window));
    assert.ok(Math.abs(window.centroidHz - [100, 1000, 5000][i]) < 100, JSON.stringify(window));
  });
  const mix = Float32Array.from({ length: RATE * 3 }, (_, i) =>
    [100, 1000, 5000].reduce((sum, hz, j) => sum + 0.4 * Math.sqrt([0.6, 0.3, 0.1][j]) * Math.sin(2 * Math.PI * hz * i / RATE), 0));
  const balance = analyze(mix).windows[1].bandBalance;
  ['low', 'mid', 'high'].forEach((key, i) => assert.ok(Math.abs(balance[key] - [0.6, 0.3, 0.1][i]) <= 0.02));
});

// Quiet frames and partial final bins must not borrow neighboring sound or
// report numerical zero as known silence pitch/noise evidence.
test('server texture abstains below the gate and keeps actual excerpt bounds', () => {
  const pcm = new Float32Array(Math.round(RATE * 2.25));
  pcm.set(tone(1000)); pcm.set(tone(1000, 1.25, 0.00001), RATE);
  const result = analyze(pcm);
  assert.equal(result.windows.length, 3);
  assert.equal(result.windows[2].end, pcm.length / RATE);
  for (const w of result.windows.slice(1)) {
    assert.equal(w.activeFraction, 0);
    for (const field of ['centroidHz', 'rolloff85Hz', 'flatness', 'bandBalance']) assert.equal(w[field], null);
  }
  assert.equal(analyze(new Float32Array(RATE)).windows[0].bandBalance, null);
  for (const tailSamples of [1, 2]) {
    const tail = new Float32Array(RATE + tailSamples).fill(0.2);
    const last = analyze(tail).windows.at(-1);
    assert.ok(last.end > last.start);
    assert.equal(last.end, tail.length / RATE);
  }
});

// Typed noise should be flatter than a pure tone after the complete frontend;
// the reported fraction is a descriptor, not a guessed instrument identity.
test('server power flatness separates fixed broadband noise from a tone', () => {
  let seed = 1;
  const noise = Float32Array.from({ length: RATE * 2 }, () => {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    return 0.4 * (2 * seed / 4294967296 - 1);
  });
  const n = analyze(noise).windows[0], t = analyze(tone(1000, 2)).windows[0];
  assert.ok(n.flatness > 0.3 && n.flatness > t.flatness + 0.3);
});

// The model service must pass the shared observations through without a second
// inference; both serialized MCP copies count toward the fixed output budget.
test('actual beat service retains spectral data and bounds both MCP representations', async () => {
  const pcm = tone(1000, 30);
  let calls = 0;
  const result = await analyzeBeatPcm(pcm, { runFrames: async () => {
    calls++;
    return { beat: new Float32Array(250).fill(-100), downbeat: new Float32Array(250).fill(-100) };
  } });
  assert.equal(calls, result.diagnostics.chunks);
  assert.equal(result.acoustics.windows.length, 30);
  assert.equal(result.acoustics.windows.at(-1).end, 30);
  const { acoustics, ...old } = result;
  const wire = value => Buffer.byteLength(JSON.stringify({ jsonrpc: '2.0', id: 1, result: {
    content: [{ type: 'text', text: JSON.stringify(value) }], structuredContent: { result: value }, isError: false,
  } }));
  const attributed = { ...acoustics, sourceKind: 'mixture', sourceAttribution: 'mixture spectrum; cannot attribute changes to a voice or instrument' };
  assert.ok(wire({ ...old, acoustics: attributed }) - wire(old) <= 30000);
  assert.ok(!('onsetRate' in acoustics.windows[0]), 'unknown attack rate is not fabricated as zero');
  assert.ok(!('spectralFrames' in result), 'raw FFT frame evidence is not serialized');
});
