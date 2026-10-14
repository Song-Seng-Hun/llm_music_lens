import test from "node:test";
import assert from "node:assert/strict";
import { appendWindowNotes, noteWindowAudio } from "../analysis/note-windows.mjs";
import { detailSchema, rhythmSchema, harmonySchema, type DetailResult } from "../lib/detail.ts";
import { wordContexts } from "../lib/alignment.ts";
import { reportSchema, summaryOf, asMarkdown, timelineOf, type MusicReport } from "../lib/report.ts";
import { callMusicTool } from "../lib/mcp.ts";
import { analyzeRhythm } from "../public/precision-rhythm.mjs";
import { detailSignal } from "../lib/browser-detail.ts";
import { deriveMusicalFeatures } from "../public/analysis/musical-features.mjs";
import { asTeamHandoff } from "../lib/handoff.ts";
import { analyzeNoteHarmony } from "../public/analysis/harmony.mjs";
import { pitchContourSchema } from "../lib/pitch-contour.ts";

function detail(): DetailResult {
  return { schema: "music-lens-detail/1", rhythm: { method: "fixture", bpm: 120, tempoCandidates: [{ bpm: 120, score: 0.9 }], beats: [0, 0.5, 1, 1.5], onsets: [{ time: 0.5, strength: 0.8, band: "low" }, { time: 0.75, strength: 0.8, band: "high" }], envelope: [], sections: [], key: { label: null, score: 0 }, chords: [], warnings: [], truncated: { onsets: false, envelope: false, chords: false } },
    notes: { status: "complete", model: "test", items: [{ start: 0.5, end: 1, midi: 60, confidence: 0.7 }, { start: 1, end: 1.2, midi: 64, confidence: 0.8 }], truncated: false, warnings: [] },
    lyrics: { status: "complete", model: "test", language: "korean", words: [{ start: 0.6, end: 0.9, text: "가나다" }], truncated: false, warnings: [] }, warnings: [] };
}

test("native pitch contour survives report readers without flooding summaries and pages losslessly", async () => {
  // Named failures: schema stripping, flat-pitch replacement, gaps lost in paging,
  // or full native arrays leaking into the compact LLM handoff.
  const d = detail(), step = 512 / 44100;
  d.notes.mode = "solo-vocal";
  d.notes.contour = pitchContourSchema.parse({ status: "complete", method: "SOME native frame MIDI/rest; centered STFT; no smoothing", duration: 500 * step, stepSeconds: step,
    chunks: [{ start: 0, midi: Array.from({ length: 500 }, (_, i) => i % 7 ? 60 + i / 1000 : null) }], truncated: false });
  const r = reportSchema.parse({ schema: "music-lens/1", id: "af32ed31-a267-4d03-a12d-cad4353f5ea3", createdAt: "2026-10-14T00:00:00Z", source: { kind: "synthetic", title: "contour fixture", duration: 500 * step }, signal: null, interpretation: null, detail: d, provenance: { mode: "local-signal", inputStatus: "fixture", accuracy: "unvalidated" } });
  assert.deepEqual(r.detail!.notes.contour, d.notes.contour);
  assert.deepEqual(r.detail!.notes.items, d.notes.items);
  const frames: (number | null)[] = []; let start = 0;
  do {
    const page = timelineOf(r, start, 6).pitchContour;
    assert.ok(page.items.length <= 200); frames.push(...page.items.map(p => p.midi));
    if (page.nextStart === null) break;
    assert.ok(page.nextStart > start); start = page.nextStart;
  } while (true);
  assert.deepEqual(frames, d.notes.contour!.chunks[0].midi);
  const services = { get: async () => r, list: async () => [] };
  const viaMcp = await callMusicTool("get_music_timeline", { id: r.id, start: 1, end: 2 }, services);
  assert.deepEqual((viaMcp as ReturnType<typeof timelineOf>).pitchContour, timelineOf(r, 1, 2).pitchContour);
  const summary = JSON.stringify(await callMusicTool("get_music_report", { id: r.id }, services));
  assert.match(summary, /"samples":500/); assert.doesNotMatch(summary, /"chunks"/);
  const handoff = asTeamHandoff(r); assert.match(handoff, /pitchContour.nextStart/); assert.doesNotMatch(handoff, /"chunks"/);
  assert.match(asMarkdown(r), /Vocal pitch contour: complete; 500 native frames/);
  assert.equal(timelineOf({ ...r, detail: detail() }, 0, 2).pitchContour.status, "unavailable");
  for (const invalid of [
    { ...d.notes.contour, chunks: [{ start: 0, midi: [60] }] },
    { ...d.notes.contour, chunks: [{ start: 0, midi: [60, 61] }, { start: step, midi: [62] }] },
    { ...d.notes.contour, stepSeconds: 0 }, { ...d.notes.contour, truncated: true },
  ]) assert.equal(pitchContourSchema.safeParse(invalid).success, false);
  assert.equal(detailSchema.safeParse({ ...d, notes: { ...d.notes, mode: "polyphonic" } }).success, false);
});

test("solo-vocal fractional pitches and unavailable confidence survive report readers", async () => {
  const d = detail();
  d.notes = { ...d.notes, mode: "solo-vocal", model: "SOME ONNX", items: [{ start: 0.5, end: 1, midi: 60.25, confidence: null }, { start: 1, end: 1.2, midi: 63.8, confidence: null }] };
  d.features = deriveMusicalFeatures(d, 2);
  const r = reportSchema.parse(JSON.parse(JSON.stringify({ schema: "music-lens/1", id: "af32ed31-a267-4d03-a12d-cad4353f5ea3", createdAt: "2026-10-13T00:00:00Z", source: { kind: "synthetic", title: "solo contract fixture", duration: 2 }, signal: null, interpretation: null, detail: d, provenance: { mode: "local-signal", model: d.notes.model, inputStatus: "fixture", accuracy: "unvalidated" } })));
  assert.deepEqual(r.detail!.notes, d.notes);
  assert.equal(r.detail!.features!.pitchSummary.lowestMidi, 60.25);
  assert.equal(r.detail!.features!.pitchSummary.highestMidi, 63.8);
  assert.deepEqual(timelineOf(r, 0, 2).noteEvents.items, d.notes.items);
  const queried = await callMusicTool("get_music_timeline", { id: r.id, start: 0, end: 2 }, { get: async () => r, list: async () => [] });
  assert.deepEqual((queried as { noteEvents: { items: unknown[] } }).noteEvents.items, d.notes.items);
  const handoff = asTeamHandoff(r);
  assert.match(handoff, /SOME requires unaccompanied single-singer audio/);
  assert.match(handoff, /confidence:null means the model provides no note confidence/);
  assert.match(handoff, /estimated solo-vocal notes/);
  assert.doesNotMatch(handoff, /adds estimated mixture note events/);
  assert.match(handoff, /"midi": 60\.25/);
  assert.match(handoff, /"confidence": null/);
  for (const midi of [20.99, 108.01, Infinity, NaN]) assert.equal(detailSchema.safeParse({ ...d, notes: { ...d.notes, items: [{ ...d.notes.items[0], midi }] } }).success, false);
  assert.equal(detailSchema.safeParse({ ...d, notes: { ...d.notes, mode: "unknown" } }).success, false);
});

test("historical integer note reports stay readable without a note mode", () => {
  const old = detail(); old.features = deriveMusicalFeatures(old, 2);
  const restored = detailSchema.parse(JSON.parse(JSON.stringify(old)));
  assert.deepEqual(restored, old);
  assert.equal(restored.notes.mode, undefined);
  assert.equal(restored.notes.items[0].midi, 60);
  assert.equal(restored.notes.items[0].confidence, 0.7);
});

test("note harmony survives storage/readers and a lone chord cannot revive a stale key", async () => {
  // Named failures: schema strips harmony; null falls back to an old chord/key;
  // a failed note model leaves spectral-only rows incompatible with the schema.
  const d = detail();
  d.notes.items = [60, 64, 67].map(midi => ({ start: 0, end: 8, midi, confidence: 0.9 }));
  d.harmony = harmonySchema.parse(analyzeNoteHarmony(d.notes, 8, []));
  assert.equal(d.harmony!.key.label, null); assert.equal(d.harmony!.chords[0].label, "C");
  d.rhythm.key = { label: d.harmony!.key.label, score: d.harmony!.key.score };
  d.rhythm.chords = d.harmony!.chords;
  const r = reportSchema.parse({ schema: "music-lens/1", id: "af32ed31-a267-4d03-a12d-cad4353f5ea3", createdAt: "2026-10-13T00:00:00Z", source: { kind: "synthetic", title: "harmony fixture", duration: 8 }, signal: { ...detailSignal(new Float32Array(22050 * 8), d), key: { label: "D major", status: "older estimate" } }, interpretation: null, detail: d, provenance: { mode: "local-signal", inputStatus: "fixture", accuracy: "unvalidated" } });
  assert.deepEqual(r.detail!.harmony, d.harmony);
  assert.equal(summaryOf(r).key, null);
  assert.match(asMarkdown(r), /Key \(estimate\): unknown/);
  assert.match(asMarkdown(r), /0\.00–8\.00s \| C \| note-events/);
  assert.equal(timelineOf(r, 2, 3).harmony!.chords.total, 1);
  assert.equal(timelineOf(r, 8, 9).harmony!.chords.total, 0);
  const packet = JSON.parse(asTeamHandoff(r).split("## Evidence (seconds; sampled lists explicitly marked; full data in companion JSON)\n")[1].split("\n").map(line => line.replace(/^    /, "")).join("\n"));
  assert.equal(packet.harmony.key.label, null); assert.equal(packet.signal.key.label, null);
  assert.deepEqual(packet.harmony.chords.items[0].pitchClasses, ["C", "E", "G"]);
  const summary = await callMusicTool("get_music_report", { id: r.id }, { get: async () => r, list: async () => [] });
  assert.deepEqual((summary as { key: unknown }).key, d.harmony!.key);
  const noNotes = { ...d.notes, status: "failed" as const, items: [] };
  const spectral = analyzeNoteHarmony(noNotes, 8, d.rhythm.chords);
  const failed = detailSchema.parse({ ...d, notes: noNotes, harmony: spectral });
  assert.equal(failed.harmony!.source, "spectral-only");
  assert.equal(failed.harmony!.chords[0].source, "spectral-only");
  assert.deepEqual(failed.harmony!.chords[0].pitchClasses, []);
  assert.equal(failed.notes.status, "failed");
});

test("last inference window has right context without extending source or report time", () => {
  // Named failure: upstream output framing omits the final ~80 ms without right context.
  const source = Float32Array.from({ length: 30 }, (_, i) => i / 30);
  const first = noteWindowAudio(source, 0, 13, 10);
  assert.equal(first.buffer, source.buffer); assert.equal(first.length, 13);
  const last = noteWindowAudio(source, 23, 30, 10);
  assert.equal(last.length, 17); assert.notEqual(last.buffer, source.buffer);
  assert.deepEqual([...last.slice(0, 7)], [...source.slice(23)]);
  assert.ok(last.slice(7).every((n: number) => n === 0)); assert.equal(source.length, 30);
  const items: { start: number; end: number; midi: number; confidence: number }[] = [];
  appendWindowNotes(items, [{ startTimeSeconds: 0.62, durationSeconds: 0.5, pitchMidi: 60, amplitude: 0.8 }], 2.3, 2.4, 3, 3);
  assert.deepEqual(items.map(n => [n.start, n.end]), [[2.92, 3]]);
  // Sample-count durations need a final clamp after decimal rounding, for both
  // a new event and an overlapping continuation. Rounding must not create zero-length events.
  const duration = 262397 / 22050;
  for (const existing of [[], [{ start: 10, end: 11.6, midi: 60, confidence: 0.8 }]]) {
    appendWindowNotes(existing, [{ startTimeSeconds: 11.5, durationSeconds: 1, pitchMidi: 60, amplitude: 0.8 }], 0, existing.length ? 11.6 : 0, duration, duration);
    assert.equal(existing.length, 1); assert.equal(existing[0].end, duration);
    assert.ok(existing[0].end > existing[0].start);
  }
  const tiny: typeof items = [];
  appendWindowNotes(tiny, [{ startTimeSeconds: duration - .00001, durationSeconds: .001, pitchMidi: 60, amplitude: .8 }], 0, 0, duration, duration);
  assert.ok(tiny.every(n => n.start < n.end && n.end <= duration));
});

test("sustained notes crossing chunk boundaries survive, repeated attacks stay separate", () => {
  const items: { start: number; end: number; midi: number; confidence: number }[] = [];
  appendWindowNotes(items, [{ startTimeSeconds: 10, durationSeconds: 3, pitchMidi: 60, amplitude: 0.8 }], 0, 0, 12, 30);
  appendWindowNotes(items, [{ startTimeSeconds: 0, durationSeconds: 5, pitchMidi: 60, amplitude: 0.8 }, { startTimeSeconds: 7, durationSeconds: 1, pitchMidi: 60, amplitude: 0.8 }], 11, 12, 24, 30);
  assert.deepEqual(items.map(n => [n.start, n.end]), [[10, 16], [18, 19]]);
});
test("word/beat context is temporal overlap, bounded and never claims sung-note identity", () => {
  const d = detail();
  const output = wordContexts(d, 0, 1);
  assert.match(output.relation, /not vocal note identity/);
  assert.equal(output.items[0].nearestBeat!.offsetSeconds, 0.1);
  assert.deepEqual(output.items[0].simultaneousNotes.items.map(n => n.midi), [60]);
  assert.equal(wordContexts(d, 0.9, 1).items.length, 0);
  d.lyrics.words = Array.from({ length: 250 }, () => ({ start: 0.6, end: 0.9, text: "가" }));
  assert.equal(wordContexts(d, 0, 2).items.length, 200); assert.equal(wordContexts(d, 0, 2).truncated, true);
  d.notes.items = Array.from({ length: 20 }, () => d.notes.items[0]);
  assert.equal(wordContexts(d, 0, 2).items[0].simultaneousNotes.truncated, true);
});
test("specialist details and extra music evidence survive schema, export and bounded query", () => {
  const d = detail(); d.features = deriveMusicalFeatures(d, 2);
  const r: MusicReport = { schema: "music-lens/1", id: "af32ed31-a267-4d03-a12d-cad4353f5ea3", createdAt: "2026-10-13T00:00:00Z", source: { kind: "synthetic", title: "fixture", duration: 2 }, signal: null, interpretation: null, detail: d, provenance: { mode: "local-signal", inputStatus: "fixture", accuracy: "unvalidated" } };
  const restored = reportSchema.parse(JSON.parse(JSON.stringify(r)));
  assert.deepEqual(restored.detail, d);
  assert.equal(timelineOf(restored, 0, 2).features!.percussionCandidates.items.length, 2);
  assert.equal(timelineOf(restored, 0, 2).wordContext!.items.length, 1);
  assert.match(asTeamHandoff(restored), /transcript\/text proxy/);
  const invalid = detail(); invalid.notes.items[0].end = 0;
  assert.equal(detailSchema.safeParse(invalid).success, false);
  const failed = detail(); failed.notes.status = "failed"; failed.notes.items = []; failed.notes.warnings = ["actual model error"];
  assert.equal(detailSchema.parse(failed).notes.status, "failed");
});

test("new acoustic evidence survives readers and unknown tempo never falls back to a stale value", async () => {
  // Named contract failure: nullish fallback replaced deliberate abstention with
  // an older estimate; Zod can silently strip undeclared new evidence fields.
  const d = detail(); d.rhythm = rhythmSchema.parse(analyzeRhythm(new Float32Array(22050 * 3), 22050));
  const r = reportSchema.parse({ schema: "music-lens/1", id: "af32ed31-a267-4d03-a12d-cad4353f5ea3", createdAt: "2026-10-13T00:00:00Z", source: { kind: "synthetic", title: "fixture", duration: 3 }, signal: { duration: 3, waveform: [], energy: [], tempo: { bpm: 99, candidates: [], beats: [0, 1], segments: [], status: "legacy" }, pitch: [], key: { label: null, status: "unknown" }, chords: [], warnings: [], method: "old" }, interpretation: null, detail: d, provenance: { mode: "local-signal", inputStatus: "fixture", accuracy: "unvalidated" } });
  assert.deepEqual(r.detail!.rhythm.acoustics, d.rhythm.acoustics);
  // Actual browser report assembly must also satisfy the legacy signal limits.
  assert.equal(reportSchema.safeParse({ ...r, signal: detailSignal(new Float32Array(22050 * 3), d) }).success, true);
  assert.equal(summaryOf(r).bpm, null);
  assert.deepEqual(timelineOf(r, 1, 2).beats.items, []);
  assert.equal(timelineOf(r, 1, 2).acoustics!.windows.total, 1);
  assert.match(asMarkdown(r), /BPM \(estimate\): unknown/);
  assert.match(asTeamHandoff(r), /"bpmEstimate": null/);
  const packet = JSON.parse(asTeamHandoff(r).split("## Evidence (seconds; sampled lists explicitly marked; full data in companion JSON)\n")[1].split("\n").map(line => line.replace(/^    /, "")).join("\n"));
  assert.deepEqual(packet.signal.beatCandidates.items, []); assert.deepEqual(packet.signal.tempoSegments.items, []);
  const result = await callMusicTool("get_music_report", { id: r.id }, { get: async () => r, list: async () => [] });
  assert.ok(result && typeof result === "object" && "bpm" in result && "detail" in result);
  assert.equal(result.bpm, null);
  assert.deepEqual((result.detail as { acoustics: { windows: number } }).acoustics.windows, 3);
});
