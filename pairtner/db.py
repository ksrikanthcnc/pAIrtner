"""SQLite database layer — re-export facade.

All implementations now live in ``pairtner.storage.*`` submodules.
This module re-exports every public name for backward compatibility.
New code should import from the storage submodules directly.
"""

# ---------------------------------------------------------------------------
# Schema & init
# ---------------------------------------------------------------------------
from pairtner.storage.schema import SCHEMA_SQL as _SCHEMA_SQL, init_db  # noqa: F401

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
from pairtner.storage.helpers import (  # noqa: F401
    _now_iso, _to_iso, _row_to_dict, _fetch_one, _fetch_all, _execute,
)

# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
from pairtner.storage.users import (  # noqa: F401
    get_user, create_user, ensure_user, update_user_lang, set_partner,
    set_user_paused, get_partner_last_active,
)

# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------
from pairtner.storage.messages import (  # noqa: F401
    log_message, get_recent_messages, get_full_history,
    get_history_after_id, soft_delete_message,
)

# ---------------------------------------------------------------------------
# Facts & memory
# ---------------------------------------------------------------------------
from pairtner.storage.facts import (  # noqa: F401
    add_fact, get_facts, get_shared_facts_for_pair, set_fact_visibility,
    update_fact_text, set_fact_status, promote_to_core, delete_fact,
    delete_all_facts, get_fact_by_id, get_active_facts,
    get_shared_facts_since, get_positive_shared_facts, get_facts_since,
    archive_fact, set_fact_expiry, get_expiring_facts, supersede_fact,
    run_fact_aging, append_memory_version, log_memory_op,
    get_memory_versions, add_fact_summary, get_fact_summaries,
)

# ---------------------------------------------------------------------------
# Pending / relay
# ---------------------------------------------------------------------------
from pairtner.storage.pending import (  # noqa: F401
    create_pending, get_pending, update_pending_status,
    update_pending_edited_draft, get_deliverable, mark_as_sent,
    get_pending_count, get_recent_pending_count, get_sent_relays,
    get_sent_relays_since, update_pending_feedback,
    mark_pending_acknowledged, get_pending_needing_feedback,
    get_feedback_stats, get_unacknowledged_relays_to_user,
    get_active_batch_for_sender, create_or_get_active_batch,
    add_item_to_batch, get_batch, get_batch_item, get_batch_items,
    update_batch_status, update_batch_item_status, reset_batch_timer,
    get_deliverable_batches,
)

# ---------------------------------------------------------------------------
# Profiles, moods, perceptions, patterns, interactions
# ---------------------------------------------------------------------------
from pairtner.storage.profiles import (  # noqa: F401
    get_user_profile, ensure_user_profile, update_user_profile,
    increment_message_count, get_message_count, update_voice_traits,
    update_learned_voice, set_user_mood, get_active_mood,
    clear_expired_moods, clear_user_mood, add_partner_perception,
    get_partner_perceptions, get_perceptions_between,
    update_perception_confidence, record_pattern, get_user_patterns,
    get_user_patterns_by_type, record_interaction, get_interaction_ratio,
)

# ---------------------------------------------------------------------------
# Couples: config, conflicts, checkins, timeline
# ---------------------------------------------------------------------------
from pairtner.storage.couples import (  # noqa: F401
    make_couple_id, set_couple_config, get_couple_config,
    delete_couple_config, get_all_couples, create_conflict,
    get_open_conflicts, get_conflict_by_id, update_conflict_status,
    add_relay_to_conflict, get_conflict_summary, create_checkin,
    get_active_checkin, update_checkin_answers, mark_checkin_complete,
    add_timeline_event, get_timeline_events, get_timeline_events_range,
    get_timeline_summary,
)

# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------
from pairtner.storage.goals import (  # noqa: F401
    add_user_goal, add_shared_goal, get_active_goals, get_shared_goals,
    update_goal_status, update_shared_goal_status, get_goal_by_id,
    get_shared_goal_by_id,
)

# ---------------------------------------------------------------------------
# Usage tracking, rate limiting, admin queries
# ---------------------------------------------------------------------------
from pairtner.storage.usage import (  # noqa: F401
    increment_usage, record_pack_snapshot, record_llm_call,
    get_usage_count, get_usage_today, get_usage_detailed,
    check_rate_limit, increment_rate_counter, get_couple_conversation,
)

# ---------------------------------------------------------------------------
# Outbox, auth tokens, REST user allocation
# ---------------------------------------------------------------------------
from pairtner.storage.outbox import (  # noqa: F401
    create_auth_token, get_auth_token, validate_auth_token,
    add_outbox_event, get_outbox_events, prune_outbox, allocate_rest_user,
)

# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------
from pairtner.storage.context import (  # noqa: F401
    get_user_context_data, _get_context_budgets, _apply_word_cap,
)

# ---------------------------------------------------------------------------
# Settings & preferences
# ---------------------------------------------------------------------------
from pairtner.storage.settings import (  # noqa: F401
    get_runtime_setting, set_runtime_setting, get_all_runtime_settings,
    get_user_preference, set_user_preference,
)
