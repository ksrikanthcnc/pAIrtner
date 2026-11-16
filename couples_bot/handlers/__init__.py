"""Transport-agnostic bot handlers — public API.

Re-exports the key types so that existing ``from couples_bot.handlers import …``
statements continue to work unchanged.
"""

from .models import BotResponse, InlineButton, parse_tags
from .service import BotService

__all__ = ["BotService", "BotResponse", "InlineButton", "parse_tags"]
