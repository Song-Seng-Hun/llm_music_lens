import json
import os
import threading
import time
import unittest

from helpers import Base, Client, build, connect, read_response, sign

UNAUTH = {"error": {"code": "unauthorized"}}


class AuthTest(Base):
    def test_status_ok_and_passthrough(self):
        st, h, b = self.c.send("GET", "/status")
        self.assertEqual(st, 200)
        self.assertEqual(b, b'{"tool_version":"fake","engine":"x"}')
        self.assertEqual(h["connection"], "close")
        self.assertEqual(int(h["content-length"]), len(b))

    def test_bad_sig(self):
        h = self.c.signed("GET", "/status")
        h["X-ML-Sig"] = ("0" if h["X-ML-Sig"][0] != "0" else "1") + h["X-ML-Sig"][1:]
        st, b = self.c.json("GET", "/status", headers=h)
        self.assertEqual((st, b), (401, UNAUTH))

    def test_wrong_key_and_unknown_client(self):
        st, b = self.c.json("GET", "/status", key=b"\x01" * 32)
        self.assertEqual((st, b), (401, UNAUTH))
        st, b = self.c.json("GET", "/status", client="nobody", key=self.c.key)
        self.assertEqual((st, b), (401, UNAUTH))

    def test_signature_covers_target_and_method(self):
        h = self.c.signed("GET", "/status")
        st, b = self.c.json("GET", "/report/" + "ab" * 32, headers=h)
        self.assertEqual(st, 401)

    def test_skew(self):
        now = int(time.time())
        for t in (now - 125, now + 125):
            st, b = self.c.json("GET", "/status", time_s=t)
            self.assertEqual((st, b), (401, UNAUTH))
        st, _ = self.c.json("GET", "/status", time_s=now - 100)
        self.assertEqual(st, 200)

    def test_time_grammar(self):
        h = self.c.signed("GET", "/status")
        for bad in ("+" + h["X-ML-Time"], "0" + h["X-ML-Time"], h["X-ML-Time"] + ".0", "-1"):
            hh = dict(h, **{"X-ML-Time": bad})
            st, _ = self.c.json("GET", "/status", headers=hh)
            self.assertEqual(st, 400, bad)
        hh = dict(h, **{"X-ML-Nonce": h["X-ML-Nonce"].upper()})
        self.assertEqual(self.c.json("GET", "/status", headers=hh)[0], 400)

    def test_replay(self):
        h = self.c.signed("GET", "/status")
        self.assertEqual(self.c.json("GET", "/status", headers=h)[0], 200)
        self.assertEqual(self.c.json("GET", "/status", headers=h), (401, UNAUTH))

    def test_same_nonce_other_client_is_distinct(self):
        n = "f" * 32
        self.assertEqual(self.c.json("GET", "/status", nonce=n)[0], 200)
        other = Client(self.env, self.h.port, "codex-window")
        self.assertEqual(other.json("GET", "/status", nonce=n)[0], 200)

    def test_concurrent_nonce(self):
        h = self.c.signed("GET", "/status")
        results = []

        def go():
            results.append(self.c.json("GET", "/status", headers=h)[0])
        ts = [threading.Thread(target=go) for _ in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(sorted(results), [200] + [401] * 7)

    def test_replay_after_restart_incl_future_time(self):
        h1 = self.c.signed("GET", "/status")
        h2 = self.c.signed("GET", "/status", time_s=int(time.time()) + 110)
        self.assertEqual(self.c.json("GET", "/status", headers=h1)[0], 200)
        self.assertEqual(self.c.json("GET", "/status", headers=h2)[0], 200)
        self.restart()
        self.assertEqual(self.c.json("GET", "/status", headers=h1), (401, UNAUTH))
        self.assertEqual(self.c.json("GET", "/status", headers=h2), (401, UNAUTH))
        # nonce log is a private, durable file
        p = os.path.join(self.env.root, "api", "nonces.log")
        self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(os.path.dirname(p)).st_mode & 0o777, 0o700)

    def test_expired_entries_dropped_at_start(self):
        p = os.path.join(self.env.root, "api", "nonces.log")
        with open(p, "a") as f:
            f.write(json.dumps({"c": "claude-window", "n": "1" * 32, "e": 1}) + "\n")
            f.write("{torn")
        self.restart()
        with open(p) as f:
            text = f.read()
        self.assertNotIn("1" * 32, text)
        self.assertNotIn("{torn", text)

    def test_get_with_body_hash_of_nonempty(self):
        # GET must sign the empty-body hash
        key = self.c.key
        now = int(time.time())
        nonce = "2" * 32
        bh = sign.hashlib.sha256(b"x").hexdigest()
        canon = sign.canonical("claude-window", "GET", "/status", "", 0, now, nonce, bh)
        h = {"X-ML-Client": "claude-window", "X-ML-Time": str(now), "X-ML-Nonce": nonce,
             "X-ML-Body-SHA256": bh, "X-ML-Sig": sign.signature(key, canon)}
        self.assertEqual(self.c.json("GET", "/status", headers=h), (401, UNAUTH))


class NonceFullTest(Base):
    limits = {"nonce_cap": 3}

    def test_store_full(self):
        for _ in range(3):
            self.assertEqual(self.c.json("GET", "/status")[0], 200)
        st, b = self.c.json("GET", "/status")
        self.assertEqual(st, 503)
        self.assertEqual(b["error"]["code"], "nonce_store_full")
        # a request that fails auth never reaches the store
        self.assertEqual(self.c.json("GET", "/status", key=b"\x02" * 32)[0], 401)


class ShortSkewTest(Base):
    limits = {"skew": 2}

    def test_expired_nonce_evicted_only_after_expiry(self):
        n = "3" * 32
        self.assertEqual(self.c.json("GET", "/status", nonce=n)[0], 200)
        self.assertEqual(self.c.json("GET", "/status", nonce=n)[0], 401)
        time.sleep(3.2)
        self.assertEqual(self.c.json("GET", "/status", nonce=n)[0], 200)


if __name__ == "__main__":
    unittest.main()
