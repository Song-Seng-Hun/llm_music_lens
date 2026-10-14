import * as tf from "@tensorflow/tfjs";
import { setWasmPaths, setThreadsCount } from "@tensorflow/tfjs-backend-wasm";
import { BasicPitch, noteFramesToTime, outputToNotesPoly } from "@spotify/basic-pitch";
import { pipeline, env } from "@huggingface/transformers";
import { analyzeRhythm } from "/precision-rhythm.mjs";
import { NOTE_MODEL, LYRIC_MODELS, LANGUAGES } from "./models.mjs";
import { appendWindowNotes, noteWindowAudio } from "./note-windows.mjs";
import { deriveMusicalFeatures } from "../public/analysis/musical-features.mjs";
import { analyzeNoteHarmony } from "../public/analysis/harmony.mjs";
import { vocalNotesFromAudio } from "./vocal-inference.mjs";
import { VOCAL_MODEL } from "./vocal-notes.mjs";

const round = n => Math.round(n * 10000) / 10000;
const clamp = n => Math.max(0, Math.min(1, n));
const progress = (stage, value, message = "") => self.postMessage({ type: "progress", stage, value: clamp(value), message });
const message = e => String(e instanceof Error ? e.message : e).slice(0, 350);

async function notesFromAudio(samples) {
  const result = { status: "complete", model: NOTE_MODEL, items: [], truncated: false, warnings: ["혼합 음원의 음표 후보입니다. 악기/보컬을 분리하거나 음표를 가사에 확정 대응한 결과가 아닙니다.", "모델 활성도는 보정된 정확도 확률이 아닙니다."] };
  setWasmPaths(new URL("/models/tfjs/", self.location.origin).href);
  setThreadsCount(1);
  // TFJS 3.21 WASM Fill omits CPU's documented inferred-dtype default.
  // Basic Pitch's tf.signal.frame pads with tf.fill(shape, 0), so normalize
  // that optional attribute before invoking the SAME WASM kernel (no fallback).
  const fill = tf.getKernel("Fill", "wasm");
  if (!fill) throw new Error("Required WASM Fill kernel is missing.");
  tf.unregisterKernel("Fill", "wasm");
  tf.registerKernel({ ...fill, kernelFunc: args => fill.kernelFunc({ ...args, attrs: { ...args.attrs, dtype: args.attrs.dtype ?? tf.util.inferDtype(args.attrs.value) } }) });
  await tf.setBackend("wasm");
  await tf.ready();
  const model = await tf.loadGraphModel(new URL("/models/basic-pitch/model.json", self.location.origin).href);
  const predictor = new BasicPitch(Promise.resolve(model));
  const rate = 22050, chunkLength = 12 * rate, overlap = rate, duration = samples.length / rate;
  try {
    for (let offset = 0; offset < samples.length; offset += chunkLength) {
      const left = Math.max(0, offset - overlap), right = Math.min(samples.length, offset + chunkLength + overlap);
      let frames = [], onsets = [];
      // The upstream inference helper does not release its intermediate tensors.
      // Model weights are created outside this single-worker scope and retained.
      tf.engine().startScope();
      try {
        await predictor.evaluateModel(noteWindowAudio(samples, left, right, rate), (f, o) => { frames.push(...f); onsets.push(...o); }, p => progress("notes", 0.28 + 0.38 * (offset + p * Math.min(chunkLength, samples.length - offset)) / samples.length, `음표 ${Math.floor(offset / rate)}초 / ${Math.ceil(duration)}초`));
        const events = noteFramesToTime(outputToNotesPoly(frames, onsets, 0.5, 0.3, 8));
        result.truncated = appendWindowNotes(result.items, events, left / rate, offset / rate, Math.min(samples.length, offset + chunkLength) / rate, duration) || result.truncated;
      } finally { tf.engine().endScope(); frames = []; onsets = []; }
    }
  } finally { model.dispose(); }
  result.items.sort((a, b) => a.start - b.start || a.midi - b.midi);
  if (result.truncated) { result.status = "partial"; result.warnings.push("음표 4,000개 한도로 일부 이벤트가 생략되었습니다."); }
  return result;
}

async function lyricsFromAudio(samples16k, choice, language) {
  const selected = LYRIC_MODELS[choice];
  const result = { status: "complete", model: `${selected.id}@${selected.revision}; transformers.js 3.8.1/q8/wasm`, language, words: [], truncated: false, warnings: ["Whisper의 음성 전사 초안입니다. 노래/랩/반주에서 잘못된 단어나 환각이 생길 수 있습니다. 원음으로 확인하세요.", "단어 시각은 추정값입니다. 음소 강제 정렬이나 가사-음표 일대일 대응이 아닙니다."] };
  env.allowLocalModels = false;
  env.useBrowserCache = true;
  env.backends.onnx.wasm.numThreads = 1;
  env.backends.onnx.wasm.wasmPaths = new URL("/models/ort/", self.location.origin).href;
  let recognizer;
  try {
    recognizer = await pipeline("automatic-speech-recognition", selected.id, {
      revision: selected.revision, dtype: "q8", device: "wasm",
      progress_callback: p => progress("lyrics", 0.68, p.status === "progress" ? `가사 모델 내려받는 중 · ${p.file ?? ""} ${Math.round(p.progress ?? 0)}%` : "가사 모델 준비 중 (첫 실행은 다운로드 필요)"),
    });
    const duration = samples16k.length / 16000;
    // Fixed disjoint 25-second windows bound attention/timestamp working memory.
    // Boundary words may be missed; no boundary overlap is passed off as exact alignment.
    const chunkSamples = 25 * 16000;
    for (let offset = 0; offset < samples16k.length; offset += chunkSamples) {
      const input = samples16k.subarray(offset, Math.min(offset + chunkSamples, samples16k.length));
      let energy = 0; for (let i = 0; i < input.length; i++) energy += input[i] * input[i];
      if (Math.sqrt(energy / input.length) < 0.001) continue;
      progress("lyrics", 0.7 + 0.28 * offset / samples16k.length, `가사 ${Math.floor(offset / 16000)}초 / ${Math.ceil(duration)}초`);
      const output = await recognizer(input, { return_timestamps: "word", task: "transcribe", ...(language !== "auto" ? { language } : {}), max_new_tokens: 256 });
      for (const word of output.chunks ?? []) {
        const [a, b] = word.timestamp ?? [];
        if (!Number.isFinite(a) || !Number.isFinite(b) || b <= a || !word.text?.trim()) { result.status = "partial"; continue; }
        const start = round(Math.max(0, offset / 16000 + a)), end = round(Math.min(duration, (offset + input.length) / 16000, offset / 16000 + b));
        if (end <= start) { result.status = "partial"; continue; }
        if (result.words.length >= 2000) { result.truncated = true; result.status = "partial"; continue; }
        result.words.push({ start, end, text: word.text.trim().slice(0, 200) });
      }
    }
    result.words.sort((a, b) => a.start - b.start);
    result.warnings.push("25초 창별 전사, 창당 생성 256토큰 한도: 경계 단어나 빠른 랩이 누락될 수 있습니다. 미검출은 보컬이 없다는 증거가 아닙니다.");
    if (result.status === "partial") result.warnings.push("시각 누락/범위 초과 또는 저장 한도로 일부 단어가 생략되었습니다.");
    return result;
  } finally { await recognizer?.dispose(); }
}

self.onmessage = async ({ data }) => {
  try {
    const { samples, samples16k, samples44k, noteMode = "polyphonic", lyrics = "off", language = "auto" } = data;
    if (!(samples instanceof Float32Array) || !samples.length || samples.length > 600 * 22050 || !LANGUAGES.includes(language) || !["off", ...Object.keys(LYRIC_MODELS)].includes(lyrics)) throw new Error("정밀 분석 입력 범위가 올바르지 않습니다.");
    if (lyrics !== "off" && (!(samples16k instanceof Float32Array) || Math.abs(samples16k.length / 16000 - samples.length / 22050) > 0.01)) throw new Error("가사 분석과 음정 분석의 시간축이 일치하지 않습니다.");
    if (!["polyphonic", "solo-vocal"].includes(noteMode)) throw new Error("알 수 없는 음표 분석 모드입니다.");
    if (noteMode === "solo-vocal") {
      if (!(samples44k instanceof Float32Array) || !samples44k.length || samples44k.length > 600 * 44100 || Math.abs(samples44k.length / 44100 - samples.length / 22050) > 0.01) throw new Error("보컬 분석의 원본 표본률과 시간축이 올바르지 않습니다.");
      for (const n of samples44k) if (!Number.isFinite(n)) throw new Error("보컬 음원에 유효하지 않은 신호가 있습니다.");
    }
    for (const n of samples) if (!Number.isFinite(n)) throw new Error("음원에 유효하지 않은 신호가 있습니다.");
    progress("rhythm", 0.02, "박자 · 대역별 타격 · 화음 후보 계산");
    const rhythm = analyzeRhythm(samples, 22050, p => progress("rhythm", 0.02 + p * 0.24));
    let notes;
    try { notes = noteMode === "solo-vocal" ? await vocalNotesFromAudio(samples44k, progress) : await notesFromAudio(samples); }
    catch (e) { console.error("Note inference failed", e instanceof Error ? e.stack : message(e)); notes = { status: "failed", mode: noteMode, model: noteMode === "solo-vocal" ? VOCAL_MODEL : NOTE_MODEL, items: [], truncated: false, warnings: [`음표 모델 실패: ${message(e)}`] }; }
    let vocal = { status: "skipped", model: "not requested", language, words: [], truncated: false, warnings: ["가사 모델이 선택되지 않았습니다."] };
    if (lyrics !== "off") {
      try { vocal = await lyricsFromAudio(samples16k, lyrics, language); }
      catch (e) { vocal = { status: "failed", model: LYRIC_MODELS[lyrics].id, language, words: [], truncated: false, warnings: [`가사 모델 실패: ${message(e)}`] }; }
    }
    const detail = { schema: "music-lens-detail/1", rhythm, notes, lyrics: vocal, warnings: ["모든 음악적 추정은 실제 곡의 정답과 대조 검증되지 않았습니다.", "대역별 타격은 악기 식별이 아니며, 강박·박자표는 사용자 확인이 필요합니다.", "분리되지 않은 혼합 음원에서 추출한 음표와 단어를 같은 보컬의 확정 대응으로 해석하지 마세요."] };
    detail.harmony = analyzeNoteHarmony(notes, samples.length / 22050, rhythm.chords);
    // Compatibility fields project the single detailed result, including abstention.
    rhythm.key = { label: detail.harmony.key.label, score: detail.harmony.key.score };
    rhythm.chords = detail.harmony.chords.map(({ start, end, label, confidence }) => ({ start, end, label, confidence }));
    rhythm.truncated.chords = detail.harmony.truncated;
    detail.features = deriveMusicalFeatures(detail, samples.length / 22050);
    self.postMessage({ type: "result", detail });
  } catch (e) { self.postMessage({ type: "error", error: message(e) }); }
};
