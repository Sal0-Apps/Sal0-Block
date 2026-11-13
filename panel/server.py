"""Sal0 Block: web settings for an independent official iSponsorBlockTV container."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

VERSION = "1.2.1"
ROOT = Path(__file__).resolve().parent
CATEGORIES = [
    ("sponsor", "Patrocínios", "Publicidade inserida pelo criador."),
    ("selfpromo", "Autopromoção", "Produtos, serviços e projetos do próprio canal."),
    ("interaction", "Pedidos de interação", "Pedidos de like, inscrição e comentários."),
    ("intro", "Introduções", "Vinhetas e aberturas antes do conteúdo."),
    ("outro", "Encerramentos", "Créditos e telas finais do vídeo."),
    ("music_offtopic", "Partes sem música", "Diálogos e cenas sem música em videoclipes."),
    ("preview", "Prévias e recapitulações", "Resumo do que passou ou do que vem a seguir."),
    ("filler", "Conteúdo de preenchimento", "Trechos sem relação direta com o assunto."),
    ("exclusive_access", "Conteúdo exclusivo", "Divulgação de conteúdo pago ou exclusivo."),
    ("hook", "Saudações e chamadas", "Cumprimentos e chamadas antes do assunto."),
]
DEFAULTS = dict(devices=[], skip_categories=["sponsor"], mute_ads=True,
                skip_ads=True, auto_play=True, minimum_skip_length=1,
                skip_count_tracking=True, join_name="Sal0 Block", apikey="",
                channel_whitelist=[], use_proxy=False,
                sponsorblock_api_url="https://sponsor.ajay.app/api/")


class Invalid(ValueError):
    status = 400


class Conflict(Invalid):
    status = 409


def number(value, label, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Invalid(f"{label}: informe um número.")
    if not math.isfinite(value) or not low <= value <= high:
        raise Invalid(f"{label}: use um valor entre {low} e {high}.")
    return value


def text(value, label, limit, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise Invalid(f"{label}: informe um texto válido (até {limit} caracteres).")
    return value.strip()


def validate(value):
    if not isinstance(value, dict):
        raise Invalid("O arquivo precisa conter uma configuração JSON do iSponsorBlockTV.")
    cfg = copy.deepcopy(DEFAULTS)
    cfg.update(copy.deepcopy(value))
    for key in ("config_file", "data_dir", "atvs"):
        cfg.pop(key, None)
    if not isinstance(cfg["devices"], list) or len(cfg["devices"]) > 100:
        raise Invalid("Lista de TVs inválida.")
    seen = set()
    for device in cfg["devices"]:
        if not isinstance(device, dict):
            raise Invalid("Dispositivo inválido no arquivo.")
        device["screen_id"] = text(device.get("screen_id"), "Identificação da TV", 1024)
        if device["screen_id"] in seen:
            raise Invalid("O arquivo contém a mesma TV mais de uma vez.")
        seen.add(device["screen_id"])
        device["name"] = text(device.get("name") or "YouTube na TV", "Nome da TV", 80)
        device["offset"] = number(device.get("offset", 0), "Ajuste de tempo (ms)", -30000, 30000)
    if cfg["skip_categories"] is None:
        cfg["skip_categories"] = ["sponsor"]
    if not isinstance(cfg["skip_categories"], list) or any(
        not isinstance(x, str) or x not in {c[0] for c in CATEGORIES}
        for x in cfg["skip_categories"]
    ):
        raise Invalid("Seleção de categorias inválida.")
    cfg["skip_categories"] = list(dict.fromkeys(cfg["skip_categories"]))
    for key in ("mute_ads", "skip_ads", "auto_play", "skip_count_tracking", "use_proxy"):
        if type(cfg[key]) is not bool:
            raise Invalid(f"Opção {key} inválida.")
    cfg["minimum_skip_length"] = number(cfg["minimum_skip_length"], "Duração mínima", 0, 600)
    cfg["join_name"] = text(cfg["join_name"], "Nome da conexão", 64)
    cfg["apikey"] = text(cfg["apikey"], "Chave de API", 256, empty=True)
    if not isinstance(cfg["channel_whitelist"], list) or len(cfg["channel_whitelist"]) > 1000:
        raise Invalid("Lista de canais inválida.")
    for item in cfg["channel_whitelist"]:
        if not isinstance(item, dict) or not re.fullmatch(r"UC[\w-]{22}", item.get("id", "")):
            raise Invalid("Use o ID do canal, que começa com UC e tem 24 caracteres.")
        item["name"] = text(item.get("name") or item["id"], "Nome do canal", 120)
    if cfg["channel_whitelist"] and not cfg["apikey"]:
        raise Invalid("Para permitir canais, informe uma chave da API do YouTube nas opções avançadas.")
    url = urlsplit(text(cfg["sponsorblock_api_url"], "Endereço SponsorBlock", 2048))
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise Invalid("Endereço SponsorBlock inválido.")
    cfg["sponsorblock_api_url"] = url.geturl().rstrip("/") + "/"
    return cfg


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".sal0-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(value, out, ensure_ascii=False, indent=2, allow_nan=False)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def device_id(device):
    return hashlib.sha256(device["screen_id"].encode()).hexdigest()[:20]


class ConfigStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / "config.json"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.error = None
        if not self.path.exists():
            atomic_json(self.path, DEFAULTS)
        self.snapshot()

    def snapshot(self):
        with self.lock:
            raw = self.path.read_bytes()
            revision = hashlib.sha256(raw).hexdigest()
            try:
                cfg = validate(json.loads(raw))
                self.error = None
            except (ValueError, TypeError, KeyError) as exc:
                self.error = f"Configuração inválida: {exc}. Importe um backup válido para corrigir."
                cfg = copy.deepcopy(DEFAULTS)
            return cfg, revision

    def change(self, fn, expected=None, recovery=False):
        with self.lock:
            cfg, revision = self.snapshot()
            if self.error and not recovery:
                raise Invalid(self.error)
            if expected is not None and expected != revision:
                raise Conflict("A configuração mudou em outra aba. Recarregue os dados antes de salvar.")
            changed = validate(fn(copy.deepcopy(cfg)))
            backups = self.directory / "backups"
            backups.mkdir(exist_ok=True)
            backup = backups / f"config-{time.time_ns()}.json"
            backup.write_bytes(self.path.read_bytes())
            backup.chmod(0o600)
            atomic_json(self.path, changed)
            self.error = None
            for old in sorted(backups.glob("config-*.json"))[:-20]:
                old.unlink()
            return changed


async def pair_tv(cfg, code):
    """Same YouTube Lounge pairing protocol used by official iSponsorBlockTV."""
    from urllib.request import Request, build_opener, ProxyHandler
    from urllib.parse import urlencode
    from urllib.error import HTTPError
    def request():
        opener = build_opener() if cfg["use_proxy"] else build_opener(ProxyHandler({}))
        req = Request("https://www.youtube.com/api/lounge/pairing/get_screen",
                      data=urlencode({"pairing_code": code}).encode(),
                      headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "Sal0-Block/" + VERSION})
        try:
            with opener.open(req, timeout=25) as response:
                screen = json.loads(response.read(512 * 1024)).get("screen", {})
        except (HTTPError, ValueError):
            return None
        if screen.get("screenId"):
            return {"screen_id": screen["screenId"], "name": screen.get("name") or "YouTube na TV"}
        return None
    return await asyncio.to_thread(request)


from external_engine import Engine, DockerError


class Panel:
    def __init__(self, directory, docker=None):
        self.store = ConfigStore(directory)
        self.engine = Engine(self.store, docker)
        self.pairing = threading.Lock()

    def state(self):
        cfg, revision = self.store.snapshot()
        public = {k: v for k, v in cfg.items() if k in DEFAULTS and k not in ("devices", "apikey")}
        public["has_api_key"] = bool(cfg["apikey"])
        return dict(version=VERSION, revision=revision, settings=public,
                    devices=[dict(id=device_id(d), name=d["name"], offset=d["offset"]) for d in cfg["devices"]],
                    categories=[dict(id=x[0], name=x[1], description=x[2]) for x in CATEGORIES],
                    config_error=self.store.error, engine=self.engine.status())

    def action(self, path, body):
        if not isinstance(body, dict):
            raise Invalid("Pedido inválido.")
        if path == "/api/pair":
            code = re.sub(r"[\s-]", "", str(body.get("code", "")))
            if not re.fullmatch(r"\d{12}", code):
                raise Invalid("Digite os 12 números do código exibido no YouTube da TV.")
            name = text(body.get("name", ""), "Nome da TV", 80, empty=True)
            if not self.pairing.acquire(blocking=False):
                raise Conflict("Já existe um pareamento em andamento. Aguarde a conclusão.")
            try:
                cfg, _ = self.store.snapshot()
                if self.store.error:
                    raise Invalid(self.store.error)
                try:
                    paired = asyncio.run(pair_tv(cfg, code))
                except (ImportError, ModuleNotFoundError):
                    raise Invalid("Motor oficial indisponível nesta instalação. Confira a imagem do compose.")
                except Exception:
                    raise Invalid("Não foi possível consultar o YouTube. Verifique a internet e tente novamente.")
                if not paired or not paired.get("screen_id"):
                    raise Invalid("Código recusado ou expirado. Abra novamente ‘Vincular com código da TV’ no YouTube e tente outro código.")
                def add(current):
                    found = next((d for d in current["devices"] if d["screen_id"] == paired["screen_id"]), None)
                    if found is not None:
                        if name:
                            found["name"] = name
                    else:
                        current["devices"].append(dict(screen_id=paired["screen_id"], name=name or paired.get("name") or "YouTube na TV", offset=0))
                    return current
                self.store.change(add)
                self.engine.log("TV vinculada com sucesso.")
            finally:
                self.pairing.release()
            self.engine.apply()
        elif path == "/api/settings":
            patch = body.get("settings")
            if not isinstance(patch, dict) or not isinstance(body.get("revision"), str):
                raise Invalid("Configurações inválidas. Recarregue o painel.")
            def update(cfg):
                for key in DEFAULTS:
                    if key not in ("devices", "apikey") and key in patch:
                        cfg[key] = patch[key]
                if patch.get("clear_api_key") is True:
                    cfg["apikey"] = ""
                elif patch.get("apikey"):
                    cfg["apikey"] = patch["apikey"]
                return cfg
            self.store.change(update, body["revision"])
            self.engine.log("Opções salvas e aplicadas.")
            self.engine.apply()
        elif path in ("/api/devices/update", "/api/devices/remove"):
            def update(cfg):
                match = next((d for d in cfg["devices"] if device_id(d) == body.get("id")), None)
                if match is None:
                    raise Conflict("Essa TV já foi removida. Atualize o painel.")
                if path.endswith("remove"):
                    cfg["devices"].remove(match)
                else:
                    match["name"] = body.get("name")
                    match["offset"] = body.get("offset", match["offset"])
                return cfg
            self.store.change(update, body.get("revision"))
            self.engine.log("Lista de TVs atualizada.")
            self.engine.apply()
        elif path == "/api/import":
            cfg = validate(body.get("config"))
            self.store.change(lambda _: cfg, body.get("revision"), recovery=True)
            self.engine.log("Backup importado e aplicado.")
            self.engine.apply()
        elif path == "/api/service":
            if body.get("action") == "restart":
                self.engine.restart()
            elif body.get("action") in ("pause", "resume"):
                self.engine.set_enabled(body["action"] == "resume")
            else:
                raise Invalid("Ação inválida.")
        else:
            raise Invalid("Caminho desconhecido.")
        return self.state()


class Handler(BaseHTTPRequestHandler):
    server_version = "Sal0Panel"
    sys_version = ""

    def log_message(self, *args):
        pass

    def send(self, status, value, mime="application/json; charset=utf-8", download=None):
        data = json.dumps(value, ensure_ascii=False).encode() if mime.startswith("application/json") else value
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'; base-uri 'self'; form-action 'self'")
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{download}"')
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/healthz":
            self.send(200, {"ok": True, "version": VERSION})
        elif path == "/api/state":
            self.send(200, self.server.panel.state())
        elif path == "/api/export":
            cfg, _ = self.server.panel.store.snapshot()
            if self.server.panel.store.error:
                self.send(400, {"error": self.server.panel.store.error})
            else:
                self.send(200, cfg, download="sal0-sponsorblock-config.json")
        else:
            static = {"/": ("index.html", "text/html; charset=utf-8"),
                      "/style.css": ("style.css", "text/css; charset=utf-8"),
                      "/appearance.css": ("appearance.css", "text/css; charset=utf-8"),
                      "/appearance.js": ("appearance.js", "application/javascript; charset=utf-8"),
                      "/app.js": ("app.js", "application/javascript; charset=utf-8"),
                      "/icon.svg": ("icon.svg", "image/svg+xml")}
            if path not in static:
                self.send(404, {"error": "Página não encontrada."})
                return
            name, mime = static[path]
            self.send(200, (ROOT / "static" / name).read_bytes(), mime)

    def do_POST(self):
        origin = self.headers.get("Origin")
        if self.headers.get("X-Sal0-Request") != "1" or (origin and urlsplit(origin).netloc != self.headers.get("Host")):
            self.send(403, {"error": "Pedido recusado. Abra o painel pelo endereço do servidor."})
            return
        if self.headers.get_content_type() != "application/json":
            self.send(415, {"error": "Use JSON para enviar configurações."})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 1024*1024:
                self.send(413, {"error": "O arquivo deve ter até 1 MB."})
                return
            self.connection.settimeout(35)
            body = json.loads(self.rfile.read(size))
            self.send(200, self.server.panel.action(urlsplit(self.path).path, body))
        except (Invalid, DockerError) as exc:
            self.send(exc.status, {"error": str(exc)})
        except (ValueError, UnicodeDecodeError):
            self.send(400, {"error": "JSON inválido. Confira o arquivo enviado."})
        except Exception:
            self.server.panel.engine.log("Erro interno ao processar uma alteração. Consulte a configuração e tente novamente.")
            self.send(500, {"error": "Não foi possível concluir a alteração. Tente novamente."})


def make_server(host="0.0.0.0", port=7860, directory="/app/data", docker=None):
    panel = Panel(directory, docker)
    try:
        server = ThreadingHTTPServer((host, port), Handler)
    except Exception:
        panel.engine.close()
        raise
    server.daemon_threads = True
    server.panel = panel
    return server


def main():
    server = make_server(os.getenv("SAL0_HOST", "0.0.0.0"), int(os.getenv("SAL0_PORT", "7860")),
                         os.getenv("iSPBTV_data_dir", "/app/data"))
    def stop(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    server.panel.engine.log(f"Sal0 Block {VERSION}: painel disponível na porta {server.server_port}.")
    print(f"Sal0 Block {VERSION} em http://0.0.0.0:{server.server_port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.panel.engine.close()
        server.server_close()


if __name__ == "__main__":
    main()
