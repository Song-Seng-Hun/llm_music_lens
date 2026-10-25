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
            self.assertEqual(result["schema_version"], "music-lens-local/2")
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

    def voice_result(self):
        result = m.emotion_shell("voice", 1, "complete")
        result["coverage"] = {"covered_seconds": 1, "uncovered_seconds": 0}
        result["windows"] = [{"start": 0, "end": 1, "category_labels": ["label" + str(i) for i in range(9)],
                               "category_scores": [0.9] + [0.0125] * 8, "dominant_emotion": "label0",
                               "arousal": 0.8, "dominance": 0.4, "valence": 0.6}]
        return result

    def fake_emotion_setup(self):
        self.lens.emotion_provenance = lambda mode: {"mode": mode, "availability": "ready", "worker_sha256": "1" * 64,
            "settings": {"window_seconds": 4, "hop_seconds": 2, "minimum_tail_seconds": 1}}
        self.lens.infer_emotion = lambda a, o, c, mode: o.write_bytes(m.encoded(self.voice_result()))

    def test_partial_layers_retained_all_failed_rejected(self):
        """A failed optional stage cannot erase words, nor empty ASR erase valid emotion."""
        first = self.lens.analyze("fixture.wav", "en", emotion_mode="voice")
        self.assertEqual(first["state"], "partial")
        self.assertEqual(first["asr_status"], "complete")
        self.assertEqual(first["emotion"]["status"], "unavailable")
        self.fake_emotion_setup()
        combined = self.lens.analyze("fixture.wav", "en", emotion_mode="voice")
        self.assertEqual(combined["state"], "complete")
        self.lens.infer = lambda a, o, c: o.write_bytes(b'{"segments":[]}')
        emotion_only = self.lens.analyze("fixture.wav", "ja", emotion_mode="voice")
        self.assertEqual(emotion_only["state"], "partial")
        self.assertEqual(emotion_only["asr_status"], "failed")
        self.assertEqual(emotion_only["word_count"], 0)
        self.assertEqual(self.lens.load(emotion_only["result_id"])["emotion"]["windows"], self.voice_result()["windows"])
        self.lens.infer_emotion = lambda a, o, c, mode: o.write_bytes(b'{}')
        before = set(self.results.glob("*.json"))
        with self.assertRaises(m.Failure):
            self.lens.analyze("fixture.wav", "ko", emotion_mode="voice")
        self.assertEqual(set(self.results.glob("*.json")), before)

    def test_emotion_validation_and_paged_portable_export(self):
        """Reject fabricated/malformed windows; portable export and paging retain native values."""
        self.fake_emotion_setup()
        result = self.lens.analyze("fixture.wav", "en", emotion_mode="voice")
        rid = result["result_id"]
        self.assertNotIn("windows", result["emotion"])
        self.assertEqual(result["emotion"]["window_count"], 1)
        page = self.lens.timeline(rid, layer="emotion", limit=1)
        self.assertEqual(page["windows"], self.voice_result()["windows"])
        self.assertEqual(page["total"], 1)
        exported = self.lens.export(rid, "Fixture")
        self.assertEqual(exported["schema"], "music-lens/1")
        self.assertEqual(exported["localAnalysis"]["emotion"]["windows"], page["windows"])
        self.assertEqual(exported["source"]["duration"], 1)
        self.assertNotIn(str(self.root), m.encoded(exported).decode())
        for change in (lambda row: row.update(end=2), lambda row: row.update(start=0.1),
                       lambda row: row.update(category_scores=[0.5]), lambda row: row.update(arousal=float("nan")),
                       lambda row: row.update(dominant_emotion="invented")):
            bad = json.loads(m.encoded(self.voice_result()))
            change(bad["windows"][0])
            with self.subTest(bad=bad), self.assertRaises(m.Failure):
                m.checked_emotion(bad, 1)
        stored = self.lens.load(rid)
        stored["emotion"]["coverage"]["covered_seconds"] = 0
        self.lens.result_path(rid).write_bytes(m.encoded(stored))
        with self.assertRaises(m.Failure):
            self.lens.load(rid)

    def test_emotion_mode_worker_and_model_affect_cache(self):
        """Mode plus actual local model/worker hashes distinguish emotion cache entries."""
        assets = self.root / "emotion"
        for directory, names in (("category", ("model.pt", "config.yaml", "tokens.txt", "configuration.json")),
                                 ("intensity", ("model.safetensors", "config.json", "preprocessor_config.json"))):
            (assets / directory).mkdir(parents=True)
            for name in names:
                (assets / directory / name).write_bytes(b"fixture asset")
        self.lens.emotion_python = Path(sys.executable)
        self.lens.emotion_assets = assets
        self.lens.infer_emotion = lambda a, o, c, mode: o.write_bytes(m.encoded(self.voice_result()))
        normal = self.lens.analyze("fixture.wav", "en")
        probe = subprocess.CompletedProcess([], 0, stdout=b'{"torch":"fake-0"}', stderr=b"")
        with patch.object(m.subprocess, "run", return_value=probe):
            first = self.lens.analyze("fixture.wav", "en", emotion_mode="voice")
            cached = self.lens.analyze("fixture.wav", "en", emotion_mode="voice")
            self.assertTrue(cached["cache_hit"])
            (assets / "category/model.pt").write_bytes(b"changed fixture model")
            changed_model = self.lens.analyze("fixture.wav", "en", emotion_mode="voice")
            old_read = Path.read_bytes
            def changed_worker(path):
                return b"different worker source" if path.name == "emotion_worker.py" else old_read(path)
            with patch.object(Path, "read_bytes", changed_worker):
                changed_code = self.lens.analyze("fixture.wav", "en", emotion_mode="voice")
        self.assertEqual(len({r["result_id"] for r in (normal, first, changed_model, changed_code)}), 4)

    def test_second_stage_cancel_never_publishes(self):
        """Cancellation after ASR or inside emotion prevents a reusable partial report."""
        self.fake_emotion_setup()
        cancel = threading.Event()
        def cancelled(a, o, c, mode):
            c.set()
            raise m.Failure("cancelled", "fixture cancellation")
        self.lens.infer_emotion = cancelled
        with self.assertRaises(m.Failure) as caught:
            self.lens.analyze("fixture.wav", "en", cancel=cancel, emotion_mode="voice")
        self.assertEqual(caught.exception.code, "cancelled")
        self.assertEqual(list(self.results.iterdir()), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
