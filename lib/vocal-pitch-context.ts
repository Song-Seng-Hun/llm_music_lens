type PitchEstimate = {
  start: number; stepSeconds: number; duration: number;
  hz: (number | null)[]; periodicity: (number | null)[];
  method: Record<string, unknown>; warnings: string[];
};
const names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
const rounded = (value: number, places: number) => Number(value.toFixed(places));
const midiOf = (hz: number) => 69 + 12 * Math.log2(hz / 440);
const nameOf = (midi: number) => names[((midi % 12) + 12) % 12] + (Math.floor(midi / 12) - 1);

/** Describe continuous periodic pitch; do not turn frame quantization into note events. */
export function vocalPitchContext(estimate?: PitchEstimate) {
  if (!estimate) return { status: 'unavailable' as const,
    reason: 'Vocal pitch requires sourceKind=isolated-vocal. The server does not separate or identify a singer in a mixture.' };
  const frequencies = estimate.hz.filter((hz): hz is number => hz !== null);
  const counts = new Map<number, number>();
  for (const hz of frequencies) {
    const midi = Math.round(midiOf(hz));
    counts.set(midi, (counts.get(midi) ?? 0) + 1);
  }
  const lowest = frequencies.length ? Math.min(...frequencies) : null;
  const highest = frequencies.length ? Math.max(...frequencies) : null;
  return {
    status: 'estimated' as const,
    sourceKind: 'isolated-vocal',
    sourceAttribution: 'Caller-declared isolated vocal; not verified or separated by this tool. Other periodic sounds can also produce pitch estimates.',
    clock: 'excerpt-relative-seconds; frame time = contour.start + index * contour.stepSeconds; add source.offsetSeconds for song time',
    contour: {
      start: estimate.start, stepSeconds: estimate.stepSeconds, duration: estimate.duration,
      hz: estimate.hz.map(hz => hz === null ? null : rounded(hz, 2)),
      midi: estimate.hz.map(hz => hz === null ? null : rounded(midiOf(hz), 4)),
      periodicity: estimate.periodicity.map(value => value === null ? null : rounded(value, 4)),
      total: estimate.hz.length, truncated: false,
    },
    noteConvention: { referenceHz: 440, referenceMidi: 69, pitchClasses: names,
      nearestNote: 'Round fractional MIDI to n; pitchClasses[n mod 12] and octave=floor(n/12)-1. C4=MIDI60. These names do not establish a key or segmented notes.' },
    summary: {
      estimatedFrames: frequencies.length, totalFrames: estimate.hz.length,
      estimatedFraction: rounded(frequencies.length / estimate.hz.length, 4),
      range: lowest === null || highest === null ? null : {
        minHz: rounded(lowest, 2), maxHz: rounded(highest, 2),
        lowestNearestNote: nameOf(Math.round(midiOf(lowest))), highestNearestNote: nameOf(Math.round(midiOf(highest))),
      },
      mostObservedNotes: [...counts].sort((a, b) => b[1] - a[1] || a[0] - b[0]).slice(0, 12)
        .map(([midi, frames]) => ({ note: nameOf(midi), midi, frames })),
    },
    method: estimate.method,
    warnings: [...estimate.warnings,
      'Null means insufficient supported periodic-pitch evidence, not verified silence or absence of a voice. Periodicity is uncalibrated, not a voicing probability.',
      'Twenty-millisecond spacing is resolution, not proven timing accuracy. Octave errors, breathy/noisy voices and overlapping sounds remain possible.',
      'Fractional MIDI preserves estimated movement. Nearest-note counts are frame observations, not a score, chord, key, vibrato classification or lyric-to-note alignment. Supplied note/lyric boundaries are unchanged.',
    ],
  };
}
