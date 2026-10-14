import test from 'node:test';
import assert from 'node:assert/strict';
import { analyzeNoteHarmony } from '../public/analysis/harmony.mjs';

const pcs = { C: 60, 'C#': 61, D: 62, 'D#': 63, E: 64, F: 65, 'F#': 66, G: 67, 'G#': 68, A: 69, 'A#': 70, B: 71 };
test('solo melody uses duration without fabricated confidence and never outputs accompaniment chords', () => {
  // Failure: null confidence drops all pitch evidence, or a melody gets chord labels.
  let time = 0;
  const items = [['C', 6], ['D', 1], ['E', 5], ['F', 1], ['G', 6], ['A', 2], ['B', 1]].map(([name, seconds]) => {
    const n = { start: time, end: time + seconds, midi: pcs[name] + 0.1, confidence: null }; time += seconds; return n;
  });
  const result = analyzeNoteHarmony({ mode: 'solo-vocal', status: 'complete', items }, time);
  assert.equal(result.key.label, 'C major');
  assert.deepEqual(result.chords, []);
  assert.match(result.method, /no confidence weighting/);
  assert.deepEqual(analyzeNoteHarmony({ mode: 'solo-vocal', status: 'failed', items: [] }, time, [{ start: 0, end: 2, label: 'C', confidence: 0.8 }]).chords, []);
});
function stage(events, status = 'complete', truncated = false) {
  return { status, items: events.map(([name, start = 0, end = 2, confidence = 0.9]) => ({ start, end, midi: pcs[name], confidence })), truncated };
}
function chord(names, start = 0, end = 2) { return names.map(n => [n, start, end]); }

test('labels supported major, minor and seventh chords', () => {
  for (const [notes, expected] of [
    [['C', 'E', 'G'], 'C'], [['C', 'D#', 'G'], 'Cm'], [['D', 'F#', 'A', 'C'], 'D7'],
    [['C', 'E', 'G', 'B'], 'Cmaj7'], [['A', 'C', 'E', 'G'], 'Am7'],
  ]) {
    const result = analyzeNoteHarmony(stage(chord(notes)), 4);
    assert.ok(result.chords.some(x => x.label === expected), `${notes}: ${result.chords.map(x => x.label)}`);
  }
});

test('a lone triad cannot establish a song key', () => {
  assert.equal(analyzeNoteHarmony(stage(chord(['C', 'E', 'G'])), 4).key.status, 'insufficient');
});

test('sequential arpeggio notes do not form a chord', () => {
  const notes = [['C', 0, 0.3], ['E', 0.5, 0.8], ['G', 1, 1.3]];
  assert.deepEqual(analyzeNoteHarmony(stage(notes), 2).chords, []);
});

test('duration weighted profiles identify clear major/minor keys', () => {
  const weighted = (entries) => { let t = 0; return entries.map(([n, seconds]) => { const event = [n, t, t + seconds]; t += seconds; return event; }); };
  const major = weighted([['C', 6], ['D', 1], ['E', 5], ['F', 1], ['G', 6], ['A', 2], ['B', 1]]);
  const minor = weighted([['A', 7], ['B', 1], ['C', 5], ['D', 2], ['E', 5], ['F', 4], ['G', 2]]);
  assert.equal(analyzeNoteHarmony(stage(major), 24).key.label, 'C major');
  assert.equal(analyzeNoteHarmony(stage(minor), 26).key.label, 'A minor');
});

test('split note representation preserves pitch-class evidence', () => {
  const whole = stage([['C', 0, 8], ['E', 0, 8], ['G', 0, 8]]);
  const split = stage([['C', 0, 4], ['C', 4, 8], ['E', 0, 4], ['E', 4, 8], ['G', 0, 4], ['G', 4, 8]]);
  assert.deepEqual(analyzeNoteHarmony(split, 8).pitchClasses, analyzeNoteHarmony(whole, 8).pitchClasses);
});

test('merged adjacent chord confidence keeps the weakest segment evidence', () => {
  const notes = [...chord(['C', 'E', 'G'], 0, 1), ...chord(['C', 'E', 'G'], 1, 2)].map(([name, start, end], i) => [name, start, end, i < 3 ? 0.9 : 0.5]);
  const result = analyzeNoteHarmony(stage(notes), 2);
  assert.equal(result.chords.length, 1);
  assert.equal(result.chords[0].confidence, 0.5);
});

test('chromatic and white-note relative-key evidence remains explicitly ambiguous', () => {
  const chromatic = Object.keys(pcs).map((n, i) => [n, i * 2, (i + 1) * 2]);
  const whiteNotes = ['C', 'D', 'E', 'F', 'G', 'A', 'B'].map((n, i) => [n, i * 2, (i + 1) * 2]);
  for (const notes of [chromatic, whiteNotes]) {
    const result = analyzeNoteHarmony(stage(notes), 24);
    assert.equal(result.key.status, 'ambiguous');
    assert.ok(result.key.candidates.length <= 4);
  }
});

test('partial notes cannot claim a whole-song key; failed notes preserve spectral chords only', () => {
  const weighted = (entries) => { let t = 0; return entries.map(([n, seconds]) => { const event = [n, t, t + seconds]; t += seconds; return event; }); };
  const clear = weighted([['C', 6], ['D', 1], ['E', 5], ['F', 1], ['G', 6], ['A', 2], ['B', 1]]);
  for (const [state, truncated] of [['partial', false], ['complete', true]]) {
    const result = analyzeNoteHarmony(stage(clear, state, truncated), 24);
    assert.equal(result.key.status, 'ambiguous');
    assert.equal(result.key.label, null);
    assert.ok(result.key.candidates.some(candidate => candidate.label === 'C major'));
  }
  const spectral = [
    { start: -1, end: 2, label: 'F#m', confidence: 1.5, pitchClasses: ['F#', 'A', 'C#'] },
    { start: 3, end: 2, label: 'invalid', confidence: 0.7 },
  ];
  const failed = analyzeNoteHarmony({ status: 'failed', items: [] }, 4, spectral);
  assert.equal(failed.source, 'spectral-only');
  assert.equal(failed.key.status, 'unavailable');
  assert.deepEqual(failed.chords, [{ start: 0, end: 2, label: 'F#m', confidence: 1, source: 'spectral-only', pitchClasses: [] }]);
  assert.deepEqual(failed.pitchClasses, []);
});

test('complete empty notes do not fall back to spectral inference', () => {
  const result = analyzeNoteHarmony({ status: 'complete', items: [] }, 4, [{ start: 0, end: 2, label: 'C' }]);
  assert.equal(result.source, 'note-events');
  assert.equal(result.key.status, 'insufficient');
  assert.deepEqual(result.chords, []);
});

test('results stay bounded and intervals stay within duration', () => {
  const events = Array.from({ length: 500 }, (_, i) => [Object.keys(pcs)[i % 12], i * 0.01, i * 0.01 + 0.5]);
  const result = analyzeNoteHarmony(stage(events), 6, []);
  assert.ok(result.chords.length <= 300);
  assert.ok(result.chords.every(c => c.start >= 0 && c.start < c.end && c.end <= 6));
  assert.ok(result.pitchClasses.length <= 12 && result.key.candidates.length <= 4);
  // Sample-derived ends need not land on the four-decimal timestamp grid.
  const duration = 22052 / 22050;
  const noteStage = stage(chord(['C', 'E', 'G'], 0, duration));
  const noteResult = analyzeNoteHarmony(noteStage, duration);
  const spectralResult = analyzeNoteHarmony({ status: 'failed', items: [] }, duration, [{ start: 0, end: duration, label: 'C', confidence: 0.8 }]);
  assert.equal(noteResult.chords[0].end, duration);
  assert.equal(spectralResult.chords[0].end, duration);
});

test('more than 300 alternating simultaneous triads are capped and marked truncated', () => {
  const events = [];
  for (let i = 0; i < 302; i++) events.push(...chord(i % 2 ? ['C', 'E', 'G'] : ['F', 'A', 'C'], i * 0.01, (i + 1) * 0.01));
  const result = analyzeNoteHarmony(stage(events), 4);
  assert.equal(result.chords.length, 300);
  assert.equal(result.truncated, true);
});

test('zero-confidence pitches do not form chords or key candidates', () => {
  const result = analyzeNoteHarmony(stage(chord(['C', 'E', 'G']).map(event => [...event.slice(0, 3), 0])), 4);
  assert.deepEqual(result.chords, []);
  assert.deepEqual(result.pitchClasses, []);
  assert.deepEqual(result.key.candidates, []);
});
