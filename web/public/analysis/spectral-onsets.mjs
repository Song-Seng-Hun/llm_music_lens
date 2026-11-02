// Positive changes in interior FFT magnitudes; these are candidate attacks,
// not drum, instrument, voice or syllable detections.
export const BROWSER_ONSET_OPTIONS = Object.freeze({
  id: 'browser-spectral-onsets-v1', frameRate: 22050 / 1024,
  peakFraction: 0.08, meanMultiplier: 1.8, minIntervalSeconds: 0.07, minRms: 0, frameContextSeconds: 0,
});
// Server recipe B; a centered 1024 FFT has 512 samples of reflected context.
export const SERVER_ONSET_OPTIONS = Object.freeze({
  ...BROWSER_ONSET_OPTIONS, id: 'server-spectral-attacks-v1', frameRate: 50, minRms: 0.0001,
  frameContextSeconds: 512 / 22050,
});

/** Index 0 is FFT bin 1. previous stores abs(FFT)/fftSize and is updated. */
export function spectralFlux(interiorMagnitudes, previous, sampleRate, fftSize, { magnitudeScale = 1 } = {}) {
  if (!Number.isInteger(fftSize) || fftSize < 4 || fftSize % 2 || interiorMagnitudes.length !== fftSize / 2 - 1 || previous.length !== interiorMagnitudes.length || previous === interiorMagnitudes) throw new RangeError('Flux requires separate, equal-length interior spectra excluding DC and Nyquist');
  if (!Number.isFinite(sampleRate) || sampleRate <= 0 || !Number.isFinite(magnitudeScale) || magnitudeScale <= 0) throw new RangeError('Sample rate and magnitude scale must be finite and positive');
  let fluxLow = 0, fluxMid = 0, fluxHigh = 0;
  for (let i = 0; i < interiorMagnitudes.length; i++) {
    const hz = (i + 1) * sampleRate / fftSize, mag = interiorMagnitudes[i] * magnitudeScale;
    const delta = Math.max(0, mag - previous[i]); previous[i] = mag;
    if (hz < 180) fluxLow += delta;
    else if (hz < 2000) fluxMid += delta;
    else fluxHigh += delta;
  }
  return { fluxLow, fluxMid, fluxHigh };
}

/** Fixed three-frame smoothing (.25, .5, .25), then thresholded local peaks. */
export function pickSpectralOnsets(frames, duration, options = BROWSER_ONSET_OPTIONS) {
  const { frameRate, peakFraction, meanMultiplier, minIntervalSeconds, minRms, frameContextSeconds } = { ...BROWSER_ONSET_OPTIONS, ...options };
  if (options.active === false || !frames.length) return [];
  const hopSeconds = 1 / frameRate;
  // A difference uses this FFT and its predecessor. Reflection at a cropped
  // edge is not observed new content; it must not set the global threshold.
  const series = frames.map(frame => frame.rms < minRms || (frameContextSeconds > 0 && (frame.time < frameContextSeconds + hopSeconds || frame.time > duration - frameContextSeconds)) ? 0 : frame.fluxLow + frame.fluxMid + frame.fluxHigh);
  const maxFlux = Math.max(0, ...series);
  if (!(maxFlux > 0)) return [];
  const smooth = series.map((value, i) => (value + (series[i - 1] ?? 0) * 0.5 + (series[i + 1] ?? 0) * 0.5) / 2);
  const threshold = Math.max(maxFlux * peakFraction, smooth.reduce((sum, value) => sum + value, 0) / smooth.length * meanMultiplier);
  let last = -Infinity;
  const events = [];
  for (let i = 1; i + 1 < smooth.length; i++) {
    const frame = frames[i];
    // Smoothing adds one previous and one following difference to that support.
    if (frame.rms < minRms || frame.time < 0 || frame.time >= duration || (frameContextSeconds > 0 && (frame.time < frameContextSeconds + 2 * hopSeconds || frame.time > duration - frameContextSeconds - hopSeconds)) || smooth[i] < threshold || smooth[i] < smooth[i - 1] || smooth[i] <= smooth[i + 1] || i / frameRate - last < minIntervalSeconds) continue;
    const bands = [['low', frame.fluxLow], ['mid', frame.fluxMid], ['high', frame.fluxHigh]].sort((a, b) => b[1] - a[1]);
    events.push({ time: frame.time, strength: Math.max(0, Math.min(1, smooth[i] / maxFlux)), band: bands[0][0],
      fluxLow: frame.fluxLow, fluxMid: frame.fluxMid, fluxHigh: frame.fluxHigh });
    last = frame.time;
  }
  return events;
}
