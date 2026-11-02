import * as ort from "onnxruntime-web/wasm";
import { appendVocalContour, CONTOUR_STEP } from "./vocal-contour.mjs";
import { VOCAL_MODEL, VOCAL_MODEL_SHA256, VOCAL_RATE, VOCAL_CORE_SECONDS, VOCAL_CONTEXT_SECONDS, decodeVocalSequence, appendVocalWindow } from "./vocal-notes.mjs";

export async function vocalNotesFromAudio(samples, progress) {
  const result = { status: "complete", mode: "solo-vocal", model: VOCAL_MODEL, items: [], truncated: false, warnings: [
    "반주 없는 단일 보컬용 음표 추정입니다. 음원 분리 기능이 아니며, 혼합곡·화음 보컬에 대한 정확도는 검증하지 않았습니다.",
    "midi는 소수 음높이를 보존합니다. confidence=null: 이 모델은 신뢰도 값을 제공하지 않습니다.",
    "연속 곡선은 약 11.61ms 간격의 음높이 추정입니다. null은 모델의 무성 추정이며 실제 무음이나 정확도 보증이 아닙니다. 비브라토·슬라이드 판정은 미검증입니다.",
  ] };
  ort.env.wasm.numThreads = 1;
  ort.env.wasm.proxy = false;
  ort.env.wasm.wasmPaths = new URL("/models/ort/", self.location.origin).href;
  progress("notes", 0.28, "보컬 음표 모델 준비 중 · 약 450 MiB");
  const session = await ort.InferenceSession.create(new URL(`/models/some/model.onnx?sha=${VOCAL_MODEL_SHA256}`, self.location.origin).href, { executionProviders: ["wasm"] });
  const duration = samples.length / VOCAL_RATE, core = VOCAL_CORE_SECONDS * VOCAL_RATE, context = VOCAL_CONTEXT_SECONDS * VOCAL_RATE;
  result.contour = { status: "complete", method: "SOME native frame MIDI/rest; centered STFT; no smoothing", duration, stepSeconds: CONTOUR_STEP, chunks: [], truncated: false };
  let omitted = 0, preserved = 0;
  try {
    for (let offset = 0; offset < samples.length; offset += core) {
      const left = Math.max(0, offset - context), right = Math.min(samples.length, offset + core + context);
      progress("notes", 0.28 + 0.38 * offset / samples.length, `보컬 음표 ${Math.floor(offset / VOCAL_RATE)}초 / ${Math.ceil(duration)}초`);
      const tensor = new ort.Tensor("float32", samples.subarray(left, right), [1, right - left]);
      let output;
      try {
        output = await session.run({ waveform: tensor });
        result.contour.truncated = appendVocalContour(result.contour.chunks, output["/Div_output_0"], output["/Less_1_output_0"], left / VOCAL_RATE, offset / VOCAL_RATE, Math.min(offset + core, samples.length) / VOCAL_RATE, duration) || result.contour.truncated;
        const decoded = decodeVocalSequence(Object.fromEntries(Object.entries(output).map(([key, value]) => [key, value.data])), (right - left) / VOCAL_RATE, { rest: output["/Less_1_output_0"], mapping: output["/Mul_2_output_0"] });
        omitted += decoded.omitted;
        preserved += decoded.preserved;
        result.truncated = appendVocalWindow(result.items, decoded.events, left / VOCAL_RATE, offset / VOCAL_RATE, Math.min(offset + core, samples.length) / VOCAL_RATE, duration) || result.truncated;
      } finally { tensor.dispose(); if (output) for (const value of Object.values(output)) value.dispose(); }
    }
  } finally { await session.release(); }
  if (samples.length > core) result.warnings.push("40초 중심 구간과 양쪽 2초 문맥으로 처리했습니다. 긴 곡의 구간 경계 정확도는 별도 검증하지 않았습니다.");
  if (result.contour.truncated) { result.contour.status = "partial"; result.status = "partial"; result.warnings.push("연속 음높이 52,000프레임 한도로 일부를 생략했습니다."); }
  if (preserved) result.warnings.push(`끝부분의 보정 근거가 오디오 밖에 있는 후보 ${preserved}개는 원래 음표 구간을 유지했습니다.`);
  if (omitted) { result.status = "partial"; result.warnings.push(`지원 음역 MIDI 21–108 밖의 후보 ${omitted}개를 생략했습니다(문맥 중복 포함).`); }
  if (result.truncated) { result.status = "partial"; result.warnings.push("음표 4,000개 한도로 일부 이벤트가 생략되었습니다."); }
  return result;
}
