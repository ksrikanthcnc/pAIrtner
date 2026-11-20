"""PWA assets, viewport meta and the responsive overview/couple pages."""
from __future__ import annotations

import json

import pytest

from tests.test_dashboard_app import all_pages, dash  # noqa: F401


def test_manifest_served(dash):
    _, client = dash
    status, body, headers = client.request("GET", "/static/manifest.json")
    assert status == 200
    assert "manifest+json" in dict(headers)["Content-Type"]
    data = json.loads(body)
    assert data["short_name"] == "pAIrtner" and data["display"] == "standalone"
    assert client.request("GET", data["icons"][0]["src"])[0] == 200


def test_sw_served(dash):
    _, client = dash
    status, body, headers = client.request("GET", "/static/sw.js")
    h = dict(headers)
    assert status == 200 and "javascript" in h["Content-Type"]
    assert "fetch" in body and h["Service-Worker-Allowed"] == "/"


def test_static_allowlist(dash):
    _, client = dash
    for p in ("/static/../app.py", "/static/%2e%2e/app.py", "/static/nope.js"):
        assert client.request("GET", p)[0] == 404


def test_pages_have_viewport_meta(dash):
    _, client = dash
    for path in all_pages(client):
        status, body, _ = client.request("GET", path)
        if status == 200 and "<html" in body:
            assert "name=viewport" in body, path
            assert 'rel="manifest"' in body and "/static/pwa.js" in body, path
    assert "name=viewport" in client.request("GET", "/login", auth=False)[1]


def test_overview_page_loads(dash):
    _, client = dash
    status, body, _ = client.request("GET", "/")
    assert status == 200
    for needle in ("active couples", "messages total", "active facts",
                   "avg health score", "Quick links", "Recent activity", "/couples"):
        assert needle in body


def test_couple_detail_page(dash):
    _, client = dash
    status, body, _ = client.request("GET", "/couples/1:2")
    assert status == 200
    for needle in ("/users/1", "/users/2", "Health", "messages this week",
                   "active facts", "pending nudges", "/timeline/1_2"):
        assert needle in body


def test_responsive_markup(dash):
    _, client = dash
    _, body, _ = client.request("GET", "/users")
    assert "@media (min-width:768px)" in body and "@media (min-width:1024px)" in body
    assert 'class="tscroll"' in body and 'class="burger"' in body
