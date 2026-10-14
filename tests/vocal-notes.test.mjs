import test from 'node:test';
import assert from 'node:assert/strict';
import { decodeVocalSequence, appendVocalWindow } from '../analysis/vocal-notes.mjs';

test('preserves continuous pitch and silence positions without inventing confidence', () => {
  // Failure: integer quantization loses detuned singing; dropping rest time shifts notes.
  const result = decodeVocalSequence({ note_midi: [0, 46.457, 0, 51.25], note_rest: [1, 0, 1, 0], note_dur: [0.5, 1, 0.25, 0.25] }, 2);
  assert.deepEqual(result.events, [{ start: 0.5, end: 1.5, midi: 46.457, confidence: null }, { start: 1.75, end: 2, midi: 51.25, confidence: null }]);
  assert.throws(() => decodeVocalSequence({ note_midi: [60], note_rest: [0], note_dur: [1] }, 2), /duration/);
  assert.throws(() => decodeVocalSequence({ note_midi: [NaN], note_rest: [0], note_dur: [1] }, 1), /values/);
});

test('context joins only sustained boundary notes, clamps EOF and reports caps', () => {
  // Failure: chunks duplicate a sustained note or lose a fresh repeated attack.
  const items = [{ start: 39, end: 40, midi: 60.1, confidence: null }];
  assert.equal(appendVocalWindow(items, [{ start: 1, end: 3, midi: 60.2 }, { start: 3, end: 5, midi: 60.2 }], 38, 40, 43, 42.875), false);
  assert.deepEqual(items, [{ start: 39, end: 41, midi: 60.1, confidence: null }, { start: 41, end: 42.875, midi: 60.2, confidence: null }]);
  assert.equal(appendVocalWindow(items, [{ start: 0, end: 1, midi: 61 }], 43, 43, 44, 44, 2), true);
});
