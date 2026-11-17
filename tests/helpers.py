"""Shared test helpers."""

from __future__ import annotations

import shutil
from pathlib import Path

from couples_bot.llm import LLMResponse

REPO_ROOT = Path(__file__).resolve().parent.parent


def copy_default_pack(dest_parent: Path, name: str = "pack") -> Path:
    """Copy ``packs/default`` (without unloaded/ and eval/) under *dest_parent*.

    The copy is a valid pack that tests can edit freely.  The ``eval`` and
    ``unloaded`` manifest keys are dropped with the folders they point at.
    """
    import yaml

    dest = dest_parent / name
    shutil.copytree(
        REPO_ROOT / "packs" / "default", dest,
        ignore=shutil.ignore_patterns("unloaded", "eval"),
    )
    manifest = yaml.safe_load((dest / "pack.yaml").read_text(encoding="utf-8"))
    manifest.pop("eval", None)
    manifest.pop("unloaded", None)
    (dest / "pack.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return dest


class MockLLMClient:
    """Deterministic LLM double for testing."""

    def __init__(self, responses: list[LLMResponse] | None = None) -> None:
        self.responses: list[LLMResponse] = responses or []
        self.calls: list[dict] = []

    async def generate(
        self,
        user_content: str,
        system_prompt: str,
        model: str | None = None,
        api_key_override: str | None = None,
    ) -> LLMResponse:
        self.calls.append({
            "user_content": user_content,
            "system_prompt": system_prompt,
            "model": model,
            "api_key_override": api_key_override,
        })
        if self.responses:
            return self.responses.pop(0)
        return {"reply": "Mock reply", "new_facts": [], "relay_draft": None}
