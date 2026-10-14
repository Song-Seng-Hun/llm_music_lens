import test from "node:test";
import assert from "node:assert/strict";
import { decodeTimingAudio, TIMING_MAX_BASE64, TIMING_SAMPLE_RATE } from "../lib/music-timing.ts";
import { callMusicTool } from "../lib/mcp.ts";
import { AppError } from "../lib/validation.ts";
import { linkVocalTiming } from "../lib/vocal-timing.ts";
import { beatContext } from "../lib/beat-context.ts";

function input(seconds = 1) {
  const bytes = Buffer.alloc(TIMING_SAMPLE_RATE * seconds * 2);
  [-32768, -1, 0, 32767].forEach((n, i) => bytes.writeInt16LE(n, i * 2));
  return { audio: { encoding: "pcm_s16le", sampleRate: 22050, channels: 1, data: bytes.toString("base64") }, offsetSeconds: 0 };
}

// Wrong signedness/endian would change actual inference; existing UI tests use
// decoded floats and cannot catch the new MCP wire-format failure.
test("server PCM decoding preserves signed samples, bytes and excerpt clock", () => {
  const value = input(); value.offsetSeconds = 40;
  const decoded = decodeTimingAudio(value);
  assert.deepEqual(Array.from(decoded.samples.subarray(0, 4)), [-1, -1 / 32768, 0, 32767 / 32768]);
  assert.equal(decoded.duration, 1); assert.equal(decoded.input.offsetSeconds, 40);
  assert.equal(Buffer.from(decoded.bytes).toString("base64"), value.audio.data);
  assert.equal(decodeTimingAudio(input(30)).samples.length, 661500);
});

// Reject malformed/oversized/unsupported audio before any heavy model call.
test("server audio rejects unsupported formats and invalid bounds", () => {
  const valid = input();
  for (const value of [
    { ...valid, audio: { ...valid.audio, channels: 2 } },
    { ...valid, audio: { ...valid.audio, sampleRate: 48000 } },
    { ...valid, audio: { ...valid.audio, data: "?".repeat(4) } },
    { ...valid, audio: { ...valid.audio, data: "AAAA" } },
    { ...valid, audio: { ...valid.audio, data: "A".repeat(TIMING_MAX_BASE64 + 4) } },
    { ...valid, audio: { ...valid.audio, url: "https://example.test/audio" } },
    { ...valid, offsetSeconds: 600 },
    { ...valid, timedVocal: { unit: "phone", source: "fixture", items: [{ start: 0.5, end: 2, text: "a" }] } },
    { ...valid, noteEvents: { source: "fixture", mode: "solo-vocal", items: [{ start: 0.8, end: 0.5, midi: 60 }] } },
  ]) assert.throws(() => decodeTimingAudio(value));
});

// Real unit boundaries must survive linkage; even word subdivision would hide
// missing alignment and change the meaning of melisma/rap timing.
test("vocal timing links supplied intervals without manufacturing syllables or meter", () => {
  const parsed = decodeTimingAudio({ ...input(2), timedVocal: { unit: "syllable", source: "fixture-only", items: [
    { start: 0.3, end: 1, text: "가" }, { start: 1, end: 1.8, text: "나" },
  ] }, noteEvents: { source: "fixture-only", mode: "solo-vocal", items: [
    { start: 0.3, end: 0.6, midi: 60 }, { start: 0.6, end: 1, midi: 62 }, { start: 1, end: 1.8, midi: 64 },
  ] } });
  const linked = linkVocalTiming(parsed.input, [0, 0.5, 1, 1.5], [0], 2);
  assert.equal(linked.status, "linked-supplied-timing");
  assert.equal(linked.items?.length, 2); assert.equal(linked.items?.[0].start, 0.3);
  assert.equal(linked.items?.[0].beatPhase?.fraction, 0.6);
  assert.equal(linked.items?.[0].nearestBeat?.offsetSeconds, -0.2);
  assert.deepEqual(linked.items?.[0].simultaneousNotes.items.map(x => x.midi), [60, 62]);
  assert.equal(linked.items?.[1].nearestBeat?.offsetSeconds, 0);
  assert.equal(linkVocalTiming(decodeTimingAudio(input()).input, [0], [], 1).status, "unavailable");
  const context = beatContext([0, 0.5, 1, 1.5, 2], [0, 2]);
  assert.equal(context.tempo.bpm, 120); assert.equal(context.meter, null);
  assert.equal(context.downbeatSpans[0].detectedBeatCount, 4);
});

// Dispatch can silently remain on saved-report paths even with working DSP.
test("MCP selects the analysis service and never substitutes a stored report", async () => {
  let calls = 0;
  const services = { list: async () => [], get: async () => { throw new Error("not a report read"); },
    analyzeTiming: async (args: unknown) => { calls++; return { duration: decodeTimingAudio(args).duration }; } };
  assert.deepEqual(await callMusicTool("analyze_music_timing", input(), services), { duration: 1 });
  assert.equal(calls, 1);
  await assert.rejects(callMusicTool("analyze_music_timing", { audio: {} }, services));
  assert.equal(calls, 1);
  await assert.rejects(callMusicTool("analyze_music_timing", input(), { list: services.list, get: services.get }), (e: AppError) => e.code === "ANALYSIS_UNAVAILABLE");
});
