"""Ear check (ear_check.py): synth layers, loudness following, WAV read/write. Needs numpy (skipped without it)."""
import os
import struct
import sys
import tempfile
import unittest

try:
    import numpy as np
except ImportError:
    np = None

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if np is not None:
    import ear_check as E  # noqa: E402

V = {"melody_notes": [{"start": 0.5, "end": 1.0, "midi": 69}],
     "bass_notes": [{"start": 1.0, "end": 1.5, "midi": 45}],
     "other_notes": [{"start": 1.5, "end": 2.0, "midi": 64}, {"start": 1.5, "end": 1.55, "midi": 60},
                     {"start": 1.5, "end": 2.0, "midi": 100}],
     "chords": [{"start": 0.0, "end": 0.5, "label": "Am"}, {"start": 2.0, "end": 3.0, "label": "N"}],
     "drum_hits": [{"time": 2.5, "class": "kick"}],
     "downbeats": {"status": "done", "method": "m", "reliability": "low_reference", "count": 2},  # stored shape: status only
     "bars": {"status": "done", "rows": [{"start": 0.0, "end": 2.75, "chord": "Am", "kick": [], "snare": []}]}}


def energy(x, a, b):
    return float(np.abs(x[int(a * E.SR):int(b * E.SR)]).sum())


@unittest.skipUnless(np is not None, "numpy missing")
class SynthTest(unittest.TestCase):
    def test_layers_land_in_their_spans(self):
        x = E.synth(V, 0.0, 3.0, E.LAYERS)
        self.assertEqual(len(x), 3 * E.SR)
        for a, b in ((0.0, 0.45), (0.55, 0.95), (1.05, 1.45), (1.55, 1.95), (2.5, 2.7)):
            self.assertGreater(energy(x, a, b), 1.0, (a, b))
        self.assertEqual(energy(x, 2.05, 2.45), 0.0)  # N chord: silence

    def test_layer_selection(self):
        x = E.synth(V, 0.0, 3.0, ("melody",))
        self.assertGreater(energy(x, 0.55, 0.95), 1.0)
        self.assertEqual(energy(x, 1.05, 3.0), 0.0)

    def test_other_filters_range_and_length(self):
        x = E.synth(dict(V, other_notes=V["other_notes"][1:]), 0.0, 3.0, ("other",))
        self.assertEqual(energy(x, 0.0, 3.0), 0.0)  # 0.05 s note and C7 are left out

    def test_pitch_kept(self):
        x = E.synth({"melody_notes": [{"start": 0.0, "end": 1.0, "midi": 81}]}, 0.0, 1.0, ("melody",))
        f = np.fft.rfftfreq(len(x), 1 / E.SR)[np.argmax(np.abs(np.fft.rfft(x)))]
        self.assertAlmostEqual(f, 880.0, delta=2.0)  # A5 stays A5, no octave moved

    def test_chord_pcs(self):
        self.assertEqual(E.chord_pcs("F#m7/C#"), [6, 9, 1, 4])
        self.assertEqual(E.chord_pcs("Bb"), [10, 2, 5])
        self.assertIsNone(E.chord_pcs(None))

    def test_clicks_from_bar_rows(self):
        x = E.synth(V, 0.0, 3.0, ("clicks",))
        self.assertGreater(energy(x, 0.0, 0.02), 0.5)    # bar start
        self.assertGreater(energy(x, 2.75, 2.77), 0.5)   # last bar end
        self.assertEqual(energy(x, 0.05, 2.7), 0.0)
        self.assertEqual(energy(E.synth({"downbeats": V["downbeats"]}, 0.0, 3.0, ("clicks",)), 0.0, 3.0), 0.0)  # no bars: silent

    def test_missing_layers_silent(self):
        self.assertEqual(energy(E.synth({}, 0.0, 1.0), 0.0, 1.0), 0.0)


@unittest.skipUnless(np is not None, "numpy missing")
class FollowTest(unittest.TestCase):
    def test_follows_original_loudness(self):
        t = np.arange(6 * E.SR) / E.SR  # step at 3 s; smoothing spans about 1.2 s, so measure well away from it
        left = (np.sin(2 * np.pi * 220 * t) * np.where(t < 3, 0.05, 0.5)).astype(np.float32)
        right = (np.sin(2 * np.pi * 330 * t) * 0.3).astype(np.float32)
        r = E.follow(left, right)
        seg = lambda x, a, b: E.rms(x[int(a * E.SR):int(b * E.SR)])
        self.assertAlmostEqual(seg(r, 4.4, 5.2) / seg(r, 0.8, 1.6), 10.0, delta=1.0)
        self.assertAlmostEqual(seg(r, 4.4, 5.2), seg(left, 4.4, 5.2), delta=0.03)

    def test_no_fade_at_the_ends(self):
        t = np.arange(2 * E.SR) / E.SR
        left = (np.sin(2 * np.pi * 220 * t) * 0.5).astype(np.float32)
        r = E.follow(left, (np.sin(2 * np.pi * 330 * t) * 0.1).astype(np.float32))
        seg = lambda x, a, b: E.rms(x[int(a * E.SR):int(b * E.SR)])
        self.assertAlmostEqual(seg(r, 0.0, 0.2) / seg(r, 0.8, 1.2), 1.0, delta=0.05)
        self.assertAlmostEqual(seg(r, 1.8, 2.0) / seg(r, 0.8, 1.2), 1.0, delta=0.05)

    def test_silent_synth_stays_silent(self):
        z = np.zeros(E.SR, np.float32)
        self.assertEqual(E.rms(E.follow(np.ones(E.SR, np.float32), z)), 0.0)


@unittest.skipUnless(np is not None, "numpy missing")
class WavTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)

    def wav(self, data, sr=22050, ch=2, bits=16, tag=1):
        p = os.path.join(self.d.name, "in.wav")
        fmt = struct.pack("<HHIIHH", tag, ch, sr, sr * ch * bits // 8, ch * bits // 8, bits)
        with open(p, "wb") as f:
            f.write(b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt
                    + b"LIST" + struct.pack("<I", 3) + b"abc\0" + b"data" + struct.pack("<I", len(data)) + data)
        return p

    def test_read_pcm16_stereo_resampled(self):
        x = np.full((22050, 2), 16384, "<i2")
        p = self.wav(x.tobytes())
        y = E.read_wav(p, 0.25, 0.75)
        self.assertEqual(len(y), E.SR // 2)
        self.assertAlmostEqual(float(y.mean()), 0.5, places=3)

    def test_read_float32_and_span_past_end(self):
        p = self.wav(np.full(22050, 0.25, "<f4").tobytes(), ch=1, bits=32, tag=3)
        self.assertEqual(len(E.read_wav(p, 0.5, 5.0)), E.SR // 2)

    def test_truncated_file_reads_whole_frames(self):
        p = self.wav(np.full((22050, 2), 1000, "<i2").tobytes())
        with open(p, "r+b") as f:
            f.truncate(os.path.getsize(p) - 3)  # data header still claims the full size
        y = E.read_wav(p, 0.5, 1.0)
        self.assertGreater(len(y), 0)
        self.assertLess(len(y), E.SR // 2 + 1)

    def test_not_wav(self):
        p = os.path.join(self.d.name, "x.wav")
        with open(p, "wb") as f:
            f.write(b"ID3" + b"\0" * 40)
        self.assertRaises(ValueError, E.read_wav, p, 0, 1)

    def test_render_stereo_out(self):
        t = np.arange(3 * 22050) / 22050
        src = self.wav((np.sin(2 * np.pi * 220 * t) * 8000).astype("<i2").tobytes(), ch=1)
        out = os.path.join(self.d.name, "out.wav")
        sec, l, r = E.render(V, src, 0.0, 3.0, out)
        self.assertAlmostEqual(sec, 3.0, places=2)
        with open(out, "rb") as f:
            h = f.read(44)
        self.assertEqual((h[:4], h[8:12], struct.unpack("<H", h[22:24])[0]), (b"RIFF", b"WAVE", 2))
        self.assertGreater(r, 0)

    def test_render_limits(self):
        src = self.wav(np.zeros(22050, "<i2").tobytes(), ch=1)
        out = os.path.join(self.d.name, "out.wav")
        self.assertRaises(ValueError, E.render, V, src, 0.0, 61.0, out)
        self.assertRaises(ValueError, E.render, V, src, 0.0, 1.0, out, ("melody", "vocals"))
        self.assertRaises(ValueError, E.render, V, src, -0.5, 0.5, out)   # negative start
        self.assertRaises(ValueError, E.render, V, src, 5.0, 6.0, out)    # past the end of the source


@unittest.skipUnless(np is not None, "numpy missing")
class ServedTest(unittest.TestCase):
    """POST /earcheck runs this file with the rhythm python under limit_exec.py --earcheck; check it works there."""

    def test_server_constants_match(self):
        import server
        self.assertEqual((server.EARCHECK_LAYERS, server.EARCHECK_DEFAULT), (E.LAYERS, E.DEFAULT))
        self.assertLessEqual(server.EARCHECK_MAX_SPAN, E.MAX_SPAN)
        self.assertEqual(server.LISTEN_RATE, E.SR)

    def test_runs_under_limit_exec(self):
        import json
        import subprocess
        import server
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as d:
            src, res, out = os.path.join(d, "listen.wav"), os.path.join(d, "r.json"), os.path.join(d, "o.wav")
            t = np.arange(3 * E.SR) / E.SR
            E.write_wav(src, (np.sin(2 * np.pi * 220 * t) * 0.3).astype(np.float32), np.zeros(len(t), np.float32))
            with open(res, "w") as f:
                json.dump(V, f)
            p = subprocess.run(["/usr/bin/python3", "-I", os.path.join(here, "limit_exec.py"), "--earcheck",
                                sys.executable, os.path.join(here, "ear_check.py"), res, src, "0.500000", "2.500000",
                                out, "melody,bass,clicks"], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                               cwd="/", timeout=120)
            self.assertEqual(p.returncode, 0, p.stderr[-2000:])
            n = server.pcm16_wav_data(out, 16 * 1024 * 1024, 44100, 2)
            self.assertEqual(n, 2 * E.SR * 4)


if __name__ == "__main__":
    unittest.main()
