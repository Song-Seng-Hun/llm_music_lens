// Bounded local spectral measurements. All scores are heuristics, not probabilities.
import { aggregateAcoustics } from './analysis/acoustics.mjs';
import { spectralDescriptors } from './analysis/spectral-descriptors.mjs';
import { estimateTempo } from './analysis/tempo-grid.mjs';
const MAX_SECONDS = 600;
const HOP = 1024;
const SIZE = 4096;
const NOTES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
const clamp = (x, lo = 0, hi = 1) => Math.max(lo, Math.min(hi, x));

function fft(re, im) {
  const n = re.length;
  for (let i = 1, j = 0; i < n; i++) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) { [re[i], re[j]] = [re[j], re[i]]; [im[i], im[j]] = [im[j], im[i]]; }
  }
  for (let len = 2; len <= n; len <<= 1) {
    const angle = -2 * Math.PI / len, wr0 = Math.cos(angle), wi0 = Math.sin(angle);
    for (let start = 0; start < n; start += len) {
      let wr = 1, wi = 0;
      for (let j = 0; j < len / 2; j++) {
        const a = start + j, b = a + len / 2;
        const tr = wr * re[b] - wi * im[b], ti = wr * im[b] + wi * re[b];
        re[b] = re[a] - tr; im[b] = im[a] - ti; re[a] += tr; im[a] += ti;
        [wr, wi] = [wr * wr0 - wi * wi0, wr * wi0 + wi * wr0];
      }
    }
  }
}

function normalizeChroma(values) {
  const sum = values.reduce((a, b) => a + b, 0);
  return sum > 1e-8 ? values.map(x => x / sum) : null;
}

function bestTriad(chroma) {
  const normalized = normalizeChroma(chroma);
  if (!normalized || Math.max(...normalized) < 0.12) return null;
  let best = null, next = 0;
  for (let root = 0; root < 12; root++) for (const [suffix, third] of [['', 4], ['m', 3]]) {
    const score = (normalized[root] + 0.8 * normalized[(root + third) % 12] + 0.7 * normalized[(root + 7) % 12]) / 2.5;
    if (!best || score > best.score) { next = best?.score ?? next; best = { label: `${NOTES[root]}${suffix}`, score }; }
    else next = Math.max(next, score);
  }
  const confidence = clamp(((best?.score ?? 0) - next) * 5);
  return confidence >= 0.12 ? { label: best.label, confidence } : null;
}

export function analyzeRhythm(samples, sampleRate, onProgress = () => {}) {
  if (!(samples instanceof Float32Array) || samples.length === 0) throw new Error('Audio must be a nonempty mono Float32Array.');
  if (sampleRate !== 22050) throw new Error('Precision rhythm analysis requires 22050 Hz mono audio.');
  const duration = samples.length / sampleRate;
  if (duration > MAX_SECONDS) throw new Error('Precision rhythm analysis supports at most 600 seconds.');
  for (const x of samples) if (!Number.isFinite(x)) throw new Error('Audio contains non-finite samples.');
  const frameRate = sampleRate / HOP, frameCount = Math.ceil(samples.length / HOP);
  const fftRe = new Float64Array(SIZE), fftIm = new Float64Array(SIZE), prev = new Float64Array(SIZE / 2);
  const frameFeatures = [], onsetSeries = [], chordBuckets = [];
  let totalEnergy = 0;
  for (let f = 0; f < frameCount; f++) {
    const center = f * HOP;
    let rmsSum = 0;
    for (let i = 0; i < SIZE; i++) {
      const sampleIndex = center + i - SIZE / 2;
      const x = sampleIndex >= 0 && sampleIndex < samples.length ? samples[sampleIndex] : 0;
      const w = 0.5 - 0.5 * Math.cos(2 * Math.PI * i / (SIZE - 1));
      fftRe[i] = x * w; fftIm[i] = 0; rmsSum += x * x;
    }
    fft(fftRe, fftIm);
    let lo = 0, mid = 0, hi = 0, fluxLo = 0, fluxMid = 0, fluxHi = 0;
    const frameChroma = Array(12).fill(0);
    for (let k = 1; k < SIZE / 2; k++) {
      const hz = k * sampleRate / SIZE, mag = Math.hypot(fftRe[k], fftIm[k]) / SIZE;
      const d = Math.max(0, mag - prev[k - 1]); prev[k - 1] = mag;
      if (hz < 180) { lo += mag; fluxLo += d; }
      else if (hz < 2000) { mid += mag; fluxMid += d; }
      else { hi += mag; fluxHi += d; }
      if (hz >= 45 && hz <= 4000 && mag > 0) {
        const midi = Math.round(69 + 12 * Math.log2(hz / 440));
        if (midi >= 36 && midi <= 84) { const pc = (midi % 12 + 12) % 12; frameChroma[pc] += mag; }
      }
    }
    const rms = Math.sqrt(rmsSum / SIZE);
    totalEnergy += rmsSum;
    frameFeatures.push({ time: Math.min(duration, center / sampleRate), lo, mid, hi, rms, fluxLo, fluxMid, fluxHi,
      ...spectralDescriptors(prev.subarray(0, SIZE / 2 - 1), sampleRate, SIZE) });
    onsetSeries.push(fluxLo + fluxMid + fluxHi);
    const bucket = Math.floor(center / sampleRate);
    chordBuckets[bucket] ??= Array(12).fill(0);
    for (let pc = 0; pc < 12; pc++) chordBuckets[bucket][pc] += frameChroma[pc];
    if (f % 128 === 0) onProgress(Math.min(0.8, f / frameCount * 0.8), 'Spectral rhythm and harmony');
  }
  const active = Math.sqrt(totalEnergy / samples.length) > 0.0005;
  const maxFlux = Math.max(0, ...onsetSeries);
  const onsetsAll = [];
  if (active && maxFlux > 0) {
    const smooth = onsetSeries.map((x, i) => (x + (onsetSeries[i - 1] ?? 0) * 0.5 + (onsetSeries[i + 1] ?? 0) * 0.5) / 2);
    const threshold = Math.max(maxFlux * 0.08, smooth.reduce((a, b) => a + b, 0) / smooth.length * 1.8);
    let last = -Infinity;
    for (let i = 1; i + 1 < smooth.length; i++) {
      if (smooth[i] < threshold || smooth[i] < smooth[i - 1] || smooth[i] <= smooth[i + 1] || i / frameRate - last < 0.07) continue;
      const ff = frameFeatures[i], bands = [['low', ff.fluxLo], ['mid', ff.fluxMid], ['high', ff.fluxHi]].sort((a, b) => b[1] - a[1]);
      onsetsAll.push({ time: ff.time, strength: clamp(smooth[i] / maxFlux), band: bands[0][0] }); last = ff.time;
    }
  }
  const envAll = [];
  const envStep = Math.max(1, Math.ceil(frameCount / 2400));
  for (let i = 0; i < frameFeatures.length; i += envStep) {
    const part = frameFeatures.slice(i, i + envStep), avg = key => part.reduce((s, x) => s + x[key], 0) / part.length;
    envAll.push({ time: part[0].time, low: avg('lo'), mid: avg('mid'), high: avg('hi'), rms: avg('rms') });
  }
  const sortedRms = envAll.map(x => x.rms).sort((a, b) => a - b), median = sortedRms[Math.floor(sortedRms.length / 2)] || 0;
  const sections = [];
  if (duration > 0) {
    let start = 0, previous = envAll[0]?.rms ?? 0;
    for (let i = 1; i < envAll.length; i++) {
      const cur = envAll[i], t = cur.time;
      if (t - start >= 8 && Math.abs(cur.rms - previous) > Math.max(0.012, median * 0.35)) {
        sections.push({ start, end: t, label: 'energy change', energy: previous }); start = t;
      }
      previous = cur.rms;
    }
    sections.push({ start, end: duration, label: 'energy region', energy: previous });
    while (sections.length > 80) sections.splice(1, 1);
  }
  const chords = [];
  if (active) {
    for (let start = 0; start < duration; start += 2) {
      const firstBucket = Math.floor(start), lastBucket = Math.min(chordBuckets.length, Math.ceil(Math.min(duration, start + 2)));
      const local = Array(12).fill(0);
      for (let b = firstBucket; b < lastBucket; b++) for (let pc = 0; pc < 12; pc++) local[pc] += chordBuckets[b]?.[pc] ?? 0;
      const triad = bestTriad(local), end = Math.min(duration, start + 2);
      if (!triad || end <= start) continue;
      const previous = chords.at(-1);
      if (previous && previous.label === triad.label && start - previous.end < 0.01) {
        previous.end = end; previous.confidence = Math.min(previous.confidence, triad.confidence);
      } else chords.push({ start, end, ...triad });
    }
  }
  const tempo = estimateTempo(onsetSeries, frameRate, active ? onsetsAll : [], duration);
  const warnings = [
    'Tempo and spectral features are local heuristic candidates; synthetic checks do not establish real-song accuracy.',
    'Half/double tempo ambiguity remains; beat phase is a candidate and does not identify downbeats or meter.',
    'Frequency bands describe spectral energy only and do not identify kick, snare, instruments, verse or chorus.',
    'Spectral-only chords are two-second mixture-chroma candidates. Detailed harmony explicitly reports whether transcribed notes or spectral-only evidence supplied the labels.',
  ];
  if (!active) warnings.push('Signal is silent or very quiet; rhythmic and harmonic estimates are withheld.');
  const result = {
    method: 'local-spectral-v2: 4096-sample Hann FFT, 1024-sample hop, spectral flux, onset/grid support + local tempo comparison, chroma triads, mono acoustic texture; heuristic',
    bpm: active ? tempo.bpm : null, tempoCandidates: active ? tempo.candidates.slice(0, 8) : [],
    tempoEvidence: tempo.evidence,
    beats: active ? tempo.beats.slice(0, 4000) : [], onsets: onsetsAll.slice(0, 6000),
    // A dominant triad is not a song key. The detailed note-harmony stage supplies it.
    envelope: envAll.slice(0, 2400), sections, key: { label: null, score: 0 }, chords: chords.slice(0, 300), warnings,
    acoustics: aggregateAcoustics(frameFeatures, samples, sampleRate, onsetsAll),
    truncated: { onsets: onsetsAll.length > 6000, envelope: envAll.length > 2400, chords: chords.length > 300 },
  };
  const bandMax = { low: Math.max(1e-9, ...result.envelope.map(x => x.low)), mid: Math.max(1e-9, ...result.envelope.map(x => x.mid)), high: Math.max(1e-9, ...result.envelope.map(x => x.high)) };
  result.envelope = result.envelope.map(x => ({ ...x, low: clamp(x.low / bandMax.low), mid: clamp(x.mid / bandMax.mid), high: clamp(x.high / bandMax.high) }));
  onProgress(1, 'Spectral rhythm and harmony complete');
  return result;
}
