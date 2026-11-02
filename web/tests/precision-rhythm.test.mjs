import test from 'node:test';
import assert from 'node:assert/strict';
import { analyzeRhythm } from '../public/precision-rhythm.mjs';
import { deriveMusicalFeatures } from '../public/analysis/musical-features.mjs';

const RATE = 22050;
function clickTrack(bpm, seconds = 16) {
  const samples = new Float32Array(RATE * seconds), period = 60 / bpm;
  for (let t = 0; t < seconds; t += period) {
    const begin = Math.round(t * RATE);
    for (let i = 0; i < 900 && begin + i < samples.length; i++) {
      const envelope = Math.exp(-i / 180);
      samples[begin + i] += 0.8 * envelope * Math.sin(2 * Math.PI * 120 * i / RATE);
    }
  }
  return samples;
}
function chord(root, third, fifth, seconds = 8) {
  const samples = new Float32Array(RATE * seconds);
  for (let i = 0; i < samples.length; i++) {
    const t = i / RATE;
    samples[i] = 0.18 * (Math.sin(2 * Math.PI * root * t) + Math.sin(2 * Math.PI * third * t) + Math.sin(2 * Math.PI * fifth * t));
  }
  return samples;
}

test('silence abstains and returns bounded finite timelines', () => {
  const result = analyzeRhythm(new Float32Array(RATE * 3), RATE);
  assert.equal(result.bpm, null);
  assert.deepEqual(result.beats, []);
  assert.equal(result.key.label, null);
  assert.deepEqual(result.chords, []);
  assert.ok(result.envelope.length <= 2400);
  for (const row of result.envelope) assert.ok(Number.isFinite(row.time) && row.time >= 0 && row.time <= 3);
});

for (const bpm of [90, 120, 180]) test(`click track ${bpm} BPM has a nearby tempo candidate`, () => {
  const result = analyzeRhythm(clickTrack(bpm), RATE);
  assert.ok(result.tempoCandidates.some(x => Math.abs(x.bpm - bpm) <= 3), JSON.stringify(result.tempoCandidates));
  assert.ok(result.beats.length > 4 && result.beats.length <= 4000);
  assert.ok(result.onsets.length > 4);
  assert.ok(result.onsets.every(x => x.time >= 0 && x.time <= 16 && x.strength >= 0 && x.strength <= 1));
});

for (const bpm of [40, 240]) test(`click track ${bpm} BPM remains eligible at the tempo search boundary`, () => {
  const result = analyzeRhythm(clickTrack(bpm, 30), RATE);
  assert.ok(result.tempoCandidates.some(x => Math.abs(x.bpm - bpm) <= 1.5), JSON.stringify(result.tempoCandidates));
  // Named regression: highest autocorrelation selected 48 for a 240 BPM pulse.
  assert.ok(result.bpm === null || Math.abs(result.bpm - bpm) <= 1.5, `wrong primary ${result.bpm}`);
});

test('steady clicks are attacks, not repeated structural energy falls', () => {
  for (const bpm of [40, 90, 120, 180, 240]) {
    const rhythm = analyzeRhythm(clickTrack(bpm, 30), RATE);
    assert.equal(rhythm.tempoEvidence.status, 'supported');
    assert.ok(Math.abs(rhythm.bpm - bpm) <= 1.5);
    assert.deepEqual(deriveMusicalFeatures({ rhythm }, 30).dynamics, [], `steady ${bpm} BPM`);
  }
});

test('acoustic descriptors distinguish tone/noise and bands, abstaining on silence', () => {
  // Named failure: independently normalized bands cannot express band balance;
  // zeros must not become a noise-like spectral-flatness claim.
  const tone = hz => Float32Array.from({ length: RATE * 3 }, (_, i) => 0.3 * Math.sin(2 * Math.PI * hz * i / RATE));
  let seed = 1977;
  const noise = Float32Array.from({ length: RATE * 3 }, () => { seed = (1664525 * seed + 1013904223) >>> 0; return (seed / 2 ** 32 - 0.5) * 0.6; });
  const low = analyzeRhythm(tone(100), RATE).acoustics.windows[1];
  const high = analyzeRhythm(tone(4000), RATE).acoustics.windows[1];
  const wide = analyzeRhythm(noise, RATE).acoustics.windows[1];
  assert.ok(low.bandBalance.low > 0.98 && high.bandBalance.high > 0.98);
  assert.ok(Math.abs(low.centroidHz - 100) < 10 && Math.abs(high.centroidHz - 4000) < 10);
  assert.ok(low.flatness < 0.01 && wide.flatness > 0.3);
  assert.ok(wide.rolloff85Hz > 7000 && low.rolloff85Hz < 150);
  const silence = analyzeRhythm(new Float32Array(RATE * 3), RATE).acoustics;
  assert.ok(silence.windows.every(w => w.centroidHz === null && w.flatness === null && w.bandBalance === null && w.rmsDbfs === null));
  const transition = new Float32Array(RATE * 3); transition.set(tone(440).subarray(0, RATE), RATE);
  const mixed = analyzeRhythm(transition, RATE).acoustics.windows;
  assert.ok(mixed[1].centroidHz > 0);
  for (const i of [0, 2]) assert.ok(mixed[i].rms === 0 && mixed[i].centroidHz === null && mixed[i].bandBalance === null && mixed[i].activeFraction === 0 && mixed[i].onsetRate === 0);
});

test('120 BPM autocorrelation peak is refined beyond integer-lag plateaus', () => {
  const result = analyzeRhythm(clickTrack(120, 20), RATE);
  assert.ok(Math.abs(result.bpm - 120) <= 1.5, `estimated ${result.bpm} BPM`);
  assert.ok(result.tempoCandidates.some(x => Math.abs(x.bpm - 120) <= 1.5), JSON.stringify(result.tempoCandidates));
  for (let i = 0; i < result.tempoCandidates.length; i++) for (let j = i + 1; j < result.tempoCandidates.length; j++) {
    const ratio = result.tempoCandidates[i].bpm / result.tempoCandidates[j].bpm;
    assert.ok(ratio < 0.96 || ratio > 1.04, `near-duplicate tempo candidates: ${JSON.stringify(result.tempoCandidates)}`);
  }
});

test('centered spectral windows timestamp attacks within one hop and preserve a 60-second beat grid', () => {
  const result = analyzeRhythm(clickTrack(120, 60), RATE);
  assert.ok(Math.abs(result.bpm - 120) <= 1.5, `estimated ${result.bpm} BPM`);
  for (let expected = 1; expected < 60; expected += 0.5) {
    const nearest = Math.min(...result.onsets.map(x => Math.abs(x.time - expected)));
    assert.ok(nearest <= 0.05, `attack at ${expected}s has ${nearest.toFixed(4)}s error`);
  }
  const finalExpectedBeat = 59.5;
  const finalBeat = result.beats.reduce((best, time) => Math.abs(time - finalExpectedBeat) < Math.abs(best - finalExpectedBeat) ? time : best, result.beats[0]);
  assert.ok(Math.abs(finalBeat - finalExpectedBeat) <= 0.05, `final grid beat ${finalBeat}`);
});

test('major/minor triads yield conservative harmonic candidates', () => {
  const major = analyzeRhythm(chord(261.63, 329.63, 392), RATE);
  const minor = analyzeRhythm(chord(261.63, 311.13, 392), RATE);
  assert.equal(major.chords[0]?.label, 'C');
  assert.equal(minor.chords[0]?.label, 'Cm');
  // A stationary triad alone cannot establish the key of a song.
  assert.equal(major.key.label, null); assert.equal(minor.key.label, null);
  assert.ok(minor.chords.every(x => x.start >= 0 && x.end <= 8 && x.start < x.end && x.confidence <= 1));
  assert.ok(major.chords.length <= 300 && minor.chords.length <= 300);
});

test('ambiguous chromatic input and stationary tone do not invent key or pulse', () => {
  const seconds = 8, chromatic = new Float32Array(RATE * seconds);
  for (let i = 0; i < chromatic.length; i++) {
    const t = i / RATE;
    for (let midi = 60; midi < 72; midi++) chromatic[i] += Math.sin(2 * Math.PI * 440 * 2 ** ((midi - 69) / 12) * t) / 12;
  }
  const ambiguous = analyzeRhythm(chromatic, RATE);
  assert.equal(ambiguous.key.label, null);
  const tone = new Float32Array(RATE * seconds);
  for (let i = 0; i < tone.length; i++) tone[i] = 0.3 * Math.sin(2 * Math.PI * 440 * i / RATE);
  const stationary = analyzeRhythm(tone, RATE);
  assert.equal(stationary.bpm, null);
});

test('invalid input and duration overflow are rejected', () => {
  assert.throws(() => analyzeRhythm(new Float32Array(0), RATE));
  assert.throws(() => analyzeRhythm(new Float32Array(RATE), 16000));
  assert.throws(() => analyzeRhythm(new Float32Array(RATE * 600 + 1), RATE));
  const invalid = new Float32Array(RATE); invalid[0] = NaN;
  assert.throws(() => analyzeRhythm(invalid, RATE));
});

test('energy changes appear as anonymous bounded section boundaries', () => {
  const samples = new Float32Array(RATE * 20);
  for (let i = 0; i < samples.length; i++) samples[i] = (i < samples.length / 2 ? 0.01 : 0.4) * Math.sin(2 * Math.PI * 220 * i / RATE);
  const result = analyzeRhythm(samples, RATE);
  assert.ok(result.sections.length <= 80);
  assert.ok(result.sections.every(x => x.start >= 0 && x.end <= 20 && x.start < x.end && Number.isFinite(x.energy)));
});
