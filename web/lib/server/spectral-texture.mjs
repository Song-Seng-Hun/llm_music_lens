import { aggregateAcoustics } from '../../public/analysis/acoustics.mjs';
import { BEAT_FFT_SIZE, BEAT_HOP_SIZE } from './beat-frontend.mjs';

/** Bounded spectral observations; energy remains in the waveform result. */
export function spectralTexture(frames, pcm, sampleRate) {
  const { windows } = aggregateAcoustics(frames, pcm, sampleRate, null);
  return {
    schema: 'spectral-texture/1',
    method: 'magnitude centroid; 85% power rolloff; power flatness; power-band shares',
    clock: 'excerpt-relative-seconds',
    analysis: { sampleRate, fftSize: BEAT_FFT_SIZE, hopSeconds: BEAT_HOP_SIZE / sampleRate,
      window: 'periodic Hann; centered; reflect padding', bins: 'positive interior only; DC and Nyquist excluded',
      magnitudeNormalization: 'FFT magnitude / fftSize', activeRmsMinimum: 0.0001, activeDbfsMinimum: -80,
      aggregationSeconds: 1, centroidRolloffFlatness: 'mean of active frames', bandBalance: 'summed active-frame power shares',
      frequencyBandsHz: { low: [0, 180], mid: [180, 2000], high: [2000, sampleRate / 2] },
      bandIntervals: 'lower inclusive, upper exclusive; subject to interior FFT bins' },
    // Preserve sub-0.1ms final tails; browser display rounding can create a
    // zero-length window or put its end beyond the exact PCM duration.
    windows: windows.map(({ start, activeFraction, centroidHz, rolloff85Hz, flatness, bandBalance }) =>
      ({ start, end: Math.min(start + 1, pcm.length / sampleRate), activeFraction, centroidHz, rolloff85Hz, flatness, bandBalance })),
    warnings: [
      'Measurements describe the supplied mono signal, not instruments, genre, emotion or artistic quality. Flatness is not calibrated noise probability.',
      'Spectral values require frame and one-second-bin RMS at least -80 dBFS; below the gate they are unknown (null), not zero-frequency sound.',
      'Centered frames include neighboring samples across bin boundaries. activeFraction is a fraction of frame centers, not exact sound duration.',
      'DC, Nyquist and frequencies above 11025 Hz are unavailable. This 1024-point frontend differs from the saved browser report FFT; do not equate their numbers.',
    ],
  };
}
