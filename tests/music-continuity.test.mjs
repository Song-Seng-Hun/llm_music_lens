import test from 'node:test';
import assert from 'node:assert/strict';
import { composeContinuity } from '../analysis/music-continuity.mjs';

function segment(id, offset = 0, duration = 2, estimated = true) {
  const total = Math.ceil(Math.round(duration * 22050) / 441);
  const hz = Array.from({ length: total }, (_, i) => i % 3 === 1 ? null : 220 + i % 400);
  return {
    source: { encoding: 'pcm_s16le', sampleRate: 22050, channels: 1, sha256: id.toString(16).padStart(64, '0'), offsetSeconds: offset, duration },
    timing: { beats: [0.2, 1.1].filter(t => t < duration), downbeats: [0.2].filter(t => t < duration),
      provenance: { checkpointSha256: 'a'.repeat(64), onnxSha256: 'b'.repeat(64), frameRate: 50, contextFrames: 250, borderFrames: 6, overlap: 'keep_first', decoder: 'official-minimal' } },
    pitch: estimated ? { status: 'estimated', sourceKind: 'isolated-vocal', method: { id: 'monophonic-yin-style-central-check-v2' },
      contour: { start: 0, stepSeconds: 0.02, duration, total, truncated: false, hz,
        midi: hz.map(value => value === null ? null : 62.1234), periodicity: hz.map(value => value === null ? null : 0.93) } }
      : { status: 'unavailable', reason: 'Mixture pitch is unavailable.' },
  };
}

// Flattening clips could lose crop nulls, change published MIDI, or silently
// choose one overlapping estimate; existing DSP tests do not compose sources.
test('song-clock views retain original values, competing overlaps and physical gaps', () => {
  const a = segment(1, 0.25), b = segment(2, 1.25);
  const args = { duration: 4, start: 0, end: 4, segments: [b, a] }, original = structuredClone(args);
  const result = composeContinuity(args);
  assert.deepEqual(args, original);
  assert.deepEqual(result.coverage, {
    intervals: [{ start: 0.25, end: 1.25, sourceIndices: [0] }, { start: 1.25, end: 2.25, sourceIndices: [0, 1] }, { start: 2.25, end: 3.25, sourceIndices: [1] }],
    gaps: [{ start: 0, end: 0.25 }, { start: 3.25, end: 4 }],
    overlaps: [{ start: 1.25, end: 2.25, sourceIndices: [0, 1] }],
  });
  assert.deepEqual(result.totals, { beats: 4, downbeats: 2, pitchFrames: 200 });
  assert.deepEqual(result.returned, result.totals);
  assert.equal(result.window.nextStart, null);
  for (const slice of result.pitchSlices) {
    const input = [a, b][slice.sourceIndex].pitch.contour;
    assert.equal(slice.start, input.start);
    assert.equal(slice.firstFrameIndex, 0);
    for (const key of ['hz', 'midi', 'periodicity']) assert.deepEqual(slice[key], input[key]);
  }
  for (const row of result.beats) {
    const input = [a, b][row.sourceIndex];
    assert.equal(row.localTime, input.timing.beats[row.index]);
    assert.equal(row.time, input.source.offsetSeconds + row.localTime);
  }
  const gap = composeContinuity({ duration: 4, start: 0, end: 0.2, segments: [a, b] });
  assert.deepEqual(gap.coverage.gaps, [{ start: 0, end: 0.2 }]);
  assert.deepEqual(gap.returned, { beats: 0, downbeats: 0, pitchFrames: 0 });
});

// Identity is semantic, independent of object/input order. Exact duplicates
// collapse, while changing evidence under the same identity must fail.
test('canonical duplicates collapse and conflicting identities reject', () => {
  const a = segment(1), reordered = structuredClone(a);
  reordered.source = Object.fromEntries(Object.entries(reordered.source).reverse());
  reordered.timing.provenance = Object.fromEntries(Object.entries(reordered.timing.provenance).reverse());
  const placed = segment(1, 2), changedRecipe = structuredClone(a);
  changedRecipe.timing.provenance.checkpointSha256 = 'c'.repeat(64);
  const args = { duration: 4, start: 0, end: 4, segments: [a, reordered, placed, changedRecipe] };
  const result = composeContinuity(args);
  assert.equal(result.duplicatesRemoved, 1);
  assert.equal(result.sources.length, 3);
  assert.deepEqual(result, composeContinuity({ ...args, segments: [...args.segments].reverse() }));
  const conflict = structuredClone(a); conflict.pitch.contour.hz[0] = 500;
  assert.throws(() => composeContinuity({ ...args, segments: [a, conflict] }), RangeError);
  const unavailable = segment(2, 0, 2, false), reasonConflict = structuredClone(unavailable);
  reasonConflict.pitch.reason = 'A conflicting caller reason.';
  assert.throws(() => composeContinuity({ ...args, segments: [unavailable, reasonConflict] }), RangeError);
});

// Shared half-open cuts must retain every co-timed observation exactly once,
// even when pitch/event caps bind independently at fractional song offsets.
test('bounded pagination preserves complete observation ledgers and stable sources', () => {
  const pitchDense = Array.from({ length: 20 }, (_, i) => {
    const s = segment(i + 1, 0.1, 30);
    s.timing.beats = s.timing.downbeats = Array.from({ length: 150 }, (_, j) => j * 0.2);
    return s;
  });
  const eventDense = Array.from({ length: 2 }, (_, i) => {
    const s = segment(i + 1, 0.1, 30, false);
    s.timing.beats = s.timing.downbeats = Array.from({ length: 1500 }, (_, j) => j * 0.02);
    return s;
  });
  for (const segments of [pitchDense, eventDense]) {
    const end = 30.1, ledger = new Set();
    let start = 0.1, sources, pages = 0, pitchFrames = 0, events = 0;
    do {
      const page = composeContinuity({ duration: end, start, end, segments });
      if (!sources) sources = page.sources; else assert.deepEqual(page.sources, sources);
      assert.ok(page.window.end > start && page.window.end <= end);
      assert.equal(page.window.requestedEnd, end);
      assert.ok(page.returned.pitchFrames <= 1000);
      assert.ok(page.returned.beats + page.returned.downbeats <= 256);
      assert.deepEqual(page.coverage.intervals, [{ start, end: page.window.end, sourceIndices: sources.map((_, i) => i) }]);
      const retain = key => { assert.equal(ledger.has(key), false); ledger.add(key); };
      for (const type of ['beats', 'downbeats']) for (const row of page[type]) {
        assert.ok(row.time >= start && row.time < page.window.end);
        assert.equal(row.time, segments[row.sourceIndex].source.offsetSeconds + segments[row.sourceIndex].timing[type][row.index]);
        retain(`${type}/${row.sourceIndex}/${row.index}`); events++;
      }
      for (const slice of page.pitchSlices) {
        const input = segments[slice.sourceIndex].pitch.contour;
        for (let j = 0; j < slice.hz.length; j++) {
          const index = slice.firstFrameIndex + j;
          const time = segments[slice.sourceIndex].source.offsetSeconds + (input.start + index * input.stepSeconds);
          assert.ok(time >= start && time < page.window.end);
          for (const key of ['hz', 'midi', 'periodicity']) assert.equal(slice[key][j], input[key][index]);
          retain(`pitch/${slice.sourceIndex}/${index}`); pitchFrames++;
        }
      }
      assert.equal(page.window.truncated, page.window.nextStart !== null);
      start = page.window.nextStart; pages++;
      assert.ok(pages <= 100);
    } while (start !== null);
    assert.equal(events, 6000);
    assert.equal(pitchFrames, segments === pitchDense ? 30000 : 0);
    assert.ok(pages > 1);
  }
});

// Malformed derived-time ties must fail instead of producing an empty page
// with an unchanged cursor. The public wrapper also rejects this clock defect.
test('an indivisible oversized time group rejects rather than looping', () => {
  const s = segment(1, 500, 1, false);
  s.timing.beats = Array.from({ length: 300 }, (_, i) => i * 1e-17);
  s.timing.downbeats = [];
  assert.throws(() => composeContinuity({ duration: 501, start: 500, end: 501, segments: [s] }), RangeError);
});
