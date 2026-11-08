"""Sheet PNG pages: sheet.pages, GET /sheet/<id>?format=png (page index), POST /sheet/<id>?format=png&page=N with the
analysed audio (roll.py under limit_exec.py --roll, faked here: the test python has no matplotlib), local_cli.py sheet
--png. The real renderer runs only with MUSIC_LENS_ROLL_PYTHON (numpy + matplotlib); without it those tests are skipped
and the image itself is unverified."""
import json
import os
import struct
import subprocess
import tempfile
import unittest

from helpers import PY, REPO, RID64, Client, Env, Harness, make_wav_bytes, server
from test_earcheck_route import AFCONVERT
from test_rhythm import RHYTHM_OUT_V40, FakeWorker, RhythmBase

import limit_exec  # noqa: E402

sheet = server.sheet
ROLL_PY = os.environ.get("MUSIC_LENS_ROLL_PYTHON", "")
AUDIO = make_wav_bytes(6.0)  # the fixture result's duration
PNG = b"\x89PNG\r\n\x1a\n" + b"fake-png" * 10

# Stands in for roll_python: limit_exec execs it as PYTHON -I roll.py RESULT SRC PAGE OUT.
FAKE_PY = r'''#!/usr/bin/python3 -I
import json, os, resource, struct, sys
HERE = os.path.dirname(os.path.realpath(__file__))
with open(os.path.join(HERE, "control.json")) as f:
    ctl = json.load(f)
args = sys.argv[1:]
with open(args[3], "rb") as f:
    head = f.read(64)
with open(os.path.join(HERE, "calls.log"), "a") as f:
    f.write(json.dumps({"argv": args, "src_rate": struct.unpack("<I", head[24:28])[0], "cwd": os.getcwd(),
                        "fsize": resource.getrlimit(resource.RLIMIT_FSIZE)[0]}) + "\n")
if ctl.get("partial"):
    open(args[-1], "wb").write(b"\x89PNG\r\n")
if ctl.get("exit"):
    sys.exit(ctl["exit"])
open(args[-1], "wb").write(ctl.get("png", "").encode("latin-1"))
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


if __name__ == "__main__":
    unittest.main()
