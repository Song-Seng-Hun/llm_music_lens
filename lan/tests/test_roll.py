"""Sheet PNG pages: sheet.pages, GET /sheet/<id>?format=png (page index), POST /sheet/<id>?format=png&page=N with the
analysed audio (roll.py under limit_exec.py --roll, faked here: the test python has no matplotlib), local_cli.py sheet
--png. The real renderer runs only with MUSIC_LENS_ROLL_PYTHON (numpy + matplotlib); without it those tests are skipped
and the image itself is unverified."""
import json
import math
import os
import random
import struct
import subprocess
import tempfile
import unittest
import wave

from helpers import PY, REPO, RID64, Client, Env, Harness, make_wav_bytes, server
from test_earcheck_route import AFCONVERT
from test_rhythm import RHYTHM_OUT_V40, FakeWorker, RhythmBase

import limit_exec  # noqa: E402
import roll  # noqa: E402  (stdlib at import; numpy only inside the render functions)

sheet = server.sheet
ROLL_PY = os.environ.get("MUSIC_LENS_ROLL_PYTHON", "")
AUDIO = make_wav_bytes(6.0)  # the fixture result's duration
PNG = b"\x89PNG\r\n\x1a\n" + b"fake-png" * 10

# Stands in for roll_python: limit_exec execs it as PYTHON -I roll.py RESULT SRC PAGE OUT [MARKS] or
# PYTHON -I roll.py --poster RESULT SRC START END OUT TITLE CREDIT.
FAKE_PY = r'''#!/usr/bin/python3 -I
import json, os, resource, struct, sys
HERE = os.path.dirname(os.path.realpath(__file__))
with open(os.path.join(HERE, "control.json")) as f:
    ctl = json.load(f)
args = sys.argv[1:]
src = args[4] if args[2] == "--poster" else args[3]
out = [a for a in args if a.endswith("/roll.png")][0]
with open(src, "rb") as f:
    head = f.read(64)
with open(os.path.join(HERE, "calls.log"), "a") as f:
    f.write(json.dumps({"argv": args, "src_rate": struct.unpack("<I", head[24:28])[0], "cwd": os.getcwd(),
                        "fsize": resource.getrlimit(resource.RLIMIT_FSIZE)[0]}) + "\n")
if ctl.get("partial"):
    open(out, "wb").write(b"\x89PNG\r\n")
if ctl.get("exit"):
    sys.exit(ctl["exit"])
open(out, "wb").write(ctl.get("png", "").encode("latin-1"))
if args[-1].endswith("/roll-marks.json"):
    open(args[-1], "w").write(ctl.get("marks", json.dumps({"page": {}, "suspects": [{"note": "A3", "flags": ["weak"]}], "missed": []})))
'''


def ihdr(png):
    return struct.unpack(">II", png[16:24])


class FakeRoll:
    def __init__(self, env):
        self.dir = os.path.join(env.dir, "fakeroll")
        os.mkdir(self.dir)
        self.path = os.path.join(self.dir, "python")
        with open(self.path, "w") as f:
            f.write(FAKE_PY)
        os.chmod(self.path, 0o700)
        self.write()

    def write(self, **ctl):
        with open(os.path.join(self.dir, "control.json"), "w") as f:
            json.dump(dict({"png": PNG.decode("latin-1")}, **ctl), f)

    def calls(self):
        p = os.path.join(self.dir, "calls.log")
        if not os.path.exists(p):
            return []
        with open(p) as f:
            return [json.loads(x) for x in f]


def result(**over):
    return dict(json.loads(json.dumps(RHYTHM_OUT_V40)), **over)


class PagesTest(unittest.TestCase):
    def test_one_page_per_section(self):
        self.assertEqual(sheet.pages(result()), [
            {"page": 1, "section": "S01", "label": "A", "start": 0.0, "end": 2.0},
            {"page": 2, "section": "S02", "label": "B", "start": 2.0, "end": 4.0},
            {"page": 3, "section": "S03", "label": "A", "start": 4.0, "end": 6.0}])

    def test_long_section_split_at_beats(self):
        beats = [round(i * 0.5, 2) for i in range(1, 100)]
        v = result(duration=50.0, beats=beats + [10.1], sections=[{"start": 0.0, "end": 50.0, "label": "A"}])
        ps = sheet.pages(v)
        self.assertEqual([(p["label"], p["start"], p["end"]) for p in ps],  # 50 s > 22 s: k = 3, cuts 16.67 and 33.33
                         [("A 1/3", 0.0, 16.5), ("A 2/3", 16.5, 33.5), ("A 3/3", 33.5, 50.0)])
        self.assertEqual(sheet.pages(dict(v, beats=[]))[1]["start"], 16.667)
        self.assertEqual(len(sheet.pages(dict(v, sections=[{"start": 0.0, "end": 22.0, "label": "A"}]))), 1)  # 1.1 x 20

    def test_cuts_strictly_increasing(self):  # PR 61 review: beats only late in the section gave [45, 33.333]
        v = result(duration=50.0, beats=[45.0, 46.0, 47.0, 48.0], sections=[{"start": 0.0, "end": 50.0, "label": "A"}])
        cuts = [(p["start"], p["end"]) for p in sheet.pages(v)]
        self.assertEqual(cuts, [(0.0, 16.667), (16.667, 45.0), (45.0, 50.0)])

    def test_no_sections(self):
        self.assertEqual(sheet.pages(result(sections=[])),
                         [{"page": 1, "section": None, "label": "all", "start": 0.0, "end": 6.0}])


class RollBase(RhythmBase):
    roll = True

    def setUp(self):
        self.env = Env()
        self.fw = FakeWorker(self.env)
        self.fr = FakeRoll(self.env)
        rh = {"python": PY, "worker": self.fw.path}
        if self.roll:
            rh["roll_python"] = self.fr.path
        self.h = Harness(self.env, rhythm=rh)
        self.c = Client(self.env, self.h.port)
        self.write_result()
        os.makedirs(os.path.dirname(self.rhythm_file()), 0o700, exist_ok=True)
        with open(self.rhythm_file(), "w") as f:
            json.dump(dict(RHYTHM_OUT_V40, **self.ids()), f)

    def post(self, query="format=png&page=1", body=AUDIO):
        return self.c.send("POST", "/sheet/%s?%s" % (RID64, query), body)

    def assert_clean(self):
        self.assertEqual(self.env.uploads_entries(), [])
        self.assertTrue(self.h.app.gate.acquire(blocking=False))
        self.h.app.gate.release()

    def assert_error(self, status, code, query="format=png&page=1", body=AUDIO):
        st, _, raw = self.post(query, body)
        b = json.loads(raw)
        self.assertEqual((st, b["error"]["code"]), (status, code), b)
        self.assert_clean()
        return b["error"]


@unittest.skipUnless(os.path.exists(AFCONVERT), "afconvert missing")
class RollRouteTest(RollBase):
    def test_index(self):
        st, b = self.c.json("GET", "/sheet/%s?format=png" % RID64)
        self.assertEqual(st, 200, b)
        self.assertEqual(b, {"result_id": RID64, "format": "png", "available": True,
                             "pages": sheet.pages(RHYTHM_OUT_V40), "render": server.ROLL_RENDER})
        for q in ("format=png&page=1", "format=png&start=0&end=2", "format=png&view=brief"):
            st, b = self.c.json("GET", "/sheet/%s?%s" % (RID64, q))
            self.assertEqual((st, b["error"]["code"]), (400, "bad_request"), q)
        st, b = self.c.json("GET", "/sheet/%s?format=png" % ("0" * 64))
        self.assertEqual((st, b["error"]["code"]), (404, "rhythm_not_found"))

    def test_page_png(self):
        st, h, raw = self.post("format=png&page=2")
        self.assertEqual(st, 200, raw[:300])
        self.assertEqual((h["content-type"], raw), ("image/png", PNG))
        call, = self.fr.calls()
        self.assertEqual(call["argv"][:3], ["-I", os.path.join(REPO, "roll.py"), self.rhythm_file()])
        self.assertEqual(call["argv"][4], "2")
        self.assertRegex(call["argv"][3], r"/uploads/rl-[0-9a-f]{32}/listen\.wav\Z")
        self.assertTrue(call["argv"][5].endswith("/roll.png"))
        self.assertEqual((call["src_rate"], call["cwd"], call["fsize"]), (44100, "/", limit_exec.ROLL_FSIZE_BYTES))
        self.assertEqual(self.env.argv_log(), [])  # no ml call
        self.assert_clean()

    def test_wrong_audio(self):
        e = self.assert_error(400, "invalid_argument", body=make_wav_bytes(4.0))
        self.assertEqual(e["detail"], server.ROLL_AUDIO_DETAIL)
        self.assert_error(400, "invalid_audio", body=b"RIFF" + b"\x00" * 100)
        self.assertEqual(self.fr.calls(), [])

    def test_bad_requests(self):
        e = self.assert_error(400, "invalid_argument", "format=png&page=4")
        self.assertIn("last page", e["detail"])
        for q in ("format=png", "format=png&page=0", "format=png&page=01", "format=png&page=1&view=brief"):
            self.assert_error(400, "bad_request", q)
        for q in ("", "format=json", "format=text&page=1"):  # the text/JSON sheet stays GET only
            self.assert_error(405, "method_not_allowed", q)
        st, b = self.c.json("POST", "/sheet/%s?format=png&page=1" % ("0" * 64), AUDIO)
        self.assertEqual((st, b["error"]["code"]), (404, "rhythm_not_found"))
        self.assertEqual(self.fr.calls(), [])

    def test_render_failure_is_never_an_image(self):
        for ctl in ({"exit": 1}, {"exit": 1, "partial": True}, {"png": "not a png"}):
            self.fr.write(**ctl)
            st, h, raw = self.post()
            self.assertEqual((st, h["content-type"], json.loads(raw)["error"]["code"]),
                             (500, "application/json", "roll_failed"), ctl)
            self.assert_clean()

    def test_busy(self):
        self.assertTrue(self.h.app.gate.acquire(blocking=False))
        try:
            st, _, raw = self.post()
        finally:
            self.h.app.gate.release()
        self.assertEqual((st, json.loads(raw)["error"]["code"]), (409, "busy"))

    def test_default_sheet_unchanged(self):
        for q in ("", "?format=json", "?view=brief", "?start=0&end=3"):
            st, b = self.c.json("GET", "/sheet/%s%s" % (RID64, q))
            self.assertEqual(st, 200)
            self.assertEqual(b, self.h.app.sheet_view(RID64, server.parse_query(q[1:], ("format", "start", "end", "view"))))


@unittest.skipUnless(os.path.exists(AFCONVERT), "afconvert missing")
class RollModesTest(RollBase):
    def test_marks_page(self):
        st, h, raw = self.post("format=png&page=2&marks=1")
        self.assertEqual((st, h["content-type"], raw), (200, "image/png", PNG))
        a = self.fr.calls()[0]["argv"]
        self.assertEqual(a[4], "2")
        ev = [e for e in self.env.log_events() if e.get("event") == "roll_exit"]
        self.assertEqual((ev[-1]["mode"], ev[-1]["page"], ev[-1]["rc"]), ("--roll-marks", 2, 0))
        self.assertTrue(a[5].endswith("/roll.png") and a[6].endswith("/roll-marks.json"), a)
        self.assert_clean()
        self.fr.write(marks="{not json")
        self.assert_error(500, "roll_failed", "format=png&page=1&marks=1")

    def test_poster(self):
        title = "Demo \u00b7 \ub370\ubaa8"
        q = "format=png&style=poster&start=0.5&end=4&title=" + server.urllib.parse.quote(title) + "&credit=c"
        st, h, raw = self.post(q)
        self.assertEqual((st, h["content-type"], raw), (200, "image/png", PNG))
        a = self.fr.calls()[0]["argv"]
        self.assertEqual(a[2:4] + a[5:7] + a[8:], ["--poster", self.rhythm_file(), "0.500000", "4.000000", title, "c"])
        self.assert_clean()
        st, _, _ = self.post("format=png&style=poster&start=0&end=6")  # no title or credit: empty
        self.assertEqual((st, self.fr.calls()[-1]["argv"][-2:]), (200, ["", ""]))

    def test_poster_end_clamped_to_duration(self):  # duration 6.0; a rounded end up to 0.01 s past it is the end
        st, _, raw = self.post("format=png&style=poster&start=1&end=6.005")
        self.assertEqual((st, raw), (200, PNG))
        self.assertEqual(self.fr.calls()[-1]["argv"][5:7], ["1.000000", "6.000000"])
        self.assert_error(400, "invalid_argument", "format=png&style=poster&start=5.505&end=6.005")  # 0.495 s once clamped
        self.assert_error(400, "invalid_argument", "format=png&style=poster&start=1&end=6.02")
        self.assertEqual(len(self.fr.calls()), 1)

    def test_mode_errors(self):
        for q in ("format=png&page=1&marks=2", "format=png&page=1&marks=1&style=poster",
                  "format=png&style=poster&start=0", "format=png&style=poster&start=0&end=2&page=1",
                  "format=png&style=poster&start=-1&end=2", "format=png&style=movie&page=1",
                  "format=png&style=poster&start=0&end=2&title=" + "x" * 81,
                  "format=png&style=poster&start=0&end=2&credit=a%0Ab"):
            self.assert_error(400, "bad_request", q)
        for q in ("format=png&style=poster&start=0&end=6.5", "format=png&style=poster&start=1&end=1",
                  "format=png&style=poster&start=1&end=1.4", "format=png&style=poster&start=5&end=5.001"):  # < 0.5 s
            self.assert_error(400, "invalid_argument", q)
        self.assertEqual(self.fr.calls(), [])

    def test_no_rhythm_block(self):  # PR 61 review: rhythm None in the config, a stored result on disk
        self.h.app.cfg = dict(self.h.app.cfg, rhythm=None)
        self.assert_error(503, "roll_unavailable")
        with self.assertRaises(server.HttpError) as cm:
            self.h.app.roll_audio("/nonexistent", "/nonexistent", RHYTHM_OUT_V40)
        self.assertEqual(cm.exception.code, "roll_unavailable")


class NoRollTest(RollBase):
    roll = False

    def test_unavailable(self):
        self.assertFalse(self.c.json("GET", "/sheet/%s?format=png" % RID64)[1]["available"])
        self.assert_error(503, "roll_unavailable")


class RollConfigTest(unittest.TestCase):
    def test_roll_python_validated(self):
        env = Env()
        self.addCleanup(env.cleanup)
        fw = FakeWorker(env)
        rh = {"python": PY, "worker": fw.path}
        load = lambda **r: server.load_config(env.write_config(env.config(rhythm=dict(rh, **r))))  # noqa: E731
        self.assertEqual(load(roll_python=PY)["rhythm"]["roll_python"], PY)
        for bad, msg in (("/nonexistent/python", "rhythm roll_python missing"),
                         (fw.path + ".json", "rhythm roll_python missing")):
            with self.assertRaises(server.ConfigError) as cm:
                load(roll_python=bad)
            self.assertEqual(str(cm.exception), msg)


@unittest.skipUnless(os.path.exists(AFCONVERT), "afconvert missing")
class RollCliTest(RollBase):
    def cli(self, *args):
        p = subprocess.run([PY, "-I", os.path.join(REPO, "local_cli.py"), "--config", self.env.config_path, "sheet", RID64]
                           + list(args), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        return p.returncode, p.stdout, p.stderr

    def test_cli_png(self):
        wav, out = os.path.join(self.env.dir, "song.wav"), tempfile.mkdtemp(dir=self.env.dir)
        with open(wav, "wb") as f:
            f.write(AUDIO)
        rc, so, se = self.cli("--png", out, "--audio", wav)
        self.assertEqual(rc, 0, se)
        b = json.loads(so)
        self.assertEqual([dict(p, path=None) for p in b["pages"]],
                         [dict(p, path=None) for p in sheet.pages(RHYTHM_OUT_V40)])
        for p in b["pages"]:
            self.assertEqual(p["path"], os.path.join(out, "%s-p%02d.png" % (RID64[:12], p["page"])))
            with open(p["path"], "rb") as f:
                self.assertEqual(f.read(), PNG)
        self.assertEqual(len(self.fr.calls()), 3)
        self.assert_clean()
        rc, so, se = self.cli("--png", out, "--audio", wav, "--page", "1")  # never overwrites
        self.assertEqual((rc, json.loads(se)["error"]["code"]), (1, "invalid_path"))
        self.assert_clean()
        rc, so, se = self.cli("--png", out)
        self.assertEqual((rc, json.loads(se)["error"]["code"]), (1, "bad_request"))

    def test_cli_marks_and_poster(self):
        wav, out = os.path.join(self.env.dir, "song.wav"), tempfile.mkdtemp(dir=self.env.dir)
        with open(wav, "wb") as f:
            f.write(AUDIO)
        rc, so, se = self.cli("--png", out, "--audio", wav, "--page", "2", "--marks")
        self.assertEqual(rc, 0, se)
        b = json.loads(so)
        p, = b["pages"]
        self.assertEqual((p["page"], p["suspects"], p["missed"]), (2, [{"note": "A3", "flags": ["weak"]}], []))
        self.assertEqual((p["path"], p["marks_path"]), tuple(os.path.join(out, RID64[:12] + "-p02-marks" + e) for e in (".png", ".json")))
        with open(p["marks_path"]) as f:
            self.assertEqual(json.load(f)["suspects"], p["suspects"])
        self.assertIn("unvalidated", b["marks_note"])
        rc, so, se = self.cli("--png", out, "--audio", wav, "--style", "poster", "--start", "1", "--end", "5.5", "--title", "T")
        self.assertEqual(rc, 0, se)
        b = json.loads(so)["poster"]
        self.assertEqual((b["start"], b["end"], b["title"], b["credit"], b["path"]),
                         (1.0, 5.5, "T", "", os.path.join(out, RID64[:12] + "-poster-1-5.5.png")))
        self.assert_clean()
        for bad in (("--style", "poster", "--start", "1", "--end", "2", "--marks"), ("--start", "1", "--end", "2"),
                    ("--style", "poster", "--start", "1"), ("--title", "x")):
            rc, so, se = self.cli("--png", out, "--audio", wav, *bad)
            self.assertEqual((rc, json.loads(se)["error"]["code"]), (1, "bad_request"), bad)
        rc, so, se = self.cli("--png", out, "--audio", wav, "--style", "poster", "--start", "1", "--end", "1.2")
        self.assertEqual((rc, json.loads(se)["error"]["code"]), (1, "invalid_argument"))


@unittest.skipUnless(os.path.isfile(ROLL_PY), "unverified: set MUSIC_LENS_ROLL_PYTHON to a python with numpy and matplotlib")
class RealRollTest(unittest.TestCase):
    def test_render_page(self):
        d = tempfile.mkdtemp()
        res, wav = os.path.join(d, "r.json"), os.path.join(d, "a.wav")
        with open(res, "w") as f:
            json.dump(result(drum_hits=[{"time": 0.5, "class": "kick"}, {"time": 1.0, "class": "snare"}]), f)
        with open(wav, "wb") as f:
            f.write(make_wav_bytes(6.0, rate=44100, channels=2, freq=261.63))
        for page in (1, 3):
            out = os.path.join(d, "p%d.png" % page)
            p = subprocess.run([ROLL_PY, "-I", os.path.join(REPO, "roll.py"), res, wav, str(page), out],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
            self.assertEqual(p.returncode, 0, p.stderr)
            with open(out, "rb") as f:
                png = f.read()
            self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
            w, h = ihdr(png)
            self.assertTrue(1100 < w < 1700 and 550 < h < 900, (w, h))  # 14 x 7 in at 110 dpi, cropped
            self.assertFalse(os.path.exists(out + ".part"))
        p = subprocess.run([ROLL_PY, "-I", os.path.join(REPO, "roll.py"), res, wav, "1", out], stderr=subprocess.PIPE)
        self.assertEqual(p.returncode, 1)  # OUT exists: never overwritten
        p = subprocess.run([ROLL_PY, "-I", os.path.join(REPO, "roll.py"), res, wav, "4", out + "4"], stderr=subprocess.PIPE)
        self.assertEqual(p.returncode, 2)


SR = 22050


def synth(path, dur, tones=(), hits=()):
    """Mono 16-bit WAV: tones (midi, start, end, gates or None) with harmonics 1-4, noise bursts at hits (pure Python,
    so the test process needs no numpy)."""
    buf = [0.0] * int(dur * SR)
    for midi, a, b, gates in tones:
        f = 440 * 2 ** ((midi - 69) / 12)
        for i in range(int(a * SR), int(b * SR)):
            t = i / SR
            if gates:
                g = max([min(1.0, (t - x) / 0.005, (y - t) / 0.01) for x, y in gates if x <= t < y] or [0.0])
            else:
                g = min(1.0, (t - a) / 0.01, (b - t) / 0.02)
            buf[i] += 0.25 * g * sum(math.sin(2 * math.pi * f * k * t) / k for k in (1, 2, 3, 4))
    r = random.Random(1)
    for t0 in hits:
        for j in range(int(0.04 * SR)):
            if int(t0 * SR) + j < len(buf):
                buf[int(t0 * SR) + j] += 0.5 * (r.random() * 2 - 1) * math.exp(-j / (0.008 * SR))
    peak = max(abs(x) for x in buf) or 1.0
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(b"".join(struct.pack("<h", int(x / peak * 0.8 * 32767)) for x in buf))


def synth_result(dur, notes):
    return {"duration": dur, "beats": [i * 0.5 for i in range(1, int(dur * 2))], "bpm": 120.0,
            "sections": [{"start": 0.0, "end": dur, "label": "A"}], "key": {"tonic": "C", "scale": "major"},
            "chords": [], "melody_notes": [{"start": a, "end": b, "midi": m} for m, a, b in notes]}


@unittest.skipUnless(os.path.isfile(ROLL_PY), "unverified: set MUSIC_LENS_ROLL_PYTHON to a python with numpy and matplotlib")
class RealMarksTest(unittest.TestCase):
    """The prototype's three synthetic checks, through roll.py's marks mode (thresholds are unvalidated heuristics:
    these pin behaviour on clear cases only)."""

    def marks(self, tones, hits, notes, dur=6.0):
        d = tempfile.mkdtemp()
        res, wav = os.path.join(d, "r.json"), os.path.join(d, "a.wav")
        with open(res, "w") as f:
            json.dump(synth_result(dur, notes), f)
        synth(wav, dur, tones, hits)
        p = subprocess.run([ROLL_PY, "-I", os.path.join(REPO, "roll.py"), res, wav, "1", os.path.join(d, "p.png"),
                            os.path.join(d, "m.json")], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr)
        with open(os.path.join(d, "m.json")) as f:
            m = json.load(f)
        with open(os.path.join(d, "p.png"), "rb") as f:
            self.assertTrue(f.read(8) == b"\x89PNG\r\n\x1a\n")
        return m

    def test_sustained_tone_with_drums_not_flagged(self):
        m = self.marks([(57, 0.5, 5.0, None)], [i * 0.5 for i in range(12)], [(57, 0.5, 5.0)])
        self.assertEqual((m["suspects"], m["missed"]), ([], []))

    def test_nine_stabs_as_one_note(self):
        gates = [(0.5 + i * 0.5, 0.8 + i * 0.5) for i in range(9)]
        m = self.marks([(57, 0.5, 5.0, gates)], [], [(57, 0.3, 5.0)])
        self.assertEqual([(s["note"], s["flags"]) for s in m["suspects"]], [("A3", ["reattack9"])])

    def test_removed_note_is_missed(self):
        m = self.marks([(60, 0.5, 2.0, None), (57, 2.0, 3.5, None), (64, 3.5, 5.0, None)], [],
                       [(60, 0.5, 2.0), (64, 3.5, 5.0)])
        self.assertEqual(m["suspects"], [])
        self.assertEqual([x["note"] for x in m["missed"]], ["A3"])

    def test_semitone_off_note(self):  # written a semitone above the tone: the row below is stronger
        m = self.marks([(60, 0.5, 3.0, None), (67, 3.0, 5.0, None)], [], [(61, 0.5, 3.0), (67, 3.0, 5.0)])
        self.assertEqual([(s["note"], "semitone" in s["flags"]) for s in m["suspects"]], [("C#4", True)])

    def test_short_removed_note_is_missed(self):  # 0.25 s: under the old 0.4 s minimum
        m = self.marks([(60, 0.5, 2.0, None), (64, 2.3, 2.55, None), (60, 3.0, 5.0, None)], [],
                       [(60, 0.5, 2.0), (60, 3.0, 5.0)])
        self.assertEqual([x["note"] for x in m["missed"]], ["E4"])

    def test_chromatic_bass_passing_not_semitone(self):  # PR 64 review: low-band smear of written neighbours
        passing = [(36, 0.5, 1.5), (37, 1.5, 1.65), (38, 1.65, 2.65), (39, 2.65, 2.8), (36, 2.8, 3.8), (37, 3.8, 3.95),
                   (38, 3.95, 4.95), (39, 4.95, 5.1)]
        m = self.marks([(n, a, b, None) for n, a, b in passing], [], passing)
        self.assertEqual([s["flags"] for s in m["suspects"] if "semitone" in s["flags"]], [])

    def test_chromatic_bass_eighths_not_missed(self):  # PR 64 review: short low runs between written eighths
        eighths = [(n, 0.5 + i * 0.22, 0.72 + i * 0.22) for i, n in enumerate([36, 37, 38, 39, 40, 39, 38, 37] * 3)]
        m = self.marks([(n, a, b, None) for n, a, b in eighths], [], eighths)
        self.assertEqual(m["missed"], [])

    def test_ringing_tail_not_missed(self):  # the tone rings 0.25 s past the written end
        m = self.marks([(57, 0.5, 3.0, None), (64, 3.5, 5.0, None)], [], [(57, 0.5, 2.75), (64, 3.5, 5.0)])
        self.assertEqual(m["missed"], [])

    def poster(self, d, res, wav, start, end, title="", name="poster.png"):
        out = os.path.join(d, name)
        p = subprocess.run([ROLL_PY, "-I", os.path.join(REPO, "roll.py"), "--poster", res, wav, start, end, out, title,
                            "$5 or $6"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr)
        return out

    def test_poster_float_span_and_text(self):
        """2.02..32.02 is 30 s for the server (round(end - start, 6)); title and credit are plain text, not mathtext
        ("$\\foo$" used to raise at draw)."""
        d = tempfile.mkdtemp()
        res, wav = os.path.join(d, "r.json"), os.path.join(d, "a.wav")
        with open(res, "w") as f:
            json.dump(dict(result(), duration=33.0, sections=[]), f)
        with open(wav, "wb") as f:
            f.write(make_wav_bytes(33.0, rate=16000, freq=261.63))
        self.assertEqual(32.02 - 2.02 > 30.0, True)  # the float case itself
        self.poster(d, res, wav, "2.020000", "32.020000", title="$\\foo$ costs $5")

    def test_poster_drum_labels_only_with_drums(self):
        d = tempfile.mkdtemp()
        wav = os.path.join(d, "a.wav")
        with open(wav, "wb") as f:
            f.write(make_wav_bytes(6.0, rate=16000, freq=261.63))
        ink = []
        for name, hits in (("with", [{"time": 1.0, "class": "kick"}]), ("without", [])):
            res = os.path.join(d, name + ".json")
            with open(res, "w") as f:
                json.dump(dict(result(), drum_hits=hits), f)
            out = self.poster(d, res, wav, "0.5", "5.5", name=name + ".png")
            # the label column left of the plot, at the kick/snare rows (figure y 0.04-0.09, x 0.03-0.072)
            p = subprocess.run([ROLL_PY, "-I", "-c", "import sys, matplotlib.pyplot as p; im = p.imread(sys.argv[1]); "
                                "r = im[1229:1296, 72:173, :3]; print(float(abs(r - im[5, 5, :3]).max()))", out],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
            self.assertEqual(p.returncode, 0, p.stderr)
            ink.append(float(p.stdout))
        self.assertGreater(ink[0], 0.2)
        self.assertLess(ink[1], 0.02)

    def test_poster_size(self):
        d = tempfile.mkdtemp()
        res, wav, out = os.path.join(d, "r.json"), os.path.join(d, "a.wav"), os.path.join(d, "poster.png")
        with open(res, "w") as f:
            json.dump(dict(result(), drum_hits=[{"time": 0.5, "class": "kick"}, {"time": 1.0, "class": "snare"}]), f)
        with open(wav, "wb") as f:
            f.write(make_wav_bytes(6.0, rate=44100, freq=261.63))
        p = subprocess.run([ROLL_PY, "-I", os.path.join(REPO, "roll.py"), "--poster", res, wav, "0.5", "5.5", out,
                            "Demo \u00b7 \ub370\ubaa8", "credit"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr)
        with open(out, "rb") as f:
            self.assertEqual(ihdr(f.read(24)), (2400, 1350))
        for bad in (["0", "31"], ["0", "6.5"], ["1", "1.3"], ["0", "2", out + "2", "x" * 81, ""], ["0", "2", out + "3", "a\nb", ""]):
            args = bad if len(bad) == 5 else bad + [out + "4", "", ""]
            p = subprocess.run([ROLL_PY, "-I", os.path.join(REPO, "roll.py"), "--poster", res, wav] + args,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
            self.assertEqual(p.returncode, 2, bad)


class GridTest(unittest.TestCase):
    """Page bar lines: at the stored downbeats when the bar layer is done, else every 4th beat with a page note."""

    def test_three_four_downbeats(self):
        bars = [{"start": 1.0 + 1.5 * i, "end": 2.5 + 1.5 * i} for i in range(4)]  # 3/4 at 120 BPM from 1.0 s
        v = {"beats": [1.0 + 0.5 * i for i in range(13)],
             "bars": {"status": "done", "beats_per_bar": 3, "rows": bars}}
        lines, note = roll.grid(v, 0.0, 10.0)
        self.assertEqual([t for t, bar in lines if bar], [1.0, 2.5, 4.0, 5.5, 7.0])
        self.assertEqual(len([t for t, bar in lines if not bar]), 8)  # the two inner beats of each bar
        self.assertEqual(note, "bar lines at downbeats (3 beats per bar)")
        self.assertEqual([t for t, bar in roll.grid(v, 3.0, 6.0)[0] if bar], [4.0, 5.5])

    def test_no_downbeats_fallback_note(self):
        v = {"beats": [0.5 * i for i in range(1, 13)], "bars": {"status": "not_provided"}}
        lines, note = roll.grid(v, 0.0, 10.0)
        self.assertEqual([t for t, bar in lines if bar], [0.5, 2.5, 4.5])
        self.assertIn("no downbeats", note)
        self.assertEqual([t for t, bar in roll.grid(v, 1.2, 10.0)[0] if bar], [2.5, 4.5])  # counted from the song start
        self.assertIn("no downbeats", roll.grid({"beats": []}, 0.0, 1.0)[1])

    def test_bars_ending_before_page(self):  # older results: the bar layer stops short of the song's end
        v = {"beats": [0.5 * i for i in range(1, 41)],
             "bars": {"status": "done", "beats_per_bar": 4, "rows": [{"start": 0.5, "end": 2.5}, {"start": 2.5, "end": 4.5}]}}
        lines, note = roll.grid(v, 10.0, 20.0)
        self.assertEqual([t for t, bar in lines if bar], [10.5, 12.5, 14.5, 16.5, 18.5])
        self.assertEqual(note, "beats counted from the first beat (every 4th darker); no downbeats on this page")
        self.assertEqual(roll.grid(v, 0.0, 5.0)[1], "bar lines at downbeats (4 beats per bar)")


@unittest.skipUnless(os.path.isfile(ROLL_PY), "unverified: set MUSIC_LENS_ROLL_PYTHON to a python with numpy and matplotlib")
class RealLowToneTest(unittest.TestCase):
    def test_semitone_apart_low_tones_split(self):
        """A1 (55 Hz) and A#1 (58.27 Hz) together: two peak rows a semitone apart with a dip between them (the single
        8192-point STFT gave one merged peak near 34.2)."""
        d = tempfile.mkdtemp()
        wav = os.path.join(d, "a.wav")
        with wave.open(wav, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(b"".join(struct.pack("<h", int(12000 * (math.sin(2 * math.pi * 55 * i / SR) +
                                                                     math.sin(2 * math.pi * 58.27 * i / SR))))
                                   for i in range(4 * SR)))
        code = ("import sys, json, numpy as np; sys.path.insert(0, sys.argv[1]); import roll; "
                "t, r, db = roll.spectrogram(np, sys.argv[2], 1.0, 3.0); p = db.mean(1); s = (r >= 31) & (r <= 36); "
                "r, p = r[s], p[s]; print(json.dumps({'peaks': [round(float(r[i]), 2) for i in range(1, len(r) - 1) "
                "if p[i] >= p[i - 1] and p[i] >= p[i + 1]], 'dip': float(p.max() - p[(r > 33.2) & (r < 33.8)].min())}))")
        p = subprocess.run([ROLL_PY, "-I", "-c", code, REPO, wav], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        out = json.loads(p.stdout)
        self.assertEqual(out["peaks"], [33.0, 34.0])
        self.assertGreater(out["dip"], 2.0)


if __name__ == "__main__":
    unittest.main()
