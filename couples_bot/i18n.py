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
            "please reach out to professional help.\n\n"
            "🔒 <b>Privacy note:</b> Messages pass through Telegram servers "
            "(not end-to-end encrypted) and are processed by Google Gemini AI. "
            "Your facts are stored locally on the bot server."
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
        "fact_item": "  {id}. [{date}] {text} <i>({visibility})</i>\n",
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
            "/setup — Configure your couple's Gemini API key\n"
            "/status — Check your relationship dashboard\n"
            "/pause — Pause relay delivery\n"
            "/resume — Resume relay delivery\n"
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
        "setup_usage": "Usage: /setup gemini_key &lt;your-key&gt; or /setup gemini_key clear",
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
    },

    "te": {
        # Onboarding
        "welcome": (
            "👋 pAIrtner కి స్వాగతం!\n\n"
            "మీ జత కోడ్: <b>{code}</b>\n"
            "మీ భాగస్వామికి ఈ కోడ్ ఇవ్వండి: /join {code}"
        ),
        "not_therapy": (
            "⚠️ <b>ఈ బాట్ వృత్తిపరమైన థెరపీకి ప్రత్యామ్నాయం కాదు.</b>\n\n"
            "🔒 <b>గోప్యతా గమనిక:</b> సందేశాలు Telegram సర్వర్ల ద్వారా వెళ్తాయి "
            "(ఎండ్-టు-ఎండ్ ఎన్‌క్రిప్ట్ చేయబడవు) మరియు Google Gemini AI ద్వారా "
            "ప్రాసెస్ చేయబడతాయి. మీ విషయాలు బాట్ సర్వర్‌లో స్థానికంగా నిల్వ చేయబడతాయి."
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
        "fact_item": "  {id}. [{date}] {text} <i>({visibility})</i>\n",
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
            "/setup — మీ Gemini API కీ సెట్ చేయండి\n"
            "/status — మీ స్థితి చూడండి\n"
            "/pause — డెలివరీ ఆపు\n"
            "/resume — డెలివరీ కొనసాగించు\n"
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

        # Status
        "status_header": "📊 <b>మీ స్థితి</b>\n\n",
        "status_partner": "👫 భాగస్వామి: {partner_name}\n",
        "status_facts": "📋 విషయాలు: {private} ప్రైవేట్, {shared} షేర్డ్\n",
        "status_pending": "📬 పెండింగ్ రిలేలు: {count}\n",
        "status_usage": "📈 ఈ రోజు API కాల్స్: {count}\n",
        "status_partner_active": "🕐 భాగస్వామి చివరి యాక్టివ్: {time}\n",
        "status_not_paired": "మీరు ఇంకా జత చేయబడలేదు. /start వాడండి.",
        "status_paused_note": "⏸️ మీ డెలివరీ ప్రస్తుతం ఆపబడింది.\n",
        "status_partner_paused_note": "⏸️ మీ భాగస్వామి డెలివరీ ఆపారు.\n",

        # Pause / Resume
        "paused_confirm": "⏸️ డెలివరీ ఆపబడింది. ఆమోదించిన సందేశాలు /resume వరకు ఉంటాయి.",
        "resumed_confirm": "▶️ డెలివరీ పునఃప్రారంభించబడింది!",
        "partner_paused_notice": (
            "ℹ️ మీ భాగస్వామి డెలివరీ ఆపారు. మెసేజ్ ఆమోదించబడింది, "
            "వారు resume చేసిన తర్వాత డెలివరీ అవుతుంది."
        ),
        "delivery_paused_notice": (
            "ℹ️ డెలివరీ ప్రస్తుతం ఆపబడింది. మెసేజ్ ఆమోదించబడింది, "
            "resume చేసిన తర్వాత డెలివరీ అవుతుంది."
        ),

        # Setup (multi-tenant BYO key)
        "setup_usage": "వాడకం: /setup gemini_key <మీ-కీ> లేదా /setup gemini_key clear",
        "setup_key_stored": "🔑 Gemini API కీ సేవ్ అయింది: {api_key}",
        "setup_key_cleared": "🔑 Gemini API కీ తొలగించబడింది. గ్లోబల్ కీ వాడబడుతుంది.",
        "setup_key_not_set": "🔑 కస్టమ్ Gemini API కీ సెట్ చేయబడలేదు. గ్లోబల్ కీ వాడబడుతుంది.",
        "setup_show_config": "🔑 ప్రస్తుత Gemini API కీ: {api_key}",
        "setup_not_paired": "ముందు భాగస్వామితో జత చేయండి. /start వాడండి.",
        "setup_no_encryption_key": (
            "⚠️ సర్వర్‌లో DB_ENCRYPTION_KEY సెట్ చేయబడలేదు. "
            "API కీలను భద్రంగా నిల్వ చేయలేము. బాట్ అడ్మిన్‌ను సంప్రదించండి."
        ),

        "no_user": "ముందు /start నొక్కండి.",

        # Batch review (cooldown accumulation)
        "batch_review_prompt": (
            "📦 {partner} కోసం {count} సందేశం(లు) క్యూలో ఉన్నాయి. "
            "పంపే ముందు సమీక్షించాలా?"
        ),
        "btn_send_all": "📤 అన్నీ పంపు",
        "btn_review_each": "🔍 ఒక్కొక్కటి చూడు",
        "btn_discard_all": "🗑️ అన్నీ రద్దు",
        "btn_item_approve": "✅",
        "btn_item_edit": "✏️",
        "btn_item_discard": "❌",
        "batch_sent": "✅ బ్యాచ్ మీ భాగస్వామికి డెలివరీ అయింది!",
        "batch_discarded": "🗑️ బ్యాచ్ రద్దు చేయబడింది.",
        "batch_item_review": "📝 సందేశం {index}/{total}:\n\n{draft}",

        # Rate limiting
        "rate_limited": (
            "మీరు చాలా సందేశాలు పంపుతున్నారు. "
            "కొంచెం విశ్రాంతి తీసుకోండి — నేను ఇక్కడే ఉంటాను."
        ),
        "rate_limit_day": "రోజువారీ సందేశ పరిమితి చేరుకుంది. రేపు కలుద్దాం!",
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
