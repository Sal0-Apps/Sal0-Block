import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "panel"))
from external_engine import DockerClient, DockerError


class DockerClientTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.container_id = "old-id"
        self.image = "ghcr.io/dmunozv04/isponsorblocktv:latest"
        self.missing = False
        self.raw_logs = b""
        def transport(method, path, timeout):
            self.calls.append((method, path))
            if self.missing:
                return 404, b"{}"
            if path.endswith("/json"):
                return 200, json.dumps({"Id": self.container_id, "Config": {"Image": self.image}, "State": {"Running": True}}).encode()
            if "/logs?" in path:
                return 200, self.raw_logs
            return 204, b""
        self.client = DockerClient(transport=transport)

    def test_only_start_stop_restart_target_name(self):
        for action in ("start", "stop", "restart"):
            self.client.control(action)
        self.assertEqual([p for m,p in self.calls if m=="POST"], [
            "/containers/isponsorblocktv/start", "/containers/isponsorblocktv/stop?t=10", "/containers/isponsorblocktv/restart?t=10"])
        with self.assertRaises(DockerError):
            self.client.control("delete")
        self.assertFalse(any("remove" in p or "exec" in p or "create" in p for _,p in self.calls))

    def test_watchtower_replacement_and_404_recovery(self):
        self.assertEqual(self.client.inspect()["Id"], "old-id")
        self.container_id = "new-id"
        self.assertEqual(self.client.inspect()["Id"], "new-id")
        self.missing = True
        with self.assertRaises(DockerError):
            self.client.inspect()
        self.missing = False
        self.assertEqual(self.client.inspect()["Id"], "new-id")

    def test_wrong_image_is_never_controlled(self):
        self.image = "postgres:latest"
        with self.assertRaises(DockerError):
            self.client.control("stop")
        self.assertFalse(any(m=="POST" for m,_ in self.calls))

    def test_multiplex_and_tty_logs(self):
        first = b"2026-11-11T09:00:00.123456789Z Connected\n"
        second = b"2026-11-11T09:00:01Z Skipping\n"
        self.raw_logs = b"\x01\0\0\0"+len(first).to_bytes(4,"big")+first+b"\x02\0\0\0"+len(second).to_bytes(4,"big")+second
        self.assertEqual(self.client.logs(), [first.decode().strip(), second.decode().strip()])
        self.raw_logs = first+second
        self.assertEqual(len(self.client.logs()), 2)

    def test_container_name_cannot_inject_paths(self):
        for name in ("../other", "engine?x=1", "/containers/other", ""):
            with self.assertRaises(ValueError):
                DockerClient(name)


if __name__ == "__main__":
    unittest.main()
