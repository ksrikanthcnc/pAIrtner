"""Internationalisation — string catalog for user-facing text."""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# String tables
# ---------------------------------------------------------------------------

_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        # Onboarding
        "welcome": (
            "👋 Welcome to pAIrtner!\n\n"
            "I help partners communicate more gently. "
            "Each of you chats with me privately — I'll soften messages "
            "and only pass along what you approve.\n\n"
            "Your pairing code is: <b>{code}</b>\n"
            "Share this code with your partner so they can run /join {code}"
        ),
        "not_therapy": (
            "⚠️ <b>This bot is not a substitute for professional therapy "
            "or counseling.</b> If you or someone you know is in crisis, "
            "please reach out to professional help."
        ),
        "privacy_note": (
            "🔒 <b>Privacy note:</b> Messages pass through {transport} "
            "and are processed by {llm}. "
            "Your facts are stored locally on the bot server."
        ),
        "crisis_resources": (
            "🆘 <b>Crisis Resources (India):</b>\n"
            "• iCall: 9152987821\n"
            "• Vandrevala Foundation: 1860-2662-345\n"
            "• AASRA: 9820466726"
        ),
        "emergency_response": (
            "💛 I can see you're going through something really heavy right "
            "now. I care about you, and I want to make sure you get the "
            "right support.\n\n"
            "🆘 <b>Please reach out to someone who can help:</b>\n"
            "• <b>iCall:</b> 9152987821\n"
            "• <b>Vandrevala Foundation:</b> 1860-2662-345\n"
            "• <b>Emergency:</b> 112\n\n"
            "You don't have to go through this alone. These people are "
            "trained to help, and they're available right now."
        ),
        "already_paired": (
            "You're already paired with {partner_name}. "
            "Use /help to see available commands."
        ),

        # Pairing
        "join_success": "🎉 You're now paired with {partner_name}!",
        "join_partner_notify": (
            "🎉 {partner_name} has joined! You're now paired."
        ),
        "join_invalid_code": "❌ Invalid pairing code. Ask your partner for the correct code.",
        "join_need_code": "Usage: /join <code>",
        "join_already_paired": "You're already paired. Use /help to see commands.",
        "join_self": "You can't pair with yourself!",

        # Tell / Relay
        "tell_no_partner": "You need to pair with a partner first. Use /start to get a code.",
        "tell_need_message": "Usage: /tell <message>",
        "tell_draft_header": "📝 Here's the softened draft for your partner:\n\n",
        "relay_sent_to_partner": "💌 A message from your partner:\n\n{message}",

        # Mediate
        "mediate_no_partner": "You need to pair with a partner first.",
        "mediate_no_shared": "No shared context yet. Chat with me and share some thoughts first.",
        "mediate_usage": (
            "Usage: /mediate [window]\n"
            "Window: week (default), month, a number of days like 14 or 14d, "
            "or weeks like 2w."
        ),
        "mediate_no_input": (
            "I have nothing to go on for {window}: no shared facts and no "
            "usable impressions. Share a fact with /whatdoyouknow or chat "
            "with me first, or try a longer window like /mediate month."
        ),
        "fact_now_shared": "🔓 Fact #{id} is now shared with your partner.",
        "fact_now_private": "🔒 Fact #{id} is now private.",
        "btn_share_fact": "🔓 Share #{id}",
        "btn_unshare_fact": "🔒 Make private #{id}",
        "whatdoyouknow_more": (
            "\n…and {count} more facts. Buttons are shown for the 30 most recent."
        ),

        # Facts
        "whatdoyouknow_header": "📋 <b>Here's what I know about you:</b>\n\n",
        "whatdoyouknow_private": "🔒 <b>Private:</b>\n",
        "whatdoyouknow_shared": "🔓 <b>Shared:</b>\n",
        "whatdoyouknow_empty": "I don't have any notes about you yet. Just chat with me!",
        "fact_item": "  {id}. [{date}] {text} <i>({visibility})</i>\n",
        # Memory kind headers
        "kind_core": "📌 Core",
        "kind_working": "💭 Working Memory",
        "kind_partner_view": "👀 Partner Impressions",
        "kind_shared": "🤝 Shared",
        # Memory status labels
        "status_active": "active",
        "status_improving": "improving",
        "status_resolved": "resolved",
        "status_outdated": "outdated",
        # Buttons
        "btn_make_core": "📌 Pin #{id}",
        "btn_archive": "📦 Archive #{id}",
        # Memory review
        "review_header": (
            "🔍 <b>Memory Review</b>\n\n"
            "Here's what I consider most important about you. "
            "Anything wrong or outdated? Reply to correct me, "
            "or use the buttons to manage individual items.\n\n"
        ),
        "review_empty": "No core memories yet. Chat with me and I'll learn about you!",
        "review_core_section": "📌 <b>Core (permanent):</b>\n",
        "review_working_section": "\n💭 <b>Active working memory:</b>\n",
        "review_footer": (
            "\n\n<i>Reply with corrections like 'actually I have 2 kids, not 1' "
            "or 'the kitchen remodel is done now'.</i>"
        ),
        "fact_saved": "✅ Noted! Saved {count} new fact(s).\n{details}",
        "keep_saved": "✅ Saved! I'll remember that.",

        # Forget
        "forget_confirm_all": (
            "⚠️ This will delete ALL your facts ({count} total). "
            "Send /forget all again to confirm."
        ),
        "forget_all_done": "🗑️ Deleted all {count} of your facts.",
        "forget_done": "🗑️ Deleted fact #{id}.",
        "forget_not_found": "❌ Fact not found or doesn't belong to you.",
        "forget_usage": "Usage: /forget <id> or /forget all",

        # Export
        "export_empty": "No facts to export yet.",
        "export_header": "# My Facts Export\n\n",
        "export_caption": "Here are your exported facts ({count} total).",

        # Help
        "help": (
            "📖 <b>pAIrtner Commands:</b>\n\n"
            "<b>Basic</b>\n"
            "/start — Get a pairing code\n"
            "/join &lt;code&gt; — Pair with your partner\n"
            "/help — Show this message\n"
            "/lang &lt;en|te&gt; — Switch language\n"
            "/status — Check your relationship dashboard\n\n"
            "<b>Communication</b>\n"
            "/tell &lt;message&gt; — Draft a gentle message for your partner\n"
            "/mediate — Get a neutral perspective on shared issues\n"
            "/pause — Pause relay delivery\n"
            "/resume — Resume relay delivery\n"
            "/history — View recent sent and received messages\n"
            "/conflicts — View open discussion topics\n"
            "/timeline — View your relationship timeline\n\n"
            "<b>Memory</b>\n"
            "/whatdoyouknow — See what I remember about you\n"
            "/review — Review and correct my core memories\n"
            "/forget &lt;id|all&gt; — Delete your facts\n"
            "/export — Download your facts as a file\n"
            "/export therapy — Download a therapy report\n"
            "/remember — Revisit a positive shared memory\n"
            "/search &lt;query&gt; — Search your memories\n"
            "/goal &lt;text&gt; — Set a personal goal\n"
            "/goal shared &lt;text&gt; — Set a shared goal with your partner\n"
            "/goal — View your active goals\n\n"
            "<b>Personalization</b>\n"
            "/settings — Open settings menu\n"
            "/mode — Switch between mediator and voice mode\n"
            "/yolo — Toggle auto-send relay mode (skip review)\n"
            "/style — View voice profile, set mood, adjust traits\n"
            "/style mood &lt;mood&gt; — Set mood (stressed|happy|angry|reflective)\n"
            "/style set &lt;trait&gt; &lt;value&gt; — Override a voice trait\n"
            "/setup — Configure Gemini API keys\n"
            "/skip — Skip onboarding questions\n\n"
            "<b>Testing</b>\n"
            "/dryrun &lt;message&gt; — Test without saving anything\n"
            "/debug &lt;message&gt; — See what the bot thinks (facts, nudges, context)\n"
            "/model &lt;name&gt; &lt;message&gt; — Use a different model for one message\n\n"
            "<b>Tags (in regular messages):</b>\n"
            "#keep — Save only, no relay\n"
            "#send — Draft a relay for your partner\n"
            "#deep — Use a more thoughtful model\n"
            "#blunt — Get direct, honest feedback\n"
            "#now — Skip cooldown on an approved relay\n\n"
            "Just send a plain message to chat with me!"
        ),

        # Language
        "lang_changed": "✅ Language switched to English.",
        "lang_usage": "Usage: /lang <en|te>",

        # Approval buttons
        "btn_approve": "✅ Approve",
        "btn_edit": "✏️ Edit",
        "btn_discard": "❌ Discard",
        "btn_save": "🤫 Keep to myself",
        "btn_relay": "💬 Share with partner",

        # Approval flow
        "approved": "✅ Approved! It will be delivered in ~{minutes} minutes.",
        "approved_now": "✅ Approved! Delivering now.",
        "discarded": "🗑️ Draft discarded.",
        "edit_prompt": "Send me the edited version:",
        "edit_updated": "📝 Updated draft:\n\n{draft}\n\nApprove, edit, or discard?",

        # Delivery
        "relay_delivered": "✅ Your message was delivered to your partner.",
        "yolo_auto_sent": "Auto-sending in ~{minutes} min (yolo mode). /yolo off to review first.",

        # Ask user (no tag)
        "ask_action": "Would you like to share this with your partner?",

        # Misc
        "save_done": "💾 Saved privately.",
        "error_generic": "Something went wrong. Please try again.",
        "not_paired": "You need to pair with a partner first. Use /start to get a code.",
        "usage_warning": (
            "⚠️ Heads up: we've used {count}/{cap} heavy model calls today. "
            "Remaining calls will use the standard model."
        ),

        # Status
        "status_header": "📊 <b>Your Status</b>\n\n",
        "status_partner": "👫 Partner: {partner_name}\n",
        "status_facts": "📋 Facts: {private} private, {shared} shared\n",
        "status_pending": "📬 Pending relays: {count}\n",
        "status_usage": "📈 API calls today: {count}\n",
        "status_partner_active": "🕐 Partner last active: {time}\n",
        "status_not_paired": "You're not paired yet. Use /start to get a pairing code.",
        "status_paused_note": "⏸️ Your delivery is currently paused.\n",
        "status_partner_paused_note": "⏸️ Your partner has paused delivery.\n",
        "status_voice": "🎨 Voice: {voice} (intensity {intensity})\n",
        "status_mood": "🎭 Mood: {mood}\n",
        "status_dampening": "📉 Dampening: {level:.2f}\n",

        # Pause / Resume
        "paused_confirm": "⏸️ Delivery paused. Approved messages will be held until you /resume.",
        "resumed_confirm": "▶️ Delivery resumed! Pending messages will be delivered on schedule.",
        "partner_paused_notice": (
            "ℹ️ Note: Your partner has paused delivery. The message is "
            "approved and will be delivered when they resume."
        ),
        "delivery_paused_notice": (
            "ℹ️ Note: Delivery is currently paused. Your message is "
            "approved and will be delivered when resumed."
        ),

        # Setup (multi-tenant BYO key)
        "setup_usage": "Usage: /setup [my-key|our-key] &lt;your-key|clear&gt;",
        "setup_key_stored": "🔑 Gemini API key stored: {api_key}",
        "setup_key_cleared": "🔑 Gemini API key removed. The global key will be used.",
        "setup_key_not_set": "🔑 No custom Gemini API key set. Using the global key.",
        "setup_show_config": "🔑 Current Gemini API key: {api_key}",
        "setup_not_paired": "You need to pair with a partner first. Use /start to get a code.",
        "setup_no_encryption_key": (
            "⚠️ DB_ENCRYPTION_KEY is not configured on the server. "
            "Cannot store API keys securely. Contact the bot admin."
        ),

        "no_user": "Please run /start first to set up.",

        # Batch review (cooldown accumulation)
        "batch_review_prompt": (
            "📦 You have {count} message(s) queued for {partner}. "
            "Review before sending?"
        ),
        "btn_send_all": "📤 Send All",
        "btn_review_each": "🔍 Review Each",
        "btn_discard_all": "🗑️ Discard All",
        "btn_item_approve": "✅",
        "btn_item_edit": "✏️",
        "btn_item_discard": "❌",
        "batch_sent": "✅ Batch delivered to your partner!",
        "batch_discarded": "🗑️ Batch discarded.",
        "batch_item_review": "📝 Message {index}/{total}:\n\n{draft}",

        # Rate limiting
        "rate_limited": (
            "You're sending a lot of messages. "
            "Take a breather — I'll be here when you're ready."
        ),
        "rate_limit_day": "Daily message limit reached. See you tomorrow!",

        # Blunt mode
        "blunt_disclaimer": "🔥 Blunt mode — here's my honest take:",

        # History
        "history_header": "📜 <b>Recent Messages:</b>\n\n",
        "history_empty": "No messages sent or received yet.",
        "history_sent": "[{date}] You → Partner: \"{message}\"\n",
        "history_received": "[{date}] Partner → You: \"{message}\"\n",
        "history_usage": "Usage: /history [limit]",

        # Fact categories
        "cat_relationship": "❤️ Relationship",
        "cat_household": "🏠 Household",
        "cat_emotional": "💭 Emotional",
        "cat_work": "💼 Work",
        "cat_health": "🏥 Health",
        "cat_other": "📌 Other",

        # Digest
        "digest_header": "📋 <b>Daily Digest</b>",
        "digest_no_activity": "No new activity today.",

        # Check-in
        "checkin_questions_header": "🔄 <b>Weekly Check-in</b>\nTake a moment to reflect on your week together:",
        "checkin_question_item": "{index}. {question}",
        "checkin_waiting_partner": "✅ Got your answers! Waiting for your partner to respond.",
        "checkin_summary_header": "🔄 <b>Weekly Check-in Summary</b>",
        "checkin_both_answered": "✅ Your check-in response has been recorded.",

        # Onboarding interview
        "onboard_welcome": (
            "🎉 Great, you're paired! Tell me a bit about yourself and "
            "your relationship — anything you'd like me to know. For example: "
            "how long you've been together, if you have kids, what you "
            "appreciate about your partner, or what you'd like to work on.\n\n"
            "Or send /skip to jump right into chatting."
        ),
        "onboard_q_relationship_type": "What's your relationship type? (married / dating / other)",
        "onboard_q_duration": "How many years have you been together? (just the number)",
        "onboard_q_kids": "Do you have kids? (yes / no)",
        "onboard_q_age_range": "What's your age range? (e.g. 25-30)",
        "onboard_q_appreciate": "Name one thing you appreciate most about your partner.",
        "onboard_q_wish": "What's one wish you have for the relationship?",
        "onboard_complete": (
            "✅ Got it, thanks for sharing! I'll keep this in mind as we chat. "
            "Send any message to start talking."
        ),
        "onboard_skip": "👍 No worries, skipped! You can always update your profile later.",

        # Style / Voice
        "style_header": "🎨 <b>Your Communication Style</b>\n\n",
        "style_current": "Voice: <b>{voice}</b> | Intensity: <b>{intensity}</b>",
        "style_voice_set": "✅ Voice updated to <b>{voice}</b>.",
        "style_intensity_set": "✅ Intensity updated to <b>{intensity}</b>.",

        # Vibe (learned voice profile)
        "vibe_header": "🎨 <b>Your Vibe Profile</b>\n\n",
        "vibe_not_mature": "📊 Messages analyzed: {count}/{threshold} — still learning your style.\n",
        "vibe_mature": "✅ Profile active ({count} messages analyzed)\n",
        "vibe_trait_line": "  • {trait}: <b>{value}</b>\n",
        "vibe_set_usage": "Usage: /style set <trait> <value>\nTraits: register, sentence_length, emoji_use, humor, directness",
        "vibe_set_success": "✅ Trait updated: {trait} = <b>{value}</b>",
        "vibe_set_invalid_trait": "❌ Unknown trait. Valid: register, sentence_length, emoji_use, humor, directness.",
        "vibe_set_invalid_value": "❌ Invalid value for {trait}. Valid: {values}.",

        # Mood
        "mood_set": "🎭 Mood set to <b>{mood}</b>. Expires in ~{hours}h.",
        "mood_current": "🎭 Current mood: <b>{mood}</b> (expires {expires}).",
        "mood_none": "No active mood set. Use /mood <stressed|happy|angry|reflective>.",
        "mood_expired": "Your previous mood has expired.",
        "mood_usage": "Usage: /mood [stressed|happy|angry|reflective]",

        # Articulation
        "articulation_tip": "💡 Tip: {tip}",

        # Fact types
        "fact_type_transient": "⏱️ Current / Transient",
        "fact_type_situational": "📅 Situational",
        "fact_type_stable": "📌 Stable",
        "fact_expiring": "⏳ expires in {time}",

        # Fact contradiction
        "fact_contradiction": (
            "🤔 I noticed something: you previously said \"{old_fact}\" "
            "but now \"{new_fact}\". Which is current?"
        ),
        "btn_keep_new": "🆕 Keep New",
        "btn_keep_both": "🔄 Keep Both",
        "btn_keep_old": "⏮️ Keep Old",
        "fact_contradiction_resolved": "✅ Got it, updated!",

        # Goals (FEAT-004)
        "goal_created": "🎯 Personal goal set: {text}",
        "goal_shared_created": "🎯 Shared goal set: {text}",
        "goal_shared_notify": "🎯 {partner_name} set a shared goal: {text}",
        "goals_header": "🎯 <b>Your Goals</b>\n\n",
        "goals_personal": "<b>Personal:</b>\n",
        "goals_shared": "<b>Shared:</b>\n",
        "goals_empty": "No active goals. Use /goal to set one!",
        "goal_achieved": "✅ Goal marked as achieved!",
        "goal_dropped": "❌ Goal dropped.",
        "goal_usage": "Usage: /goal <your goal>",
        "together_usage": "Usage: /together <shared goal>",
        "together_no_partner": "You need to pair with a partner first. Use /start.",

        # Settings (FEAT-004)
        "settings_header": "⚙️ <b>Settings</b>\n\nChoose a category:",
        "settings_voice": "🗣 Voice",
        "settings_dampening": "🎚 Dampening",
        "settings_goals": "🎯 Goals",
        "settings_profile": "👤 Profile",
        "settings_mood": "🎭 Mood Presets",
        "settings_dampening_current": "🎚 Current dampening base: <b>{level:.1f}</b>",
        "settings_dampening_updated": "🎚 Dampening updated to <b>{level:.1f}</b>.",
        "settings_profile_current": (
            "👤 <b>Your Profile</b>\n"
            "Type: {relationship_type}\n"
            "Duration: {duration_years} years\n"
            "Kids: {has_kids}\n"
            "Age range: {age_range}\n\n"
            "Tap a field to edit:"
        ),
        "settings_profile_updated": "✅ Profile updated: {field} = {value}",

        # Setup keys (FEAT-004)
        "setup_my_key_stored": "🔑 Your personal Gemini key stored: {api_key}",
        "setup_my_key_cleared": "🔑 Your personal Gemini key removed.",
        "setup_our_key_stored": "🔑 Couple Gemini key stored: {api_key}",
        "setup_our_key_cleared": "🔑 Couple Gemini key removed.",
        "setup_tier_info": (
            "🔑 <b>API Key Status</b>\n"
            "Your key: {user_key}\n"
            "Couple key: {couple_key}\n"
            "Active tier: <b>{active_tier}</b>"
        ),

        # Remember (FEAT-001 batch4)
        "remember_header": "💭 <b>A Shared Memory</b>",
        "remember_empty": "No shared memories yet. Chat with me and share some moments together!",
        "remember_item": "✨ {text}",

        # Search (FEAT-001 batch5)
        "search_no_query": "Usage: /search <query>",
        "search_no_results": "🔍 No results found. Try different keywords.",
        "search_results_header": "🔍 <b>Search Results</b> ({count} found)\n\n",
        "search_result_item_v2": "  #{id} {snippet} <i>({kind}, {badge})</i>\n",
        "search_page_footer": "\nPage {page} of {pages} -- /search {query} page {next}",
        "search_page_footer_last": "\nPage {page} of {pages}",
        "search_results_header_v2": "🔍 <b>Search Results</b> ({total} found)\n\n",
        "search_result_item": "  {id}. [{date}] {snippet} <i>({category})</i>\n",

        # Nudges (FEAT-001 batch4)
        "nudge_appreciation": "💛 What's one thing you appreciate about {partner_name} right now?",
        "nudge_generic": "💬 How are things going? Anything on your mind?",

        # Horsemen / appreciation (FEAT-001 batch4)
        "horsemen_tip": "💡 {tip}",
        "appreciation_nudge": "💛 Take a moment to think about something positive about your relationship.",
        "appreciation_trend_positive": "Your positive interactions have been strong this week! 🌟",
        "appreciation_trend_negative": "This week has been a bit tense. Focus on the good moments. 💛",
        "conflicts_header": "⚡ <b>Communication Patterns</b>",
        "conflicts_empty": "No open topics right now. Things are going well! 🌟",
        "conflicts_item": "• <b>{theme}</b> — open for {duration} ({relay_count} relays)\n",
        "conflicts_resolved_note": "✅ Resolved: {theme}",
        "checkin_conflicts_summary": "This week, {resolved} out of {total} discussion topics were resolved. Still open: {open_themes}.",

        # Feedback loop (FEAT-003 batch4)
        "feedback_ask": "🔄 Your relay about \"{topic}\" was delivered. Did it land well?",
        "feedback_yes": "👍 Yes",
        "feedback_not_sure": "🤷 Not sure",
        "feedback_no": "👎 No",
        "feedback_thanks": "Thanks for the feedback! This helps me calibrate better. 🙏",

        # Memory ops (core proposal)
        "propose_core_confirm": "💡 Should I remember this as a core fact about you?\n\n\"{text}\"",
        "yes": "✅ Yes",
        "no": "❌ No",
        "core_saved": "✅ Got it, saved as a core fact about you.",
        "core_dismissed": "No worries, I'll keep it as a regular note.",

        # A/B rewrite (FEAT-003 batch4)
        "ab_option_header": "📝 Here are two versions for your partner:\n\n",
        "ab_option_a": "<b>Option A</b> (lighter touch):\n{draft}\n\n",
        "ab_option_b": "<b>Option B</b> (more softened):\n{draft}",
        "btn_send_a": "📤 Send A",
        "btn_send_b": "📤 Send B",

        # Read receipts (FEAT-003 batch4)
        "history_acknowledged": " ✓",

        # Timeline (FEAT-004 batch4)
        "timeline_header": "📅 <b>Relationship Timeline</b>",
        "timeline_empty": "No timeline events yet. Your story is just beginning! ✨",
        "timeline_event_item": "[{date}] {emoji} {summary}\n",
        "timeline_month_header": "📅 <b>Timeline — {month}</b>",

        # Therapy export (FEAT-004 batch4)
        "export_therapy_caption": "📋 Here's your therapy report. Share it with your therapist.",
        "export_therapy_generating": "⏳ Generating your therapy report...",
        "export_therapy_empty": "Not enough data yet to generate a therapy report. Keep chatting!",
    },

}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def t(lang: str, key: str, **kwargs: Any) -> str:
    """Look up a translated string. Falls back to English if missing."""
    table = _STRINGS.get(lang, _STRINGS["en"])
    template = table.get(key, _STRINGS["en"].get(key, key))
    try:
        return template.format(**kwargs) if kwargs else template
    except (KeyError, IndexError):
        return template
