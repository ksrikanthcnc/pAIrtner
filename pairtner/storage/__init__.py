"""Storage package — modular database layer.

Organized by domain:
    from pairtner.storage.users import get_user, ensure_user
    from pairtner.storage.facts import add_fact, get_active_facts
    from pairtner.storage.messages import log_message, get_recent_messages
    from pairtner.storage.pending import create_pending, get_deliverable
    from pairtner.storage.profiles import get_user_profile, record_pattern
    from pairtner.storage.couples import make_couple_id, create_conflict
    from pairtner.storage.goals import add_user_goal, get_active_goals
    from pairtner.storage.usage import increment_usage, check_rate_limit
    from pairtner.storage.outbox import add_outbox_event, validate_auth_token
    from pairtner.storage.context import get_user_context_data
    from pairtner.storage.settings import get_runtime_setting
    from pairtner.storage.schema import init_db
    from pairtner.storage.helpers import _now_iso, _to_iso
"""
