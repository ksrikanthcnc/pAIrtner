"""Monthly fact summarization — on-demand, LLM-driven.

Generates concise summaries of each past month's facts and stores them
in the ``fact_summaries`` table for use by the smart context window.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pairtner import db as db_mod
from pairtner import pack
from pairtner.core.prompts import load_task_prompt


async def generate_monthly_summary(
    db_path: str,
    llm,
    user_id: int,
    year: int,
    month: int,
) -> str | None:
    """Generate and store a summary for a specific month's facts.

    Returns the summary text, or ``None`` when no facts exist for the
    given month.
    """
    year_month = f"{year:04d}-{month:02d}"

    # Date range for the month
    start = f"{year_month}-01"
    if month == 12:
        end = f"{year + 1:04d}-01-01"
    else:
        end = f"{year:04d}-{month + 1:02d}-01"

    facts = await db_mod._fetch_all(
        db_path,
        "SELECT text, category, visibility FROM facts "
        "WHERE owner_id = ? AND created_at >= ? AND created_at < ? "
        "AND archived_at IS NULL "
        "ORDER BY created_at",
        (user_id, start, end),
    )

    if not facts:
        return None

    # Build prompt using the shared summarize task template
    p = pack.current()
    pack.set_trace(p, "summarize")
    system = load_task_prompt("summarize") or ""

    fact_lines = []
    for f in facts:
        line = p.s(
            "summarize_fact_line",
            category=f.get("category") or "other",
            visibility=f["visibility"],
            text=f["text"],
        )
        fact_lines.append(f"- {line}")

    content = (
        f"## {p.s('summarize_heading', year_month=year_month)}\n"
        + "\n".join(fact_lines)
        + f"\n\n{p.s('summarize_request')}"
    )

    result = await llm.generate(content, system)
    summary_text = result.get("reply", "") if isinstance(result, dict) else str(result)

    await db_mod.add_fact_summary(
        db_path, user_id, year_month, summary_text, len(facts),
    )

    return summary_text


async def check_and_summarize(
    db_path: str,
    llm,
    user_id: int,
) -> None:
    """Generate missing monthly summaries for all past months.

    Only processes months that are already complete (not the current one)
    and that have facts but no stored summary yet.  Called on-demand
    when the smart context window activates.
    """
    now = datetime.now(timezone.utc)
    current_ym = f"{now.year:04d}-{now.month:02d}"

    months_with_facts = await db_mod._fetch_all(
        db_path,
        "SELECT DISTINCT strftime('%Y-%m', created_at) AS ym "
        "FROM facts WHERE owner_id = ? AND archived_at IS NULL "
        "ORDER BY ym",
        (user_id,),
    )

    existing_summaries = await db_mod._fetch_all(
        db_path,
        "SELECT year_month FROM fact_summaries WHERE user_id = ?",
        (user_id,),
    )
    existing_set = {s["year_month"] for s in existing_summaries}

    for row in months_with_facts:
        ym = row["ym"]
        if ym and ym != current_ym and ym not in existing_set:
            year, month = int(ym[:4]), int(ym[5:7])
            await generate_monthly_summary(
                db_path, llm, user_id, year, month,
            )
