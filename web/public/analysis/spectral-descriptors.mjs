/**
 * Raw spectral texture, not source/instrument identity. Index 0 is FFT bin 1;
 * callers exclude DC and Nyquist and supply exactly fftSize / 2 - 1 magnitudes.
 * magnitudeScale converts the supplied magnitudes to abs(FFT) / fftSize.
 * Accumulation order and floors preserve the existing browser measurements.
 */
export function spectralDescriptors(interiorMagnitudes, sampleRate, fftSize, { magnitudeScale = 1 } = {}) {
  if (!Number.isInteger(fftSize) || fftSize < 4 || fftSize % 2 || interiorMagnitudes.length !== fftSize / 2 - 1) throw new RangeError('Expected all interior positive-frequency bins, excluding DC and Nyquist');
  if (!Number.isFinite(sampleRate) || sampleRate <= 0 || !Number.isFinite(magnitudeScale) || magnitudeScale <= 0) throw new RangeError('Sample rate and magnitude scale must be finite and positive');
  let lo = 0, mid = 0, hi = 0, weightedHz = 0, powerLow = 0, powerMid = 0, powerHigh = 0, logPower = 0;
  for (let i = 0; i < interiorMagnitudes.length; i++) {
    const hz = (i + 1) * sampleRate / fftSize, mag = interiorMagnitudes[i] * magnitudeScale;
    const power = mag * mag;
    weightedHz += hz * mag; logPower += Math.log(Math.max(1e-20, power));
    if (hz < 180) { powerLow += power; lo += mag; }
    else if (hz < 2000) { powerMid += power; mid += mag; }
    else { powerHigh += power; hi += mag; }
  }
  const totalPower = powerLow + powerMid + powerHigh, totalMagnitude = lo + mid + hi;
  let cumulative = 0, rolloffHz = null;
  if (totalPower > 1e-20) for (let i = 0; i < interiorMagnitudes.length; i++) {
    cumulative += (interiorMagnitudes[i] * magnitudeScale) ** 2;
    if (cumulative >= 0.85 * totalPower) { rolloffHz = (i + 1) * sampleRate / fftSize; break; }
  }
  return {
    centroidHz: totalMagnitude > 1e-10 ? weightedHz / totalMagnitude : null,
    flatness: totalPower > 1e-20 ? Math.max(0, Math.min(1, Math.exp(logPower / interiorMagnitudes.length) / (totalPower / interiorMagnitudes.length))) : null,
    rolloffHz, powerLow, powerMid, powerHigh,
  };
}
