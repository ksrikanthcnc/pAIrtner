"""Run a prompt pack's eval cases through the real engine and record the results.

    uv run python dev/prompt-eval/run_eval.py [--pack packs/default] [--llm gemini|fake]
        [--model M] [--only ID,ID] [--area AREA] [--rpm 12] [--max-calls 150]
        [--out dev/prompt-eval/runs] [--resume RUN_DIR]

The engine runs in-process on a throwaway DB behind the REST adapter (Telegram
is never started).  Each case gets two fresh paired users, seeded context, and
one message from user A.  Replies, LLM calls, new memory rows and the pack trace
are saved; nothing is judged.  Exit code 3 means the run stopped early (quota,
model unavailable or --max-calls); partial results are written and ``--resume``
continues them.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import logging
import os
import pathlib
import sys
import tempfile
import time
import urllib.error
import urllib.request
from typing import Any, Callable

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _p in (str(ROOT), str(ROOT / "dev" / "localtest"), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import eval_lib  # noqa: E402
import rig  # noqa: E402
from couples_bot import pack as pack_mod  # noqa: E402
from couples_bot.llm import FALLBACK_REPLY, TracingLLM, _create_inner_client  # noqa: E402

EXIT_STOPPED = 3
HTTP_TIMEOUT = 180.0


class StopRun(Exception):
    """Raised internally to end the run early; carries the user-facing reason."""

    def __init__(self, kind: str, note: str) -> None:
        super().__init__(note)
        self.kind = kind
        self.note = note


class ProviderTimeout(RuntimeError):
    """The engine answered HTTP 504: the model call took too long (provider trouble)."""


class Throttle:
    """Spaces LLM calls to at most ``rpm`` per minute and counts them."""

    def __init__(
        self, inner: Any, rpm: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self._inner = inner
        self.interval = 60.0 / rpm if rpm and rpm > 0 else 0.0
        self._clock = clock
        self._sleep = sleep
        self._next = 0.0
        self.count = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def generate(self, *args: Any, **kwargs: Any) -> Any:
        if self.interval:
            wait = self._next - self._clock()
            if wait > 0:
                await self._sleep(wait)
            self._next = max(self._clock(), self._next) + self.interval
        self.count += 1
        return await self._inner.generate(*args, **kwargs)


class _LogWatch(logging.Handler):
    """Notes 429 and other provider trouble logged by the LLM clients."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.quota = False
        self.unavailable = False

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if "429" in msg:
            self.quota = True
        elif "404" in msg or "503" in msg or "trying next" in msg:
            self.unavailable = True


class Api:
    """Blocking REST client for one user (call through ``asyncio.to_thread``)."""

    def __init__(self, base: str) -> None:
        self.base = base
        self.token = ""
        self.uid = 0

    def request(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if data is not None else {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                status, raw = resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        return status, json.loads(raw) if raw else None

    def register(self, name: str) -> None:
        status, body = self.request("POST", "/api/v1/register", {"name": name, "lang": "en"})
        if status != 201:
            raise RuntimeError(f"register {name} failed with HTTP {status}")
        self.uid, self.token = body["user_id"], body["token"]

    def say(self, text: str) -> list[dict[str, Any]]:
        status, body = self.request("POST", "/api/v1/message", {"text": text})
        if status == 504:
            raise ProviderTimeout("the REST adapter timed out waiting for the model")
        if status != 200:
            raise RuntimeError(f"message failed with HTTP {status}")
        return body.get("replies", [])


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pack", default="packs/default")
    ap.add_argument("--llm", choices=["gemini", "fake", "ollama"], default="gemini")
    ap.add_argument("--model", default=os.environ.get("EVAL_MODEL") or None)
    ap.add_argument("--only", default=None, help="comma separated case ids")
    ap.add_argument("--area", default=None)
    ap.add_argument("--rpm", type=float, default=12, help="max LLM calls per minute (ignored for fake)")
    ap.add_argument("--max-calls", type=int, default=150)
    ap.add_argument("--out", default=str(HERE / "runs"))
    ap.add_argument("--resume", default=None, metavar="RUN_DIR")
    return ap.parse_args(argv)


def _resolve_pack(path: str) -> pathlib.Path:
    p = pathlib.Path(path)
    if p.is_absolute():
        return p
    for base in (pathlib.Path.cwd(), ROOT):
        if (base / p).is_dir():
            return (base / p).resolve()
    return (pathlib.Path.cwd() / p).resolve()


async def run_case(
    case: dict[str, Any], *, base: str, db: str, calls: list[dict[str, Any]],
) -> dict[str, Any]:
    """Drive one case and return its result record (no verdict)."""
    a, b = Api(base), Api(base)
    await asyncio.to_thread(a.register, "Alice")
    await asyncio.to_thread(b.register, "Bob")
    start = await asyncio.to_thread(a.say, "/start")
    code = eval_lib.find_pairing_code(start)
    if not code:
        raise RuntimeError("no pairing code in the /start reply")
    await asyncio.to_thread(b.say, f"/join {code}")
    await asyncio.to_thread(a.say, "/skip")
    await asyncio.to_thread(b.say, "/skip")
    notes = await eval_lib.seed_context(db, case, a.uid, b.uid)
    before = await asyncio.to_thread(eval_lib.memory_snapshot, db, a.uid)
    calls.clear()
    sent = eval_lib.message_text(case)
    replies = await asyncio.to_thread(a.say, sent)
    after = await asyncio.to_thread(eval_lib.memory_snapshot, db, a.uid)
    llm_calls = [
        {
            "task": c["task"], "model": c["model"], "pack": c["pack"],
            "prompt_sha256": c["prompt_sha256"], "system": c["system"],
            "user_content": c["user_content"], "parsed": c["result"],
        }
        for c in calls
    ]
    draft = next(
        (c["parsed"].get("relay_draft") for c in reversed(llm_calls)
         if isinstance(c["parsed"], dict) and c["parsed"].get("relay_draft")),
        None,
    )
    expect = case.get("expect") or {}
    return {
        "id": case["id"], "area": case.get("area"), "tag": case.get("tag"),
        "message": case.get("message"), "sent_text": sent, "status": "run",
        "replies": [{"text": r.get("text"), "buttons": r.get("buttons")} for r in replies],
        "llm_calls": llm_calls, "relay_draft": draft,
        "memory_rows": eval_lib.new_rows(before, after),
        "memory_expectation": eval_lib.memory_expectation(case),
        "seeding_notes": notes,
        "must": expect.get("must") or [], "must_not": expect.get("must_not") or [],
        "pack": pack_mod.current().snapshot(),
    }


def skipped_record(case: dict[str, Any], reason: str) -> dict[str, Any]:
    expect = case.get("expect") or {}
    return {
        "id": case["id"], "area": case.get("area"), "tag": case.get("tag"),
        "message": case.get("message"), "status": "skipped", "skip_reason": reason,
        "llm_calls": [], "must": expect.get("must") or [],
        "must_not": expect.get("must_not") or [],
    }


def _stop_note(kind: str, detail: str = "") -> str:
    notes = {
        "quota": "quota exhausted (HTTP 429 from the model provider)",
        "unavailable": "model unavailable (the provider did not answer; the engine returned its fallback reply)",
        "max_calls": f"reached --max-calls ({detail})",
    }
    return notes[kind]


def _run_dir(args: argparse.Namespace, p: pack_mod.Pack, model: str) -> pathlib.Path:
    if args.resume:
        return pathlib.Path(args.resume).resolve()
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return (
        pathlib.Path(args.out) / eval_lib.safe_name(p.name) / eval_lib.safe_name(p.version)
        / f"{stamp}_{args.llm}-{eval_lib.safe_name(model)}"
    )


def _write_all(
    run_dir: pathlib.Path, meta: dict[str, Any], results: list[dict[str, Any]],
) -> None:
    eval_lib.dump_json(run_dir / "results.json", {"meta": meta, "cases": [
        {k: v for k, v in r.items() if k not in ("llm_calls",)} | {"llm_call_count": len(r.get("llm_calls") or [])}
        for r in results
    ]})
    (run_dir / "summary.md").write_text(eval_lib.summary_markdown(meta, results), encoding="utf-8")


async def run(
    args: argparse.Namespace,
    *,
    inner_factory: Callable[[Any, str], Any] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Any] = asyncio.sleep,
) -> int:
    """Run the evaluation; return the process exit code (0 done, 3 stopped early)."""
    pack_root = _resolve_pack(args.pack)
    active = pack_mod.activate(pack_root)  # validates the pack (raises PackError)
    all_cases = eval_lib.load_cases(eval_lib.eval_path(pack_root))
    selected = {c["id"] for c in eval_lib.select_cases(all_cases, args.only, args.area)}
    srv = None
    with tempfile.TemporaryDirectory(prefix="pack-eval-") as tmp:
        db = str(pathlib.Path(tmp) / "eval.db")
        if args.llm == "fake":
            import fake_llm
            srv = fake_llm.start(0)
            url = f"http://127.0.0.1:{srv.server_address[1]}/v1"
            config = rig.make_config(
                db, llm_url=url, model=args.model or "fake-upper",
                prompt_pack=str(pack_root),
            )
        elif args.llm == "ollama":
            url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
            config = rig.make_config(
                db, llm_url=url, model=args.model or "qwen3:8b",
                prompt_pack=str(pack_root),
            )
        else:
            config = rig.make_gemini_config(db, prompt_pack=str(pack_root))
            if args.model:
                import dataclasses
                config = dataclasses.replace(config, model_default=args.model)
        model = config.llm_model_default or config.model_default
        run_dir = _run_dir(args, active, model)
        prior: dict[str, dict[str, Any]] = {}
        if args.resume:
            for f in (run_dir / "cases").glob("*.json"):
                rec = json.loads(f.read_text(encoding="utf-8"))
                prior[rec["id"]] = rec

        calls: list[dict[str, Any]] = []
        flags = {"fallback": False}

        def observer(rec: dict[str, Any]) -> None:
            calls.append(rec)
            result = rec["result"]
            if isinstance(result, dict) and result.get("reply") == FALLBACK_REPLY:
                flags["fallback"] = True

        inner = (inner_factory or _create_inner_client)(config, db)
        throttle = Throttle(inner, 0 if args.llm in ("fake", "ollama") else args.rpm, clock, sleep)
        llm = TracingLLM(throttle, db, on_call=observer)
        watch = _LogWatch()
        llm_logger = logging.getLogger("couples_bot.llm")
        llm_logger.addHandler(watch)
        _service, adapter, task = await rig.start_rig(config, llm)
        base = f"http://127.0.0.1:{adapter.bound_port}"
        results: list[dict[str, Any]] = []
        stop: StopRun | None = None
        try:
            for case in all_cases:
                earlier = prior.get(case["id"])
                if earlier and (earlier["status"] == "run" or case["id"] not in selected):
                    results.append(earlier)  # kept from the run being resumed
                    continue
                if case["id"] not in selected:
                    continue
                if stop is None and throttle.count >= args.max_calls:
                    stop = StopRun("max_calls", _stop_note("max_calls", str(args.max_calls)))
                if stop is not None:
                    results.append(skipped_record(case, stop.note))
                    eval_lib.dump_json(run_dir / "cases" / f"{case['id']}.json", results[-1])
                    continue
                flags["fallback"] = False
                watch.quota = watch.unavailable = False
                try:
                    rec = await run_case(case, base=base, db=db, calls=calls)
                except ProviderTimeout:
                    flags["fallback"] = True
                    rec = skipped_record(case, "")
                if flags["fallback"]:
                    kind = "quota" if watch.quota else "unavailable"
                    stop = StopRun(kind, _stop_note(kind))
                    rec = skipped_record(case, stop.note) | {
                        "partial_replies": rec.get("replies", []),
                    }
                results.append(rec)
                eval_lib.dump_json(run_dir / "cases" / f"{case['id']}.json", rec)
        finally:
            llm_logger.removeHandler(watch)
            await rig.stop_rig(adapter, task)
            if srv is not None:
                srv.shutdown()
                srv.server_close()

        snap = pack_mod.current().snapshot()
        meta = {
            "pack_name": snap["name"], "pack_version": snap["version"],
            "pack_hash": snap["pack_hash"], "pack_files": snap["files"],
            "llm": args.llm, "model": model,
            "date": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "stop_note": stop.note if stop else next(
                (r["skip_reason"] for r in results if r["status"] == "skipped"), "",
            ),
        }
        _write_all(run_dir, meta, results)
        print(f"results: {run_dir}")
        print(f"summary: {run_dir / 'summary.md'}")
        if stop:
            ran = sum(1 for r in results if r["status"] == "run")
            print(
                f"STOPPED EARLY: {stop.note}. {ran} of {len(results)} cases completed; "
                f"partial results were written.\n"
                f"Resume later (quota resets daily) with:\n"
                f"  uv run python dev/prompt-eval/run_eval.py --llm {args.llm} --resume {run_dir}",
            )
            return EXIT_STOPPED
        print(f"done: {len(results)} cases")
        return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    for noisy in ("httpx", "httpcore", "openai", "google_genai", "aiosqlite"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger().setLevel(logging.WARNING)  # couples_bot.__main__ set INFO at import
    try:
        return asyncio.run(run(args))
    except pack_mod.PackError as exc:
        print(f"pack problem: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
