import test from 'node:test';
import assert from 'node:assert/strict';
import { decodeVocalSequence, appendVocalWindow, VOCAL_FRAME_STEP as H } from '../analysis/vocal-notes.mjs';

function fixture(counts, pitches, rests, masks) {
  const durations = counts.map(n => Math.fround(n * H));
  const mask = masks?.flat() ?? counts.flatMap((n, i) => Array(n).fill(rests[i]));
  const ids = counts.flatMap((n, i) => Array(n).fill(BigInt(i + 1)));
  return { values: { note_midi: pitches, note_rest: rests, note_dur: durations }, duration: durations.reduce((a, b) => a + b, 0),
    frames: { rest: { type: 'bool', dims: [1, mask.length], data: Uint8Array.from(mask) }, mapping: { type: 'int64', dims: [1, ids.length], data: BigInt64Array.from(ids) } } };
}

test('preserves continuous pitch and silence positions without inventing confidence', () => {
  // Failure: integer quantization loses detuned singing; dropping rest time shifts notes.
  const f = fixture([2, 4, 1, 1], [0, 46.457, 0, 51.25], [1, 0, 1, 0]);
  const d = f.values.note_dur, result = decodeVocalSequence(f.values, f.duration, f.frames);
  assert.deepEqual(result.events, [{ start: d[0], end: d[0] + d[1], midi: 46.457, confidence: null }, { start: d[0] + d[1] + d[2], end: f.duration, midi: 51.25, confidence: null }]);
  assert.throws(() => decodeVocalSequence(f.values, f.duration + 1, f.frames), /duration/);
  assert.throws(() => decodeVocalSequence({ ...f.values, note_midi: [NaN, 46.457, 0, 51.25] }, f.duration, f.frames), /values/);
});

test('frame edges preserve internal gaps and native clock; clamp EOF after trim', () => {
  // Failure: removing rest frames shifts later notes or subtracts EOF twice.
  const f = fixture([6, 4], [60.25, 64.125], [0, 0], [[1, 0, 1, 0, 0, 1], [0, 0, 0, 0]]);
  const d = f.values.note_dur, end = f.duration - 0.005;
  assert.deepEqual(decodeVocalSequence(f.values, end, f.frames).events, [
    { start: H, end: d[0] - H, midi: 60.25, confidence: null },
    { start: d[0], end, midi: 64.125, confidence: null },
  ]);
  const eof = fixture([4], [60], [0], [[0, 0, 0, 1]]);
  assert.equal(decodeVocalSequence(eof.values, 2.8 * H, eof.frames).events[0].end, 2.8 * H);
  const beyond = fixture([2], [60], [0], [[1, 0]]), kept = decodeVocalSequence(beyond.values, H / 2, beyond.frames);
  assert.equal(kept.preserved, 1); assert.deepEqual(kept.events, [{ start: 0, end: H / 2, midi: 60, confidence: null }]);
});

test('incompatible model or corrupt frame mapping fails visibly; omissions remain counted', () => {
  // Failure: stale graph or unsafe int64 IDs silently produce apparently complete notes.
  const f = fixture([2, 2], [20, 60], [0, 0]);
  assert.equal(decodeVocalSequence(f.values, f.duration, f.frames).omitted, 1);
  assert.throws(() => decodeVocalSequence(f.values, f.duration), /prepare-vocal-model/);
  const bad = { ...f.frames, mapping: { ...f.frames.mapping, data: BigInt64Array.from([1n, 1n, 9007199254740993n, 2n]) } };
  assert.throws(() => decodeVocalSequence(f.values, f.duration, bad), /frame ID/);
  assert.throws(() => decodeVocalSequence({ ...f.values, note_dur: [H, H] }, f.duration, f.frames), /aggregation/);
  assert.throws(() => decodeVocalSequence({ ...f.values, note_rest: [1, 0] }, f.duration, f.frames), /aggregation/);
});

test('context joins only sustained boundary notes, clamps EOF and reports caps', () => {
  // Failure: chunks duplicate a sustained note or lose a fresh repeated attack.
  const items = [{ start: 39, end: 40, midi: 60.1, confidence: null }];
  assert.equal(appendVocalWindow(items, [{ start: 1, end: 3, midi: 60.2 }, { start: 3, end: 5, midi: 60.2 }], 38, 40, 43, 42.875), false);
  assert.deepEqual(items, [{ start: 39, end: 41, midi: 60.1, confidence: null }, { start: 41, end: 42.875, midi: 60.2, confidence: null }]);
  assert.equal(appendVocalWindow(items, [{ start: 0, end: 1, midi: 61 }], 43, 43, 44, 44, 2), true);
});
