"""Contract regressions. Fake inference is orchestration evidence, never ASR evidence."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import wave

import music_lens_local as m


class LocalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.audio = self.root / "audio"
        self.model = self.root / "model"
        self.results = self.root / "results"
        self.audio.mkdir()
        self.model.mkdir()
        (self.model / "config.json").write_text("{}")
        (self.model / "weights.npz").write_bytes(b"fake model; not executable weights")
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\0\0" * 16000)
        self.wav = self.audio / "fixture.wav"
        self.wav.write_bytes(buffer.getvalue())
        self.lens = m.LocalLens(self.audio, self.results, self.model)
        self.lens.engine_version = lambda: "fake-0"
        self.calls = 0

        def fake_infer(audio_file, output_file, cancel):
            self.calls += 1
            output_file.write_bytes(m.encoded({"language": "en", "segments": [{"words": [
                {"word": " hello", "start": 0.1, "end": 0.3, "probability": 0.8},
                {"word": " world", "start": 0.4, "end": 0.9}]}]}))
        self.lens.infer = fake_infer

    def test_audio_and_result_confinement(self):
        """Reject traversal/escaping symlinks, wrong WAV format and caller-selected result paths."""
        outside = self.root / "outside.wav"
        outside.write_bytes(self.wav.read_bytes())
        (self.audio / "escape.wav").symlink_to(outside)
        for path in ("../outside.wav", str(outside), "escape.wav"):
            with self.subTest(path=path), self.assertRaises(m.Failure):
                self.lens.read_audio(path)
        invalid = bytearray(self.wav.read_bytes())
        invalid[24:28] = (8000).to_bytes(4, "little")
        with self.assertRaises(m.Failure):
            m.wav_info(bytes(invalid))
        for rid in ("../outside", "a" * 63, "A" * 64):
            with self.assertRaises(m.Failure):
                self.lens.report(rid)
        rid = "a" * 64
        self.lens.result_path(rid).symlink_to(outside)
        with self.assertRaises(m.Failure):
            self.lens.report(rid)

    def test_cache_identity_and_immutable_publication(self):
        """Same config avoids inference; language/model/version changes cannot reuse stale results."""
        first = self.lens.analyze("fixture.wav", "en")
        report_bytes = self.lens.result_path(first["result_id"]).read_bytes()
        second = self.lens.analyze("fixture.wav", "en")
        self.assertTrue(second["cache_hit"])
        self.assertEqual(self.calls, 1)
        self.assertEqual(report_bytes, self.lens.result_path(first["result_id"]).read_bytes())
        third = self.lens.analyze("fixture.wav", "auto")
        (self.model / "config.json").write_text('{"changed":true}')
        fourth = self.lens.analyze("fixture.wav", "en")
        self.lens.engine_version = lambda: "fake-1"
        fifth = self.lens.analyze("fixture.wav", "en")
        self.assertEqual(len({x["result_id"] for x in (first, third, fourth, fifth)}), 4)
        self.assertNotIn("words", first)
        self.assertEqual(first["word_count"], 2)
        # A corrupt local entry must not become a reusable successful transcript.
        path = self.lens.result_path(first["result_id"])
        broken = json.loads(path.read_bytes())
        broken["words"][0]["end"] = 99
        path.write_bytes(m.encoded(broken))
        with self.assertRaises(m.Failure):
            self.lens.report(first["result_id"])
        for schema_version in (None, "other-format/1"):
            broken = json.loads(report_bytes)
            if schema_version is None:
                del broken["schema_version"]
            else:
                broken["schema_version"] = schema_version
            path.write_bytes(m.encoded(broken))
            with self.subTest(schema_version=schema_version), self.assertRaises(m.Failure):
                self.lens.report(first["result_id"])

    def test_failure_and_busy_never_publish_success(self):
        """Failed/malformed/empty inference and named-lock contention leave no reusable result."""
        for payload in (b"not-json", b"{}", b'{"segments":[]}'):
            self.lens.infer = lambda a, o, c: o.write_bytes(payload)
            with self.assertRaises(m.Failure):
                self.lens.analyze("fixture.wav", "en")
            self.assertEqual(list(self.results.glob("*.json")), [])
            self.assertEqual(list(self.results.glob("*.lock")), [])
        def failure(a, o, c):
            raise m.Failure("inference_failed", "isolated fake failure")
        self.lens.infer = failure
        with self.assertRaises(m.Failure):
            self.lens.analyze("fixture.wav", "en")
        self.assertEqual(list(self.results.iterdir()), [])
        def busy(a, o, c):
            with self.assertRaises(m.Failure) as caught:
                self.lens.analyze("fixture.wav", "en")
            self.assertEqual(caught.exception.code, "busy")
            raise m.Failure("inference_failed", "end fake outer call")
        self.lens.infer = busy
        with self.assertRaises(m.Failure):
            self.lens.analyze("fixture.wav", "en")
        self.assertEqual(list(self.results.iterdir()), [])

    def test_invalid_native_data_is_partial_or_error(self):
        """NaN/backwards/out-of-audio words are omitted visibly, not silently accepted as lyrics."""
        words, warnings, _, notes = m.normalize({"segments": [{"words": [
            {"word": "ok", "start": 0.123456789, "end": 0.8, "probability": float("nan")},
            {"word": "bad", "start": float("nan"), "end": 1},
            {"word": "bad", "start": 0.9, "end": 0.7},
            {"word": "native zero length", "start": 0.9, "end": 0.9},
            {"word": "bad", "start": 0.9, "end": 2}]}]}, 1)
        self.assertEqual(len(words), 2)
        self.assertEqual(words[0]["start"], 0.123456789)
        self.assertNotIn("probability", words[0])
        self.assertEqual(len(warnings), 2)
        self.assertEqual(words[1]["start"], words[1]["end"])
        self.assertEqual(notes, [])
        with self.assertRaises(m.Failure):
            m.normalize({"segments": [{"words": [{"word": "bad", "start": -1, "end": 0}]}]}, 1)
        # A native end within 50ms remains unmodified and survives report reload;
        # beyond the tolerance is an explicit partial omission.
        for end in (1.05, 1.050001):
            def timed_fake(a, o, c):
                o.write_bytes(m.encoded({"segments": [{"words": [
                    {"word": "zero", "start": 0.1, "end": 0.1},
                    {"word": "end", "start": 0.9, "end": end}]}]}))
            self.lens.infer = timed_fake
            language = "en" if end == 1.05 else "auto"
            result = self.lens.analyze("fixture.wav", language)
            stored = self.lens.load(result["result_id"])
            self.assertEqual(result["state"], "complete" if end == 1.05 else "partial")
            self.assertEqual(result["schema_version"], "music-lens-local/1")
            if end == 1.05:
                self.assertEqual(stored["words"][1]["end"], end)
                self.assertEqual(stored["timing_notes"][0]["word_count"], 1)
                self.assertEqual(stored["warnings"], [])
                self.assertTrue(self.lens.analyze("fixture.wav", language)["cache_hit"])
                stored["timing_notes"] = []
                self.lens.result_path(result["result_id"]).write_bytes(m.encoded(stored))
                with self.assertRaises(m.Failure):
                    self.lens.load(result["result_id"])
            else:
                self.assertEqual(stored["word_count"], 1)
                self.assertTrue(stored["warnings"])
                self.assertEqual(stored["timing_notes"], [])

    def test_timeline_range_cursor_and_validation(self):
        """Paging retains every matching word and rejects ambiguous/invalid cursor/range values."""
        result = self.lens.analyze("fixture.wav", "en")
        rid = result["result_id"]
        first = self.lens.timeline(rid, limit=1)
        second = self.lens.timeline(rid, cursor=first["continuation"], limit=1)
        self.assertEqual(first["total"], 2)
        self.assertTrue(first["truncated"])
        self.assertFalse(second["truncated"])
        self.assertEqual([first["words"][0]["text"], second["words"][0]["text"]], [" hello", " world"])
        self.assertEqual(self.lens.timeline(rid, start=0.35, end=0.5)["total"], 1)
        for args in ({"cursor": -1}, {"cursor": True}, {"cursor": 3}, {"limit": 201}, {"start": 1, "end": 0}, {"end": float("nan")}):
            with self.subTest(args=args), self.assertRaises(m.Failure):
                self.lens.timeline(rid, **args)

    def test_child_timeout_is_reaped(self):
        """Timeout kills and waits for the fixed child; fake Popen avoids MLX/inference."""
        class FakeProcess:
            pid = 7654321
            stdin = io.BytesIO()
            waited = False
            def poll(self):
                return None
            def wait(self):
                self.waited = True
        process = FakeProcess()
        self.lens.timeout = 0
        with patch.object(m.subprocess, "Popen", return_value=process) as popen, patch.object(m.os, "killpg") as kill:
            with self.assertRaises(m.Failure) as caught:
                m.LocalLens.infer(self.lens, self.wav, self.root / "unused.json", threading.Event())
        self.assertEqual(caught.exception.code, "timeout")
        self.assertTrue(process.waited)
        kill.assert_called_once_with(process.pid, m.signal.SIGKILL)
        command = popen.call_args.args[0]
        self.assertEqual(command, [sys.executable, "-I", str(Path(m.__file__).resolve()), "_infer"])
        self.assertEqual(popen.call_args.kwargs["env"]["HF_HUB_OFFLINE"], "1")

    def test_real_serve_entrypoint_no_stdout_noise(self):
        """Actual CLI process negotiates MCP and handles schema/status/notifications without importing MLX."""
        command = [sys.executable, m.__file__, "--audio-root", str(self.audio), "--result-root", str(self.results), "--model-dir", str(self.model), "serve"]
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "status", "arguments": {}}},
            {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": "absent"}},
            {"jsonrpc": "2.0", "id": 4, "method": "ping"}]
        run = subprocess.run(command, input=b"\n".join(m.encoded(x) for x in requests) + b"\n", capture_output=True, timeout=5)
        self.assertEqual(run.returncode, 0, run.stderr)
        responses = {x["id"]: x for x in map(json.loads, run.stdout.splitlines())}
        self.assertEqual(set(responses), {1, 2, 3, 4})
        self.assertEqual({t["name"] for t in responses[2]["result"]["tools"]}, {"status", "analyze", "report", "timeline"})
        self.assertFalse(responses[3]["result"]["isError"])
        self.assertFalse(json.loads(responses[3]["result"]["content"][0]["text"])["automatic_downloads"])
        self.assertEqual(run.stderr, b"")


if __name__ == "__main__":
    unittest.main(verbosity=2)
