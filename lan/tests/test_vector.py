import json
import os
import shutil
import subprocess
import unittest

from helpers import REPO, sign

NODE = os.environ.get("NODE") or shutil.which("node") or ""


class VectorTest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(REPO, "tests", "vectors.json"), encoding="utf-8") as f:
            self.want = json.load(f)

    def test_python_matches_frozen_vector(self):
        got = sign.vector_values()
        for k in ("post_target", "post_canonical_hex", "post_sig", "get_canonical_hex", "get_sig"):
            self.assertEqual(got[k], self.want[k], k)
        # Korean label percent-encoded as UTF-8 in the target
        self.assertIn("name=%ED%85%8C%EC%8A%A4%ED%8A%B8%20%EA%B3%A1", got["post_target"])
        # canonical: 8 LF-joined fields, no trailing LF; GET uses empty CT, 0 length, empty-body hash
        canon_get = bytes.fromhex(got["get_canonical_hex"]).decode()
        self.assertEqual(canon_get.split("\n"), ["claude-window", "GET", "/status", "", "0", "1793000000",
                                                 "0123456789abcdef0123456789abcdef", sign.EMPTY_SHA256])
        self.assertFalse(canon_get.endswith("\n"))

    @unittest.skipUnless(os.path.exists(NODE), "node not present")
    def test_node_matches_python(self):
        out = subprocess.run([NODE, os.path.join(REPO, "sign_vector.mjs")], check=True, stdout=subprocess.PIPE)
        node = json.loads(out.stdout.decode("utf-8"))
        self.assertEqual(node, sign.vector_values())
        chk = subprocess.run([NODE, os.path.join(REPO, "sign_vector.mjs"), "--check",
                              os.path.join(REPO, "tests", "vectors.json")], stdout=subprocess.PIPE)
        self.assertEqual(chk.returncode, 0, chk.stdout)

    def test_key_format(self):
        good = "a" * 64
        self.assertEqual(sign.parse_key_text(good), bytes.fromhex(good))
        self.assertEqual(sign.parse_key_text(good + "\n"), bytes.fromhex(good))
        for bad in (good + "\n\n", "A" * 64, "a" * 63, good + " ", good + "\r\n", "\n" + good):
            with self.assertRaises(sign.KeyFormatError):
                sign.parse_key_text(bad)


if __name__ == "__main__":
    unittest.main()
