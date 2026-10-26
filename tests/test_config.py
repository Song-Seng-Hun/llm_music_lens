import ipaddress
import json
import os
import subprocess
import unittest

from helpers import PY, REPO, Env, server


class Log:
    def __init__(self):
        self.events = []

    def event(self, name, **kw):
        self.events.append(dict(kw, event=name))


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.env = Env()

    def tearDown(self):
        self.env.cleanup()

    def load(self, mode=0o600, **over):
        cfg = self.env.config(**over)
        return server.load_config(self.env.write_config(cfg, mode))

    def assertRefused(self, **over):
        with self.assertRaises(server.ConfigError):
            self.load(**over)

    def test_test_mode_loads(self):
        cfg = self.load()
        self.assertTrue(cfg["test_mode"])

    def test_production_refuses_loopback_and_any(self):
        self.assertRefused(test_mode=False, bind=["127.0.0.1"], port=8765, allow="172.30.1.0/24")
        self.assertRefused(test_mode=False, bind=["0.0.0.0"], port=8765, allow="172.30.1.0/24")
        self.assertRefused(test_mode=False, bind=["en0"], port=8765, allow="127.0.0.0/8")
        self.assertRefused(test_mode=False, bind=["en0"], port=8765, allow="0.0.0.0/0")
        self.assertRefused(test_mode=False, bind=["en0"], port=8765, allow="8.8.8.0/24")
        self.assertRefused(test_mode=False, bind=["en0"], port=8765, allow="172.16.0.0/12")
        self.assertRefused(test_mode=True, bind=["0.0.0.0"])
        cfg = self.load(test_mode=False, bind=["en0", "en1"], port=8765, allow="172.30.1.0/24")
        self.assertFalse(cfg["test_mode"])

    def test_production_refuses_test_only_keys(self):
        self.assertRefused(test_mode=False, bind=["en0"], port=8765, allow="172.30.1.0/24", limits={"skew": 5})
        self.assertRefused(test_mode=False, bind=["en0"], port=0, allow="172.30.1.0/24")

    def test_file_modes_and_keys(self):
        self.assertRefused(mode=0o644)
        os.chmod(self.env.keyfiles["claude-window"], 0o644)
        self.assertRefused()
        os.chmod(self.env.keyfiles["claude-window"], 0o600)
        with open(self.env.keyfiles["claude-window"], "w") as f:
            f.write("A" * 64 + "\n")
        self.assertRefused()
        with open(self.env.keyfiles["claude-window"], "w") as f:
            f.write("a" * 64 + "\n\n")
        self.assertRefused()

    def test_duplicate_json_keys_and_unknown(self):
        p = self.env.config_path
        with open(p, "w") as f:
            f.write('{"port": 1, "port": 2}')
        os.chmod(p, 0o600)
        with self.assertRaises(server.ConfigError):
            server.load_config(p)
        self.assertRefused(extra_key=1)

    def test_invalid_config_no_start(self):
        cfg = self.env.config()
        cfg["clients"] = {}
        self.env.write_config(cfg)
        p = subprocess.run([PY, "-I", os.path.join(REPO, "server.py"), "--config", self.env.config_path],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        self.assertEqual(p.returncode, 2)

    def test_address_discovery_dedup_and_allow(self):
        cfg = self.load(test_mode=False, bind=["en0", "en1", "en2"], port=8765, allow="172.30.1.0/24")
        table = {"en0": ipaddress.ip_address("172.30.1.22"), "en1": ipaddress.ip_address("172.30.1.22"),
                 "en2": ipaddress.ip_address("10.0.0.5")}
        log = Log()
        self.assertEqual(server.discover_addresses(cfg, log, lookup=table.get), ["172.30.1.22"])
        self.assertIn({"event": "bind_skip", "iface": "en2", "reason": "outside_allow"}, log.events)
        table = {"en0": None, "en1": ipaddress.ip_address("172.30.1.9"), "en2": None}
        self.assertEqual(server.discover_addresses(cfg, Log(), lookup=table.get), ["172.30.1.9"])
        self.assertEqual(server.discover_addresses(cfg, Log(), lookup=lambda n: None), [])

    def test_no_address_exits(self):
        # test_mode bind outside allow -> nothing bound -> exit 1 (KeepAlive would retry)
        self.env.write_config(self.env.config(bind=["127.0.0.1"], allow="10.9.8.0/24"))
        p = subprocess.run([PY, "-I", os.path.join(REPO, "server.py"), "--config", self.env.config_path],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        self.assertEqual(p.returncode, 1)

    def test_launchd_log_rotation(self):
        p = os.path.join(self.env.dir, "launchd.err")
        with open(p, "wb") as f:
            f.write(b"x" * 2000)
        cfg = self.load(launchd_logs=[p], limits={"log_bytes": 1000, "log_files": 3})
        app = server.App(cfg)
        app.close()
        self.assertFalse(os.path.exists(p))
        self.assertTrue(os.path.exists(p + ".1"))

    def test_api_log_rotation(self):
        cfg = self.load(limits={"log_bytes": 2000, "log_files": 3})
        app = server.App(cfg)
        for i in range(200):
            app.log.event("filler", i=i)
        app.close()
        logs = sorted(os.listdir(os.path.join(self.env.root, "logs")))
        self.assertEqual(logs, ["api.log", "api.log.1", "api.log.2"])
        for n in logs:
            self.assertLessEqual(os.path.getsize(os.path.join(self.env.root, "logs", n)), 2100)


if __name__ == "__main__":
    unittest.main()
