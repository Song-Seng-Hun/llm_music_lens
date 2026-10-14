const HOP_SECONDS = 0.01;
const WINDOW_SECONDS = 0.02;
const MAX_EVENTS = 64;
const dbfs = power => power > 0 ? 10 * Math.log10(power) : null;
const roundedDb = value => value === null ? null : Math.round(value * 100) / 100;

// Compensated updates avoid losing a quiet window when loud samples leave it.
function slidingSum() {
  let sum = 0, correction = 0;
  return {
    add(value) {
      const next = sum + value;
      correction += Math.abs(sum) >= Math.abs(value) ? (sum - next) + value : (value - next) + sum;
      sum = next;
    },
    clear() { sum = 0; correction = 0; },
    value() { return Math.max(0, sum + correction); },
  };
}

function activityRegions(power, duration, thresholdDbfs) {
  const releaseThresholdDbfs = thresholdDbfs - 6;
  const enterPower = 10 ** (thresholdDbfs / 10), releasePower = 10 ** (releaseThresholdDbfs / 10);
  const regions = [];
  let total = 0, active = null, aboveStart = -1, aboveCount = 0, belowStart = -1, belowCount = 0;
  const finish = (end, endClipped) => {
    total++;
    if (regions.length < MAX_EVENTS) regions.push({ start: active.start, end, peakTime: active.peakIndex / 100, peakDbfs: roundedDb(dbfs(active.peakPower)), startClipped: active.start === 0, endClipped });
    active = null; aboveCount = 0; aboveStart = -1; belowCount = 0; belowStart = -1;
  };
  for (let frame = 0; frame < power.length; frame++) {
    if (!active) {
      if (power[frame] >= enterPower) {
        if (!aboveCount) aboveStart = frame;
        if (++aboveCount === 2) {
          const peakIndex = power[frame] > power[aboveStart] ? frame : aboveStart;
          active = { start: aboveStart / 100, peakIndex, peakPower: power[peakIndex] };
        }
      } else { aboveCount = 0; aboveStart = -1; }
      continue;
    }
    if (power[frame] > active.peakPower) { active.peakPower = power[frame]; active.peakIndex = frame; }
    if (power[frame] < releasePower) {
      if (!belowCount) belowStart = frame;
      if (++belowCount === 3) finish(belowStart / 100, false);
    } else { belowCount = 0; belowStart = -1; }
  }
  if (active) finish(duration, true);
  return { thresholdDbfs, releaseThresholdDbfs, regions, total, truncated: total > regions.length };
}

function sustainedChanges(power, releaseThresholdDbfs) {
  const items = [];
  let total = 0, previous = null, run = null;
  const finish = () => {
    if (run && run.steps >= 2 && run.end - run.start >= 0.2 - 1e-9 && Math.abs(run.endDb - run.startDb) >= 3) {
      total++;
      if (items.length < MAX_EVENTS) items.push({ kind: run.kind, start: run.start, end: run.end, deltaDb: roundedDb(run.endDb - run.startDb) });
    }
    run = null;
  };
  // Fixed 100 ms groups of the same 10 ms frame powers. Partial groups use
  // their actual count; no zero padding or null-to-floor transitions.
  for (let first = 0; first < power.length; first += 10) {
    const end = Math.min(first + 10, power.length);
    let sum = 0;
    for (let frame = first; frame < end; frame++) sum += power[frame];
    const level = dbfs(sum / (end - first)), time = (first + end - 1) / 200;
    if (level === null || level < releaseThresholdDbfs) { finish(); previous = null; continue; }
    if (previous) {
      const delta = level - previous.level;
      const kind = delta > 0.05 ? 'rise' : delta < -0.05 ? 'fall' : null;
      if (!kind || (run && run.kind !== kind)) finish();
      if (kind) {
        if (!run) run = { kind, start: previous.time, startDb: previous.level, steps: 0 };
        run.end = time; run.endDb = level; run.steps++;
      }
    }
    previous = { level, time };
  }
  finish();
  return { items, total, truncated: total > items.length };
}

/** Raw waveform energy observations, not perceived loudness or source identity. */
export function analyzeWaveformEnvelope(samples, sampleRate, { detail = 'summary' } = {}) {
  if (!(samples instanceof Float32Array) || !samples.length) throw new TypeError('Waveform must be a nonempty Float32Array');
  if (!Number.isInteger(sampleRate) || sampleRate < 1000 || sampleRate > 192000) throw new RangeError('Sample rate must be an integer between 1000 and 192000 Hz');
  if (samples.length > sampleRate * 30) throw new RangeError('Waveform analysis supports at most 30 seconds');
  if (detail !== 'summary' && detail !== 'fine') throw new RangeError('Waveform detail must be summary or fine');
  let totalPower = 0, peak = 0;
  for (const sample of samples) {
    if (!Number.isFinite(sample) || sample < -1 || sample > 1) throw new RangeError('PCM samples must be finite and within [-1, 1]');
    totalPower += sample * sample;
    peak = Math.max(peak, Math.abs(sample));
  }
  const duration = samples.length / sampleRate;
  const power = new Float64Array(Math.ceil(samples.length * 100 / sampleRate));
  const energy = slidingSum();
  let left = 0, right = 0, nonzero = 0, maximumPower = 0;
  for (let frame = 0; frame < power.length; frame++) {
    const begin = Math.max(0, Math.round((frame - 1) * sampleRate / 100));
    const end = Math.min(samples.length, Math.round((frame + 1) * sampleRate / 100));
    while (left < begin) { const value = samples[left++]; energy.add(-value * value); if (value !== 0) nonzero--; }
    while (right < end) { const value = samples[right++]; energy.add(value * value); if (value !== 0) nonzero++; }
    if (!nonzero) energy.clear();
    power[frame] = energy.value() / (end - begin);
    maximumPower = Math.max(maximumPower, power[frame]);
  }
  const thresholdDbfs = Math.max(-60, (dbfs(maximumPower) ?? -Infinity) - 40);
  const activity = activityRegions(power, duration, thresholdDbfs);
  const step = detail === 'fine' ? 1 : 10;
  const rmsDbfs = [];
  for (let frame = 0; frame < power.length; frame += step) rmsDbfs.push(roundedDb(dbfs(power[frame])));
  return {
    analysisWindowSeconds: WINDOW_SECONDS, analysisHopSeconds: HOP_SECONDS,
    curve: { startSeconds: 0, stepSeconds: step / 100, rmsDbfs },
    activity, changes: sustainedChanges(power, activity.releaseThresholdDbfs),
    summary: { rmsDbfs: roundedDb(dbfs(totalPower / samples.length)), peakDbfs: roundedDb(dbfs(peak * peak)) },
    method: {
      id: 'waveform-energy-v1', referenceAmplitude: 1, dcRemoval: false, levelPrecisionDb: 0.01,
      frameDefinition: 'centers k/100 < duration; [max(0,round((k-1)*rate/100)), min(N,round((k+1)*rate/100))); actual sample-count divisor',
      activity: 'enter >= max(-60 dBFS, max frame dBFS - 40 dB) for 2 frames; release < threshold - 6 dB for 3 frames; edges backdated to first qualifying frame',
      changes: '100 ms frame-power groups at mean center; same-direction >0.05 dB steps for >=200 ms and >=3 dB net; no bridging below release threshold',
    },
    warnings: [
      'Raw RMS includes DC offset and measures signal energy, not perceived loudness.',
      'Energy regions and rises/falls do not identify voices, breaths, phonemes, notes or emotions.',
      'A below-threshold signal is not proven silence; only zero RMS is encoded as null.',
      'Activity thresholds depend on the excerpt peak; differently cropped excerpts can produce different regions.',
      '20 ms windows and 10 ms centers specify measurement resolution, not exact boundary accuracy. Clipped region flags mean a boundary was not observed inside the excerpt.',
    ],
  };
}
