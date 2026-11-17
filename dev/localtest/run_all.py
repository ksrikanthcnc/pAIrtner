"""Run every scripted flow against the real bot with the deterministic fake LLM.

    uv run python dev/localtest/run_all.py

One command, no Ollama, no Telegram, no .env and no secret: the fake LLM starts
on a free port and each phase gets its own temp DB and RestAdapter on a free
port. Prints ``===== FLAGS =====`` and ``- none`` (exit 0) or the flags (exit 1).

Phase 1  review_before_send=true,  cooldown 0: all scenarios but the two below
Phase 2  review_before_send=false:             relay_no_review
Phase 3  short loop intervals, digest and check-in set to the next minute:
         periodic (digest, check-in with answers, nudges, backup); takes up to ~80 s
"""
from __future__ import annotations

import asyncio
import logging
import pathlib
import sys
import tempfile
from datetime import datetime, timedelta, timezone

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fake_llm  # noqa: E402
import rig  # noqa: E402
from scenarios import PHASE1, Env, run_scenarios  # noqa: E402

MIN_LEAD_SECONDS = 25  # setup of the periodic scenario must finish before the target minute


async def run_phase(
    title: str, names: list[str], llm_url: str, fake_url: str, lines: list[str], *,
    review: bool, periodic: bool = False,
) -> list[str]:
    """Start a fresh rig, run *names* in a worker thread, stop the rig; return the flags."""
    with tempfile.TemporaryDirectory(prefix="pairtner-rig-") as tmp:
        tmp_path = pathlib.Path(tmp)
        db = str(tmp_path / "rig.db")
        backups = str(tmp_path / "backups")
        extra: dict = {"backup_directory": backups}
        periodic_info = None
        if periodic:
            now = datetime.now(timezone.utc)
            target = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
            if (target - now).total_seconds() < MIN_LEAD_SECONDS:
                target += timedelta(minutes=1)
            hhmm = target.strftime("%H:%M")
            extra.update(
                digest_time=hhmm, digest_timezone="UTC",
                checkin_day=target.strftime("%A").lower(), checkin_time=hhmm, checkin_timezone="UTC",
                nudges_quiet_start="00:00", nudges_quiet_end="23:59",
                loops_digest_poll_seconds=1, loops_checkin_poll_seconds=1,
                loops_nudge_interval_seconds=20, loops_backup_interval_seconds=5,
            )
            periodic_info = {"time": hhmm, "target_epoch": target.timestamp(), "backup_dir": backups}
        config = rig.make_config(
            db, llm_url=llm_url, model="fake-upper", cooldown=0, review_before_send=review,
            delivery_interval=1, loops=periodic, **extra,
        )
        _service, adapter, task = await rig.start_rig(config)
        env = Env(
            f"http://127.0.0.1:{adapter.bound_port}", db_path=db, fake_url=fake_url,
            review=review, periodic=periodic_info, relay_wait=40,
            log=lambda s: (print(s, flush=True), lines.append(s)),
        )
        env.log(f"\n######## {title} (port {adapter.bound_port}) ########")
        try:
            await asyncio.to_thread(run_scenarios, env, names)
        finally:
            await rig.stop_rig(adapter, task)
        return [f"{title}: {f}" for f in env.flags]


async def main() -> int:
    srv = fake_llm.start(0)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    llm_url = f"{base}/v1"
    lines: list[str] = []
    flags: list[str] = []
    try:
        flags += await run_phase("phase 1 (review on)", PHASE1, llm_url, base, lines, review=True)
        flags += await run_phase("phase 2 (review off)", ["relay_no_review"], llm_url, base, lines, review=False)
        flags += await run_phase("phase 3 (periodic)", ["periodic"], llm_url, base, lines,
                                 review=False, periodic=True)
    finally:
        srv.shutdown()
        srv.server_close()
    out = ["\n===== FLAGS ====="] + [f"  - {f}" for f in flags or ["none"]]
    print("\n".join(out), flush=True)
    lines += out
    (HERE / "transcript-run_all.log").write_text("\n".join(lines))
    return 1 if flags else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    sys.exit(asyncio.run(main()))
