"""Pipeline debug view: queries, rendering, route."""
from __future__ import annotations

import asyncio
import json
import re

import pytest

from pairtner import db as db_mod
from pairtner.dashboard import pipeline, store
from pairtner.dashboard.store import NotFound
from tests.dashboard_seed import RAW, seed_db
from tests.test_dashboard_app import all_pages, dash, dash_no_raw  # noqa: F401


@pytest.fixture
def env(tmp_path):
    path = str(tmp_path / "p.db")

    async def build():
        await db_mod.init_db(path)
        await db_mod.create_user(path, 1, "a")
        m1 = await db_mod.log_message(path, 1, "user", f"{RAW}-m1")
        m2 = await db_mod.log_message(path, 1, "user", f"{RAW}-m2-nofacts")
        f1 = await db_mod.add_fact(
            path, 1, f"{RAW}-f1", "private", tag=["kids"], source_message_id=m1,
            model="mock", metadata=json.dumps({"narrative": f"{RAW}-narr"}))
        f2 = await db_mod.add_fact(path, 1, f"{RAW}-f2", "shared", source_message_id=m1)
        s = await db_mod.add_fact(
            path, 1, f"{RAW}-summary", "private", kind="daily",
            source_ids=json.dumps([f1, 999]))
        bad = await db_mod.add_fact(
            path, 1, "bad", "private", kind="weekly", source_ids="not json")
        return m1, m2, f1, f2, s, bad

    ids = asyncio.run(build())
    import sqlite3
    w = sqlite3.connect(path)
    w.execute("INSERT INTO memory_versions (fact_id, version, text, op, reason, "
              "source_msg_id) VALUES (?, 1, 'v1', 'add', 'init', ?)", (ids[2], ids[0]))
    w.execute("INSERT INTO memory_versions (fact_id, version, text, op, reason, "
              "source_msg_id) VALUES (?, 2, 'v2', 'update', 'edit', ?)", (ids[2], ids[1]))
    w.commit()
    w.close()
    c = store.open_readonly(path)
    yield c, ids
    c.close()


@pytest.fixture
def conn(env):
    return env[0]


@pytest.fixture
def ids(env):
    return env[1]


def test_build_pipeline_links_message_fact_summary(conn, ids):
    m1, m2, f1, f2, s, _ = ids
    d = pipeline.build_pipeline(conn)
    by_id = {m["id"]: m for m in d["messages"]}
    assert [f["id"] for f in by_id[m1]["facts"]] == [f1, f2]
    assert by_id[m2]["facts"] == []
    fact = by_id[m1]["facts"][0]
    assert fact["tags"] == ["kids"] and fact["model"] == "mock"
    assert [x["id"] for x in fact["summaries"]] == [s]
    assert [v["op"] for v in fact["versions"]] == ["add", "update"]
    assert by_id[m1]["facts"][1]["summaries"] == []
    # version op caused by m2 shows on m2 even though fact's source is m1
    assert by_id[m2]["version_ops"][0]["fact_id"] == f1


def test_paging_and_user_filter(conn):
    d = pipeline.build_pipeline(conn, limit=1)
    assert len(d["messages"]) == 1 and d["has_more"]
    d2 = pipeline.build_pipeline(conn, limit=5, before_id=d["next_before"])
    assert all(m["id"] < d["next_before"] for m in d2["messages"])
    assert pipeline.build_pipeline(conn, user_id=42)["messages"] == []
    assert pipeline.parse_limit("abc") == pipeline.DEFAULT_LIMIT
    assert pipeline.parse_limit("100000") == pipeline.MAX_LIMIT
    assert pipeline.parse_limit("0") == 1


def test_raw_hidden(conn):
    d = pipeline.build_pipeline(conn, show_raw=False)
    blob = json.dumps(d)
    assert RAW not in blob and "-narr" not in blob
    f = d["messages"][-1]["facts"][0] if d["messages"][-1]["facts"] else None
    html = pipeline.render_pipeline(d)
    assert RAW not in html and "[hidden]" in html
    assert f is None or f["metadata"] == {"narrative": "[hidden]"}


def test_fact_trace(conn, ids):
    m1, _, f1, _, s, _ = ids
    t = pipeline.build_fact_trace(conn, s)
    assert [x["id"] for x in t["sources"]] == [f1]
    t = pipeline.build_fact_trace(conn, f1)
    assert t["origin"]["id"] == m1 and t["fact"]["summaries"][0]["id"] == s
    with pytest.raises(NotFound):
        pipeline.build_fact_trace(conn, 424242)


def test_malformed_source_ids():
    assert pipeline.parse_source_ids("not json") == []
    assert pipeline.parse_source_ids('{"a":1}') == []
    assert pipeline.parse_source_ids('[1,"2",true,null,"x"]') == [1, 2]


def test_render_escapes_html(conn):
    path_html = pipeline.render_pipeline({
        "messages": [{"id": 1, "user_id": 1, "role": "user",
                      "content": "<script>alert(1)</script>", "created_at": "t",
                      "facts": [], "version_ops": []}],
        "has_more": False, "next_before": 1})
    assert "<script>" not in path_html


def test_route_renders(dash):
    cfg, client = dash
    status, text, _ = client.request("GET", "/pipeline")
    assert status == 200
    assert "Pipeline debug" in text and f"{RAW}-msg-one" in text
    status, text, _ = client.request("GET", "/pipeline?user_id=1&limit=1")
    assert status == 200
    status, _, _ = client.request("GET", "/pipeline?user_id=abc")
    assert status == 404
    status, _, _ = client.request("GET", "/pipeline/fact/99999")
    assert status == 404
    status, text, _ = client.request("GET", "/pipeline/fact/1")
    assert status == 200 and "Fact" in text


def test_route_hides_raw(dash_no_raw):
    cfg, client = dash_no_raw
    _, text, _ = client.request("GET", "/pipeline")
    assert RAW not in text


def test_route_requires_auth(tmp_path, monkeypatch):
    from tests.test_dashboard_app import start
    server, _, client = start(tmp_path, monkeypatch)
    try:
        assert client.request("GET", "/pipeline")[0] in (302, 401)
        assert client.request("GET", "/pipeline/fact/1")[0] in (302, 401)
    finally:
        server.shutdown()
        server.server_close()
