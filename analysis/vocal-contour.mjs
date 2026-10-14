// Native centered STFT frame times. Null is model-unvoiced, not proven silence.
export const CONTOUR_STEP = 512 / 44100;
export const CONTOUR_LIMIT = 52000;

export function appendVocalContour(chunks, pitch, rest, left, coreStart, coreEnd, duration) {
  if (!pitch || pitch.type !== "float32" || !rest || rest.type !== "bool" || pitch.dims.length !== 2 || rest.dims.length !== 2 || pitch.dims[0] !== 1 || rest.dims[0] !== 1 || pitch.dims[1] !== pitch.data.length || rest.dims[1] !== pitch.data.length || rest.data.length !== pitch.data.length || !pitch.data.length) throw new Error("Invalid SOME pitch contour outputs. Re-run scripts/prepare-vocal-model.py.");
  const midi = [];
  let start = null, retained = chunks.reduce((n, c) => n + c.midi.length, 0), truncated = false;
  for (let i = 0; i < pitch.data.length; i++) {
    const value = pitch.data[i], mask = rest.data[i], sample = Math.round(left * 44100) + i * 512;
    if (!Number.isFinite(value) || value < 0 || value > 127 || ![0, 1, false, true].includes(mask)) throw new Error("Invalid SOME pitch contour value.");
    if (sample < Math.round(coreStart * 44100) || sample >= Math.round(Math.min(coreEnd, duration) * 44100)) continue;
    if (retained >= CONTOUR_LIMIT) { truncated = true; continue; }
    if (start === null) start = sample / 44100;
    midi.push(mask ? null : Math.round(value * 10000) / 10000);
    retained++;
  }
  if (midi.length) chunks.push({ start, midi });
  return truncated;
}
