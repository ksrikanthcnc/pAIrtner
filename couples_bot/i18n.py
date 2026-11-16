"""Internationalisation — simple string table with English and Telugu."""

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
        "crisis_resources": (
            "🆘 <b>Crisis Resources (India):</b>\n"
            "• iCall: 9152987821\n"
            "• Vandrevala Foundation: 1860-2662-345\n"
            "• AASRA: 9820466726"
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

        # Facts
        "whatdoyouknow_header": "📋 <b>Here's what I know about you:</b>\n\n",
        "whatdoyouknow_private": "🔒 <b>Private:</b>\n",
        "whatdoyouknow_shared": "🔓 <b>Shared:</b>\n",
        "whatdoyouknow_empty": "I don't have any notes about you yet. Just chat with me!",
        "fact_item": "  {idx}. {text} <i>(id: {id})</i>\n",
        "fact_saved": "✅ Noted! Saved {count} new fact(s).",
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
            "📖 <b>pAIrtner Commands:</b>\n"
            "/start — Get a pairing code\n"
            "/join &lt;code&gt; — Pair with your partner\n"
            "/tell &lt;message&gt; — Draft a gentle message for your partner\n"
            "/mediate — Get a neutral perspective on shared issues\n"
            "/whatdoyouknow — See what I remember about you\n"
            "/forget &lt;id|all&gt; — Delete your facts\n"
            "/export — Download your facts as a file\n"
            "/lang &lt;en|te&gt; — Switch language\n"
            "/help — Show this message\n\n"
            "<b>Tags (in regular messages):</b>\n"
            "#keep — Save only, no relay\n"
            "#send — Draft a relay for your partner\n"
            "#deep — Use a more thoughtful model\n"
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
        "btn_save": "💾 Save privately",
        "btn_relay": "💌 Pass it on gently",

        # Approval flow
        "approved": "✅ Approved! It will be delivered in ~{minutes} minutes.",
        "approved_now": "✅ Approved! Delivering now.",
        "discarded": "🗑️ Draft discarded.",
        "edit_prompt": "Send me the edited version:",
        "edit_updated": "📝 Updated draft:\n\n{draft}\n\nApprove, edit, or discard?",

        # Delivery
        "relay_delivered": "✅ Your message was delivered to your partner.",

        # Ask user (no tag)
        "ask_action": "What would you like to do with this?",

        # Misc
        "save_done": "💾 Saved privately.",
        "error_generic": "Something went wrong. Please try again.",
        "not_paired": "You need to pair with a partner first. Use /start to get a code.",
        "usage_warning": (
            "⚠️ Heads up: we've used {count}/{cap} heavy model calls today. "
            "Remaining calls will use the standard model."
        ),
        "no_user": "Please run /start first to set up.",
    },

    "te": {
        # Onboarding
        "welcome": (
            "👋 pAIrtner కి స్వాగతం!\n\n"
            "మీ జత కోడ్: <b>{code}</b>\n"
            "మీ భాగస్వామికి ఈ కోడ్ ఇవ్వండి: /join {code}"
        ),
        "not_therapy": (
            "⚠️ <b>ఈ బాట్ వృత్తిపరమైన థెరపీకి ప్రత్యామ్నాయం కాదు.</b>"
        ),
        "crisis_resources": (
            "🆘 <b>సంక్షోభ సహాయం (India):</b>\n"
            "• iCall: 9152987821\n"
            "• Vandrevala Foundation: 1860-2662-345\n"
            "• AASRA: 9820466726"
        ),
        "already_paired": "మీరు ఇప్పటికే {partner_name} తో జత చేయబడ్డారు.",

        # Pairing
        "join_success": "🎉 మీరు ఇప్పుడు {partner_name} తో జత చేయబడ్డారు!",
        "join_partner_notify": "🎉 {partner_name} చేరారు! మీరు ఇప్పుడు జత.",
        "join_invalid_code": "❌ చెల్లని కోడ్. మీ భాగస్వామిని అడగండి.",
        "join_need_code": "వాడకం: /join <code>",
        "join_already_paired": "మీరు ఇప్పటికే జత చేయబడ్డారు.",
        "join_self": "మీరు మీతోనే జత చేయలేరు!",

        # Tell / Relay
        "tell_no_partner": "ముందు భాగస్వామితో జత చేయండి. /start వాడండి.",
        "tell_need_message": "వాడకం: /tell <సందేశం>",
        "tell_draft_header": "📝 మీ భాగస్వామి కోసం మృదువైన సందేశం:\n\n",
        "relay_sent_to_partner": "💌 మీ భాగస్వామి నుండి సందేశం:\n\n{message}",

        # Mediate
        "mediate_no_partner": "ముందు భాగస్వామితో జత చేయండి.",
        "mediate_no_shared": "ఇంకా షేర్డ్ సందర్భం లేదు.",

        # Facts
        "whatdoyouknow_header": "📋 <b>నాకు మీ గురించి తెలిసినవి:</b>\n\n",
        "whatdoyouknow_private": "🔒 <b>ప్రైవేట్:</b>\n",
        "whatdoyouknow_shared": "🔓 <b>షేర్డ్:</b>\n",
        "whatdoyouknow_empty": "మీ గురించి ఇంకా ఏమీ లేదు. నాతో మాట్లాడండి!",
        "fact_item": "  {idx}. {text} <i>(id: {id})</i>\n",
        "fact_saved": "✅ {count} కొత్త విషయాలు సేవ్ అయ్యాయి.",
        "keep_saved": "✅ సేవ్ అయింది!",

        # Forget
        "forget_confirm_all": "⚠️ మీ అన్ని విషయాలు ({count}) తొలగించబడతాయి. మళ్ళీ /forget all పంపండి.",
        "forget_all_done": "🗑️ {count} విషయాలు తొలగించబడ్డాయి.",
        "forget_done": "🗑️ విషయం #{id} తొలగించబడింది.",
        "forget_not_found": "❌ విషయం కనుగొనబడలేదు.",
        "forget_usage": "వాడకం: /forget <id> లేదా /forget all",

        # Export
        "export_empty": "ఎగుమతి చేయడానికి విషయాలు లేవు.",
        "export_header": "# నా విషయాల ఎగుమతి\n\n",
        "export_caption": "మీ ఎగుమతి చేసిన విషయాలు ({count} మొత్తం).",

        # Help
        "help": (
            "📖 <b>pAIrtner ఆదేశాలు:</b>\n"
            "/start — జత కోడ్ పొందండి\n"
            "/join — భాగస్వామితో జత చేయండి\n"
            "/tell — భాగస్వామికి మృదువుగా సందేశం పంపండి\n"
            "/mediate — తటస్థ దృక్పథం పొందండి\n"
            "/whatdoyouknow — నేను ఏమి గుర్తుంచుకుంటున్నాను\n"
            "/forget — మీ విషయాలు తొలగించండి\n"
            "/export — మీ విషయాలు డౌన్‌లోడ్ చేయండి\n"
            "/lang — భాష మార్చండి\n"
            "/help — ఈ సందేశం చూపించండి"
        ),

        # Language
        "lang_changed": "✅ భాష తెలుగుకు మార్చబడింది.",
        "lang_usage": "వాడకం: /lang <en|te>",

        # Approval buttons
        "btn_approve": "✅ ఆమోదించు",
        "btn_edit": "✏️ మార్చు",
        "btn_discard": "❌ రద్దు",
        "btn_save": "💾 ప్రైవేటుగా సేవ్",
        "btn_relay": "💌 మృదువుగా పంపు",

        # Approval flow
        "approved": "✅ ఆమోదించబడింది! ~{minutes} నిమిషాల్లో డెలివరీ.",
        "approved_now": "✅ ఆమోదించబడింది! ఇప్పుడే డెలివరీ.",
        "discarded": "🗑️ డ్రాఫ్ట్ రద్దు.",
        "edit_prompt": "మార్చిన వెర్షన్ పంపండి:",
        "edit_updated": "📝 అప్‌డేట్ చేసిన డ్రాఫ్ట్:\n\n{draft}",

        # Delivery
        "relay_delivered": "✅ మీ సందేశం డెలివరీ అయింది.",

        # Ask user (no tag)
        "ask_action": "దీనితో ఏమి చేయాలి?",

        # Misc
        "save_done": "💾 ప్రైవేటుగా సేవ్ అయింది.",
        "error_generic": "ఏదో తప్పు జరిగింది. మళ్ళీ ప్రయత్నించండి.",
        "not_paired": "ముందు భాగస్వామితో జత చేయండి. /start వాడండి.",
        "usage_warning": "⚠️ ఈ రోజు {count}/{cap} హెవీ కాల్స్ వాడబడ్డాయి.",
        "no_user": "ముందు /start నొక్కండి.",
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
