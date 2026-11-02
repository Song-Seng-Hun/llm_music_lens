const MAX_TIME = 600;
const EVENT_CAP = 2000;
const NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
const round = value => Math.round(value * 10000) / 10000;
const clamp = (value, lo = 0, hi = 1) => Math.max(lo, Math.min(hi, value));
const finite = value => typeof value === 'number' && Number.isFinite(value);

function lowerBound(items, time) {
  let lo = 0, hi = items.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (items[mid].time < time) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

function upperBound(items, time) {
  let lo = 0, hi = items.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (items[mid].time <= time) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

function cleanTime(value, duration) {
  return finite(value) ? round(clamp(value, 0, duration)) : null;
}

function validOnsets(rhythm, duration) {
  return (Array.isArray(rhythm?.onsets) ? rhythm.onsets : [])
    .filter(x => finite(x?.time) && finite(x?.strength) && ['low', 'mid', 'high'].includes(x.band))
    .map(x => ({ time: cleanTime(x.time, duration), strength: clamp(x.strength, 0, 1), band: x.band }))
    .filter(x => x.time !== null && x.time < duration)
    .sort((a, b) => a.time - b.time);
}

function deriveBeats(rhythm, onsets, duration) {
  const beats = (Array.isArray(rhythm?.beats) ? rhythm.beats : [])
    .filter(finite).map(t => cleanTime(t, duration)).filter(t => t !== null && t < duration).sort((a, b) => a - b);
  const bpm = rhythm?.bpm;
  if (rhythm?.tempoEvidence && rhythm.tempoEvidence.status !== 'supported') return { beats: [], period: null, origin: 0 };
  const score = rhythm?.tempoCandidates?.find(c => finite(c?.bpm) && finite(c?.score))?.score ?? 0;
  if (!finite(bpm) || bpm < 40 || bpm > 240 || score < 0.12 || beats.length < 4) return { beats: [], period: null, origin: 0 };
  const period = 60 / bpm;
  const intervals = beats.slice(1).map((t, i) => t - beats[i]);
  // A supported grid can intentionally omit silent intervals. Preserve valid
  // regions without synthesizing beats in the omitted gap.
  const regular = intervals.every(x => Math.round(x / period) >= 1 && Math.abs(x - Math.round(x / period) * period) <= period * 0.08);
  return regular ? { beats, period, origin: beats[0] } : { beats: [], period: null, origin: 0 };
}

function rhythmEvents(rhythm, duration) {
  const onsets = validOnsets(rhythm, duration);
  const grid = deriveBeats(rhythm, onsets, duration);
  const strong = [], offbeats = [], percussion = onsets.map(x => ({
    time: x.time, strength: round(x.strength), kind: `${x.band}-hit`,
  }));
  if (grid.period) {
    let onsetIndex = 0;
    for (const beat of grid.beats) {
      while (onsetIndex < onsets.length && onsets[onsetIndex].time < beat - grid.period * 0.18) onsetIndex++;
      let best = null;
      for (let i = onsetIndex; i < onsets.length && onsets[i].time <= beat + grid.period * 0.18; i++) {
        if (!best || onsets[i].strength > best.strength) best = onsets[i];
      }
      if (best && Math.abs(best.time - beat) <= Math.max(0.06, grid.period * 0.14) && best.strength >= 0.12) {
        strong.push({ time: round(beat), strength: round(best.strength) });
      }
    }
    for (const onset of onsets) {
      const phase = ((onset.time - grid.origin) / grid.period % 1 + 1) % 1;
      if (phase >= 0.25 && phase <= 0.75 && onset.strength >= 0.2) {
        offbeats.push({ time: round(onset.time), strength: round(onset.strength), beatPhase: round(phase) });
      }
    }
  }
  return { strong, offbeats, percussion };
}

function deriveDynamics(rhythm, duration) {
  const raw = (Array.isArray(rhythm?.envelope) ? rhythm.envelope : [])
    .filter(x => finite(x?.time) && finite(x?.rms) && x.rms >= 0)
    .map(x => ({ time: cleanTime(x.time, duration), rms: x.rms, low: finite(x.low) && x.low >= 0 ? x.low : x.rms }))
    .filter(x => x.time !== null && x.time < duration).sort((a, b) => a.time - b.time);
  // Two-second power averages separate structural energy from individual hit
  // decay. Partial final bins are excluded rather than inventing a final drop.
  const envelope = [];
  let index = 0;
  for (let start = 0; start + 2 <= duration; start += 2) {
    let power = 0, low = 0, count = 0;
    while (index < raw.length && raw[index].time < start + 2) {
      power += raw[index].rms ** 2; low += raw[index].low; count++; index++;
    }
    if (count) envelope.push({ time: start, rms: Math.sqrt(power / count), low: low / count });
  }
  if (envelope.length < 5 || envelope.every(x => x.rms <= 1e-5)) return [];
  const sorted = envelope.map(x => x.rms).sort((a, b) => a - b), median = sorted[Math.floor(sorted.length / 2)] || 0;
  if (sorted.at(-1) - sorted[0] <= Math.max(0.002, median * 0.08)) return [];
  const events = [];
  const trend = (a, b, direction) => {
    const values = envelope.slice(a, b + 1);
    if (values.length < 3) return false;
    const tolerance = Math.max(0.0001, median * 0.01);
    let directional = 0;
    for (let k = 1; k < values.length; k++) {
      const delta = values[k].rms - values[k - 1].rms;
      if (direction * delta > tolerance) directional++;
      if (direction * delta < -tolerance) return false;
    }
    return directional / (values.length - 1) >= 0.7;
  };
  // A build-up needs repeated intermediate rises, not just different endpoints.
  for (let i = 0; i < envelope.length; i++) {
    const start = envelope[i];
    let j = i + 1;
    while (j < envelope.length && envelope[j].time - start.time < 4) j++;
    if (j >= envelope.length || envelope[j].time - start.time < 3) continue;
    const end = envelope[j];
    const rise = end.rms - start.rms;
    const requiredRise = Math.max(0.012, median * 0.28);
    if (rise >= requiredRise && trend(i, j, 1)) {
      const score = clamp(rise / Math.max(0.04, median + 0.05), 0, 1);
      events.push({ start: round(start.time), end: round(end.time), kind: 'build-up', score: round(score) });
    }
  }
  const onsets = validOnsets(rhythm, duration);
  // A drop candidate requires a return after contrast: higher RMS and low-band
  // energy plus a denser attack window. Decreases never receive the drop label.
  for (let i = 1; i < envelope.length; i++) {
    const at = envelope[i].time;
    const before = envelope.filter(x => x.time >= at - 2 && x.time < at);
    const after = envelope.filter(x => x.time >= at && x.time < at + 2);
    if (!before.length || !after.length) continue;
    const mean = (arr, key) => arr.reduce((sum, x) => sum + x[key], 0) / arr.length;
    const beforeRms = mean(before, 'rms'), afterRms = mean(after, 'rms');
    const beforeLow = mean(before, 'low'), afterLow = mean(after, 'low');
    if (beforeRms > Math.max(0.05, median * 0.72) || afterRms < Math.max(beforeRms * 1.6, beforeRms + 0.035)) continue;
    if (afterLow < Math.max(beforeLow * 1.5, beforeLow + 0.02)) continue;
    const risePoint = after.find(x => x.rms >= Math.max(beforeRms * 1.6, beforeRms + 0.035)
      && x.low >= Math.max(beforeLow * 1.5, beforeLow + 0.02));
    if (!risePoint) continue;
    const candidateTime = risePoint.time;
    const beforeHits = lowerBound(onsets, candidateTime) - lowerBound(onsets, candidateTime - 1.5);
    const afterHits = upperBound(onsets, candidateTime + 1.5) - lowerBound(onsets, candidateTime);
    if (afterHits < 3 || afterHits < beforeHits + 2) continue;
    const score = clamp(Math.min((afterRms - beforeRms) / Math.max(0.05, beforeRms),
      (afterLow - beforeLow) / Math.max(0.05, beforeLow), (afterHits - beforeHits) / 6));
    events.push({ start: round(candidateTime), end: round(Math.min(duration, after.at(-1).time + 2)), kind: 'drop', score: round(score) });
  }
  // Rapid and sustained decreases are energy-fall candidates, never drops.
  for (let i = 1; i < envelope.length; i++) {
    const before = envelope[i - 1], after = envelope[i], fall = before.rms - after.rms;
    if (fall > Math.max(0.025, before.rms * 0.42)) {
      events.push({ start: round(before.time), end: round(after.time), kind: 'energy-fall', score: round(clamp(fall / Math.max(0.05, before.rms))) });
    }
  }
  for (let i = 0; i < envelope.length; i++) {
    let j = i + 1;
    while (j < envelope.length && envelope[j].time - envelope[i].time < 6) j++;
    if (j >= envelope.length || envelope[j].time - envelope[i].time < 4) continue;
    const a = envelope[i], b = envelope[j], fall = a.rms - b.rms;
    if (fall >= Math.max(0.016, median * 0.3) && a.rms > Math.max(0.02, median * 0.65) && trend(i, j, -1)) {
      events.push({ start: round(a.time), end: round(b.time), kind: 'energy-fall', score: round(clamp(fall / Math.max(0.05, a.rms))) });
    }
  }
  events.sort((a, b) => a.start - b.start || a.end - b.end);
  const merged = [];
  for (const event of events) {
    const previous = merged.at(-1);
    if (previous?.kind === event.kind && event.start <= previous.end) {
      previous.end = Math.max(previous.end, event.end);
      previous.score = Math.max(previous.score, event.score);
    } else merged.push({ ...event });
  }
  return merged.filter(event => event.end > event.start);
}

function hangulSyllables(text) {
  const lexical = text.normalize('NFC').replace(/[\s\p{P}\p{S}\p{N}]/gu, '');
  if (!lexical || !/^[가-힣]+$/u.test(lexical)) return null;
  return [...lexical].length;
}

function deriveVocalDensity(lyrics, duration) {
  if (!lyrics || !['complete', 'partial'].includes(lyrics.status) || !Array.isArray(lyrics.words)) return [];
  const bins = new Map();
  for (const word of lyrics.words) {
    if (!finite(word?.start) || !finite(word?.end) || word.end <= word.start || typeof word.text !== 'string' || !word.text.trim()) continue;
    const start = cleanTime(word.start, duration), end = cleanTime(word.end, duration);
    if (start === null || end === null || start >= duration || end <= start) continue;
    const index = Math.floor(start / 5), bin = bins.get(index) ?? { count: 0, syllables: 0, hangulOnly: true };
    bin.count++;
    const syllables = hangulSyllables(word.text);
    if (syllables === null) bin.hangulOnly = false;
    else bin.syllables += syllables;
    bins.set(index, bin);
  }
  return [...bins.entries()].sort((a, b) => a[0] - b[0]).map(([index, bin]) => {
    const start = index * 5, end = Math.min(duration, start + 5), seconds = end - start;
    return { start: round(start), end: round(end), wordsPerSecond: round(bin.count / seconds),
      syllableProxyPerSecond: bin.hangulOnly ? round(bin.syllables / seconds) : null, source: 'transcript-proxy' };
  });
}

function derivePitch(notes, duration) {
  const items = (Array.isArray(notes?.items) ? notes.items : []).filter(x => finite(x?.midi) && x.midi >= 21 && x.midi <= 108 && finite(x?.start) && finite(x?.end) && x.end > x.start && x.start < duration && x.end > 0);
  if (!items.length) return { lowestMidi: null, highestMidi: null, noteCount: 0, pitchClasses: [] };
  const counts = Array(12).fill(0);
  for (const note of items) counts[(Math.round(note.midi) % 12 + 12) % 12]++;
  return { lowestMidi: Math.min(...items.map(x => x.midi)), highestMidi: Math.max(...items.map(x => x.midi)), noteCount: items.length,
    pitchClasses: counts.map((count, index) => ({ name: NOTE_NAMES[index], share: round(count / items.length) })).filter(x => x.share > 0) };
}

export function deriveMusicalFeatures(detail, duration) {
  const boundedDuration = finite(duration) && duration > 0 ? clamp(duration, 0, MAX_TIME) : 0;
  const events = rhythmEvents(detail?.rhythm ?? {}, boundedDuration);
  const dynamics = deriveDynamics(detail?.rhythm ?? {}, boundedDuration);
  const vocalDensity = deriveVocalDensity(detail?.lyrics, boundedDuration);
  const notes = detail?.notes?.status === 'failed' || detail?.notes?.status === 'skipped' ? [] : (detail?.notes ?? {});
  const warnings = [
    'Strong-beat and offbeat entries are timing candidates; they do not establish meter, downbeats or musical intent.',
    'Low, mid and high hits are spectral-band attacks, not identified drums or instruments.',
    'Dynamics are envelope trend and attack-return candidates; no verse, chorus, musical drop intent or rap classification is inferred.',
    'Vocal density comes from transcript timing; Hangul syllable rate is a text proxy, not measured pronunciation timing.',
    'Pitch-class shares summarize note-event candidates and do not infer a scale or key.',
  ];
  if (!vocalDensity.length) warnings.push('Vocal density is unavailable because transcript evidence is absent or its stage did not complete.');
  const limit = (array, cap = EVENT_CAP) => array.slice(0, cap);
  const output = {
    method: 'derived-features-v2: supported pulse-grid onset proximity, spectral-band onset labels, 2 s RMS-power envelope trends, transcript timing bins and observed note-event pitch classes',
    strongBeats: limit(events.strong), offbeats: limit(events.offbeats), percussion: limit(events.percussion),
    dynamics: limit(dynamics, 80), vocalDensity: limit(vocalDensity), pitchSummary: derivePitch(notes, boundedDuration), warnings,
    truncated: {
      strongBeats: events.strong.length > EVENT_CAP, offbeats: events.offbeats.length > EVENT_CAP,
      percussion: events.percussion.length > EVENT_CAP, dynamics: dynamics.length > 80,
      vocalDensity: vocalDensity.length > EVENT_CAP,
    },
  };
  return output;
}
