// Deterministic, self-generated test audio; no external recording or song.
export function demoAudio(): File {
  const rate = 22050, seconds = 16, count = rate * seconds;
  const bytes = new ArrayBuffer(44 + count * 2), v = new DataView(bytes);
  const str = (at: number, s: string) => [...s].forEach((c, i) => v.setUint8(at + i, c.charCodeAt(0)));
  str(0, "RIFF"); v.setUint32(4, bytes.byteLength - 8, true); str(8, "WAVEfmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); str(36, "data"); v.setUint32(40, count * 2, true);
  for (let i = 0; i < count; i++) {
    const t = i / rate, phase = t % 0.5, chordPhase = t % 2;
    const notes = t < 8 ? [261.6256, 329.6276, 391.9954] : [220, 261.6256, 329.6276];
    const chord = notes.reduce((n, hz) => n + Math.sin(2 * Math.PI * hz * t), 0) * Math.min(1, chordPhase * 30) * Math.exp(-chordPhase * 1.2) * 0.1;
    const beat = 0.6 * Math.exp(-phase * 70) * Math.sin(2 * Math.PI * 110 * phase);
    const fade = Math.min(1, t * 100, (seconds - t) * 100);
    v.setInt16(44 + i * 2, Math.max(-32767, Math.min(32767, (chord + beat) * fade * 32767)), true);
  }
  return new File([bytes], "합성 데모 · 120 BPM.wav", { type: "audio/wav" });
}
