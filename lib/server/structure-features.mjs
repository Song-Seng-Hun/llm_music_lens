/** Compact observations from the existing 50 Hz frontend; no additional FFT.
 * RMS is the mean squared centered-frame RMS, not sample-bin RMS or LUFS.
 */
export function extractStructureFrames({ features, frameCount, spectralFrames, duration }) {
  if (!Number.isFinite(duration) || duration < 1 || duration > 30 ||
      !Number.isInteger(frameCount) || frameCount !== Math.floor(Math.round(duration * 22050) / 441) + 1 ||
      features?.length !== frameCount * 128 || spectralFrames?.length !== frameCount) {
    throw new RangeError('Structure extraction requires the bounded native beat frontend');
  }
  const round = value => Math.round(value * 10000) / 10000;
  const rows = [];
  let index = 0;
  for (let start = 0; start < duration; start++) {
    const end = Math.min(start + 1, duration), mel = Array(16).fill(0);
    let count = 0, active = 0, energy = 0, low = 0, totalPower = 0;
    while (index < frameCount && spectralFrames[index].time < end) {
      const frame = spectralFrames[index];
      if (!Number.isFinite(frame.rms) || frame.rms < 0 || frame.rms > 1 || Math.abs(frame.time - index / 50) > 1e-9) {
        throw new RangeError('Invalid frontend frame clock or RMS');
      }
      count++; energy += frame.rms ** 2;
      if (frame.rms >= 0.0001) {
        active++;
        low += frame.powerLow;
        totalPower += frame.powerLow + frame.powerMid + frame.powerHigh;
        for (let band = 0; band < 128; band++) {
          const value = features[index * 128 + band];
          if (!Number.isFinite(value) || value < 0) throw new RangeError('Invalid frontend log-mel value');
          mel[Math.floor(band / 8)] += value;
        }
      }
      index++;
    }
    const rms = Math.sqrt(energy / Math.max(1, count)), activeFraction = active / Math.max(1, count);
    const norm = Math.hypot(...mel), available = activeFraction >= 0.5 && rms >= 0.0001 && norm > 0 && totalPower > 0;
    let rmsDbfs = rms > 0 ? round(Math.max(-160, 20 * Math.log10(rms))) : null;
    // Rounding must not promote a below-gate frame into a known spectral bin.
    if (rms > 0 && rms < 0.0001) rmsDbfs = Math.min(-80.0001, rmsDbfs);
    rows.push({ start, end, activeFraction: round(activeFraction),
      rmsDbfs,
      lowShare: available && totalPower > 0 ? round(low / totalPower) : null,
      mel: available ? mel.map(value => Math.round(value / norm * 100000) / 100000) : null });
  }
  return rows;
}
