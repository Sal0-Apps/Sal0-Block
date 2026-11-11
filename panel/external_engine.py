"""Control only the configured, official iSponsorBlockTV container.

The panel never creates, replaces, removes or supervises that container. Docker
and Watchtower retain ownership of its lifecycle and image updates.
"""
from collections import deque
from datetime import datetime, timezone
import hashlib
import http.client
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import socket
import threading
import time
from urllib.parse import quote


class DockerError(RuntimeError):
    status = 503


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path, timeout=15):
        super().__init__("localhost", timeout=timeout)
        self.socket_path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


class DockerClient:
    def __init__(self, name="isponsorblocktv", socket_path="/var/run/docker.sock", transport=None):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name):
            raise ValueError("Nome de container inválido.")
        self.name = name
        self.socket_path = socket_path
        self.transport = transport
        self.prefix = "/containers/" + quote(name, safe="")

    def _request(self, method, suffix, timeout=15):
        path = self.prefix + suffix
        if self.transport:
            status, data = self.transport(method, path, timeout)
        else:
            conn = UnixConnection(self.socket_path, timeout=timeout)
            try:
                conn.request(method, path, headers={"Content-Length": "0"})
                response = conn.getresponse()
                status, data = response.status, response.read(2 * 1024 * 1024)
            except (OSError, http.client.HTTPException) as exc:
                raise DockerError("Sem acesso ao Docker. Confira o volume /var/run/docker.sock do painel.") from exc
            finally:
                conn.close()
        if status == 404:
            raise DockerError("Container iSponsorBlockTV não encontrado. Ele pode estar sendo recriado pelo Watchtower; atualize o painel em instantes.")
        if status not in (200, 204, 304):
            raise DockerError(f"Docker recusou a operação (HTTP {status}). Aguarde a atualização do motor e tente novamente.")
        return data

    def inspect(self):
        try:
            value = json.loads(self._request("GET", "/json"))
        except (ValueError, TypeError) as exc:
            raise DockerError("Resposta de estado inválida do Docker.") from exc
        image = value.get("Config", {}).get("Image", "")
        repository = image.split("@")[0].split(":")[0].lower()
        if repository not in ("ghcr.io/dmunozv04/isponsorblocktv", "dmunozv04/isponsorblocktv"):
            raise DockerError("O container selecionado não usa a imagem oficial do iSponsorBlockTV. Nenhuma ação foi executada.")
        return value

    def control(self, action):
        if action not in ("start", "stop", "restart"):
            raise DockerError("Operação não permitida.")
        # Resolve by name every time: Watchtower replaces container IDs.
        self.inspect()
        suffix = "/" + action + ("?t=10" if action in ("stop", "restart") else "")
        self._request("POST", suffix, timeout=25)

    def logs(self):
        raw = self._request("GET", "/logs?stdout=1&stderr=1&timestamps=1&tail=120")
        chunks, offset = [], 0
        while offset + 8 <= len(raw) and raw[offset] in (0, 1, 2) and raw[offset+1:offset+4] == b"\0\0\0":
            size = int.from_bytes(raw[offset+4:offset+8], "big")
            if offset+8+size > len(raw):
                break
            chunks.append(raw[offset+8:offset+8+size])
            offset += 8 + size
        text = (b"".join(chunks) if chunks and offset == len(raw) else raw).decode("utf-8", errors="replace")
        return text.splitlines()[-120:]


def atomic_runtime(path, value):
    import tempfile
    fd, temporary = tempfile.mkstemp(prefix=".sal0-runtime-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as out:
            json.dump(value, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Engine:
    def __init__(self, store, docker=None):
        self.store = store
        self.docker = docker or DockerClient(os.getenv("SPONSORBLOCK_CONTAINER", "isponsorblocktv"),
                                             os.getenv("DOCKER_SOCKET", "/var/run/docker.sock"))
        self.lock = threading.RLock()
        self.runtime = store.directory / "sal0-panel.json"
        try:
            self.enabled = json.loads(self.runtime.read_text()).get("enabled", True) is not False
        except (OSError, ValueError):
            self.enabled = True
        self.logs = deque(maxlen=200)
        self.seq = 0
        self.last_error = None
        self.logger = logging.getLogger(f"sal0.block.{id(self)}")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        handler = RotatingFileHandler(store.directory / "sal0-activity.log", maxBytes=1024*1024, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        self.logger.addHandler(handler)
        # Deliberately no start/stop, monitor thread, or subprocess at startup.

    def redact(self, line):
        cfg, _ = self.store.snapshot()
        for secret in [cfg.get("apikey", "")] + [d["screen_id"] for d in cfg["devices"]]:
            if secret:
                line = line.replace(secret, "[oculto]")
        return re.sub(r"(loungeToken|loungeIdToken|pairing_code|key)([=:'\" ]+)[^\s,&}\"]+", r"\1\2[oculto]", str(line), flags=re.I)[:3000]

    def log(self, message, source="painel"):
        with self.lock:
            self.seq += 1
            entry = dict(id=f"p{self.seq}", time=time.time(), source=source, message=self.redact(str(message)))
            self.logs.append(entry)
            self.logger.info(json.dumps(entry, ensure_ascii=False))

    def apply(self):
        """Called only after a user changes configuration, never when panel exits."""
        with self.lock:
            try:
                current = self.docker.inspect()
                cfg, _ = self.store.snapshot()
                running = current.get("State", {}).get("Running", False)
                if not self.enabled or not cfg["devices"]:
                    if running:
                        self.docker.control("stop")
                else:
                    self.docker.control("restart" if running else "start")
                self.last_error = None
            except DockerError as exc:
                self.last_error = "Configuração salva, mas não foi possível aplicá-la ao motor: " + str(exc)
                self.log(self.last_error)

    def set_enabled(self, enabled):
        if type(enabled) is not bool:
            raise DockerError("Estado inválido.")
        with self.lock:
            self.docker.control("start" if enabled else "stop")
            atomic_runtime(self.runtime, {"enabled": enabled})
            self.enabled = enabled
            self.last_error = None
            self.log("Proteção retomada." if enabled else "Proteção pausada pelo painel.")

    def restart(self):
        with self.lock:
            self.docker.control("restart")
            atomic_runtime(self.runtime, {"enabled": True})
            self.enabled = True
            self.last_error = None
            self.log("Reinício do motor solicitado pelo painel.")

    def status(self):
        with self.lock:
            cfg, _ = self.store.snapshot()
            entries = list(self.logs)
            result = dict(state="unavailable", enabled=self.enabled, running=False,
                          uptime=0, pid=None, image=None, container=self.docker.name,
                          last_error=self.last_error, logs=entries)
            try:
                current = self.docker.inspect()
                info = current.get("State", {})
                running, restarting = bool(info.get("Running")), bool(info.get("Restarting"))
                result.update(running=running, enabled=running or restarting, pid=info.get("Pid") if running else None,
                              image=current.get("Config", {}).get("Image"))
                if info.get("StartedAt") and running:
                    try:
                        result["uptime"] = max(0, int(time.time()-datetime.fromisoformat(info["StartedAt"].replace("Z", "+00:00")).timestamp()))
                    except ValueError:
                        pass
                result["state"] = ("config_error" if self.store.error else "waiting" if not cfg["devices"] else
                                   "restarting" if restarting else "running" if running else
                                   "paused" if not self.enabled else "stopped")
                for line in self.docker.logs():
                    when, _, message = line.partition(" ")
                    try:
                        timestamp = datetime.fromisoformat(when.replace("Z", "+00:00")).timestamp()
                    except ValueError:
                        timestamp, message = 0, line
                    entries.append(dict(id="d"+hashlib.sha256(line.encode()).hexdigest()[:18], time=timestamp,
                                        source="motor", message=self.redact(message)))
            except DockerError as exc:
                result["state"] = "unavailable"
                result["last_error"] = str(exc)
            result["logs"] = sorted(entries, key=lambda x:x["time"])[-150:]
            return result

    def close(self):
        # Closing/updating the panel MUST leave the independent engine untouched.
        for handler in list(self.logger.handlers):
            handler.close()
            self.logger.removeHandler(handler)
