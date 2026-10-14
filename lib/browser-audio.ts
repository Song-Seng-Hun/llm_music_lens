import { MAX_DURATION, MAX_FILE_BYTES, signalSchema, type SignalResult } from "./report";

export async function decodeFile(file: File, rate = 16000) {
  if (file.size > MAX_FILE_BYTES || !file.size) throw new Error("음원은 0보다 크고 12 MiB 이하여야 합니다.");
  const bytes = await file.arrayBuffer();
  const sha256 = await fingerprint(bytes);
  // The solo model consumes native 44.1 kHz. Avoid a device-rate round trip
  // (often 48 kHz) before inference; existing quick/polyphonic paths stay intact.
  const context = rate === 44100 ? new AudioContext({ sampleRate: 44100 }) : new AudioContext();
  let decoded: AudioBuffer;
  try { decoded = await context.decodeAudioData(bytes); }
  catch { throw new Error("이 브라우저에서 음원을 읽지 못했습니다. MP3 또는 WAV 파일을 사용해 주세요."); }
  finally { await context.close(); }
  if (decoded.duration <= 0 || decoded.duration > MAX_DURATION) throw new Error("로컬 분석은 10분 이하의 음원을 지원합니다.");
  if (rate === 44100 && decoded.sampleRate === rate && decoded.numberOfChannels === 1) return { samples: decoded.getChannelData(0).slice(), sampleRate: rate, duration: decoded.duration, sha256 };
  const offline = new OfflineAudioContext(1, Math.ceil(decoded.duration * rate), rate);
  const source = offline.createBufferSource(); source.buffer = decoded;
  source.connect(offline.destination); source.start();
  const mono = await offline.startRendering();
  return { samples: mono.getChannelData(0).slice(), sampleRate: rate, duration: decoded.duration, sha256 };
}

export function runSignalWorker(samples: Float32Array, sampleRate: number, progress: (n: number) => void) {
  const worker = new Worker("/audio-worker.mjs", { type: "module" });
  let rejectPending: (reason: Error) => void = () => {};
  const promise = new Promise<SignalResult>((resolve, reject) => {
    rejectPending = reject;
    worker.onmessage = ({ data }) => {
      if (data.type === "progress") progress(data.progress);
      if (data.type === "result") { worker.terminate(); const parsed = signalSchema.safeParse(data.result); if (!parsed.success) reject(new Error("신호 분석 결과 형식을 확인하지 못했습니다.")); else resolve(parsed.data); }
      if (data.type === "error") { worker.terminate(); reject(new Error(data.error || "신호 분석에 실패했습니다.")); }
    };
    worker.onerror = () => { worker.terminate(); reject(new Error("분석 작업자를 실행하지 못했습니다.")); };
    worker.postMessage({ id: crypto.randomUUID(), samples, sampleRate }, [samples.buffer]);
  });
  return { promise, cancel: () => { worker.terminate(); rejectPending(new DOMException("분석을 취소했습니다.", "AbortError")); } };
}

export async function fingerprint(bytes: ArrayBuffer) {
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(hash), n => n.toString(16).padStart(2, "0")).join("");
}
export function audioMime(file: File) {
  const extension = file.name.split(".").pop()?.toLowerCase();
  const types: Record<string, string> = { mp3: "audio/mpeg", wav: "audio/wav", flac: "audio/flac", ogg: "audio/ogg", m4a: "audio/mp4", aac: "audio/aac", webm: "audio/webm" };
  if (!types[extension ?? ""]) throw new Error("MP3, WAV, FLAC, OGG, M4A, AAC, WEBM 파일을 사용해 주세요.");
  return types[extension!];
}
export function downloadText(text: string, filename: string, type: string) {
  const url = URL.createObjectURL(new Blob([text], { type })); const a = document.createElement("a"); a.href = url; a.download = filename; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
