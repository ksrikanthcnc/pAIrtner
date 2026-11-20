"""Docs stay in step with the code: routes, error codes and setting names."""

import re
from pathlib import Path

from pairtner.rest import api

ROOT = Path(__file__).resolve().parent.parent
REST_DOC = (ROOT / "docs" / "REST_API.md").read_text(encoding="utf-8")
CONFIG_SRC = (ROOT / "pairtner" / "config.py").read_text(encoding="utf-8")
ENV_NAME = re.compile(r"`((?:REST|LOOPS|MEDIATE)_[A-Z_]+)`")
ENV_DOCS = ("README.md", "DEPLOYMENT.md", "docs/REST_API.md", "settings.yaml")
# Secrets are read at use time, outside Config.
NOT_IN_CONFIG = {"REST_INVITE_CODE", "REST_ID_BASE", "REST_BIND", "DASHBOARD_BIND"}


def test_every_route_is_documented():
    missing = [
        f"{r.method} {r.path}"
        for r in api.ROUTES
        if r.path not in REST_DOC
    ]
    assert not missing, f"routes missing from docs/REST_API.md: {missing}"


def test_every_documented_route_method_matches():
    for r in api.ROUTES:
        if r.handler == "_static":
            continue
        assert f"`{r.method} {r.path}" in REST_DOC, f"{r.method} {r.path} heading missing"


def test_every_error_code_in_docs_exists_in_code():
    src = (ROOT / "pairtner" / "rest" / "api.py").read_text(encoding="utf-8")
    codes = set(re.findall(r"\| \d{3} \| `([a-z_]+)` \|", REST_DOC))
    assert codes, "no error table found in docs/REST_API.md"
    unknown = [c for c in codes if f'"{c}"' not in src]
    assert not unknown, f"documented error codes not in rest/api.py: {unknown}"


def test_every_error_code_in_code_is_documented():
    src = (ROOT / "pairtner" / "rest" / "api.py").read_text(encoding="utf-8")
    raised = set(re.findall(r'ApiError\(\d{3}, "([a-z_]+)"\)', src))
    undocumented = [c for c in raised if f"`{c}`" not in REST_DOC]
    assert not undocumented, f"error codes missing from docs/REST_API.md: {undocumented}"


def test_documented_env_names_exist_in_config():
    names: set[str] = set()
    for rel in ENV_DOCS:
        text = (ROOT / rel).read_text(encoding="utf-8")
        names.update(ENV_NAME.findall(text))
        # settings.yaml mentions names in comments without backticks
        names.update(re.findall(r"\b((?:REST|LOOPS|MEDIATE)_[A-Z_]{3,})\b(?!\.md)", text))
    names -= NOT_IN_CONFIG
    # Prefix groups written like `REST_HOST` / `_PORT` are not full names.
    missing = sorted(n for n in names if f'"{n}"' not in CONFIG_SRC)
    assert not missing, f"env names documented but not read in config.py: {missing}"


def test_documented_yaml_keys_match_settings_file():
    text = (ROOT / "settings.yaml").read_text(encoding="utf-8")
    for key in ("rest:", "loops:", "mediate:"):
        assert re.search(rf"^{key}", text, re.M), f"{key} block missing in settings.yaml"
