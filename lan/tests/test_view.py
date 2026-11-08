"""Sheet HTML (one offline page per song): POST /sheet/<id>?format=html[&marks=1] with the analysed audio (afconvert to
AAC under limit_exec.py --aac, then view.py under limit_exec.py --view, faked here: the test python has no numpy),
local_cli.py sheet --html, and view.py itself with MUSIC_LENS_ROLL_PYTHON (skipped without it). The page's JavaScript
(playback, seeking, toggles, hover) is not unit-tested."""
import json
import os
import re
import subprocess
import tempfile
import unittest
import wave

from helpers import PY, REPO, RID64, make_wav_bytes, server
from test_earcheck_route import AFCONVERT
from test_roll import AUDIO, ROLL_PY, RollBase, synth, synth_result

import limit_exec  # noqa: E402

HTML = "<!doctype html>\n<p>fake view</p>\n"

# Stands in for roll_python running view.py: PYTHON -I view.py RESULT SRC AAC OUT [--marks] (args[0] is -I)
FAKE_VIEW = r'''#!/usr/bin/python3 -I
import json, os, resource, sys
HERE = os.path.dirname(os.path.realpath(__file__))
with open(os.path.join(HERE, "control.json")) as f:
    ctl = json.load(f)
args = sys.argv[1:]
with open(args[4], "rb") as f:
    aac = f.read(12)
with open(os.path.join(HERE, "calls.log"), "a") as f:
    f.write(json.dumps({"argv": args, "aac_ftyp": aac[4:8].decode("latin-1"), "cwd": os.getcwd(),
                        "fsize": resource.getrlimit(resource.RLIMIT_FSIZE)[0],
                        "cpu": resource.getrlimit(resource.RLIMIT_CPU)[0]}) + "\n")
if ctl.get("exit"):
    sys.exit(ctl["exit"])
open(args[5], "w").write(ctl.get("html", ""))
'''


class ViewBase(RollBase):
    def setUp(self):
        super().setUp()
        with open(self.fr.path, "w") as f:
            f.write(FAKE_VIEW)
        self.view(html=HTML)

    def view(self, **ctl):
        with open(os.path.join(self.fr.dir, "control.json"), "w") as f:
            json.dump(ctl, f)


@unittest.skipUnless(os.path.exists(AFCONVERT), "afconvert missing")
class ViewRouteTest(ViewBase):
    def test_html(self):
        for q, marks in (("format=html", False), ("format=html&marks=1", True)):
            st, h, raw = self.post(q)
            self.assertEqual(st, 200, raw[:300])
            self.assertEqual((h["content-type"], raw.decode()), ("text/html; charset=utf-8", HTML))
            call = self.fr.calls()[-1]
            self.assertEqual(call["argv"][:3], ["-I", os.path.join(REPO, "view.py"), self.rhythm_file()])
            self.assertRegex(call["argv"][3], r"/uploads/rl-[0-9a-f]{32}/listen\.wav\Z")
            self.assertTrue(call["argv"][4].endswith("/view.m4a") and call["argv"][5].endswith("/view.html"))
            self.assertEqual(call["argv"][6:], ["--marks"] if marks else [])
            self.assertEqual(call["aac_ftyp"], "ftyp")  # afconvert wrote an M4A
            self.assertEqual((call["cwd"], call["fsize"], call["cpu"]),
                             ("/", limit_exec.VIEW_FSIZE_BYTES, limit_exec.VIEW_CPU_SECONDS))
            self.assert_clean()
        self.assertEqual(self.env.argv_log(), [])  # no ml call

    def test_bad_requests(self):
        for q in ("format=html&page=1", "format=html&marks=2", "format=html&style=poster", "format=html&start=0"):
            self.assert_error(400, "bad_request", q)
        st, b = self.c.json("GET", "/sheet/%s?format=html" % RID64)
        self.assertEqual((st, b["error"]["code"], b["error"]["detail"]), (400, "bad_request", server.HTML_DETAIL))
        self.assert_error(400, "invalid_argument", "format=html", make_wav_bytes(4.0))  # not this result's audio
        self.assertEqual(self.fr.calls(), [])

    def test_render_failure_is_never_a_page(self):
        self.view(exit=1)
        self.assert_error(500, "roll_failed", "format=html")
        self.view(html="<p>no doctype</p>")
        self.assert_error(500, "roll_failed", "format=html")

    def test_large_page_passes_response_max(self):  # the page carries the audio: its own cap, not response_max
        big = "<!doctype html>" + "x" * (self.h.app.limits["response_max"] + 10)
        self.view(html=big)
        st, _, raw = self.post("format=html")
        self.assertEqual((st, len(raw)), (200, len(big)))
        self.assert_clean()

    def test_default_png_unchanged(self):
        with open(self.fr.path, "w") as f:  # back to the PNG fake
            f.write(__import__("test_roll").FAKE_PY)
        self.fr.write()
        st, h, raw = self.post("format=png&page=1")
        self.assertEqual((st, h["content-type"]), (200, "image/png"))


class NoViewTest(ViewBase):
    roll = False

    @unittest.skipUnless(os.path.exists(AFCONVERT), "afconvert missing")
    def test_unavailable(self):
        self.assert_error(503, "roll_unavailable", "format=html")


@unittest.skipUnless(os.path.exists(AFCONVERT), "afconvert missing")
class ViewCliTest(ViewBase):
    def cli(self, *args):
        p = subprocess.run([PY, "-I", os.path.join(REPO, "local_cli.py"), "--config", self.env.config_path, "sheet", RID64]
                           + list(args), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        return p.returncode, p.stdout, p.stderr

    def test_cli_html(self):
        wav, out = os.path.join(self.env.dir, "song.wav"), tempfile.mkdtemp(dir=self.env.dir)
        with open(wav, "wb") as f:
            f.write(AUDIO)
        rc, so, se = self.cli("--html", out, "--audio", wav, "--marks")
        self.assertEqual(rc, 0, se)
        b = json.loads(so)
        path = os.path.join(out, RID64[:12] + ".html")
        self.assertEqual({k: b[k] for k in ("result_id", "format", "path", "bytes", "marks")},
                         {"result_id": RID64, "format": "html", "path": path, "bytes": len(HTML), "marks": True})
        self.assertIn("unvalidated", b["marks_note"])
        with open(path) as f:
            self.assertEqual(f.read(), HTML)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o644)
        self.assertEqual(self.fr.calls()[-1]["argv"][6:], ["--marks"])
        self.assert_clean()
        rc, so, se = self.cli("--html", out, "--audio", wav)  # never overwrites
        self.assertEqual((rc, json.loads(se)["error"]["code"]), (1, "invalid_path"))
        for extra in (["--page", "1"], ["--png", out], []):
            args = ["--html", out] + (["--audio", wav] if extra else []) + extra
            rc, so, se = self.cli(*args)
            self.assertEqual((rc, json.loads(se)["error"]["code"]), (1, "bad_request"), extra)
        self.assertEqual(len(self.fr.calls()), 1)
        self.assert_clean()


class LimitExecViewTest(unittest.TestCase):
    def test_view_modes(self):
        a = ["/p/py", "/p/view.py", "/p/r.json", "/p/l.wav", "/p/a.m4a", "/p/o.html"]
        self.assertEqual(limit_exec.view_command(["x", "--view"] + a), ["/p/py", "-I"] + a[1:])
        self.assertEqual(limit_exec.view_command(["x", "--view-marks"] + a), ["/p/py", "-I"] + a[1:] + ["--marks"])
        self.assertEqual(limit_exec.view_command(["x", "--view"] + a[:-1]), ())
        self.assertEqual(limit_exec.view_command(["x", "--view"] + a[:-1] + ["rel.html"]), ())
        self.assertIsNone(limit_exec.view_command(["x", "--roll"] + a))
        self.assertEqual(limit_exec.aac_command("/a", "/b")[:6], [limit_exec.AFCONVERT, "-f", "m4af", "-d", "aac@44100", "-b"])

    @unittest.skipUnless(os.path.exists(AFCONVERT), "afconvert missing")
    def test_aac_any_channels(self):  # PR 65 review: a 6-channel 48 kHz WAV failed with the channels kept
        d = tempfile.mkdtemp()
        for ch, rate in ((6, 48000), (1, 16000), (2, 44100)):
            src, dst = os.path.join(d, "in%d.wav" % ch), os.path.join(d, "out%d.m4a" % ch)
            with open(src, "wb") as f:
                f.write(make_wav_bytes(1.0, rate=rate, channels=ch))
            p = subprocess.run([PY, "-I", os.path.join(REPO, "limit_exec.py"), "--aac", src, dst], stderr=subprocess.PIPE)
            self.assertEqual(p.returncode, 0, (ch, p.stderr))
            with open(dst, "rb") as f:
                self.assertEqual(f.read(8)[4:], b"ftyp")


EVIL = "</script><img src=x onerror=alert(1)>& "


@unittest.skipUnless(os.path.isfile(ROLL_PY) and os.path.exists(AFCONVERT),
                     "unverified: set MUSIC_LENS_ROLL_PYTHON to a python with numpy, matplotlib and Pillow")
class RealViewTest(unittest.TestCase):
    """view.py on a synthetic song: the file's structure and safety, not its look or its JavaScript."""

    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()
        dur = 6.0
        v = synth_result(dur, [(60, 0.5, 2.0), (64, 3.5, 5.0)])
        v["chords"] = [{"start": 0.0, "end": 3.0, "label": EVIL, "display": EVIL}]
        v["sections"] = [{"start": 0.0, "end": dur, "label": EVIL, "function": EVIL}]
        cls.res, cls.wav, cls.aac = (os.path.join(cls.d, n) for n in ("r.json", "a.wav", "a.m4a"))
        with open(cls.res, "w") as f:
            json.dump(v, f)
        synth(cls.wav, dur, [(60, 0.5, 2.0, None), (57, 2.0, 3.5, None), (64, 3.5, 5.0, None)])
        subprocess.run([AFCONVERT, "-f", "m4af", "-d", "aac@44100", "-b", "96000", cls.wav, cls.aac], check=True)

    def build(self, *extra, name="v.html", code=None):
        out = os.path.join(self.d, name)
        argv = [ROLL_PY, "-I", os.path.join(REPO, "view.py"), self.res, self.wav, self.aac, out] + list(extra)
        if code:
            argv = [ROLL_PY, "-I", "-c", code, REPO] + argv[3:]
        p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        return p, out

    def data(self, html):
        m = re.search(r'<script type="application/json" id="data">(.*?)</script>', html, re.S)
        return json.loads(m.group(1))

    def test_page(self):
        p, out = self.build("--marks", name="m.html")
        self.assertEqual(p.returncode, 0, p.stderr)
        with open(out, encoding="utf-8") as f:
            html = f.read()
        self.assertTrue(html.startswith("<!doctype html>"))
        csp = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]*)">', html).group(1)
        self.assertIn("default-src 'none'", csp)
        self.assertIn("img-src data:", csp)
        self.assertIn("media-src data:", csp)
        self.assertEqual(html.count("<script"), 2)  # the JSON block and the one inline script, no src
        rest = html.replace(re.search(r"<meta http-equiv[^>]*>", html).group(0), "")
        rest = re.sub(r"[A-Za-z0-9+/=]{200,}", "", rest)  # base64 tiles and audio can hold any 4 letters
        self.assertNotIn("http", rest.lower())
        markup = re.sub(r'id="data">.*?</script>', "", rest, flags=re.S)
        self.assertNotRegex(markup, r"<[^>]*\b(src|href)=")  # no tag loads anything; images and audio come from the JSON
        block = re.search(r'id="data">(.*?)</script>', html, re.S).group(1)
        for ch in ("<", ">", "&", " "):
            self.assertNotIn(ch, block)
        d = self.data(html)
        self.assertEqual((d["chords"][0]["l"], d["sections"][0]["l"]), (EVIL[:16], EVIL[:24]))
        self.assertTrue(d["audio"].startswith("data:audio/mp4;base64,"))
        self.assertTrue(d["tiles"] and all(t["src"].startswith("data:image/jpeg;base64,") for t in d["tiles"]))
        self.assertEqual([n["m"] for n in d["lanes"]["melody"]["notes"]], [60, 64])
        self.assertEqual([m["note"] for m in d["marks"]["missed"]], ["A3"])
        self.assertIn("marks: unvalidated heuristics, not accuracy", d["notes"])
        self.assertNotIn("innerHTML", html)
        self.assertEqual(os.stat(out).st_mode & 0o777, 0o644)

    def test_plain_and_no_overwrite(self):
        p, out = self.build(name="p.html")
        self.assertEqual(p.returncode, 0, p.stderr)
        with open(out, encoding="utf-8") as f:
            d = self.data(f.read())
        self.assertIsNone(d["marks"])
        self.assertNotIn("marks: unvalidated heuristics, not accuracy", d["notes"])
        p, _ = self.build(name="p.html")
        self.assertEqual(p.returncode, 2)

    def test_size_cap(self):
        code = "import sys; sys.path.insert(0, sys.argv[1]); import view; view.SIZE_MAX = 1000; sys.exit(view.main(sys.argv[1:]))"
        p, out = self.build(name="big.html", code=code)
        self.assertEqual(p.returncode, 1)
        self.assertIn(b"over 1000 bytes", p.stderr)
        self.assertFalse(os.path.lexists(out) or os.path.lexists(out + ".part"))


if __name__ == "__main__":
    unittest.main()
