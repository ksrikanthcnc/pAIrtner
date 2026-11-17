"""``python -m couples_bot`` refuses to start with a broken pack."""

from __future__ import annotations

import os
import subprocess
import sys

from tests.helpers import REPO_ROOT, copy_default_pack


def _run(pack_path: str, tmp_path) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PROMPT_PACK": pack_path,
        "TELEGRAM_BOT_TOKEN": "x",
        "GEMINI_API_KEY": "x",
        "ADAPTER": "cli",
        "DB_PATH": str(tmp_path / "startup.db"),
    }
    return subprocess.run(
        [sys.executable, "-m", "couples_bot"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, timeout=60,
    )


def test_broken_pack_exits_nonzero_naming_pack_and_file(tmp_path):
    root = copy_default_pack(tmp_path, name="broken_pack")
    (root / "persona.md").write_text("")
    result = _run(str(root), tmp_path)
    output = result.stdout + result.stderr
    assert result.returncode == 2
    assert "broken_pack" in output
    assert "persona.md" in output
    assert not (tmp_path / "startup.db").exists(), "DB must not be touched"


def test_missing_pack_exits_nonzero(tmp_path):
    missing = tmp_path / "no_such_pack"
    result = _run(str(missing), tmp_path)
    assert result.returncode == 2
    assert "no_such_pack" in result.stdout + result.stderr
