// Bounded monophonic periodic-pitch evidence. This YIN-style recipe is not
// pYIN, voice detection, calibrated confidence or automatic note boundaries.
const RATE = 22050, DECIMATION = 4, PITCH_RATE = RATE / DECIMATION;
const FRAME_SIZE = 512, HOP = 441, FILTER_RADIUS = 47, CUTOFF_HZ = 1800;
const FRAME_RADIUS = (FRAME_SIZE - 1) * DECIMATION / 2;
const SUPPORT_RADIUS = FRAME_RADIUS + FILTER_RADIUS;
const MIN_HZ = 70, MAX_HZ = 1000, MAX_LAG = Math.ceil(PITCH_RATE / MIN_HZ);
const THRESHOLD = 0.15, MIN_AC_RMS = 0.001;
const round = value => Math.round(value * 10000) / 10000;

// Symmetric 95-tap Blackman-windowed sinc, normalized to unit DC gain.
// Centered convolution compensates group delay; absent support is never padded.
const kernel = Float64Array.from({ length: FILTER_RADIUS + 1 }, (_, distance) => {
  const ideal = distance ? Math.sin(2 * Math.PI * CUTOFF_HZ / RATE * distance) / (Math.PI * distance) : 2 * CUTOFF_HZ / RATE;
  const window = 0.42 + 0.5 * Math.cos(Math.PI * distance / FILTER_RADIUS) + 0.08 * Math.cos(2 * Math.PI * distance / FILTER_RADIUS);
  return ideal * window;
});
const gain = kernel[0] + 2 * kernel.subarray(1).reduce((sum, value) => sum + value, 0);
for (let i = 0; i < kernel.length; i++) kernel[i] /= gain;

function lowpass(samples) {
  const filtered = new Float64Array(samples.length);
  for (let i = FILTER_RADIUS; i < samples.length - FILTER_RADIUS; i++) {
    let sum = kernel[0] * samples[i];
    for (let distance = 1; distance <= FILTER_RADIUS; distance++) sum += kernel[distance] * (samples[i - distance] + samples[i + distance]);
    filtered[i] = sum;
  }
  return filtered;
}

function framePitch(frame, normalizedDifference) {
  let mean = 0;
  for (const value of frame) mean += value;
  mean /= FRAME_SIZE;
  let energy = 0;
  for (let i = 0; i < FRAME_SIZE; i++) { frame[i] -= mean; energy += frame[i] * frame[i]; }
  if (Math.sqrt(energy / FRAME_SIZE) < MIN_AC_RMS) return null;
  let cumulative = 0;
  normalizedDifference[0] = 1;
  for (let lag = 1; lag <= MAX_LAG + 1; lag++) {
    let difference = 0;
    const count = FRAME_SIZE - lag;
    // All pairs of this lag within one fixed frame: their mean midpoint is
    // always the frame center. Divide by count before cumulative normalization.
    for (let i = 0; i < count; i++) { const delta = frame[i] - frame[i + lag]; difference += delta * delta; }
    difference /= count;
    cumulative += difference;
    normalizedDifference[lag] = cumulative > 0 ? difference * lag / cumulative : 1;
  }
  // Search short lags as well: an accepted above-range fundamental must not
  // fall through to a convenient in-range subharmonic later in the scan.
  for (let lag = 2; lag <= MAX_LAG; lag++) {
    const previous = normalizedDifference[lag - 1], center = normalizedDifference[lag], next = normalizedDifference[lag + 1];
    if (center > previous || center >= next) continue;
    const denominator = previous - 2 * center + next;
    const correction = denominator ? Math.max(-0.5, Math.min(0.5, 0.5 * (previous - next) / denominator)) : 0;
    // Test the interpolated minimum too: short-lag integer sampling can miss
    // an above-range fundamental, otherwise accepting a later subharmonic.
    const depth = center + 0.5 * (next - previous) * correction + 0.5 * denominator * correction * correction;
    if (depth >= THRESHOLD) continue;
    const hz = PITCH_RATE / (lag + correction);
    if (hz < MIN_HZ || hz > MAX_HZ) return null;
    return { hz: round(hz), periodicity: round(Math.max(0, Math.min(1, 1 - depth))) };
  }
  return null;
}

export function analyzeVocalPitch(samples, sampleRate = RATE) {
  if (!(samples instanceof Float32Array) || sampleRate !== RATE) throw new TypeError('Vocal pitch requires mono Float32 PCM at exactly 22050 Hz');
  if (samples.length < RATE || samples.length > RATE * 30) throw new RangeError('Vocal pitch accepts 1–30 seconds per request');
  for (const value of samples) if (!Number.isFinite(value) || value < -1 || value > 1) throw new RangeError('PCM must contain finite samples within [-1, 1]');
  const count = Math.ceil(samples.length / HOP), duration = samples.length / RATE;
  const hz = Array(count).fill(null), periodicity = Array(count).fill(null);
  const filtered = lowpass(samples), frame = new Float64Array(FRAME_SIZE), normalizedDifference = new Float64Array(MAX_LAG + 2);
  for (let index = 0; index < count; index++) {
    const center = index * HOP;
    if (center - SUPPORT_RADIUS < 0 || center + SUPPORT_RADIUS >= samples.length) continue;
    // 4*(j-255.5) is an integer native offset, so every frame is exactly
    // centered on k*441 even while the decimation phase cycles through 0..3.
    for (let j = 0; j < FRAME_SIZE; j++) frame[j] = filtered[center - FRAME_RADIUS + j * DECIMATION];
    const candidate = framePitch(frame, normalizedDifference);
    if (candidate) { hz[index] = candidate.hz; periodicity[index] = candidate.periodicity; }
  }
  return {
    start: 0, stepSeconds: HOP / RATE, duration, hz, periodicity,
    method: {
      id: 'monophonic-yin-style-v1', sampleRate: RATE, analysisRate: PITCH_RATE, frameSize: FRAME_SIZE,
      frameSpanSeconds: 2 * FRAME_RADIUS / RATE, hopSeconds: HOP / RATE,
      rangeHz: [MIN_HZ, MAX_HZ], threshold: THRESHOLD, minAcRms: MIN_AC_RMS,
      filter: { kind: 'symmetric Blackman-windowed sinc', taps: 2 * FILTER_RADIUS + 1, cutoffHz: CUTOFF_HZ, dcGain: 1, delay: 'centered; no padding', decimation: DECIMATION },
      edgeSupportSeconds: SUPPORT_RADIUS / RATE,
      difference: 'mean squared difference over all frameSize-lag pairs; every lag has the same mean pair midpoint; cumulative mean normalization',
      selection: 'first local trough whose parabolically interpolated depth is below threshold, parabolic lag refinement, range rejection; no global-minimum fallback or temporal smoothing',
      periodicity: '1 minus parabolically interpolated normalized-difference depth, clamped to [0,1]; uncalibrated, not a voicing probability; null when pitch is unavailable',
      clock: 'exact native sample centers k*441; all centers below duration retained, including unknowns',
    },
    warnings: [
      'Designed for one periodic source. Caller-declared isolated vocal is not verified voice detection or source separation.',
      'Null means insufficient in-range periodic-pitch evidence or missing crop context, not verified silence.',
      'Twenty-millisecond spacing is not temporal accuracy. The approximately 97 ms filter/frame support can blur pitch changes; no frame uses zero or reflected crop padding.',
      'Periodicity is an uncalibrated signal measure, not confidence or a voicing probability. Harmonics, breathiness, noise and multiple sources can cause missed or octave-shifted estimates.',
      'These frames do not infer note boundaries, lyrics, syllables, key, emotion or vocal effort.',
    ],
  };
}
