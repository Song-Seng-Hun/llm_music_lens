// SOME outputs continuous MIDI, rests and durations, not calibrated confidence.
export const VOCAL_MODEL = "SOME some-0.0.1; ONNX 7b5a4eb8e5b1; native pitch contour; frame-rest edges; browser WASM";
export const VOCAL_MODEL_SHA256 = "7b5a4eb8e5b1486902e3918516020a8f0f78d0d22c02ebd4d5410b2dc60e8e75";
export const VOCAL_RATE = 44100;
export const VOCAL_CORE_SECONDS = 40;
export const VOCAL_CONTEXT_SECONDS = 2;

// Exact float32 hop stored by the pinned graph; retain its original note clock.
export const VOCAL_FRAME_STEP = 0.01160997711122036;
export function decodeVocalSequence(values, duration, frames) {
  const midi = values.note_midi, rest = values.note_rest, durations = values.note_dur;
  if (!midi || !rest || !durations || midi.length !== rest.length || midi.length !== durations.length || midi.length > 100000) throw new Error("Invalid SOME output dimensions.");
  const mask = frames?.rest, mapping = frames?.mapping;
  if (!mask || !mapping || mask.type !== "bool" || mapping.type !== "int64" || mask.dims.length !== 2 || mapping.dims.length !== 2 || mask.dims[0] !== 1 || mapping.dims[0] !== 1 || mask.dims[1] !== mask.data.length || mapping.dims[1] !== mask.data.length || mapping.data.length !== mask.data.length || !mask.data.length) throw new Error("Invalid SOME frame outputs. Re-run scripts/prepare-vocal-model.py.");
  const segments = Array.from({ length: midi.length }, () => ({ count: 0, voiced: 0, first: -1, last: -1 }));
  let previous = 1;
  for (let f = 0; f < mapping.data.length; f++) {
    let id = mapping.data[f];
    if (typeof id === "bigint") {
      if (id < 1n || id > BigInt(segments.length)) throw new Error("Invalid SOME frame ID.");
      id = Number(id); // Bounds checked before converting native BigInt64Array.
    }
    if (!Number.isInteger(id) || id < 1 || id > segments.length || (f === 0 && id !== 1) || id < previous || id > previous + 1 || ![false, true, 0, 1].includes(mask.data[f])) throw new Error("Invalid SOME frame mapping.");
    previous = id;
    const segment = segments[id - 1], local = segment.count++;
    if (!mask.data[f]) { if (segment.first < 0) segment.first = local; segment.last = local; segment.voiced++; }
  }
  const events = [];
  let time = 0, omitted = 0, preserved = 0;
  for (let i = 0; i < midi.length; i++) {
    if (!Number.isFinite(midi[i]) || !Number.isFinite(durations[i]) || durations[i] < 0 || ![0, 1, false, true].includes(rest[i])) throw new Error("Invalid SOME output values.");
    const segment = segments[i], rawStart = time;
    if (!segment.count || Math.fround(segment.count * VOCAL_FRAME_STEP) !== durations[i] || Boolean(rest[i]) !== (segment.voiced * 2 < segment.count)) throw new Error("SOME frame aggregation mismatch.");
    time += durations[i]; // Rests and trimmed edges still occupy original time.
    const originalEnd = Math.min(time, duration);
    if (rest[i] || originalEnd <= rawStart) continue;
    if (midi[i] < 21 || midi[i] > 108) { omitted++; continue; }
    let start = rawStart + segment.first * VOCAL_FRAME_STEP;
    let end = Math.min(time - (segment.count - 1 - segment.last) * VOCAL_FRAME_STEP, duration);
    // Clipping after trimming avoids subtracting the same EOF overhang twice.
    // Keep the native interval if its trimmed support lies beyond EOF.
    if (!(end > start)) { start = rawStart; end = originalEnd; preserved++; }
    events.push({ start, end, midi: midi[i], confidence: null });
  }
  if (Math.abs(time - duration) > 0.05) throw new Error("SOME output duration does not match audio.");
  return { events, omitted, preserved };
}

export function appendVocalWindow(items, events, left, coreStart, coreEnd, duration, cap = 4000) {
  let truncated = false;
  for (const event of events) {
    const rawStart = left + event.start, rawEnd = left + event.end;
    const start = Math.max(coreStart, rawStart), end = Math.min(coreEnd, rawEnd, duration);
    if (!(end > start)) continue;
    // Only a sustained event entering from context can join a preceding core.
    // Do not quantize pitch or fit boundaries to reference labels.
    const previous = rawStart < coreStart ? items.at(-1) : null;
    if (previous && Math.abs(previous.end - coreStart) < 1e-6 && Math.abs(previous.midi - event.midi) <= 0.5) {
      previous.end = end;
      continue;
    }
    if (items.length >= cap) { truncated = true; continue; }
    items.push({ start, end, midi: event.midi, confidence: null });
  }
  return truncated;
}
