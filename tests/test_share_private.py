"""Tests for /share and /private bulk visibility commands."""
import pytest

from pairtner.db import add_fact, get_facts
from pairtner.storage.facts import add_private_tag, get_private_tags


@pytest.mark.asyncio
async def test_share_tag_requires_confirm_then_flips(service, test_db, paired_users):
    a_id, _ = paired_users
    fid = await add_fact(test_db, a_id, "kid has a cold", visibility="private", tag="kids")
    await add_private_tag(test_db, a_id, "kids")

    first = await service.handle_share(a_id, "kids")
    assert "kid has a cold" in first[0].text
    assert [f for f in await get_facts(test_db, a_id, "shared") if f["id"] == fid] == []

    done = await service.handle_share(a_id, "kids")
    assert "Shared 1" in done[0].text
    shared = await get_facts(test_db, a_id, "shared")
    assert any(f["id"] == fid for f in shared)
    assert any(f["kind"] == "working" and f["id"] != fid for f in shared)
    assert "kids" not in await get_private_tags(test_db, a_id)


@pytest.mark.asyncio
async def test_private_tag_flips_and_adds_counter_fact(service, test_db, paired_users):
    a_id, _ = paired_users
    fid = await add_fact(test_db, a_id, "work stress", visibility="shared", tag="work")

    resp = await service.handle_private(a_id, "work")
    assert "Marked 1" in resp[0].text
    private = await get_facts(test_db, a_id, "private")
    assert any(f["id"] == fid for f in private)
    assert any("Exclude from partner context" in f["text"] for f in private)
    assert "work" in await get_private_tags(test_db, a_id)


@pytest.mark.asyncio
async def test_usage_and_none(service, test_db, paired_users):
    a_id, _ = paired_users
    assert "Usage" in (await service.handle_share(a_id, None))[0].text
    assert "No shared" in (await service.handle_private(a_id, "nonexistenttag"))[0].text
