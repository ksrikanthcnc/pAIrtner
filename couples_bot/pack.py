"""Prompt packs: every word the model reads lives in a pack directory.

The engine owns structure (block order, separators, markdown markers, which
data may appear in whose prompt); a pack owns wording (persona, contract,
blocks, ``strings.yaml``, voices, tasks).  See ``packs/default/`` and
``docs/PACKS.md``.

* :func:`activate` loads and strictly validates a pack (startup).
* :func:`current` returns the active pack and hot-reloads it when a file
  changed on disk.  A pack that fails re-validation is ignored and the last
  good state stays active, so a half-saved edit cannot take the bot down.
"""

from __future__ import annotations

import hashlib
import logging
import string
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)
trace_logger = logging.getLogger("couples_bot.trace")

ENGINE_CONTRACT = 1

MANIFEST_NAME = "pack.yaml"
DEFAULT_PACK_PATH = "packs/default"
REPO_ROOT = Path(__file__).resolve().parent.parent

REQUIRED_FILES = ("persona", "contract", "blocks", "strings", "voices", "tasks")
REQUIRED_BLOCKS = ("role", "not_therapy", "tone")
REQUIRED_TASKS = ("nudge", "appreciation", "summarize", "therapy_export")
FALLBACK_VOICE = "casual_en"


class PackError(Exception):
    """A prompt pack is missing, malformed or incompatible with the engine."""


def _keys(**spec: str) -> dict[str, frozenset[str]]:
    return {k: frozenset(v.split()) for k, v in spec.items()}


# The engine's contract with a pack: every key must exist in strings.yaml and
# may only use the listed ``{placeholders}`` (literal braces are doubled).
STRING_KEYS: dict[str, frozenset[str]] = _keys(
    # system-prompt headings
    style_heading="",
    task_heading="",
    # per-mode intro / tail paragraphs
    tell_intro="",
    tell_tail="",
    mediate_intro="",
    mediate_tail="",
    keep_intro="",
    keep_tail="",
    blunt_intro="",
    blunt_tail="",
    digest_system="",
    checkin_system="",
    # data-block labels
    label_private_notes="",
    label_shared_notes="",
    label_recent_conversation="",
    label_current_message="",
    label_current_message_blunt="",
    label_relay_message="",
    label_keep_message="",
    label_period="",
    label_shared_context="",
    label_impressions="",
    impressions_intro="",
    impressions_of="who about",
    impressions_one_sided="",
    label_request="",
    request_text="",
    partner_a_fallback="",
    partner_b_fallback="",
    # goals / partner summary / smart context
    goals_heading="",
    goals_personal="",
    goals_shared="",
    goals_closing="",
    partner_summary_heading="",
    smart_recent="recent_days",
    smart_relevant="",
    smart_monthly="",
    # history labels
    history_assistant="",
    history_user="",
    # style
    dampening_very_high="",
    dampening_high="",
    dampening_medium="",
    dampening_low="",
    dampening_blunt_bypass="",
    learned_voice="parts",
    # digest and check-in summary
    digest_facts_heading="",
    digest_relays_heading="",
    digest_request="",
    checkin_partner_a_heading="",
    checkin_partner_b_heading="",
    checkin_shared_heading="",
    checkin_request="",
    checkin_goals_heading="",
    checkin_goals_personal="",
    checkin_goals_shared="",
    checkin_trends_heading="",
    trend_strong="",
    trend_tense="",
    trend_counts="pos neg",
    trend_horsemen="",
    checkin_conflicts_heading="",
    conflict_resolved="resolved total",
    conflict_open="open_themes",
    conflict_all_resolved="",
    conflict_none="",
    # nudges and appreciation
    partner_fallback="",
    nudge_partner="partner_name",
    nudge_language="lang",
    nudge_facts_heading="",
    nudge_request="",
    appreciation_request="",
    # monthly summary
    summarize_heading="year_month",
    summarize_fact_line="category visibility text",
    summarize_request="",
    # therapy export
    therapy_intro="",
    therapy_privacy="",
    therapy_timeline_heading="",
    therapy_timeline_line="date event_type summary",
    therapy_counts_heading="",
    therapy_patterns_heading="",
    therapy_pattern_line="pattern pattern_type frequency",
    therapy_notes_heading="",
    therapy_shared_heading="",
    therapy_goals_heading="",
    therapy_goal_personal="text status",
    therapy_goal_shared="text status",
    therapy_ratio_heading="",
    therapy_ratio_line="positive negative ratio",
    therapy_request="user_name",
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _placeholders(template: str) -> set[str]:
    """Return the field names used by a ``str.format`` template."""
    names: set[str] = set()
    for _lit, field, _spec, _conv in string.Formatter().parse(template):
        if field is None:
            continue
        names.add(field.split(".")[0].split("[")[0])
    return names


@dataclass(frozen=True)
class _State:
    """Everything read from disk for one validated version of a pack."""

    name: str
    version: str
    manifest: dict[str, Any]
    persona: str
    contract: str
    blocks: dict[str, str]
    strings: dict[str, str]
    voices: dict[str, str]
    tasks: dict[str, str]
    file_hashes: dict[str, str]
    pack_hash: str


class Pack:
    """A validated prompt pack with stat-based hot reload."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self._state = self._load()
        self._sig = self._signature()
        self._bad_sig: tuple | None = None

    # -- loading ----------------------------------------------------------

    def _err(self, where: str, problem: str) -> PackError:
        return PackError(f"prompt pack {self.root}: {where}: {problem}")

    def _read_bytes(self, path: Path) -> bytes:
        rel = path.relative_to(self.root).as_posix() if path.is_relative_to(
            self.root) else str(path)
        if not path.is_file():
            raise self._err(rel, "file is missing")
        try:
            return path.read_bytes()
        except OSError as exc:
            raise self._err(rel, f"cannot read file ({exc})") from exc

    def _read_text(self, path: Path, hashes: dict[str, str]) -> str:
        """Read a UTF-8 file, record its hash, return stripped non-empty text."""
        raw = self._read_bytes(path)
        rel = path.relative_to(self.root).as_posix()
        hashes[rel] = _sha256(raw)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise self._err(rel, f"not valid UTF-8 ({exc})") from exc
        text = text.strip()
        if not text:
            raise self._err(rel, "file is empty")
        return text

    def _resolve(self, key: str, rel: Any) -> Path:
        if not isinstance(rel, str) or not rel.strip():
            raise self._err(MANIFEST_NAME, f"files.{key} must be a non-empty path")
        path = (self.root / rel).resolve()
        if not path.is_relative_to(self.root):
            raise self._err(MANIFEST_NAME, f"files.{key} points outside the pack")
        return path

    def _load(self) -> _State:
        if not self.root.is_dir():
            raise PackError(f"prompt pack {self.root}: directory not found")
        hashes: dict[str, str] = {}
        manifest_path = self.root / MANIFEST_NAME
        raw = self._read_bytes(manifest_path)
        hashes[MANIFEST_NAME] = _sha256(raw)
        try:
            manifest = yaml.safe_load(raw.decode("utf-8"))
        except (yaml.YAMLError, UnicodeDecodeError) as exc:
            raise self._err(MANIFEST_NAME, f"malformed YAML ({exc})") from exc
        if not isinstance(manifest, dict):
            raise self._err(MANIFEST_NAME, "must be a mapping")

        name = manifest.get("name")
        if not isinstance(name, str) or not name.strip():
            raise self._err(MANIFEST_NAME, "'name' must be a non-empty string")
        if manifest.get("version") is None:
            raise self._err(MANIFEST_NAME, "'version' is required")
        version = str(manifest["version"])
        contract_version = manifest.get("engine_contract")
        if contract_version != ENGINE_CONTRACT:
            raise self._err(
                MANIFEST_NAME,
                f"engine_contract is {contract_version!r}, this engine "
                f"needs {ENGINE_CONTRACT}",
            )

        files = manifest.get("files")
        if not isinstance(files, dict):
            raise self._err(MANIFEST_NAME, "'files' must be a mapping")
        for key in REQUIRED_FILES:
            if key not in files:
                raise self._err(MANIFEST_NAME, f"files.{key} is missing")
        paths = {key: self._resolve(key, files[key]) for key in REQUIRED_FILES}

        unloaded = manifest.get("unloaded")
        if unloaded is not None:
            unloaded_dir = self._resolve("unloaded", unloaded)
            for key, path in paths.items():
                if path == unloaded_dir or path.is_relative_to(unloaded_dir):
                    raise self._err(
                        MANIFEST_NAME,
                        f"files.{key} points inside unloaded/, which the "
                        "engine never loads",
                    )

        persona = self._read_text(paths["persona"], hashes)
        contract = self._read_text(paths["contract"], hashes)
        blocks = self._read_dir(paths["blocks"], "blocks", hashes)
        for block in REQUIRED_BLOCKS:
            if block not in blocks:
                raise self._err(
                    f"{paths['blocks'].relative_to(self.root).as_posix()}/{block}.md",
                    "file is missing",
                )
        voices = self._read_dir(paths["voices"], "voices", hashes)
        if FALLBACK_VOICE not in voices:
            raise self._err(
                f"{paths['voices'].relative_to(self.root).as_posix()}/"
                f"{FALLBACK_VOICE}.md",
                "file is missing (it is the fallback voice)",
            )
        tasks = self._read_dir(paths["tasks"], "tasks", hashes)
        for task in REQUIRED_TASKS:
            if task not in tasks:
                raise self._err(
                    f"{paths['tasks'].relative_to(self.root).as_posix()}/{task}.md",
                    "file is missing",
                )
        strings = self._load_strings(paths["strings"], hashes)

        eval_rel = manifest.get("eval")
        if eval_rel is not None:
            eval_path = self._resolve("eval", eval_rel)
            if not eval_path.is_file():
                raise self._err(MANIFEST_NAME, f"eval path {eval_rel!r} does not exist")

        digest = "\n".join(f"{rel}:{h}" for rel, h in sorted(hashes.items()))
        return _State(
            name=name, version=version, manifest=manifest, persona=persona,
            contract=contract, blocks=blocks, strings=strings, voices=voices,
            tasks=tasks, file_hashes=dict(sorted(hashes.items())),
            pack_hash=_sha256(digest.encode("utf-8")),
        )

    def _read_dir(
        self, directory: Path, label: str, hashes: dict[str, str],
    ) -> dict[str, str]:
        if not directory.is_dir():
            raise self._err(
                directory.relative_to(self.root).as_posix() + "/",
                f"{label} directory is missing",
            )
        return {
            path.stem: self._read_text(path, hashes)
            for path in sorted(directory.glob("*.md"))
        }

    def _load_strings(self, path: Path, hashes: dict[str, str]) -> dict[str, str]:
        rel = path.relative_to(self.root).as_posix()
        raw = self._read_bytes(path)
        hashes[rel] = _sha256(raw)
        try:
            data = yaml.safe_load(raw.decode("utf-8"))
        except (yaml.YAMLError, UnicodeDecodeError) as exc:
            raise self._err(rel, f"malformed YAML ({exc})") from exc
        if not isinstance(data, dict):
            raise self._err(rel, "must be a mapping of key to string")
        for key, allowed in STRING_KEYS.items():
            if key not in data:
                raise self._err(rel, f"key {key!r} is missing")
            value = data[key]
            if not isinstance(value, str):
                raise self._err(rel, f"key {key!r} must be a string")
            if not value.strip():
                raise self._err(rel, f"key {key!r} is empty")
            try:
                used = _placeholders(value)
            except ValueError as exc:
                raise self._err(rel, f"key {key!r} is not a valid template ({exc})") from exc
            unknown = used - allowed
            if unknown:
                raise self._err(
                    rel,
                    f"key {key!r} uses unknown placeholder(s) "
                    f"{sorted(unknown)}; allowed: {sorted(allowed)}",
                )
        extra = sorted(set(data) - set(STRING_KEYS))
        if extra:
            logger.warning("prompt pack %s: %s has unused keys %s", self.root, rel, extra)
        return {k: data[k] for k in STRING_KEYS}

    # -- hot reload -------------------------------------------------------

    def _watched(self) -> list[Path]:
        """Paths whose stat is compared on every refresh()."""
        watched = [self.root / MANIFEST_NAME]
        files = self._state.manifest.get("files") or {}
        for key in REQUIRED_FILES:
            rel = files.get(key)
            if not isinstance(rel, str):
                continue
            path = self.root / rel
            if path.is_dir():
                watched.append(path)
                watched.extend(sorted(path.glob("*.md")))
            else:
                watched.append(path)
        return watched

    def _signature(self) -> tuple:
        sig = []
        for path in self._watched():
            try:
                st = path.stat()
            except OSError:
                sig.append((str(path), None, None))
            else:
                sig.append((str(path), st.st_mtime_ns, st.st_size))
        return tuple(sig)

    def refresh(self) -> bool:
        """Reload when a pack file changed.  Returns True if state changed.

        On a failed re-validation the last good state stays active and one
        ERROR is logged per distinct bad state.
        """
        sig = self._signature()
        if sig == self._sig:
            return False
        self._sig = sig
        try:
            state = self._load()
        except PackError as exc:
            if sig != self._bad_sig:
                logger.error("prompt pack reload failed, keeping last good: %s", exc)
                self._bad_sig = sig
            return False
        self._bad_sig = None
        changed = state.pack_hash != self._state.pack_hash
        self._state = state
        # The set of watched files may have changed (new voice, new task).
        self._sig = self._signature()
        if changed:
            logger.info(
                "prompt pack reloaded name=%s version=%s pack_hash=%s",
                state.name, state.version, state.pack_hash[:12],
            )
            self.log_loaded()
        return changed

    def log_loaded(self) -> None:
        """Log the pack identity and per-file content hashes (trace logger)."""
        st = self._state
        trace_logger.info(
            "pack_loaded name=%s version=%s pack_hash=%s files=%s",
            st.name, st.version, st.pack_hash[:12],
            {rel: h[:12] for rel, h in st.file_hashes.items()},
        )

    # -- accessors --------------------------------------------------------

    @property
    def name(self) -> str:
        return self._state.name

    @property
    def version(self) -> str:
        return self._state.version

    def persona(self) -> str:
        return self._state.persona

    def contract(self) -> str:
        return self._state.contract

    def block(self, name: str) -> str:
        try:
            return self._state.blocks[name]
        except KeyError:
            raise PackError(f"prompt pack {self.root}: no block {name!r}") from None

    def s(self, key: str, **kw: Any) -> str:
        """Return the pack string *key* with ``{placeholders}`` filled in."""
        try:
            template = self._state.strings[key]
        except KeyError:
            raise PackError(f"prompt pack {self.root}: no string {key!r}") from None
        return template.format_map(kw)

    def voice(self, name: str) -> str:
        voices = self._state.voices
        if name in voices:
            return voices[name]
        logger.info("Voice %r not found — falling back to %s", name, FALLBACK_VOICE)
        return voices[FALLBACK_VOICE]

    def task(self, name: str) -> str | None:
        return self._state.tasks.get(name)

    def snapshot(self) -> dict[str, Any]:
        st = self._state
        return {
            "name": st.name,
            "version": st.version,
            "pack_hash": st.pack_hash,
            "files": dict(st.file_hashes),
        }


_current: Pack | None = None

# Set by every build_* in prompts.py: (task name, pack snapshot) of the prompt
# assembled last in this context.  The call tracer reads it at generate() time.
last_trace: ContextVar[tuple[str, dict[str, Any]] | None] = ContextVar(
    "last_trace", default=None,
)


def set_trace(p: Pack, task: str) -> None:
    """Record that the prompt for *task* is being assembled from *p*."""
    last_trace.set((task, p.snapshot()))
def _resolve_path(path: str | Path) -> Path:
    p = Path(path)
    if p.is_absolute():
        return p
    for base in (Path.cwd(), REPO_ROOT):
        candidate = base / p
        if candidate.is_dir():
            return candidate
    return Path.cwd() / p


def activate(path: str | Path = DEFAULT_PACK_PATH) -> Pack:
    """Load and validate the pack at *path* and make it the active pack.

    Relative paths resolve against the CWD first, then the repo root.
    Raises :class:`PackError` when the pack is invalid.
    """
    global _current
    pack = Pack(_resolve_path(path))
    _current = pack
    pack.log_loaded()
    return pack


def current() -> Pack:
    """Return the active pack, hot-reloading changed files.

    Activates the default pack when none was activated yet.
    """
    pack = _current if _current is not None else activate(DEFAULT_PACK_PATH)
    pack.refresh()
    return pack
