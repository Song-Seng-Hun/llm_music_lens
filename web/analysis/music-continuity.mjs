const PITCH_CAP = 1000, EVENT_CAP = 256;
const orderedObject = value => Object.fromEntries(Object.keys(value).sort().map(key => [key, value[key]]));
const canonical = value => JSON.stringify(value, (_, item) =>
  item !== null && typeof item === 'object' && !Array.isArray(item) ? orderedObject(item) : item);
const compareText = (a, b) => a === b ? 0 : a < b ? -1 : 1;

function canonicalSources(segments) {
  const identities = new Map();
  let duplicatesRemoved = 0;
  for (const segment of segments) {
    const { source, timing, pitch } = segment;
    const key = canonical({ source, provenance: timing.provenance,
      pitchRecipe: pitch.status === 'estimated' ? pitch.method.id : 'unavailable' });
    const evidence = canonical({ beats: timing.beats, downbeats: timing.downbeats, pitch });
    const previous = identities.get(key);
    if (previous) {
      if (previous.evidence !== evidence) throw new RangeError('Conflicting observations for the same source, placement and recipe');
      duplicatesRemoved++;
    } else identities.set(key, { key, evidence, segment });
  }
  const records = [...identities.values()].sort((a, b) => {
    const left = a.segment.source, right = b.segment.source;
    return left.offsetSeconds - right.offsetSeconds || left.duration - right.duration ||
      compareText(left.sha256, right.sha256) || compareText(a.key, b.key);
  });
  const sources = records.map(({ segment: { source, timing, pitch } }) => ({
    source: orderedObject(source), provenance: orderedObject(timing.provenance),
    pitch: pitch.status === 'estimated' ? { status: pitch.status, methodId: pitch.method.id } : { status: pitch.status, reason: pitch.reason },
  }));
  return { records, sources, duplicatesRemoved };
}

function lowerBound(length, time, boundary) {
  let left = 0, right = length;
  while (left < right) {
    const middle = Math.floor((left + right) / 2);
    if (time(middle) < boundary) left = middle + 1; else right = middle;
  }
  return left;
}

function queryStreams(records, start, end) {
  const streams = [], totals = { beats: 0, downbeats: 0, pitchFrames: 0 };
  function append(kind, sourceIndex, length, time, values) {
    const from = lowerBound(length, time, start), to = lowerBound(length, time, end);
    totals[kind] += to - from;
    if (from < to) streams.push({ kind, sourceIndex, from, to, cursor: from, time, values });
  }
  records.forEach(({ segment: { source, timing, pitch } }, sourceIndex) => {
    for (const kind of ['beats', 'downbeats']) {
      const values = timing[kind];
      append(kind, sourceIndex, values.length, index => source.offsetSeconds + values[index], values);
    }
    if (pitch.status === 'estimated') {
      const contour = pitch.contour;
      append('pitchFrames', sourceIndex, contour.hz.length,
        index => source.offsetSeconds + (contour.start + index * contour.stepSeconds), contour);
    }
  });
  return { streams, totals };
}

function pageStreams(streams, requestedEnd) {
  const returned = { beats: 0, downbeats: 0, pitchFrames: 0 };
  let end = requestedEnd;
  while (true) {
    let time = Infinity;
    const group = [];
    for (const stream of streams) {
      if (stream.cursor >= stream.to) continue;
      const next = stream.time(stream.cursor);
      if (next < time) { time = next; group.length = 0; }
      if (next === time) group.push(stream);
    }
    if (!group.length) break;
    let pitchCount = 0, eventCount = 0;
    const advances = group.map(stream => {
      let to = stream.cursor + 1;
      while (to < stream.to && stream.time(to) === time) to++;
      if (stream.kind === 'pitchFrames') pitchCount += to - stream.cursor;
      else eventCount += to - stream.cursor;
      return { stream, to };
    });
    if (pitchCount > PITCH_CAP || eventCount > EVENT_CAP) throw new RangeError('Simultaneous observations exceed indivisible page capacity');
    if (returned.pitchFrames + pitchCount > PITCH_CAP || returned.beats + returned.downbeats + eventCount > EVENT_CAP) {
      end = time; break;
    }
    for (const { stream, to } of advances) { returned[stream.kind] += to - stream.cursor; stream.cursor = to; }
  }
  return { end, returned };
}

function pageCoverage(sources, start, end) {
  const endpoints = new Set([start, end]);
  for (const { source } of sources) {
    const left = Math.max(start, source.offsetSeconds), right = Math.min(end, source.offsetSeconds + source.duration);
    if (left < right) { endpoints.add(left); endpoints.add(right); }
  }
  const cuts = [...endpoints].sort((a, b) => a - b), intervals = [], gaps = [];
  for (let i = 1; i < cuts.length; i++) {
    const left = cuts[i - 1], right = cuts[i], sourceIndices = [];
    sources.forEach(({ source }, sourceIndex) => {
      if (source.offsetSeconds <= left && source.offsetSeconds + source.duration >= right) sourceIndices.push(sourceIndex);
    });
    if (!sourceIndices.length) {
      if (gaps.at(-1)?.end === left) gaps.at(-1).end = right;
      else gaps.push({ start: left, end: right });
    } else {
      const previous = intervals.at(-1);
      if (previous?.end === left && previous.sourceIndices.length === sourceIndices.length && previous.sourceIndices.every((value, j) => value === sourceIndices[j])) previous.end = right;
      else intervals.push({ start: left, end: right, sourceIndices });
    }
  }
  return { intervals, gaps, overlaps: intervals.filter(interval => interval.sourceIndices.length > 1) };
}

/** Lossless composition of validated projections; no model, state or IO.
 * Caller hashes/recipe claims identify views, not authenticated song evidence.
 */
export function composeContinuity({ start, end, segments }) {
  const { records, sources, duplicatesRemoved } = canonicalSources(segments);
  const { streams, totals } = queryStreams(records, start, end);
  const page = pageStreams(streams, end), beats = [], downbeats = [], pitchSlices = [];
  for (const stream of streams) {
    const { sourceIndex, from, cursor, values } = stream;
    if (from === cursor) continue;
    if (stream.kind === 'pitchFrames') {
      pitchSlices.push({ sourceIndex, firstFrameIndex: from, start: values.start, stepSeconds: values.stepSeconds,
        hz: values.hz.slice(from, cursor), midi: values.midi.slice(from, cursor), periodicity: values.periodicity.slice(from, cursor) });
    } else {
      const rows = stream.kind === 'beats' ? beats : downbeats;
      for (let index = from; index < cursor; index++) rows.push({ sourceIndex, index, localTime: values[index], time: stream.time(index) });
    }
  }
  const eventOrder = (a, b) => a.time - b.time || a.sourceIndex - b.sourceIndex || a.index - b.index;
  beats.sort(eventOrder); downbeats.sort(eventOrder);
  const truncated = page.end < end;
  return { schema: 'music-continuity/1',
    window: { start, end: page.end, requestedEnd: end, nextStart: truncated ? page.end : null, truncated },
    sources, duplicatesRemoved, beats, downbeats, pitchSlices, totals, returned: page.returned,
    coverage: pageCoverage(sources, start, page.end) };
}
