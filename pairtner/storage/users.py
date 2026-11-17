"""User management — CRUD, pairing, profiles, auth tokens.

Re-exports from db.py during migration. New code should import from here.
"""
from pairtner.db import (  # noqa: F401
    get_user, create_user, ensure_user, update_user_lang,
    set_partner, set_user_paused,
    # Profiles
    get_user_profile, ensure_user_profile, update_user_profile,
    increment_message_count, get_message_count,
    update_voice_traits, update_learned_voice,
    # Auth
    create_auth_token, get_auth_token, validate_auth_token,
    allocate_rest_user,
    # Rate limiting
    check_rate_limit, increment_rate_counter,
)
