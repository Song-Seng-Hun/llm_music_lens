import test from 'node:test';
import assert from 'node:assert/strict';
import { deriveMusicalFeatures } from '../public/analysis/musical-features.mjs';

function detail({ beats = [], bpm = 120, score = 0.9, onsets = [], envelope = [], lyrics, notes = [] } = {}) {
  return {
    rhythm: { bpm, tempoCandidates: [{ bpm, score }], beats, onsets, envelope },
    notes: { status: 'complete', items: notes },
    lyrics: lyrics ?? { status: 'complete', words: [] },
  };
}

test('strong-beat accent and offbeat candidates preserve their timing relationship', () => {
  const beats = Array.from({ length: 12 }, (_, i) => i * 0.5);
  const onsets = beats.map(time => ({ time, strength: 0.8, band: 'low' }));
  onsets.push({ time: 0.75, strength: 0.65, band: 'high' }, { time: 2.25, strength: 0.5, band: 'mid' });
  const result = deriveMusicalFeatures(detail({ beats, onsets }), 6);
  assert.ok(result.strongBeats.length >= 10);
  assert.ok(result.offbeats.some(x => Math.abs(x.time - 0.75) < 0.001 && x.beatPhase >= 0.25 && x.beatPhase <= 0.75));
  assert.ok(result.percussion.some(x => x.kind === 'high-hit'));
  assert.ok(result.warnings.some(x => x.includes('do not establish meter')));
});

test('a silent gap in a supported grid preserves accent evidence outside the gap', () => {
  const beats = Array.from({ length: 28 }, (_, i) => i * 0.5).filter(t => t < 5 || t >= 9);
  const onsets = beats.map(time => ({ time, strength: 0.8, band: 'low' }));
  const d = detail({ beats, onsets });
  d.rhythm.tempoEvidence = { status: 'supported' };
  const result = deriveMusicalFeatures(d, 14);
  assert.equal(result.strongBeats.length, beats.length);
  assert.ok(result.strongBeats.every(x => x.time < 5 || x.time >= 9));
  d.rhythm.tempoEvidence.status = 'ambiguous';
  assert.deepEqual(deriveMusicalFeatures(d, 14).strongBeats, []);
});

test('silence and steady envelopes abstain from dynamics', () => {
  const silence = deriveMusicalFeatures(detail({ bpm: null, score: 0, beats: [], envelope: Array.from({ length: 20 }, (_, i) => ({ time: i, rms: 0 })) }), 20);
  const steady = deriveMusicalFeatures(detail({ envelope: Array.from({ length: 20 }, (_, i) => ({ time: i, rms: 0.2 })) }), 20);
  assert.deepEqual(silence.dynamics, []);
  assert.deepEqual(steady.dynamics, []);
});

test('sustained rise and energy fall are distinguished without calling a fall a drop', () => {
  const values = Array.from({ length: 30 }, (_, i) => ({ time: i, rms: i < 10 ? 0.03 + i * 0.035 : i < 16 ? 0.38 : i < 20 ? 0.08 : 0.08 }));
  const result = deriveMusicalFeatures(detail({ envelope: values }), 30);
  assert.ok(result.dynamics.some(x => x.kind === 'build-up'));
  assert.ok(result.dynamics.some(x => x.kind === 'energy-fall'));
  assert.equal(result.dynamics.some(x => x.kind === 'drop'), false);
  assert.ok(result.dynamics.every(x => x.start >= 0 && x.end <= 30 && x.start < x.end && x.score >= 0 && x.score <= 1));
});

test('an isolated energy step is not a build-up', () => {
  const envelope = Array.from({ length: 16 }, (_, i) => ({ time: i, rms: i < 6 ? 0.05 : 0.4, low: i < 6 ? 0.04 : 0.3 }));
  const result = deriveMusicalFeatures(detail({ envelope }), 16);
  assert.equal(result.dynamics.some(x => x.kind === 'build-up'), false);
});

test('positive energy, low-band and attack return after contrast may be a drop candidate', () => {
  const envelope = Array.from({ length: 40 }, (_, i) => ({
    time: i * 0.25, rms: i < 24 ? 0.03 : 0.3, low: i < 24 ? 0.02 : 0.25,
  }));
  const onsets = [6.05, 6.3, 6.55].map(time => ({ time, strength: 0.8, band: 'low' }));
  const result = deriveMusicalFeatures(detail({ envelope, onsets }), 10);
  assert.ok(result.dynamics.some(x => x.kind === 'drop' && x.start >= 6 && x.start < 6.5));
});

test('Hangul transcript gets a text syllable proxy; English remains null', () => {
  const korean = deriveMusicalFeatures(detail({ lyrics: { status: 'complete', words: [
    { start: 0.2, end: 0.5, text: '안녕' }, { start: 2, end: 2.4, text: '세계!' },
  ] } }), 10);
  assert.equal(korean.vocalDensity[0].wordsPerSecond, 0.4);
  assert.ok(korean.vocalDensity[0].syllableProxyPerSecond > 0);
  const normalizedHangul = deriveMusicalFeatures(detail({ lyrics: { status: 'complete', words: [
    { start: 0.2, end: 0.5, text: '\u1100\u1161' },
  ] } }), 5);
  assert.ok(normalizedHangul.vocalDensity[0].syllableProxyPerSecond > 0);
  const compatibilityJamo = deriveMusicalFeatures(detail({ lyrics: { status: 'complete', words: [
    { start: 0.2, end: 0.5, text: 'ㄱㅏ' },
  ] } }), 5);
  assert.equal(compatibilityJamo.vocalDensity[0].syllableProxyPerSecond, null);
  const english = deriveMusicalFeatures(detail({ lyrics: { status: 'complete', words: [
    { start: 0.2, end: 0.5, text: 'hello' }, { start: 2, end: 2.4, text: 'world' },
  ] } }), 10);
  assert.equal(english.vocalDensity[0].syllableProxyPerSecond, null);
  const unavailable = deriveMusicalFeatures(detail({ lyrics: { status: 'failed', words: [{ start: 0, end: 1, text: '안녕' }] } }), 10);
  assert.deepEqual(unavailable.vocalDensity, []);
});

test('pitch classes describe observed candidates and event arrays report caps', () => {
  const beats = Array.from({ length: 2501 }, (_, i) => i * 0.04);
  const onsets = beats.map(time => ({ time, strength: 0.7, band: 'mid' }));
  const notes = Array.from({ length: 2005 }, (_, i) => ({ start: i * 0.02, end: i * 0.02 + 0.01, midi: 60 + i % 3, confidence: 0.8 }));
  const result = deriveMusicalFeatures(detail({ beats, bpm: 1500, score: 0.8, onsets, notes }), 101);
  assert.equal(result.pitchSummary.noteCount, 2005);
  assert.equal(result.pitchSummary.lowestMidi, 60);
  assert.equal(result.pitchSummary.highestMidi, 62);
  assert.ok(Math.abs(result.pitchSummary.pitchClasses.reduce((s, x) => s + x.share, 0) - 1) < 0.001);
  assert.ok(result.percussion.length <= 2000);
  assert.equal(result.truncated.percussion, true);
  assert.ok(result.strongBeats.length <= 2000);
});

test('overlapping windows for one rising trend merge into one build-up', () => {
  const envelope = Array.from({ length: 100 }, (_, i) => ({ time: i * 0.1, rms: 0.02 + i * 0.005, low: 0.01 + i * 0.004 }));
  const result = deriveMusicalFeatures(detail({ envelope }), 10);
  assert.equal(result.dynamics.filter(x => x.kind === 'build-up').length, 1);
  assert.equal(result.truncated.dynamics, false);
});

test('all exported timestamps and scores are rounded, finite and duration-bounded', () => {
  const result = deriveMusicalFeatures(detail({
    beats: [0, 0.5, 1, 1.5, 2], onsets: [{ time: 0.75001234, strength: 1.5, band: 'high' }],
    envelope: Array.from({ length: 10 }, (_, i) => ({ time: i * 1.333333, rms: i / 10 })),
    lyrics: { status: 'partial', words: [{ start: 0.2, end: 0.5, text: '가나다' }] },
  }), 5);
  const times = [
    ...result.strongBeats.map(x => x.time), ...result.offbeats.map(x => x.time), ...result.percussion.map(x => x.time),
    ...result.dynamics.flatMap(x => [x.start, x.end]), ...result.vocalDensity.flatMap(x => [x.start, x.end]),
  ];
  assert.ok(times.every(x => Number.isFinite(x) && x >= 0 && x <= 5 && x === Math.round(x * 10000) / 10000));
  assert.ok(result.warnings.length <= 12 && result.warnings.every(x => x.length <= 500));
});
