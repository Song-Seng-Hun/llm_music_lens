import { detailSchema, type DetailResult } from "./detail.ts";
import type { SignalResult } from "./report.ts";

export type DetailOptions = { lyrics: "off" | "base" | "small"; language: string; notes?: "polyphonic" | "solo-vocal" };
export async function resampleSamples(samples: Float32Array, sourceRate: number, targetRate: number) {
  const offline = new OfflineAudioContext(1, Math.ceil(samples.length * targetRate / sourceRate), targetRate);
  const buffer = offline.createBuffer(1, samples.length, sourceRate);
  buffer.copyToChannel(new Float32Array(samples), 0);
  const source = offline.createBufferSource(); source.buffer = buffer; source.connect(offline.destination); source.start();
  return (await offline.startRendering()).getChannelData(0).slice();
}
export function speechSamples(samples: Float32Array, sourceRate = 22050) {
  return resampleSamples(samples, sourceRate, 16000);
}
export function detailSignal(samples: Float32Array, detail: DetailResult): SignalResult {
  const r = detail.rhythm, duration = samples.length / 22050;
  const waveform = Array.from({ length: 600 }, (_, i) => {
    let peak = 0;
    for (let j = Math.floor(i * samples.length / 600); j < Math.floor((i + 1) * samples.length / 600); j++) peak = Math.max(peak, Math.abs(samples[j]));
    return Math.round(peak * 10000) / 10000;
  });
  return { duration, waveform, energy: r.envelope.map(({ time, rms }) => ({ time, rms })),
    tempo: { bpm: r.bpm, candidates: r.tempoCandidates, beats: r.beats, segments: r.tempoEvidence?.segments.map(({ start, end, bpm }) => ({ start, end, bpm })) ?? [], status: r.tempoEvidence ? `${r.tempoEvidence.status}; estimated pulse, not verified meter` : "spectral beat candidates; half/double ambiguity; not verified downbeats" },
    pitch: [], key: { label: detail.harmony ? detail.harmony.key.label : r.key.label, status: detail.harmony ? `${detail.harmony.key.status}; major/minor profile estimate, unvalidated` : "legacy chroma estimate; unvalidated" }, chords: r.chords,
    warnings: r.warnings.slice(0, 20), method: r.method };
}
export function runDetailWorker(samples: Float32Array, samples16k: Float32Array | null, options: DetailOptions, onProgress: (value: number, message: string) => void, samples44k: Float32Array | null = null) {
  const worker = new Worker("/precision-worker.mjs", { type: "module" });
  let rejectPending: (error: Error) => void = () => {};
  const promise = new Promise<DetailResult>((resolve, reject) => {
    rejectPending = reject;
    worker.onmessage = ({ data }) => {
      if (data.type === "progress") onProgress(data.value, data.message || `${data.stage} 분석 중`);
      if (data.type === "result") {
        worker.terminate();
        const parsed = detailSchema.safeParse(data.detail);
        if (parsed.success) resolve(parsed.data);
        else reject(new Error(`정밀 분석 결과 형식을 확인하지 못했습니다: ${parsed.error.issues[0]?.path.join(".")}`));
      }
      if (data.type === "error") { worker.terminate(); reject(new Error(data.error)); }
    };
    worker.onerror = () => { worker.terminate(); reject(new Error("정밀 분석 작업자를 실행하지 못했습니다. 준비 스크립트와 브라우저 WASM 지원을 확인하세요.")); };
    const copy = samples.slice();
    worker.postMessage({ samples: copy, samples16k, samples44k, lyrics: options.lyrics, language: options.language, noteMode: options.notes ?? "polyphonic" }, [copy.buffer, ...(samples16k ? [samples16k.buffer] : []), ...(samples44k ? [samples44k.buffer] : [])]);
  });
  return { promise, cancel: () => { worker.terminate(); rejectPending(new DOMException("정밀 분석을 취소했습니다.", "AbortError")); } };
}
