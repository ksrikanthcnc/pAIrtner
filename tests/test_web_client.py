"""Static checks and Node tests for the PWA client (pairtner/web)."""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from pairtner.config import Config
from pairtner.handlers import BotService
from pairtner.rest.api import SECURITY_HEADERS, STATIC_FILES, WEB_DIR, Request, RestApi
from tests.helpers import MockLLMClient

ROOT = Path(__file__).resolve().parent.parent
JS_DIR = ROOT / "tests" / "js"
CLIENT_TEXT = sorted(list(WEB_DIR.glob("*.js")) + list(WEB_DIR.glob("*.html")))

FORBIDDEN = [
    r"\.innerHTML", r"\.outerHTML", r"insertAdjacentHTML", r"document\.write",
    r"\beval\s*\(", r"new\s+Function", r"\.setAttribute\(\s*[\"']on",
    r"\.setAttribute\(\s*[\"']style",
]


def test_client_files_exist():
    names = {p.name for p in CLIENT_TEXT}
    assert {"app.js", "render.js", "sw.js", "index.html", "duo.html"} <= names


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_node_render_tests():
    proc = subprocess.run(
        ["node", "--test", "tests/js/"], capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("path", CLIENT_TEXT, ids=lambda p: p.name)
def test_no_forbidden_sinks(path):
    src = path.read_text(encoding="utf-8")
    for pattern in FORBIDDEN:
        assert not re.search(pattern, src), f"{path.name} matches {pattern}"


@pytest.mark.parametrize("path", [p for p in CLIENT_TEXT if p.suffix == ".html"],
                         ids=lambda p: p.name)
def test_html_has_no_inline_script_style_or_handlers(path):
    src = path.read_text(encoding="utf-8")
    for m in re.finditer(r"<script\b([^>]*)>(.*?)</script>", src, re.S | re.I):
        assert "src=" in m.group(1), "inline script"
        assert m.group(2).strip() == "", "script tag with src has a body"
    assert not re.search(r"<style\b", src, re.I)
    assert not re.search(r"\sstyle\s*=", src, re.I)
    assert not re.search(r"\son[a-z]+\s*=", src, re.I)


def test_manifest_valid():
    data = json.loads((WEB_DIR / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert data["name"] and data["start_url"] == "/" and data["display"] == "standalone"
    sizes = {i["sizes"] for i in data["icons"]}
    assert {"192x192", "512x512"} <= sizes
    for icon in data["icons"]:
        f = WEB_DIR / icon["src"].lstrip("/")
        assert f.is_file(), icon["src"]
        if icon["type"] == "image/png":
            assert f.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert any(i.get("purpose") == "maskable" for i in data["icons"])


def test_service_worker_never_caches_api():
    src = (WEB_DIR / "sw.js").read_text(encoding="utf-8")
    shell = re.search(r"var SHELL = \[(.*?)\];", src, re.S)
    assert shell, "SHELL list not found"
    assert "/api/" not in shell.group(1)
    assert 'indexOf("/api/") === 0' in src  # network-only bypass guard
    assert re.search(r'CACHE = "pairtner-shell-v\d+"', src)
    assert "caches.delete" in src  # activate cleans old caches


def test_app_registers_service_worker():
    assert 'serviceWorker.register("/sw.js")' in (WEB_DIR / "app.js").read_text(encoding="utf-8")


@pytest.fixture
def api(test_db):
    cfg = Config(telegram_token="", db_path=test_db, rest_registration="open")
    return RestApi(BotService(cfg, test_db, MockLLMClient()), cfg)


@pytest.mark.parametrize("path", sorted(STATIC_FILES))
async def test_static_served_with_headers(api, path):
    resp = await api.handle(Request("GET", path, {}, b"", "10.0.0.1"))
    assert resp.status == 200
    assert resp.content_type == STATIC_FILES[path][1]
    assert len(resp.body) > 0
    headers = {k.lower(): v for k, v in resp.headers}
    for key, value in SECURITY_HEADERS.items():
        assert headers[key.lower()] == value
    if path in ("/", "/index.html"):
        assert "script-src 'self'" in headers["content-security-policy"]


def test_duo_has_no_secrets_and_uses_same_origin_iframes():
    src = (WEB_DIR / "duo.html").read_text(encoding="utf-8")
    assert src.count("<iframe") == 2
    assert "profile=alice" in src and "profile=bob" in src
    assert "token" not in src.lower() and "http" not in src.lower()


def test_ui_strings_have_en_te_parity():
    src = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    block = re.search(r"var UI = \{(.*?)\n  \};", src, re.S).group(1)
    en, te = re.split(r"\n    te: \{", block)
    keys = lambda s: set(re.findall(r"^\s*(?:\w+: .*?)?\b(\w+): \"", s, re.M)) | set(
        re.findall(r"\b(\w+): \"", s))
    assert keys(en) == keys(te) and len(keys(en)) > 10
