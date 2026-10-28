import os
import socket
import ssl
import time
import unittest

from helpers import Base, Client, build, connect, read_response, server, sign, silent_wav_bytes

WAV = silent_wav_bytes(0.5)
A = "/analyze?language=en&emotion_mode=voice"


class FramingTest(Base):
    def code(self, st_b):
        st, b = st_b
        return st, (b or {}).get("error", {}).get("code")

    def test_duplicate_x_ml_header(self):
        h = self.c.signed("GET", "/status")
        st, b = self.c.json("GET", "/status", headers=h, extra=[("X-ML-Nonce", h["X-ML-Nonce"])])
        self.assertEqual((st, b["error"]["code"]), (400, "bad_request"))

    def test_missing_x_ml_header(self):
        st, _ = self.c.json("GET", "/status", drop=("X-ML-Sig",))
        self.assertEqual(st, 400)

    def test_duplicate_content_length(self):
        h = self.c.signed("POST", A, WAV)
        st, _ = self.c.json("POST", A, WAV, headers=h, extra=[("Content-Length", str(len(WAV)))])
        self.assertEqual(st, 400)

    def test_non_decimal_content_length(self):
        h = self.c.signed("POST", A, WAV)
        for bad in ("+%d" % len(WAV), "0%d" % len(WAV), "%d " % len(WAV) + "x", "0x10"):
            hh = dict(h, **{"Content-Length": bad})
            self.assertEqual(self.c.json("POST", A, WAV, headers=hh)[0], 400, bad)

    def test_transfer_encoding(self):
        h = self.c.signed("POST", A, WAV)
        del h["Content-Length"]
        chunked = ("%x\r\n" % len(WAV)).encode() + WAV + b"\r\n0\r\n\r\n"
        st, _ = self.c.json("POST", A, headers=h, extra=[("Transfer-Encoding", "chunked")], raw_body=chunked)
        self.assertEqual(st, 400)
        self.assertEqual(self.env.argv_log(), [])

    def test_cl_plus_te(self):
        h = self.c.signed("POST", A, WAV)
        st, _ = self.c.json("POST", A, WAV, headers=h, extra=[("Transfer-Encoding", "identity")])
        self.assertEqual(st, 400)
        self.assertEqual(self.env.uploads_entries(), [])

    def test_content_encoding(self):
        h = self.c.signed("POST", A, WAV)
        self.assertEqual(self.c.json("POST", A, WAV, headers=h, extra=[("Content-Encoding", "gzip")])[0], 400)
        st, _ = self.c.json("POST", A, WAV, extra=[("Content-Encoding", "identity")])
        self.assertEqual(st, 200)

    def test_post_without_length_and_wrong_type(self):
        h = self.c.signed("POST", A, WAV)
        del h["Content-Length"]
        self.assertEqual(self.c.json("POST", A, headers=h, raw_body=b"")[0], 400)
        h = self.c.signed("POST", A, WAV, content_type="audio/wav")
        self.assertEqual(self.c.json("POST", A, WAV, headers=h)[0], 400)

    def test_get_with_body(self):
        h = self.c.signed("GET", "/status")
        self.assertEqual(self.c.json("GET", "/status", headers=h, extra=[("Content-Length", "3")], raw_body=b"abc")[0], 400)
        h = self.c.signed("GET", "/status")
        self.assertEqual(self.c.json("GET", "/status", headers=h, raw_body=b"abc")[0], 400)
        h = self.c.signed("GET", "/status")
        self.assertEqual(self.c.json("GET", "/status", headers=h, extra=[("Content-Length", "0")])[0], 200)

    def test_oversize_413_after_auth(self):
        big = 64 * 1024 * 1024 + 1
        h = self.c.signed("POST", A, b"")
        canon = sign.canonical("claude-window", "POST", A, "application/octet-stream", big, h["X-ML-Time"],
                               h["X-ML-Nonce"], "0" * 64)
        h.update({"Content-Length": str(big), "X-ML-Body-SHA256": "0" * 64, "X-ML-Sig": sign.signature(self.c.key, canon)})
        st, b = self.c.json("POST", A, headers=h, raw_body=b"")
        self.assertEqual((st, b["error"]["code"]), (413, "payload_too_large"))
        # unauthenticated oversize gets 401, not 413
        h2 = dict(h, **{"X-ML-Nonce": "9" * 32})
        self.assertEqual(self.c.json("POST", A, headers=h2, raw_body=b"")[0], 401)
        self.assertEqual(self.env.uploads_entries(), [])

    def test_truncated_body_deletes_staging(self):
        h = self.c.signed("POST", A, WAV)
        s = connect(self.h.port, self.env.cert)
        s.sendall(build("POST", A, list(h.items()), WAV[:100]))
        time.sleep(0.3)
        s.close()  # premature EOF
        rid = h["X-ML-Nonce"]
        r = self.wait_receipt(rid, "failed")
        self.assertEqual(r["error_code"], "truncated_body")
        self.assertEqual(self.env.uploads_entries(), [])
        self.assertEqual(self.env.argv_log(), [])

    def test_body_hash_mismatch(self):
        h = self.c.signed("POST", A, WAV)
        bad = WAV[:-1] + b"\x01"
        st, b = self.c.json("POST", A, headers=h, raw_body=bad)
        self.assertEqual((st, b), (401, {"error": {"code": "unauthorized"}}))
        self.assertEqual(self.env.uploads_entries(), [])
        self.assertEqual(self.env.argv_log(), [])

    def test_header_limits(self):
        h = self.c.signed("GET", "/status")
        many = [("X-Pad-%d" % i, "v") for i in range(40)]
        self.assertEqual(self.c.send("GET", "/status", headers=h, extra=many)[0], 431)
        h = self.c.signed("GET", "/status")
        self.assertEqual(self.c.send("GET", "/status", headers=h, extra=[("X-Pad", "v" * 17000)])[0], 431)
        long_target = "/timeline/" + "ab" * 32 + "?layer=words&" + "start=0&" * 260
        st, _, _ = self.c.send("GET", long_target[:2100])
        self.assertEqual(st, 414)

    def test_bad_header_lines(self):
        h = self.c.signed("GET", "/status")
        self.assertEqual(self.c.send("GET", "/status", headers=h, extra=[(" Folded", "x")])[0], 400)
        h = self.c.signed("GET", "/status")
        self.assertEqual(self.c.send("GET", "/status", headers=h, extra=[("X-Bin", "é")])[0], 400)

    def test_expect(self):
        st, b = self.c.json("POST", A, WAV, extra=[("Expect", "100-continue")])
        self.assertEqual(st, 200)
        st, b = self.c.json("POST", A, WAV, extra=[("Expect", "something")])
        self.assertEqual(st, 417)

    def test_expect_continue_only_after_auth(self):
        h = self.c.signed("POST", A, WAV)
        h["X-ML-Sig"] = "0" * 64
        s = connect(self.h.port, self.env.cert)
        s.sendall(build("POST", A, list(h.items()) + [("Expect", "100-continue")]))
        s.settimeout(5)
        data = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
        s.close()
        self.assertTrue(data.startswith(b"HTTP/1.1 401"), data[:40])

    def test_method_and_route(self):
        self.assertEqual(self.c.json("POST", "/status", b"x")[0], 405)
        self.assertEqual(self.c.json("GET", A)[0], 405)
        self.assertEqual(self.c.json("GET", "/nope")[0], 404)
        self.assertEqual(self.c.json("GET", "/report/" + "AB" * 32)[0], 404)
        self.assertEqual(self.c.json("GET", "/status?x=1")[0], 400)
        self.assertEqual(self.c.send("DELETE", "/status", headers={})[0], 405)

    def test_query_validation(self):
        t = "/timeline/" + "ab" * 32
        for q in ("?layer=words&layer=words", "?bogus=1", "?layer=%zz", "?layer=%ff", "?start=nan", "?start=inf",
                  "?start=1e3", "?limit=0", "?limit=201", "?cursor=-1", "?layer=all", "?start", "?limit=01"):
            self.assertEqual(self.c.json("GET", t + q)[0], 400, q)
        self.assertEqual(self.env.argv_log(), [])
        st, _ = self.c.json("GET", t + "?layer=emotion&start=0.5&end=10&limit=200&cursor=3")
        self.assertEqual(st, 200)
        self.assertEqual(self.env.argv_log()[-1], ["timeline", "ab" * 32, "--layer=emotion", "--start=0.5",
                                                   "--end=10", "--limit=200", "--cursor=3"])

    def test_analyze_query_validation(self):
        for q in ("?language=xx", "?emotion_mode=voice", "?language=en&language=en", "?language=en&name=" + "a" * 121,
                  "?language=en&name=%01x", "?language=en&name=%ED%85", "?language=en&extra=1"):
            self.assertEqual(self.c.json("POST", "/analyze" + q, WAV)[0], 400, q)
        self.assertEqual(self.env.uploads_entries(), [])
        name = sign.encode_name("테스트 곡")
        st, b = self.c.json("POST", "/analyze?language=ko&name=" + name, WAV)
        self.assertEqual((st, b["name"]), (200, "테스트 곡"))
        self.assertEqual(self.env.argv_log()[-1][2:], ["--language", "ko", "--emotion-mode", "none"])
        # name never reaches a filename, argv or the log
        self.assertNotIn("테스트", repr(self.env.argv_log()))
        self.assertNotIn(name, self.env.log_text())


class DeadlineTest(Base):
    limits = {"header_deadline": 1.0, "max_conns": 3}

    def test_header_deadline(self):
        s = connect(self.h.port, self.env.cert)
        s.sendall(b"GET /status HTTP/1.1\r\nHost: x\r\n")
        t0 = time.time()
        s.settimeout(5)
        try:
            data = s.recv(100)
        except (ssl.SSLError, ConnectionResetError):
            data = b""
        self.assertEqual(data, b"")
        self.assertLess(time.time() - t0, 3)
        s.close()

    def test_tls_handshake_deadline(self):
        raw = socket.create_connection(("127.0.0.1", self.h.port))
        raw.settimeout(5)
        t0 = time.time()
        try:
            data = raw.recv(100)
        except ConnectionResetError:
            data = b""
        self.assertEqual(data, b"")
        self.assertLess(time.time() - t0, 3)
        raw.close()

    def test_connection_cap(self):
        held = [connect(self.h.port, self.env.cert) for _ in range(3)]
        try:
            with self.assertRaises((ssl.SSLError, ConnectionError, OSError)):
                s = connect(self.h.port, self.env.cert, timeout=3)
                s.sendall(build("GET", "/status", list(self.c.signed("GET", "/status").items())))
                if s.recv(10) == b"":
                    raise ConnectionError("closed")
        finally:
            for s in held:
                s.close()
        time.sleep(1.5)
        self.assertEqual(self.c.json("GET", "/status")[0], 200)


class PeerTest(Base):
    def test_ip_not_allowed_closed_before_reading(self):
        import ipaddress
        self.h.app.allow_net = ipaddress.ip_network("10.1.2.0/24")
        raw = socket.create_connection(("127.0.0.1", self.h.port))
        raw.settimeout(5)
        raw.sendall(b"\x16\x03\x01")  # start of a ClientHello; server must not answer
        try:
            data = raw.recv(100)
        except ConnectionResetError:
            data = b""
        self.assertEqual(data, b"")
        raw.close()

        class FakeSock:
            reads = 0

            def recv(self, *a):
                FakeSock.reads += 1
                return b""
        lst = self.h.listeners[0]
        self.assertFalse(lst.verify_request(FakeSock(), ("10.9.9.9", 1234)))
        self.assertEqual(FakeSock.reads, 0)
        self.h.app.allow_net = ipaddress.ip_network("127.0.0.0/8")
        self.assertEqual(self.c.json("GET", "/status")[0], 200)


if __name__ == "__main__":
    unittest.main()
