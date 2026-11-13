"""Optional browser test. Requires Playwright and an installed Chromium."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

from playwright.sync_api import sync_playwright, expect

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "panel"))
import server as panel

shots = Path(os.getenv("SAL0_TEST_SCREENSHOTS", "/tmp/sal0-sponsorblock-qa"))
shots.mkdir(parents=True, exist_ok=True)
temp = tempfile.TemporaryDirectory()
from test_panel import FakeDocker
fake = FakeDocker()
server = panel.make_server("127.0.0.1", 0, temp.name, fake)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
errors = []

async def pair(cfg, code):
    if code == "000000000000":
        return None
    return {"screen_id": "pairing-fixture-secret-123456789", "name": "YouTube na TV"}

try:
    with patch.object(panel, "pair_tv", pair), sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.getenv("CHROMIUM_PATH"), headless=True, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1280, "height": 1080})
        page.on("pageerror", lambda err: errors.append(str(err)))
        page.goto(f"http://127.0.0.1:{server.server_port}")
        expect(page.locator("#connection")).to_have_text("Conectado")
        expect(page.locator("#empty-devices")).to_be_visible()
        page.screenshot(path=str(shots / "desktop-empty.png"), full_page=True)
        page.locator("#add-device").click()
        page.locator("#pair-code").fill("000000000000")
        page.locator("#pair-submit").click()
        expect(page.locator("#pair-error")).to_contain_text("expirado")
        page.locator("#pair-code").fill("123456789012")
        expect(page.locator("#pair-code")).to_have_value("123 456 789 012")
        page.locator("#pair-name").fill("Fire TV do quarto")
        page.locator("#pair-submit").click()
        expect(page.locator("#pair-dialog")).not_to_be_visible()
        expect(page.locator("#device-list")).to_contain_text("Fire TV do quarto")
        page.wait_for_function("document.getElementById('engine-badge').textContent === 'Ativa'", timeout=12000)
        page.screenshot(path=str(shots / "desktop-devices.png"), full_page=True)

        page.locator("#tab-settings").click()
        page.locator('[data-category="intro"]').check()
        page.locator(".advanced > summary").click()
        page.locator("#join_name").fill("Teste de digitação intacta")
        page.locator("#join_name").focus()
        page.wait_for_timeout(5500)
        expect(page.locator("#join_name")).to_have_value("Teste de digitação intacta")
        assert page.locator("#join_name").evaluate("el => document.activeElement === el")
        page.locator("#save-settings").click()
        expect(page.locator("#save-state")).to_have_text("Tudo salvo")
        saved = json.loads((Path(temp.name) / "config.json").read_text())
        assert saved["skip_categories"] == ["sponsor", "intro"]
        assert saved["join_name"] == "Teste de digitação intacta"
        page.locator(".advanced > summary").click()
        page.screenshot(path=str(shots / "desktop-settings.png"), full_page=True)
        page.locator("#tab-activity").click()
        page.locator("#toggle-service").click()
        expect(page.locator("#activity-badge")).to_have_text("Pausada")
        expect(page.locator("#toggle-service")).to_have_text("Retomar proteção")
        page.reload()
        page.locator("#tab-activity").click()
        expect(page.locator("#activity-badge")).to_have_text("Pausada")
        page.locator("#toggle-service").click()
        expect(page.locator("#toggle-service")).to_have_text("Pausar proteção")
        page.screenshot(path=str(shots / "desktop-activity.png"), full_page=True)

        for width in (390, 360):
            page.set_viewport_size({"width": width, "height": 844})
            for section in ("devices", "settings", "activity"):
                page.locator("#tab-" + section).click()
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (width, section, page.locator("body *").evaluate_all("els => els.filter(el => el.getBoundingClientRect().right > innerWidth + 1).map(el => ({tag:el.tagName,cls:el.className,text:el.textContent.slice(0,80)})).slice(0,12)"))
                page.screenshot(path=str(shots / f"mobile-{width}-{section}.png"), full_page=True)
            page.locator("#tab-devices").click()
            page.locator("#add-device").click()
            assert page.locator("#pair-dialog").evaluate("el => el.getBoundingClientRect().right <= window.innerWidth")
            page.screenshot(path=str(shots / f"mobile-{width}-pair.png"), full_page=True)
            page.locator("#pair-dialog .close-dialog").click()
        page.locator("#theme").click()
        page.locator("[data-sal0-mode-choice=light]").click()
        page.locator("[data-sal0-close]").click()
        expect(page.locator("body")).to_have_class("light")
        page.screenshot(path=str(shots / "mobile-light.png"), full_page=True)
        page.reload()
        expect(page.locator("body")).to_have_class("light")
        expect(page.locator("#device-list")).to_contain_text("Fire TV do quarto")
        page.get_by_role("button", name="Editar Fire TV do quarto", exact=True).click()
        page.locator("#edit-name").fill("Sala")
        page.locator("#edit-dialog summary").click()
        page.locator("#edit-offset").fill("250")
        page.locator("#edit-submit").click()
        expect(page.locator("#device-list")).to_contain_text("Sala")
        page.get_by_role("button", name="Remover Sala", exact=True).click()
        page.locator("#confirm-action").click()
        expect(page.locator("#empty-devices")).to_be_visible()
        assert not errors, errors
        browser.close()
        print("Browser OK: pairing success/error, save, focus during poll, pause persistence, rename/remove, themes, 1280/390/360px.")
finally:
    server.shutdown()
    thread.join(timeout=2)
    server.panel.engine.close()
    server.server_close()
    temp.cleanup()
