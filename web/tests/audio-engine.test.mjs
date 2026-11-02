import test from 'node:test';
import assert from 'node:assert/strict';
import { analyzeAudio } from '../public/audio-engine.mjs';

const rate = 8000;
function clicks(bpm, seconds = 16) {
  const samples = new Float32Array(seconds * rate);
  for (let time = 0.3; time < seconds; time += 60 / bpm) {
    const start = Math.round(time * rate);
    for (let i = 0; i < 32 && start + i < samples.length; i++) samples[start + i] = 0.8 * (1 - i / 32);
  }
  return samples;
}

// Intent: prevent fabricated musical claims on silent input; no prior DSP checks exist.
test('silence has no invented tempo, pitch, key or chords', async () => {
  const result = await analyzeAudio(new Float32Array(rate * 2), rate);
  assert.equal(result.tempo.bpm, null);
  assert.deepEqual(result.tempo.candidates, []);
  assert.deepEqual(result.pitch, []);
  assert.equal(result.key.label, null);
  assert.deepEqual(result.chords, []);
  assert.ok(result.energy.every(({ rms }) => rms === 0));
});

// Intent: distinguish pulse rates while explicitly retaining plausible tempo octaves.
test('click tracks retain distinct tempo and octave candidates', async () => {
  for (const bpm of [90, 120, 180]) {
    const result = await analyzeAudio(clicks(bpm), rate);
    assert.ok(Math.abs(result.tempo.bpm - bpm) < 2, `${bpm}: ${result.tempo.bpm}`);
    assert.ok(result.tempo.candidates.some((candidate) => Math.abs(candidate.bpm - bpm / 2) < 2));
    assert.ok(result.tempo.beats.length > 10);
    assert.match(result.tempo.status, /ambiguous/);
    assert.ok(result.tempo.segments.some((segment) => Math.abs(segment.bpm - bpm) < 2));
  }
  const changingTempo = new Float32Array(rate * 30);
  changingTempo.set(clicks(120, 15));
  changingTempo.set(clicks(90, 15), rate * 15);
  const local = await analyzeAudio(changingTempo, rate);
  assert.ok(Math.abs(local.tempo.segments[0].bpm - 120) < 2);
  assert.ok(Math.abs(local.tempo.segments[1].bpm - 90) < 2);
});

// Intent: catch frequency/octave mistakes at the public analysis boundary.
test('440 Hz tone has approximate A4 pitch and no fabricated tempo', async () => {
  const samples = Float32Array.from({ length: rate * 2 }, (_, i) => 0.4 * Math.sin(2 * Math.PI * 440 * i / rate));
  const result = await analyzeAudio(samples, rate);
  assert.ok(result.pitch.length > 0);
  assert.ok(result.pitch.every(({ hz, midi, note }) => Math.abs(hz - 440) < 5 && Math.abs(midi - 69) < 0.2 && note === 'A4'));
  assert.equal(result.tempo.bpm, null);
});

// Intent: enforce duration/output limits and progress without audio-sized report arrays.
test('duration, outputs and progress are bounded; invalid PCM is rejected', async () => {
  const updates = [];
  const result = await analyzeAudio(clicks(120, 31), rate, { onProgress: (value) => updates.push(value) });
  assert.ok(result.waveform.length <= 800);
  assert.ok(result.energy.length <= 311);
  assert.ok(result.pitch.length <= 124);
  assert.equal(updates[0], 0);
  assert.equal(updates.at(-1), 1);
  assert.ok(updates.every((value, i) => value >= 0 && value <= 1 && (i === 0 || value >= updates[i - 1])));
  assert.ok(result.tempo.beats.every((value) => value >= 0 && value <= result.duration));
  const longest = await analyzeAudio(new Float32Array(600 * 4000), 4000);
  assert.equal(longest.duration, 600);
  assert.equal(longest.waveform.length, 800);
  assert.equal(longest.energy.length, 6000);
  await assert.rejects(analyzeAudio(new Float32Array(601 * 1000), 1000), /600 seconds/);
  await assert.rejects(analyzeAudio(new Float32Array([NaN]), rate), /non-finite/);
  await assert.rejects(analyzeAudio(new Float32Array(1), 0), /Sample rate/);
});
