"""Integration tests with a real HTTP server and a supervised fixture process."""
import asyncio
import copy
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "panel"))
import server as panel

class FakeDocker:
    name = "isponsorblocktv"
    def __init__(self):
        self.running = False
        self.calls = []
        self.error = False
    def inspect(self):
        if self.error:
            raise panel.DockerError("Docker indisponível")
        return {"State": {"Running": self.running, "Pid": 321, "StartedAt": "2026-11-11T08:00:00Z"}, "Config": {"Image": "ghcr.io/dmunozv04/isponsorblocktv:latest"}}
    def control(self, action):
        self.inspect()
        self.calls.append(action)
        self.running = action != "stop"
    def logs(self):
        return []



class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.docker = FakeDocker()
        self.server = panel.make_server("127.0.0.1", 0, self.temp.name, self.docker)
        self.app = self.server.panel
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.app.engine.close()
        self.server.server_close()
        self.temp.cleanup()

    def request(self, method, path, data=None, headers=None):
        h = {"Content-Type": "application/json", "X-Sal0-Request": "1"}
        if headers is not None:
            h = headers
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request(method, path, json.dumps(data).encode() if data is not None else None, h)
        response = conn.getresponse()
        raw = response.read()
        status, response_headers = response.status, dict(response.getheaders())
        conn.close()
        return status, json.loads(raw) if response_headers["Content-Type"].startswith("application/json") else raw, response_headers

    def get(self):
        return self.request("GET", "/api/state")[1]

    def post(self, path, data):
        return self.request("POST", path, data)[:2]

    def wait_state(self, state):
        for _ in range(60):
            current = self.get()
            if current["engine"]["state"] == state:
                return current
            time.sleep(.05)
        self.fail(f"expected {state}; got {current['engine']}")

    def add_device(self, name="Quarto"):
        async def pair(cfg, code):
            self.assertEqual(code, "123456789012")
            return {"screen_id": "private-screen-token-abcdef", "name": "YouTube na TV"}
        with patch.object(panel, "pair_tv", pair):
            status, value = self.post("/api/pair", {"name": name, "code": "123 456 789 012"})
        self.assertEqual(status, 200, value)
        return value

    def test_panel_start_stop_never_controls_independent_engine(self):
        self.docker.running = True
        self.get()
        self.assertEqual(self.docker.calls, [])
        self.app.engine.close()
        self.assertTrue(self.docker.running)
        self.assertEqual(self.docker.calls, [])

    def test_engine_replacement_or_unavailable_keeps_panel_usable(self):
        self.add_device()
        self.docker.error = True
        current = self.get()
        self.assertEqual(current["engine"]["state"], "unavailable")
        self.assertEqual(self.request("GET", "/healthz")[0], 200)
        status, saved = self.post("/api/settings", {"revision": current["revision"], "settings": {"mute_ads": False}})
        self.assertEqual(status, 200)
        self.assertFalse(saved["settings"]["mute_ads"])
        self.assertIn("salva", saved["engine"]["last_error"] if "salva" in (saved["engine"]["last_error"] or "") else self.app.engine.last_error)
        self.docker.error = False
        self.docker.running = True
        self.assertEqual(self.get()["engine"]["state"], "running")

    def test_pair_engine_pause_resume_and_persistence(self):
        self.assertEqual(self.get()["engine"]["state"], "waiting")
        added = self.add_device()
        self.assertEqual(added["devices"][0]["name"], "Quarto")
        self.assertNotIn("private-screen", json.dumps(added))
        self.assertNotIn("screen_id", json.dumps(added))
        self.assertTrue(self.wait_state("running")["engine"]["pid"])
        self.post("/api/service", {"action": "pause"})
        self.assertFalse(self.wait_state("paused")["engine"]["running"])
        self.assertFalse(json.loads(self.app.engine.runtime.read_text())["enabled"])
        self.post("/api/service", {"action": "resume"})
        self.assertTrue(self.wait_state("running")["engine"]["running"])

    def test_pair_failure_does_not_modify_config(self):
        before = self.app.store.path.read_bytes()
        async def pair(*_):
            return None
        with patch.object(panel, "pair_tv", pair):
            status, data = self.post("/api/pair", {"code": "123456789012"})
        self.assertEqual(status, 400)
        self.assertIn("expirado", data["error"])
        self.assertEqual(before, self.app.store.path.read_bytes())
        self.assertEqual(self.post("/api/pair", {"code": "123"})[0], 400)

    def test_deduplicate_rename_remove(self):
        first = self.add_device()
        self.add_device("Sala")
        current = self.get()
        self.assertEqual(len(current["devices"]), 1)
        self.assertEqual(current["devices"][0]["name"], "Sala")
        ident = first["devices"][0]["id"]
        status, current = self.post("/api/devices/update", {"id": ident, "name": "Fire TV", "offset": 120, "revision": current["revision"]})
        self.assertEqual(status, 200)
        self.assertEqual(current["devices"][0]["offset"], 120)
        status, current = self.post("/api/devices/remove", {"id": ident, "revision": current["revision"]})
        self.assertEqual(status, 200)
        self.assertEqual(len(current["devices"]), 0)
        self.wait_state("waiting")

    def test_revision_conflict_and_preserve_key_and_unknown_fields(self):
        cfg = copy.deepcopy(panel.DEFAULTS)
        cfg.update(apikey="secret-api-key", future_option={"keep": True})
        status, data = self.post("/api/import", {"config": cfg, "revision": self.get()["revision"]})
        self.assertEqual(status, 200)
        self.assertTrue(data["settings"]["has_api_key"])
        self.assertNotIn("secret-api-key", json.dumps(data))
        old = data["revision"]
        status, data = self.post("/api/settings", {"revision": old, "settings": {"skip_categories": [], "apikey": "", "mute_ads": False}})
        self.assertEqual(status, 200)
        saved = json.loads(self.app.store.path.read_text())
        self.assertEqual(saved["skip_categories"], [])
        self.assertEqual(saved["apikey"], "secret-api-key")
        self.assertEqual(saved["future_option"], {"keep": True})
        self.assertEqual(self.post("/api/settings", {"revision": old, "settings": {"mute_ads": True}})[0], 409)
        backups = list((self.app.store.directory / "backups").glob("*.json"))
        self.assertGreaterEqual(len(backups), 2)

    def test_invalid_import_and_settings_never_clobber(self):
        original = self.app.store.path.read_bytes()
        cfg = copy.deepcopy(panel.DEFAULTS)
        cfg["devices"] = [{"screen_id": "", "name": "Broken"}]
        self.assertEqual(self.post("/api/import", {"config": cfg})[0], 400)
        self.assertEqual(self.app.store.path.read_bytes(), original)
        for patch_ in [{"minimum_skip_length": -1}, {"skip_ads": "true"}, {"channel_whitelist": [{"id": "bad", "name": "Bad"}]}]:
            self.assertEqual(self.post("/api/settings", {"revision": self.get()["revision"], "settings": patch_})[0], 400)
        self.assertEqual(self.app.store.path.read_bytes(), original)

    def test_broken_config_recovery_and_redaction(self):
        self.app.store.path.write_text('{invalid json')
        self.assertEqual(self.get()["engine"]["state"], "config_error")
        self.assertEqual(self.request("GET", "/api/export")[0], 400)
        self.assertEqual(self.post("/api/import", {"config": panel.DEFAULTS})[0], 200)
        self.add_device()
        self.app.engine.log("private-screen-token-abcdef loungeToken=topsecret")
        self.assertNotIn("private-screen", json.dumps(self.get()))
        self.assertNotIn("topsecret", json.dumps(self.get()))

    def test_csrf_export_and_static(self):
        self.assertEqual(self.request("POST", "/api/service", {"action": "pause"}, {"Content-Type": "application/json"})[0], 403)
        self.assertEqual(self.request("POST", "/api/service", {"action": "pause"}, {"Content-Type": "application/json", "X-Sal0-Request": "1", "Origin": "http://foreign.local"})[0], 403)
        for path in ("/", "/app.js", "/style.css", "/icon.svg", "/healthz"):
            status, content, headers = self.request("GET", path)
            self.assertEqual(status, 200)
            self.assertIn("Content-Security-Policy", headers)
        self.assertIn("attachment", self.request("GET", "/api/export")[2]["Content-Disposition"])
        self.assertEqual(self.request("GET", "/../server.py")[0], 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
