"""CI-only integration with disposable Docker fixtures; no TV traffic."""
import json
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.request import Request, urlopen

ENGINE = "sal0-block-ci-engine"
PANEL = "sal0-block-ci-panel"
OFFICIAL = "ghcr.io/dmunozv04/isponsorblocktv:latest"


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


def inspect(name):
    return json.loads(docker("inspect", name))[0]


def start_fixture(data_path):
    docker("run", "-d", "--name", ENGINE, "-v", data_path+":/app/data", "--entrypoint", "python3", OFFICIAL,
           "-u", "-c", "import time; print('Docker fixture ready', flush=True); time.sleep(600)")


try:
    docker("pull", OFFICIAL)
    with tempfile.TemporaryDirectory() as temp:
        config = Path(temp) / "config.json"
        config.write_text(json.dumps({"devices": [{"screen_id": "sal0-ci-offline-fixture", "name": "CI fixture", "offset": 0}]}))
        start_fixture(temp)
        docker("run", "-d", "--name", PANEL, "-p", "127.0.0.1::7860",
               "-e", "SPONSORBLOCK_CONTAINER="+ENGINE, "--volumes-from", ENGINE,
               "-v", "/var/run/docker.sock:/var/run/docker.sock:ro", "sal0-block:ci")
        port = inspect(PANEL)["NetworkSettings"]["Ports"]["7860/tcp"][0]["HostPort"]
        base = "http://127.0.0.1:"+port
        def request(path="/api/state", body=None):
            req = Request(base+path, data=json.dumps(body).encode() if body is not None else None,
                          headers={"Content-Type": "application/json", "X-Sal0-Request": "1"})
            with urlopen(req, timeout=30) as response:
                return json.load(response)
        for _ in range(40):
            try:
                state = request()
                break
            except OSError:
                time.sleep(.25)
        else:
            raise RuntimeError("Panel did not start")
        assert state["engine"]["state"] == "running", state["engine"]
        assert request("/api/service", {"action": "pause"})["engine"]["state"] == "paused"
        assert not inspect(ENGINE)["State"]["Running"]
        assert request("/api/service", {"action": "resume"})["engine"]["running"]
        state = request()
        saved = request("/api/settings", {"revision": state["revision"], "settings": {"mute_ads": False}})
        assert not saved["settings"]["mute_ads"]
        assert inspect(ENGINE)["State"]["Running"]
        # Mimic the replacement performed by Watchtower: same name, different ID.
        previous = inspect(ENGINE)["Id"]
        docker("rm", "-f", ENGINE)
        assert request()["engine"]["state"] == "unavailable"
        start_fixture(temp)
        assert inspect(ENGINE)["Id"] != previous
        assert request()["engine"]["state"] == "running"
        before = inspect(ENGINE)["State"]["StartedAt"]
        docker("stop", PANEL)
        assert inspect(ENGINE)["State"]["Running"]
        assert inspect(ENGINE)["State"]["StartedAt"] == before
        docker("start", PANEL)
        for _ in range(40):
            try:
                assert request()["engine"]["state"] == "running"
                break
            except OSError:
                time.sleep(.25)
        assert inspect(ENGINE)["State"]["StartedAt"] == before
        print("Docker OK: inherited config mount, external engine control, settings, replacement recovery and panel independence.")
finally:
    for name in (PANEL, ENGINE):
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
