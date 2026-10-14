export function noteWindowAudio(samples, left, right, sampleRate = 22050) {
  const audio = samples.subarray(left, right);
  if (right < samples.length) return audio;
  // Basic Pitch's final frame crop/time conversion needs right context to
  // cover the source tail. Returned events are still clipped to source time.
  const padded = new Float32Array(audio.length + sampleRate);
  padded.set(audio);
  return padded;
}

// Retain each window's core, including notes sustained across its left edge.
export function appendWindowNotes(items, events, left, coreStart, coreEnd, duration, cap = 4000) {
  let truncated = false;
  const round = n => Math.round(n * 10000) / 10000;
  for (const n of events) {
    const rawStart = n.startTimeSeconds + left, rawEnd = rawStart + n.durationSeconds;
    const start = Math.max(coreStart, rawStart, 0), end = Math.min(coreEnd, rawEnd, duration);
    if (!(end > start) || n.pitchMidi < 21 || n.pitchMidi > 108) continue;
    // Merge only a continuation entering from the overlap, not a fresh attack.
    const previous = rawStart < coreStart ? items.findLast(v => v.midi === n.pitchMidi && Math.abs(v.end - coreStart) < 0.001) : null;
    const boundedEnd = Math.min(coreEnd, duration, round(end));
    if (previous) { if (boundedEnd > previous.start) previous.end = boundedEnd; continue; }
    const roundedStart = round(start);
    if (!(boundedEnd > roundedStart)) continue;
    if (items.length >= cap) { truncated = true; continue; }
    items.push({ start: roundedStart, end: boundedEnd, midi: n.pitchMidi, confidence: round(Math.max(0, Math.min(1, n.amplitude))) });
  }
  return truncated;
}
