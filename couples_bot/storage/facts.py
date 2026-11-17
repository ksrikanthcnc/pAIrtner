"""Fact/memory CRUD — add, get, archive, search, aging, versions.

Re-exports from db.py during migration. New code should import from here.
"""
from couples_bot.db import (  # noqa: F401
    add_fact, get_facts, get_active_facts, get_fact_by_id,
    delete_fact, delete_all_facts,
    set_fact_visibility, update_fact_text,
    set_fact_status, promote_to_core,
    archive_fact, set_fact_expiry, get_expiring_facts,
    supersede_fact, run_fact_aging,
    get_shared_facts_for_pair, get_shared_facts_since,
    get_facts_since, get_positive_shared_facts,
    # Memory versions & audit
    append_memory_version, log_memory_op, get_memory_versions,
    # Summaries
    add_fact_summary, get_fact_summaries,
)
