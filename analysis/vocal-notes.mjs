// SOME outputs continuous MIDI, rests and durations, not calibrated confidence.
export const VOCAL_MODEL = "SOME some-0.0.1; ONNX 9d55f9d239e9; native fractional MIDI; browser WASM";
export const VOCAL_MODEL_SHA256 = "9d55f9d239e9d70d1bf039506cf1c428b657df7bbb8b94104ca882af6b721e06";
export const VOCAL_RATE = 44100;
export const VOCAL_CORE_SECONDS = 40;
export const VOCAL_CONTEXT_SECONDS = 2;

export function decodeVocalSequence(values, duration) {
  const midi = values.note_midi, rest = values.note_rest, durations = values.note_dur;
  if (!midi || !rest || !durations || midi.length !== rest.length || midi.length !== durations.length || midi.length > 100000) throw new Error("Invalid SOME output dimensions.");
  const events = [];
  let time = 0, omitted = 0;
  for (let i = 0; i < midi.length; i++) {
    if (!Number.isFinite(midi[i]) || !Number.isFinite(durations[i]) || durations[i] < 0 || ![0, 1, false, true].includes(rest[i])) throw new Error("Invalid SOME output values.");
    const start = time;
    time += durations[i]; // Rests occupy time; never concatenate voiced notes.
    const end = Math.min(time, duration);
    if (rest[i] || end <= start) continue;
    if (midi[i] < 21 || midi[i] > 108) { omitted++; continue; }
    events.push({ start, end, midi: midi[i], confidence: null });
  }
  if (Math.abs(time - duration) > 0.05) throw new Error("SOME output duration does not match audio.");
  return { events, omitted };
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
