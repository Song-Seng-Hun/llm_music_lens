// Intent: imported local output must survive validation/storage contracts and stay bounded; invalid evidence must never reach the write service.
import assert from "node:assert/strict";
import { reportSchema, summaryOf, timelineOf, MAX_REPORT_BYTES } from "../lib/report.ts";
import { VOICE_LABELS, MUSIC_LABELS } from "../lib/local-analysis.ts";
import { callMusicTool, MCP_TOOLS } from "../lib/mcp.ts";

const sha = "a".repeat(64), other = "b".repeat(64);
const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value));
function sample() {
  const row = (start: number, end: number, arousal: number) => ({ start, end, category_labels: [...VOICE_LABELS], category_scores: [0.8, 0.025, 0.025, 0.025, 0.025, 0.025, 0.025, 0.025, 0.025], dominant_emotion: VOICE_LABELS[0], arousal, dominance: 0.4, valence: 0.3 });
  return { schema: "music-lens/1", id: "b5de43d8-6b5f-4a4e-a0f1-5df99d87a5e2", createdAt: "2026-10-25T20:00:00+00:00", source: { kind: "file", title: "Public speech fixture", sha256: sha, duration: 4 }, signal: null, interpretation: null,
    provenance: { mode: "imported", inputStatus: "local model estimates; not independently validated", accuracy: "unvalidated" },
    localAnalysis: { schema_version: "music-lens-local/2", result_id: other, state: "complete", clock: "seconds_from_audio_start", audio: { duration_seconds: 4, sample_rate_hz: 16000, channels: 1, sample_format: "signed16_pcm", frames: 64000, sha256: sha },
      provenance: { tool_version: "0.2.0", engine: "mlx-whisper", engine_version: "0.4.3", model_files: [{ file: "weights.npz", sha256: other, bytes: 143724204 }], source_sha256: sha,
        settings: { language: "en", word_timestamps: true, temperature: 0 }, runner_sha256: other,
        emotion: { mode: "voice", settings: { window_seconds: 4, hop_seconds: 2, minimum_tail_seconds: 1 }, worker_sha256: other, availability: "ready", model_files: [{ file: "category/model.pt", sha256: other, bytes: 1000 }], engine_versions: { torch: "2.9.1", funasr: "1.4.16" } } },
      detected_language: "en", word_count: 2, warnings: [], timing_notes: [{ code: "native_end_overshoot", word_count: 1, max_overshoot_seconds: 4.02 - 4, tolerance_seconds: 0.05 }], probability_semantics: "native_uncalibrated", created_unix: 1792958400,
      words: [{ text: " The", start: 0, end: 0, probability: 0.35 }, { text: " train.", start: 3.9, end: 4.02, probability: 0.8 }], asr_status: "complete",
      emotion: { mode: "voice", status: "complete", warnings: [], window_seconds: 4, hop_seconds: 2, minimum_tail_seconds: 1, coverage: { covered_seconds: 4, uncovered_seconds: 0 }, windows: [row(0, 4, 0.4), row(2, 4, 0.9)], score_semantics: "native_uncalibrated", accuracy: "unvalidated_for_singing", dimension_scale: "model native, approximately 0..1" } } };
}
let count = 0;
function check(name: string, fn: () => void) { fn(); count++; console.log(`PASS ${name}`); }

check("native words/emotion round-trip without precision loss", () => {
  const input = sample(), parsed = reportSchema.parse(input);
  assert.deepEqual(parsed, input);
  assert.equal(parsed.localAnalysis!.words[0].end, 0);
  assert.equal(parsed.localAnalysis!.words[1].end, 4.02);
  assert.equal(parsed.localAnalysis!.emotion.windows[1].arousal, 0.9);
  const terminal = sample(); terminal.localAnalysis.words.push({ text: " end", start: 4, end: 4, probability: 0.2 }); terminal.localAnalysis.word_count++;
  assert.equal(timelineOf(reportSchema.parse(terminal), 0, 4).localWords.items.at(-1)!.text, " end");
});
check("invalid native/state/hash/path evidence rejected", () => {
  const cases: [string, (v: any) => void][] = [
    ["schema", v => { v.localAnalysis.schema_version = "music-lens-local/999"; }],
    ["source hash", v => { v.source.sha256 = other; }], ["duration", v => { v.source.duration = 3; }],
    ["audio frames", v => { v.localAnalysis.audio.frames = 63999; }],
    ["probability", v => { v.localAnalysis.words[0].probability = 1.1; }],
    ["reordered words", v => { v.localAnalysis.words.reverse(); }],
    ["overshoot", v => { v.localAnalysis.words[1].end = 4.051; }],
    ["overshoot note", v => { v.localAnalysis.timing_notes = []; }],
    ["label order", v => { v.localAnalysis.emotion.windows[0].category_labels.reverse(); }],
    ["score shape", v => { v.localAnalysis.emotion.windows[0].category_scores.pop(); }],
    ["top label", v => { v.localAnalysis.emotion.windows[0].dominant_emotion = VOICE_LABELS[1]; }],
    ["window order", v => { v.localAnalysis.emotion.windows.reverse(); }],
    ["window range", v => { v.localAnalysis.emotion.windows[0].end = 4.01; }],
    ["coverage", v => { v.localAnalysis.emotion.coverage.covered_seconds = 3; }],
    ["state", v => { v.localAnalysis.asr_status = "failed"; }],
    ["model path", v => { v.localAnalysis.provenance.model_files[0].file = "/Users/private/model.npz"; }],
    ["extra credential field", v => { v.localAnalysis.provenance.token = "forbidden"; }],
  ];
  for (const [name, mutate] of cases) { const input = sample(); mutate(input); assert.equal(reportSchema.safeParse(input).success, false, name); }
});
check("legacy minimal report remains valid", () => {
  const input: any = sample(); delete input.localAnalysis;
  assert.deepEqual(reportSchema.parse(input), input);
  assert.equal(summaryOf(reportSchema.parse(input)).localAnalysis, null);
});
check("independent failed layer and unrequested emotion remain explicit", () => {
  const input: any = sample(); const local = input.localAnalysis;
  local.asr_status = "failed"; local.words = []; local.word_count = 0; local.timing_notes = []; local.state = "partial"; local.warnings = ["ASR failed (empty_output)"];
  assert.equal(reportSchema.parse(input).localAnalysis!.emotion.windows.length, 2);
  local.provenance.tool_version = "0.2.1"; local.provenance.failure_attempt = "c".repeat(32);
  const transient = summaryOf(reportSchema.parse(input)).localAnalysis!;
  assert.equal(transient.cacheable, false); assert.equal(transient.retry_recommended, true);
  const invalid: any = sample(); invalid.localAnalysis.provenance.failure_attempt = "c".repeat(32);
  assert.equal(reportSchema.safeParse(invalid).success, false, "complete result cannot claim a failed attempt");
  const none: any = sample(); none.localAnalysis.emotion = { mode: "none", status: "not_requested", warnings: [], window_seconds: 0, hop_seconds: 0, minimum_tail_seconds: 0, coverage: { covered_seconds: 0, uncovered_seconds: 4 }, windows: [], score_semantics: "native_uncalibrated", accuracy: "unmeasured", dimension_scale: "model native, approximately 1..9" };
  none.localAnalysis.provenance.emotion = { mode: "none", settings: { window_seconds: 0, hop_seconds: 0, minimum_tail_seconds: 0 } };
  assert.equal(reportSchema.parse(none).localAnalysis!.emotion.status, "not_requested");
});
check("native music scale remains separate and unclamped", () => {
  const input: any = sample(), local = input.localAnalysis;
  local.provenance.emotion.mode = "music"; local.provenance.emotion.settings = { window_seconds: 5, hop_seconds: 2.5, minimum_tail_seconds: 3 };
  local.emotion = { mode: "music", status: "complete", warnings: [], window_seconds: 5, hop_seconds: 2.5, minimum_tail_seconds: 3, coverage: { covered_seconds: 4, uncovered_seconds: 0 }, windows: [{ start: 0, end: 4, mood_labels: [...MUSIC_LABELS], mood_scores: [0.1, 0.6, 0.1, 0.1, 0.1], top_mood: MUSIC_LABELS[1], arousal: 9.2, valence: 0.9 }], score_semantics: "native_uncalibrated", accuracy: "unmeasured", dimension_scale: "model native, approximately 1..9" };
  assert.equal(reportSchema.parse(input).localAnalysis!.emotion.windows[0].arousal, 9.2);
});
check("summary omits full arrays and timeline includes point words with caps", () => {
  const input: any = sample(), local = input.localAnalysis;
  local.words = Array.from({ length: 250 }, (_, i) => ({ text: `w${i}`, start: i / 100, end: i / 100, probability: 0.4 })); local.word_count = 250; local.timing_notes = [];
  const report = reportSchema.parse(input), summary = JSON.stringify(summaryOf(report));
  assert.equal(summary.includes('"words"'), false); assert.equal(summary.includes('"windows"'), false);
  const page = timelineOf(report, 0, 4);
  assert.equal(page.localWords.items.length, 200); assert.equal(page.localWords.total, 250); assert.equal(page.localWords.truncated, true);
  assert.equal(page.localWords.items[0].end, 0); assert.equal(page.localEmotion.items.length, 2);
  assert.deepEqual(page.words.items, []); // Browser/local layers are never silently merged.
});
let writes = 0;
const services = { list: async () => [], get: async () => reportSchema.parse(sample()), importReport: async (report: ReturnType<typeof reportSchema.parse>) => { writes++; return report; } };
const saved = await callMusicTool("import_music_report", { report: sample() }, services);
assert.equal(writes, 1); assert.equal((saved as any).localAnalysis.word_count, 2);
assert.equal(JSON.stringify(saved).includes('"words"'), false);
await assert.rejects(callMusicTool("import_music_report", { report: { ...sample(), localAnalysis: { invalid: true } } }, services));
await assert.rejects(callMusicTool("import_music_report", { report: { ...sample(), extra: "x".repeat(MAX_REPORT_BYTES) } }, services));
assert.equal(writes, 1);
const tool = MCP_TOOLS.find(t => t.name === "import_music_report")!;
assert.equal(tool.annotations.readOnlyHint, false);
count++; console.log("PASS MCP import validates before the scoped service; bounded summary only");
console.log(`${count} focused checks passed`);
