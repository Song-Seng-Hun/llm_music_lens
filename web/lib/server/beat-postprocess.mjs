export const BEAT_CONTEXT_FRAMES = 250;
export const BEAT_BORDER_FRAMES = 6;
export const BEAT_BINS = 128;

// Official split_piece(..., avoid_short_end=True), fixed-size export padding.
export function beatChunkStarts(frameCount) {
  if (!Number.isInteger(frameCount) || frameCount < 1) throw new RangeError('frameCount must be positive');
  const starts = [];
  for (let start = -BEAT_BORDER_FRAMES; start < frameCount - BEAT_BORDER_FRAMES; start += BEAT_CONTEXT_FRAMES - 2 * BEAT_BORDER_FRAMES) starts.push(start);
  if (frameCount > BEAT_CONTEXT_FRAMES - 2 * BEAT_BORDER_FRAMES) starts[starts.length - 1] = frameCount - (BEAT_CONTEXT_FRAMES - BEAT_BORDER_FRAMES);
  return starts;
}

export function beatFeatureChunk(features, frameCount, start) {
  const chunk = new Float32Array(BEAT_CONTEXT_FRAMES * BEAT_BINS);
  const from = Math.max(0, start), to = Math.min(frameCount, start + BEAT_CONTEXT_FRAMES);
  chunk.set(features.subarray(from * BEAT_BINS, to * BEAT_BINS), (from - start) * BEAT_BINS);
  return chunk;
}

function peakFrames(logits) {
  const peaks = [];
  for (let i = 0; i < logits.length; i++) {
    if (!(logits[i] > 0)) continue;
    let maximum = true;
    for (let j = Math.max(0, i - 3); j <= Math.min(logits.length - 1, i + 3); j++) if (logits[j] > logits[i]) { maximum = false; break; }
    if (maximum) peaks.push(i);
  }
  // Preserve upstream running-mean adjacency behavior (not transitive grouping).
  const deduplicated = [];
  let mean, count = 0;
  for (const peak of peaks) {
    if (count && peak - mean <= 1) { count++; mean += (peak - mean) / count; }
    else { if (count) deduplicated.push(mean); mean = peak; count = 1; }
  }
  if (count) deduplicated.push(mean);
  return deduplicated;
}

export function decodeBeatFrames(beat, downbeat, durationSeconds = Infinity) {
  const beats = peakFrames(beat).map(frame => frame / 50).filter(time => time < durationSeconds);
  const downbeats = peakFrames(downbeat).map(frame => frame / 50).filter(time => time < durationSeconds).map(time => {
    if (!beats.length) return time;
    let nearest = beats[0];
    for (const candidate of beats) if (Math.abs(candidate - time) < Math.abs(nearest - time)) nearest = candidate;
    return nearest;
  });
  return { beats, downbeats: [...new Set(downbeats)].sort((a, b) => a - b) };
}
