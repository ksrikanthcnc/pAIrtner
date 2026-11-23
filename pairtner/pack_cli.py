"""Prompt-pack validation CLI.

    uv run python -m pairtner.pack_cli validate packs/default
    uv run python -m pairtner.pack_cli diff packs/default packs/simple
    uv run python -m pairtner.pack_cli diff packs/default --git HEAD~1

Runs the engine's own strict loader (:class:`pairtner.pack.Pack`) and adds
lint-style checks: file layout, extra files, ``{{block}}`` references,
contract JSON examples and task/contract coverage.  Exit code 0 means no
errors (warnings are reported but do not fail), 1 means errors.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from pairtner import pack as pack_mod

_BLOCK_REF_RE = re.compile(r"\{\{\s*([A-Za-z0-9_\-]+)\s*\}\}")
_PBLOCK_RE = re.compile(r"""p\.block\(\s*['"]([A-Za-z0-9_\-]+)['"]\s*\)""")
_JSON_FENCE_RE = re.compile(r"```json\s*\n(.*?)```", re.S)
_CONTRACT_TASK_RE = re.compile(r"^## Task:\s*(\S+)\s*$", re.M)

# Entries at the pack root that are legitimate but not loaded by the engine.
_KNOWN_ROOT_EXTRAS = {"README.md", "eval", "unloaded", "memory.md",
                      "random_questions.md"}
# Tasks that need no ``## Task:`` section in contract.md (free-text output).
_TASKS_WITHOUT_CONTRACT = {"appreciation", "nudge", "summarize", "therapy_export"}


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, int | str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


def _check_block_refs(root: Path, blocks: set[str], rep: Report) -> None:
    """Every ``{{name}}`` / ``p.block('name')`` in pack markdown must exist."""
    for path in sorted(root.rglob("*.md")):
        rel = path.relative_to(root)
        if rel.parts[0] in ("unloaded", "eval"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        refs = set(_BLOCK_REF_RE.findall(text)) | set(_PBLOCK_RE.findall(text))
        for ref in sorted(refs - blocks):
            rep.errors.append(f"{rel.as_posix()}: references missing block {ref!r}")


def _check_contract(contract: str, tasks: set[str], rep: Report) -> None:
    sections = _CONTRACT_TASK_RE.findall(contract)
    rep.stats["contract_sections"] = len(sections)
    if not sections:
        rep.errors.append("contract.md: no '## Task: <name>' sections found")
    dupes = {s for s in sections if sections.count(s) > 1}
    for d in sorted(dupes):
        rep.errors.append(f"contract.md: duplicate section '## Task: {d}'")

    blocks = _JSON_FENCE_RE.findall(contract)
    rep.stats["contract_json_examples"] = len(blocks)
    for i, body in enumerate(blocks, 1):
        try:
            json.loads(body)
        except json.JSONDecodeError as exc:
            line = contract[: contract.find(body)].count("\n") + 1 + exc.lineno
            rep.errors.append(
                f"contract.md: JSON example #{i} is invalid ({exc.msg}, line {line})"
            )

    for task in sorted(tasks - set(sections) - _TASKS_WITHOUT_CONTRACT):
        rep.warnings.append(
            f"tasks/{task}.md has no matching '## Task: {task}' section in contract.md"
        )


def validate(path: str | Path) -> Report:
    """Validate the pack at *path* and return a :class:`Report`."""
    rep = Report()
    root = pack_mod._resolve_path(path)
    if not root.is_dir():
        rep.errors.append(f"{root}: directory not found")
        return rep

    try:
        pack = pack_mod.Pack(root)
    except pack_mod.PackError as exc:
        rep.errors.append(str(exc))
        _fallback_checks(root, rep)
        return rep

    st = pack._state
    rep.stats.update(
        name=st.name, version=st.version,
        blocks=len(st.blocks), voices=len(st.voices), tasks=len(st.tasks),
        strings=len(st.strings), pack_hash=st.pack_hash[:12],
    )

    # extra / missing files
    declared = {MANIFEST for MANIFEST in (pack_mod.MANIFEST_NAME,)}
    files = st.manifest.get("files", {})
    declared |= {str(v).rstrip("/").split("/")[0] for v in files.values()}
    for key in ("eval", "unloaded"):
        if isinstance(st.manifest.get(key), str):
            declared.add(st.manifest[key].rstrip("/").split("/")[0])
    for entry in sorted(root.iterdir()):
        if entry.name.startswith(".") or entry.name == "__pycache__":
            continue
        if entry.name not in declared and entry.name not in _KNOWN_ROOT_EXTRAS:
            rep.warnings.append(f"extra file not referenced by pack.yaml: {entry.name}")
    for sub in ("blocks", "voices", "tasks"):
        d = root / files.get(sub, f"{sub}/")
        if d.is_dir():
            for f in sorted(d.iterdir()):
                if f.is_file() and f.suffix != ".md":
                    rep.warnings.append(f"{sub}/{f.name}: not a .md file, ignored by engine")

    # unused strings keys in strings.yaml
    raw = yaml.safe_load((root / files["strings"]).read_text(encoding="utf-8")) or {}
    extra_keys = sorted(set(raw) - set(pack_mod.STRING_KEYS))
    if extra_keys:
        rep.warnings.append(f"strings.yaml: unused keys {extra_keys}")

    _check_block_refs(root, set(st.blocks), rep)
    _check_contract(st.contract, set(st.tasks), rep)
    return rep


def _fallback_checks(root: Path, rep: Report) -> None:
    """When the loader fails, still lint what can be read."""
    contract = root / "contract.md"
    if contract.is_file():
        _check_contract(contract.read_text(encoding="utf-8", errors="replace"), set(), rep)
    blocks_dir = root / "blocks"
    blocks = {p.stem for p in blocks_dir.glob("*.md")} if blocks_dir.is_dir() else set()
    _check_block_refs(root, blocks, rep)


def format_report(rep: Report, path: str | Path) -> str:
    lines: list[str] = []
    if rep.ok:
        lines.append(f"OK: {path}")
    else:
        lines.append(f"FAILED: {path}")
    if rep.stats:
        lines.append("  " + ", ".join(f"{k}={v}" for k, v in rep.stats.items()))
    for e in rep.errors:
        lines.append(f"  ERROR: {e}")
    for w in rep.warnings:
        lines.append(f"  WARN:  {w}")
    return "\n".join(lines)


# --------------------------------------------------------------------- diff

_DIFF_SKIP_DIRS = {"__pycache__", ".git"}


def _read_tree(root: Path) -> dict[str, bytes]:
    """Map pack-relative posix path -> file bytes for every file under *root*."""
    out: dict[str, bytes] = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if p.is_file() and not (set(rel.parts) & _DIFF_SKIP_DIRS):
            out[rel.as_posix()] = p.read_bytes()
    return out


def _git(cwd: Path, *args: str) -> bytes:
    try:
        res = subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True)
    except FileNotFoundError as exc:
        raise ValueError("git is not installed") from exc
    except subprocess.CalledProcessError as exc:
        raise ValueError(exc.stderr.decode("utf-8", "replace").strip() or "git failed") from exc
    return res.stdout


def _read_git_tree(root: Path, rev: str) -> dict[str, bytes]:
    """Files of the pack directory *root* as committed at git revision *rev*."""
    top = Path(_git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    rel = root.resolve().relative_to(top).as_posix()
    prefix = "" if rel == "." else rel + "/"
    names = _git(top, "ls-tree", "-r", "--name-only", "-z", rev, "--", rel).decode()
    out: dict[str, bytes] = {}
    for name in filter(None, names.split("\0")):
        sub = name[len(prefix):]
        if not set(sub.split("/")) & _DIFF_SKIP_DIRS:
            out[sub] = _git(top, "show", f"{rev}:{name}")
    return out


def _text(data: bytes) -> str | None:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _flatten(value: object, prefix: str = "") -> dict[str, object]:
    """Flatten nested YAML mappings to dotted keys (lists are leaves)."""
    if isinstance(value, dict):
        out: dict[str, object] = {}
        for k, v in value.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
        return out
    return {prefix: value}


def _yaml_changes(old: str, new: str) -> list[str] | None:
    """Key-level change lines for two YAML documents, or None if unparsable."""
    try:
        a = _flatten(yaml.safe_load(old))
        b = _flatten(yaml.safe_load(new))
    except yaml.YAMLError:
        return None
    lines: list[str] = []
    for key in sorted(set(a) | set(b)):
        if key not in b:
            lines.append(f"  - {key}: {a[key]!r}")
        elif key not in a:
            lines.append(f"  + {key}: {b[key]!r}")
        elif a[key] != b[key]:
            lines.append(f"  ~ {key}: {a[key]!r} -> {b[key]!r}")
    return lines


@dataclass
class DiffResult:
    changed: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return (f"{len(self.changed)} files changed, "
                f"{len(self.added)} added, {len(self.removed)} removed")


def diff_trees(old: dict[str, bytes], new: dict[str, bytes],
               old_label: str = "a", new_label: str = "b") -> DiffResult:
    """Compare two ``{relpath: bytes}`` trees."""
    res = DiffResult()
    for rel in sorted(set(old) | set(new)):
        if rel not in new:
            res.removed.append(rel)
            res.lines.append(f"removed: {rel}")
        elif rel not in old:
            res.added.append(rel)
            res.lines.append(f"added:   {rel}")
        elif old[rel] != new[rel]:
            res.changed.append(rel)
            res.lines.append(f"changed: {rel}")
            a, b = _text(old[rel]), _text(new[rel])
            if a is None or b is None:
                res.lines.append("  (binary file differs)")
            elif rel.endswith((".yaml", ".yml")):
                keys = _yaml_changes(a, b)
                if keys is None:
                    keys = list(difflib.unified_diff(
                        a.splitlines(), b.splitlines(),
                        f"{old_label}/{rel}", f"{new_label}/{rel}", lineterm=""))
                res.lines.extend(keys)
            else:
                res.lines.extend(difflib.unified_diff(
                    a.splitlines(), b.splitlines(),
                    f"{old_label}/{rel}", f"{new_label}/{rel}", lineterm=""))
    return res


def diff_packs(path_a: str | Path, path_b: str | Path | None = None,
               git_rev: str | None = None) -> DiffResult:
    """Diff pack *path_a* against pack *path_b*, or against itself at *git_rev*.

    Raises ValueError for a missing directory or a bad arguments combination.
    """
    root_a = pack_mod._resolve_path(path_a)
    if not root_a.is_dir():
        raise ValueError(f"{root_a}: directory not found")
    if git_rev:
        if path_b:
            raise ValueError("give either a second pack or --git, not both")
        return diff_trees(_read_git_tree(root_a, git_rev), _read_tree(root_a),
                          git_rev, "working")
    if not path_b:
        raise ValueError("second pack path (or --git REV) required")
    root_b = pack_mod._resolve_path(path_b)
    if not root_b.is_dir():
        raise ValueError(f"{root_b}: directory not found")
    return diff_trees(_read_tree(root_a), _read_tree(root_b),
                      Path(path_a).name, Path(path_b).name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pairtner.pack_cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate", help="validate a prompt pack directory")
    v.add_argument("path", help="pack directory, e.g. packs/default")
    d = sub.add_parser("diff", help="compare two packs, or a pack against a git revision")
    d.add_argument("path", help="pack directory (the 'old' side)")
    d.add_argument("other", nargs="?", help="second pack directory (the 'new' side)")
    d.add_argument("--git", metavar="REV",
                   help="compare PATH at git revision REV (old) with the working tree (new)")
    args = parser.parse_args(argv)

    if args.cmd == "diff":
        try:
            res = diff_packs(args.path, args.other, args.git)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        for line in res.lines:
            print(line)
        print(f"\n{res.summary}")
        return 0

    rep = validate(args.path)
    print(format_report(rep, args.path))
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
