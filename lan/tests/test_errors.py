import json
import os
import time
import unittest

from helpers import RID64, Base, silent_wav_bytes

SECRET = "DIAG-SENTINEL-do-not-leak"
REPORT = "/report/" + RID64
A = "/analyze?language=en&emotion_mode=voice"


class ErrorMapTest(Base):
    def fail_with(self, cmd, code=None, line=None, exit_code=1):
        lines = [SECRET + " traceback line 1", SECRET + " line 2"]
        if line is not None:
            lines.append(line)
        elif code is not None:
            lines.append(json.dumps({"error": {"code": code, "message": SECRET}}))
        self.env.write_control(**{cmd: {"exit": exit_code, "stderr_lines": lines}})

    def test_map_table(self):
        table = {
            "busy": (409, "busy"),
            "invalid_audio": (400, "invalid_audio"), "invalid_path": (400, "invalid_path"),
            "invalid_id": (400, "invalid_id"), "invalid_argument": (400, "invalid_argument"),
            "not_found": (404, "not_found"), "timeout": (504, "timeout"),
            "pin_mismatch": (500, "server_misconfigured"), "configuration": (500, "server_misconfigured"),
            "dependency_missing": (500, "server_misconfigured"),
            "invalid_output": (500, "invalid_output"), "invalid_result": (500, "invalid_result"),
            "empty_output": (500, "empty_output"), "cancelled": (500, "cancelled"),
            "internal_error": (500, "internal_error"), "output_limit": (500, "internal_error"),
            "something_new": (500, "internal_error"),
        }
        for native, (status, code) in table.items():
            self.fail_with("report", native)
            st, raw_h, raw = self.c.send("GET", REPORT)
            b = json.loads(raw)
            self.assertEqual((st, b["error"]["code"]), (status, code), native)
            self.assertNotIn(SECRET, raw.decode())
            self.assertEqual(set(b["error"]), {"code", "message"})
        self.assertNotIn(SECRET, self.env.log_text())

    def test_diagnostics_before_last_line_ignored(self):
        self.env.write_control(report={"exit": 1, "stderr_lines": [
            json.dumps({"error": {"code": "not_found"}}), "plain diag " + SECRET,
            json.dumps({"error": {"code": "invalid_id"}}), ""]})
        st, b = self.c.json("GET", REPORT)
        self.assertEqual((st, b["error"]["code"]), (400, "invalid_id"))

    def test_unparsable_last_line(self):
        for line in ("not json " + SECRET, json.dumps({"error": "x"}), json.dumps([1]),
                     json.dumps({"error": {"code": "x" * 5000}})):
            self.fail_with("report", line=line)
            st, b = self.c.json("GET", REPORT)
            self.assertEqual((st, b["error"]["code"]), (500, "internal_error"), line[:30])
        self.fail_with("report", line='{"error": {"code": "not_found", "pad": "' + "p" * 5000 + '"}}')
        self.assertEqual(self.c.json("GET", REPORT)[1]["error"]["code"], "internal_error")  # last line > 4 KiB
        self.env.write_control(report={"exit": 2})  # no stderr at all
        self.assertEqual(self.c.json("GET", REPORT)[1]["error"]["code"], "internal_error")

    def test_stdout_checks(self):
        self.env.write_control(report={"stdout": ""})
        self.assertEqual(self.c.json("GET", REPORT)[1]["error"]["code"], "empty_output")
        self.env.write_control(report={"stdout": "{not json"})
        self.assertEqual(self.c.json("GET", REPORT)[1]["error"]["code"], "invalid_output")
        self.env.write_control(report={"stdout": "[1,2]"})
        self.assertEqual(self.c.json("GET", REPORT)[1]["error"]["code"], "invalid_output")

    def test_native_passthrough_bytes(self):
        st, h, raw = self.c.send("GET", REPORT)
        self.assertEqual(raw, ('{"result_id":"%s","state":"complete", "x" : 1.50}' % RID64).encode())
        st, h, raw = self.c.send("GET", "/timeline/" + RID64 + "?layer=words")
        self.assertEqual(raw, ('{"result_id":"%s","layer":"words","words":[]}' % RID64).encode())

    def test_analyze_error_mapping_and_receipt(self):
        body = silent_wav_bytes(0.5)
        self.fail_with("analyze", "pin_mismatch")
        h = self.c.signed("POST", A, body)
        st, b = self.c.json("POST", A, body, headers=h)
        self.assertEqual((st, b["error"]["code"], b["request_id"]), (500, "server_misconfigured", h["X-ML-Nonce"]))
        r = self.c.json("GET", "/receipt/" + h["X-ML-Nonce"])[1]
        self.assertEqual((r["state"], r["http_status"], r["error_code"], r["result_id"]),
                         ("failed", 500, "server_misconfigured", None))
        self.assertEqual(os.listdir(self.env.inbox), [])
        self.assertNotIn(SECRET, self.env.log_text())

    def test_log_fields(self):
        self.c.json("GET", "/timeline/" + RID64 + "?layer=words&start=1.25")
        rec = [e for e in self.env.log_events() if e.get("route") == "/timeline/:id"][-1]
        self.assertEqual(set(rec), {"t", "client", "ip", "method", "route", "id", "status", "bytes", "ms"})
        self.assertNotIn("1.25", self.env.log_text())
        self.assertNotIn(RID64, self.env.log_text())


class SmallCapsTest(Base):
    limits = {"ml_stdout": 100, "read_timeout": 1.0}

    def test_stdout_cap_and_read_timeout(self):
        self.env.write_control(report={"stdout_size": 200})
        self.assertEqual(self.c.json("GET", REPORT)[1]["error"]["code"], "invalid_output")
        self.env.write_control(report={"sleep": 3, "stdout": "{}"})
        t0 = time.time()
        st, b = self.c.json("GET", REPORT)
        self.assertEqual((st, b["error"]["code"]), (504, "timeout"))
        self.assertLess(time.time() - t0, 2.5)


if __name__ == "__main__":
    unittest.main()
