// Descriptors of the decoded mono mixture, not instrument or emotion labels.
const round = n => Math.round(n * 10000) / 10000;
const db = n => n > 0 ? round(Math.max(-160, 20 * Math.log10(n))) : null;
const quantile = (items, q) => items.length ? items[Math.floor((items.length - 1) * q)] : null;

export function aggregateAcoustics(frames, samples, sampleRate, onsets) {
  const duration = samples.length / sampleRate, windows = [];
  let frameIndex = 0, onsetIndex = 0, totalEnergy = 0, peak = 0, clipped = 0;
  for (let start = 0; start < duration; start++) {
    const end = Math.min(duration, start + 1), a = Math.floor(start * sampleRate), b = Math.min(samples.length, Math.floor(end * sampleRate));
    let energy = 0, localPeak = 0;
    for (let i = a; i < b; i++) {
      const value = Math.abs(samples[i]); energy += value * value; localPeak = Math.max(localPeak, value);
      if (value >= 0.999) clipped++;
    }
    totalEnergy += energy; peak = Math.max(peak, localPeak);
    const rms = Math.sqrt(energy / Math.max(1, b - a));
    let count = 0, active = 0, centroid = 0, flatness = 0, rolloff = 0, low = 0, mid = 0, high = 0;
    while (frameIndex < frames.length && frames[frameIndex].time < end) {
      const f = frames[frameIndex++]; count++;
      if (f.rms < 0.0001 || f.centroidHz === null) continue;
      active++; centroid += f.centroidHz; flatness += f.flatness; rolloff += f.rolloffHz;
      low += f.powerLow; mid += f.powerMid; high += f.powerHigh;
    }
    let hits = 0;
    while (onsetIndex < onsets.length && onsets[onsetIndex].time < end) { onsetIndex++; hits++; }
    // Centered frames can see across a second boundary. A silent bin must not
    // inherit the neighboring bin's tone/noise description from that context.
    if (rms < 0.0001) { active = 0; low = 0; mid = 0; high = 0; }
    const sum = low + mid + high;
    windows.push({ start, end: round(end), rms: round(rms), rmsDbfs: db(rms), peakDbfs: db(localPeak),
      crestDb: rms > 0 && localPeak > 0 ? round(20 * Math.log10(localPeak / rms)) : null,
      activeFraction: round(active / Math.max(1, count)),
      centroidHz: active ? round(centroid / active) : null,
      rolloff85Hz: active ? round(rolloff / active) : null,
      flatness: active ? round(flatness / active) : null,
      bandBalance: sum > 0 ? { low: round(low / sum), mid: round(mid / sum), high: round(high / sum) } : null,
      onsetRate: rms < 0.0001 ? 0 : round(hits / (end - start)) });
  }
  const activeDb = windows.filter(w => w.rms >= 0.0001).map(w => w.rmsDbfs).sort((a, b) => a - b);
  return { method: 'mono-acoustics-v1: 1 s bins; magnitude centroid, 85% power rolloff, power flatness, power-band shares (<180/180-2000/>=2000 Hz); sample RMS/peak; detected-onset count',
    windows,
    summary: { rmsDbfs: db(Math.sqrt(totalEnergy / samples.length)), samplePeakDbfs: db(peak),
      nearFullScaleFraction: round(clipped / samples.length),
      activeWindowDynamicRangeDb: activeDb.length >= 2 ? round(quantile(activeDb, 0.9) - quantile(activeDb, 0.1)) : null },
    warnings: [
      'Decoded 22050 Hz mono measurements: stereo width, phase, frequencies above 11025 Hz and true peak are unavailable.',
      'Spectral descriptors indicate mixture texture, not an instrument, genre, emotion, source separation or artistic quality.',
      'RMS/dynamic range are not LUFS or perceived loudness. Near-full-scale samples suggest possible clipping, not confirmed distortion.',
      'Spectral values average frames above -80 dBFS RMS; silence is null. Onset rate counts detected attacks, not syllables or specific drums.',
    ] };
}
