// Local signal measurements only. Scores are heuristics, never probabilities.
const MAX_SECONDS = 600;
const PROCESS_RATE = 4000;
const NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
const pause = () => new Promise((resolve) => setTimeout(resolve, 0));

function median(values) {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted.length ? sorted[Math.floor(sorted.length / 2)] : 0;
}

function tempoFromOnsets(onsets, start, end) {
  const beats = onsets.filter((t) => t >= start && t < end);
  if (beats.length < 4) return { bpm: null, candidates: [], beats, status: 'unknown' };
  const intervals = beats.slice(1).map((t, i) => t - beats[i]);
  const middle = median(intervals);
  // Reject isolated long gaps before estimating the repeated pulse interval.
  const repeated = intervals.filter((v) => Math.abs(v - middle) <= middle * 0.2);
  if (repeated.length < intervals.length * 0.7 || middle <= 0) {
    return { bpm: null, candidates: [], beats, status: 'unreliable' };
  }
  const mean = repeated.reduce((sum, v) => sum + v, 0) / repeated.length;
  let bpm = 60 / mean;
  while (bpm > 240) bpm /= 2;
  while (bpm < 40) bpm *= 2;
  const variation = repeated.reduce((sum, v) => sum + Math.abs(v - mean), 0) / repeated.length / mean;
  const score = Math.min(0.95, (repeated.length / intervals.length) * Math.max(0, 1 - variation * 3));
  const candidates = [{ bpm: Math.round(bpm * 10) / 10, score }];
  if (bpm / 2 >= 40) candidates.push({ bpm: Math.round(bpm * 5) / 10, score: score * 0.8 });
  if (bpm * 2 <= 240) candidates.push({ bpm: Math.round(bpm * 20) / 10, score: score * 0.6 });
  return { bpm: candidates[0].bpm, candidates, beats, status: 'heuristic-octave-ambiguous' };
}

function estimatePitch(signal, offset, size, rate) {
  let mean = 0;
  for (let i = 0; i < size; i++) mean += signal[offset + i];
  mean /= size;
  const frame = new Float32Array(size);
  let power = 0;
  for (let i = 0; i < size; i++) {
    frame[i] = signal[offset + i] - mean;
    power += frame[i] * frame[i];
  }
  if (power / size < 0.000001) return null;
  const minLag = Math.max(2, Math.floor(rate / 800));
  const maxLag = Math.min(Math.ceil(rate / 70), Math.floor(size / 2) - 1);
  const correlations = new Float64Array(maxLag + 2);
  for (let lag = minLag - 1; lag <= maxLag + 1; lag++) {
    let cross = 0, left = 0, right = 0;
    for (let i = 0; i < size - lag; i++) {
      const a = frame[i], b = frame[i + lag];
      cross += a * b;
      left += a * a;
      right += b * b;
    }
    correlations[lag] = left && right ? cross / Math.sqrt(left * right) : 0;
  }
  // First strong local autocorrelation maximum reduces octave-down errors on tones.
  for (let lag = minLag; lag <= maxLag; lag++) {
    const previous = correlations[lag - 1], center = correlations[lag], next = correlations[lag + 1];
    if (center < 0.88 || center < previous || center <= next) continue;
    const denominator = previous - 2 * center + next;
    const correction = denominator ? Math.max(-0.5, Math.min(0.5, 0.5 * (previous - next) / denominator)) : 0;
    const hz = rate / (lag + correction);
    if (hz < 70 || hz > 800) continue;
    const midi = 69 + 12 * Math.log2(hz / 440);
    const roundedMidi = Math.round(midi);
    return { hz, midi, note: NOTE_NAMES[(roundedMidi % 12 + 12) % 12] + (Math.floor(roundedMidi / 12) - 1), confidence: center };
  }
  return null;
}

/**
 * Analyze <=600 seconds of mono PCM. No models, network calls or exact transcription.
 * The worker may be cancelled by termination. Direct callers receive stage progress.
 */
export async function analyzeAudio(samples, sampleRate, { onProgress } = {}) {
  if (!(samples instanceof Float32Array) || samples.length === 0) throw new Error('Audio must be a nonempty mono Float32Array.');
  if (!Number.isFinite(sampleRate) || sampleRate < 1000 || sampleRate > 192000) throw new Error('Sample rate must be between 1000 and 192000 Hz.');
  const duration = samples.length / sampleRate;
  if (duration > MAX_SECONDS) throw new Error('Local analysis supports at most 600 seconds. Trim the audio first.');
  const progress = (value, label) => onProgress?.(Math.max(0, Math.min(1, value)), label);
  progress(0, 'Preparing mono audio');
  const rate = Math.min(PROCESS_RATE, sampleRate);
  const length = Math.max(1, Math.ceil(duration * rate));
  const signal = new Float32Array(length);
  const peakCount = Math.min(800, samples.length);
  const waveform = Array(peakCount).fill(0);
  const ratio = sampleRate / rate;
  const progressStep = Math.max(1, Math.ceil(length / 20));
  for (let bin = 0; bin < length; bin++) {
    const begin = Math.floor(bin * ratio), end = Math.min(samples.length, Math.floor((bin + 1) * ratio));
    let sum = 0;
    for (let i = begin; i < end; i++) {
      const value = samples[i];
      if (!Number.isFinite(value)) throw new Error('Audio contains non-finite samples.');
      sum += value;
      const peakIndex = Math.min(peakCount - 1, Math.floor(i * peakCount / samples.length));
      waveform[peakIndex] = Math.max(waveform[peakIndex], Math.abs(value));
    }
    signal[bin] = end > begin ? sum / (end - begin) : 0;
    if (bin % progressStep === 0) {
      progress(0.2 * bin / length, 'Downsampling and waveform');
      await pause();
    }
  }

  const hop = Math.max(1, Math.round(rate / 100));
  const rmsFrames = [];
  for (let i = 0; i < signal.length; i += hop) {
    let sum = 0;
    const end = Math.min(signal.length, i + hop);
    for (let j = i; j < end; j++) sum += signal[j] * signal[j];
    rmsFrames.push(Math.sqrt(sum / (end - i)));
  }
  const energy = [];
  for (let i = 0; i < rmsFrames.length; i += 10) {
    const group = rmsFrames.slice(i, i + 10);
    energy.push({ time: i * hop / rate, rms: Math.sqrt(group.reduce((sum, v) => sum + v * v, 0) / group.length) });
  }
  const totalRms = Math.sqrt(rmsFrames.reduce((sum, v) => sum + v * v, 0) / rmsFrames.length);
  const rises = rmsFrames.map((value, i) => Math.max(0, value - (rmsFrames[i - 1] ?? value)));
  let maxRise = 0;
  for (const value of rises) maxRise = Math.max(maxRise, value);
  const onsets = [];
  for (let i = 1; i < rises.length - 1; i++) {
    const time = i * hop / rate;
    // Steady tones have small window-boundary RMS ripples; they are not onsets.
    if (totalRms >= 0.001 && rises[i] > Math.max(0.002, totalRms * 0.08, maxRise * 0.22) && rises[i] >= rises[i - 1] && rises[i] > rises[i + 1] && time - (onsets.at(-1) ?? -1) >= 0.08) onsets.push(time);
  }
  const tempo = tempoFromOnsets(onsets, 0, duration);
  const segments = [];
  for (let start = 0; start < duration; start += 15) {
    const end = Math.min(duration, start + 15);
    const local = tempoFromOnsets(onsets, start, end);
    if (local.bpm !== null) segments.push({ start, end, bpm: local.bpm });
  }
  tempo.segments = segments;
  progress(0.35, 'Energy rises and tempo candidates');
  await pause();

  const pitch = [];
  const frameSize = Math.min(512, Math.round(rate * 0.128));
  const pitchHop = Math.max(frameSize, Math.round(rate * 0.25));
  const frameCount = Math.max(0, Math.floor((signal.length - frameSize) / pitchHop) + 1);
  for (let index = 0; index < frameCount; index++) {
    const offset = index * pitchHop;
    const estimate = totalRms >= 0.001 ? estimatePitch(signal, offset, frameSize, rate) : null;
    if (estimate) {
      const start = offset / rate, end = (offset + frameSize) / rate;
      const previous = pitch.at(-1);
      // Merge only adjacent sampled spans with the same rounded note.
      if (previous && previous.note === estimate.note && start - previous.end < 0.15 && Math.abs(previous.midi - estimate.midi) < 0.3) {
        previous.end = end;
        previous.confidence = Math.min(previous.confidence, estimate.confidence);
      } else pitch.push({ start, end, ...estimate });
    }
    if (index % 80 === 0) {
      progress(0.35 + 0.6 * index / Math.max(1, frameCount), 'Dominant periodic pitch (heuristic)');
      await pause();
    }
  }
  const warnings = [
    'Local DSP is a pilot heuristic; scores are not calibrated probabilities or real-song accuracy evidence.',
    'Pitch is sparsely sampled (about every 250 ms), limited to 70–800 Hz, and unreliable for polyphony, percussion and vocals with multiple sources.',
    'Key and chords are unknown: this baseline does not substantiate harmonic transcription.',
    'Instruments, lyrics and rap delivery require separate interpretation; they are not measured here.',
  ];
  if (tempo.bpm !== null) warnings.push('Tempo has half/double ambiguity. Beat times are detected energy rises, not a verified beat grid.');
  else warnings.push('No stable repeated energy-rise pulse was found; tempo is unknown or unreliable.');
  if (totalRms < 0.001) warnings.push('Audio is silent or very quiet; musical estimates are withheld.');
  progress(1, 'Local analysis complete');
  return { duration, waveform, energy, tempo, pitch, key: { label: null, status: 'unknown' }, chords: [], warnings, method: 'local-dsp-v1: box-average downsampling, RMS energy rises/repeated pulse intervals, normalized autocorrelation pitch; heuristic only' };
}
