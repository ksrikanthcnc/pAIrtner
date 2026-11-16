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
            "/conflicts — View open discussion topics\n\n"
            "<b>Memory</b>\n"
            "/whatdoyouknow — See what I remember about you\n"
            "/forget &lt;id|all&gt; — Delete your facts\n"
            "/export — Download your facts as a file\n"
            "/remember — Revisit a positive shared memory\n"
            "/goal &lt;text&gt; — Set a personal goal\n"
            "/together &lt;text&gt; — Set a shared goal with your partner\n"
            "/goals — View your active goals\n\n"
            "<b>Personalization</b>\n"
            "/settings — Open settings menu\n"
            "/style — Change your voice and intensity\n"
            "/mood &lt;mood&gt; — Set your mood (stressed|happy|angry|reflective)\n"
            "/setup — Configure Gemini API keys\n"
            "/skip — Skip onboarding questions\n\n"
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
            "🎉 Great, you're paired! Let me get to know you a little. "
            "Answer a few quick questions (or send /skip to skip)."
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

        # A/B rewrite (FEAT-003 batch4)
        "ab_option_header": "📝 Here are two versions for your partner:\n\n",
        "ab_option_a": "<b>Option A</b> (lighter touch):\n{draft}\n\n",
        "ab_option_b": "<b>Option B</b> (more softened):\n{draft}",
        "btn_send_a": "📤 Send A",
        "btn_send_b": "📤 Send B",

        # Read receipts (FEAT-003 batch4)
        "history_acknowledged": " ✓",
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
        "emergency_response": (
            "💛 మీరు ఇప్పుడు చాలా కష్టమైన సమయంలో ఉన్నారని నాకు తెలుసు. "
            "మీ గురించి నాకు శ్రద్ధ ఉంది, మీకు సరైన సహాయం అందాలని "
            "కోరుకుంటున్నాను.\n\n"
            "🆘 <b>దయచేసి సహాయం కోసం సంప్రదించండి:</b>\n"
            "• <b>iCall:</b> 9152987821\n"
            "• <b>Vandrevala Foundation:</b> 1860-2662-345\n"
            "• <b>Emergency:</b> 112\n\n"
            "మీరు ఒంటరిగా ఈ పరిస్థితి ఎదుర్కొనవలసిన అవసరం లేదు. "
            "వీరు సహాయం చేయడానికి శిక్షణ పొందారు, ఇప్పుడే అందుబాటులో ఉన్నారు."
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
            "📖 <b>pAIrtner ఆదేశాలు:</b>\n\n"
            "<b>ప్రాథమిక</b>\n"
            "/start — జత కోడ్ పొందండి\n"
            "/join — భాగస్వామితో జత చేయండి\n"
            "/help — ఈ సందేశం చూపించండి\n"
            "/lang — భాష మార్చండి\n"
            "/status — మీ స్థితి చూడండి\n\n"
            "<b>కమ్యూనికేషన్</b>\n"
            "/tell — భాగస్వామికి మృదువుగా సందేశం పంపండి\n"
            "/mediate — తటస్థ దృక్పథం పొందండి\n"
            "/pause — డెలివరీ ఆపు\n"
            "/resume — డెలివరీ కొనసాగించు\n"
            "/history — ఇటీవల పంపిన/అందుకున్న సందేశాలు\n"
            "/conflicts — ఓపెన్ చర్చా అంశాలు చూడండి\n\n"
            "<b>జ్ఞాపకం</b>\n"
            "/whatdoyouknow — నేను ఏమి గుర్తుంచుకుంటున్నాను\n"
            "/forget — మీ విషయాలు తొలగించండి\n"
            "/export — మీ విషయాలు డౌన్‌లోడ్ చేయండి\n"
            "/remember — మంచి జ్ఞాపకాన్ని చూడండి\n"
            "/goal — వ్యక్తిగత లక్ష్యం సెట్ చేయండి\n"
            "/together — భాగస్వామ్య లక్ష్యం సెట్ చేయండి\n"
            "/goals — మీ లక్ష్యాలు చూడండి\n\n"
            "<b>వ్యక్తిగతీకరణ</b>\n"
            "/settings — సెట్టింగ్స్ మెనూ\n"
            "/style — మీ వాయిస్ మరియు ఇంటెన్సిటీ మార్చండి\n"
            "/mood — మీ మూడ్ సెట్ చేయండి\n"
            "/setup — Gemini API కీలు సెట్ చేయండి\n"
            "/skip — ఆన్‌బోర్డింగ్ ప్రశ్నలు దాటండి"
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
        "status_voice": "🎨 వాయిస్: {voice} (ఇంటెన్సిటీ {intensity})\n",
        "status_mood": "🎭 మూడ్: {mood}\n",
        "status_dampening": "📉 డాంపెనింగ్: {level:.2f}\n",

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
        "setup_usage": "వాడకం: /setup [my-key|our-key] <మీ-కీ|clear>",
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

        # Blunt mode
        "blunt_disclaimer": "🔥 బ్లంట్ మోడ్ — నా నిజాయితీ అభిప్రాయం:",

        # History
        "history_header": "📜 <b>ఇటీవల సందేశాలు:</b>\n\n",
        "history_empty": "ఇంకా పంపిన లేదా అందుకున్న సందేశాలు లేవు.",
        "history_sent": "[{date}] మీరు → భాగస్వామి: \"{message}\"\n",
        "history_received": "[{date}] భాగస్వామి → మీరు: \"{message}\"\n",
        "history_usage": "వాడకం: /history [పరిమితి]",

        # Fact categories
        "cat_relationship": "❤️ సంబంధం",
        "cat_household": "🏠 గృహం",
        "cat_emotional": "💭 భావోద్వేగం",
        "cat_work": "💼 పని",
        "cat_health": "🏥 ఆరోగ్యం",
        "cat_other": "📌 ఇతర",

        # Digest
        "digest_header": "📋 <b>రోజువారీ సారాంశం</b>",
        "digest_no_activity": "ఈ రోజు కొత్త కార్యకలాపం లేదు.",

        # Check-in
        "checkin_questions_header": "🔄 <b>వారపు చెక్-ఇన్</b>\nమీ వారం గురించి ఆలోచించండి:",
        "checkin_question_item": "{index}. {question}",
        "checkin_waiting_partner": "✅ మీ సమాధానాలు అందాయి! మీ భాగస్వామి కోసం వేచి ఉన్నాము.",
        "checkin_summary_header": "🔄 <b>వారపు చెక్-ఇన్ సారాంశం</b>",
        "checkin_both_answered": "✅ మీ చెక్-ఇన్ సమాధానం నమోదు అయింది.",

        # Onboarding interview
        "onboard_welcome": (
            "🎉 బాగుంది, మీరు జత చేయబడ్డారు! మిమ్మల్ని కొంచెం తెలుసుకుంటాను. "
            "కొన్ని ప్రశ్నలకు సమాధానం ఇవ్వండి (లేదా /skip పంపండి)."
        ),
        "onboard_q_relationship_type": "మీ సంబంధం ఏ రకం? (married / dating / other)",
        "onboard_q_duration": "ఎన్ని సంవత్సరాలుగా కలిసి ఉన్నారు? (సంఖ్య మాత్రమే)",
        "onboard_q_kids": "పిల్లలు ఉన్నారా? (yes / no)",
        "onboard_q_age_range": "మీ వయస్సు శ్రేణి? (ఉదా. 25-30)",
        "onboard_q_appreciate": "మీ భాగస్వామిలో మీకు ఎక్కువగా నచ్చే ఒక విషయం చెప్పండి.",
        "onboard_q_wish": "సంబంధం కోసం మీ ఒక కోరిక ఏమిటి?",
        "onboard_complete": (
            "✅ ధన్యవాదాలు! మన చాట్‌లో ఇది గుర్తుంచుకుంటాను. "
            "మాట్లాడటం మొదలుపెట్టడానికి ఏదైనా సందేశం పంపండి."
        ),
        "onboard_skip": "👍 పర్వాలేదు, దాటేశాం! తర్వాత ఎప్పుడైనా ప్రొఫైల్ అప్‌డేట్ చేయవచ్చు.",

        # Style / Voice
        "style_header": "🎨 <b>మీ కమ్యూనికేషన్ స్టైల్</b>\n\n",
        "style_current": "వాయిస్: <b>{voice}</b> | ఇంటెన్సిటీ: <b>{intensity}</b>",
        "style_voice_set": "✅ వాయిస్ <b>{voice}</b> కి అప్‌డేట్ అయింది.",
        "style_intensity_set": "✅ ఇంటెన్సిటీ <b>{intensity}</b> కి అప్‌డేట్ అయింది.",

        # Mood
        "mood_set": "🎭 మూడ్ <b>{mood}</b> కి సెట్ అయింది. ~{hours}గంటల్లో ఎక్స్‌పైర్ అవుతుంది.",
        "mood_current": "🎭 ప్రస్తుత మూడ్: <b>{mood}</b> ({expires} లో ఎక్స్‌పైర్).",
        "mood_none": "యాక్టివ్ మూడ్ లేదు. /mood <stressed|happy|angry|reflective> వాడండి.",
        "mood_expired": "మీ మునుపటి మూడ్ ఎక్స్‌పైర్ అయింది.",
        "mood_usage": "వాడకం: /mood [stressed|happy|angry|reflective]",

        # Articulation
        "articulation_tip": "💡 సూచన: {tip}",

        # Fact types
        "fact_type_transient": "⏱️ ప్రస్తుత / తాత్కాలిక",
        "fact_type_situational": "📅 పరిస్థితిగత",
        "fact_type_stable": "📌 స్థిర",
        "fact_expiring": "⏳ {time} లో ఎక్స్‌పైర్",

        # Fact contradiction
        "fact_contradiction": (
            "🤔 ఒక విషయం గమనించాను: మీరు ఇంతకు ముందు \"{old_fact}\" "
            "అని చెప్పారు కానీ ఇప్పుడు \"{new_fact}\". ఏది ప్రస్తుతం?"
        ),
        "btn_keep_new": "🆕 కొత్తది ఉంచు",
        "btn_keep_both": "🔄 రెండూ ఉంచు",
        "btn_keep_old": "⏮️ పాతది ఉంచు",
        "fact_contradiction_resolved": "✅ అర్థమైంది, అప్‌డేట్ అయింది!",

        # Goals (FEAT-004)
        "goal_created": "🎯 వ్యక్తిగత లక్ష్యం సెట్ అయింది: {text}",
        "goal_shared_created": "🎯 భాగస్వామ్య లక్ష్యం సెట్ అయింది: {text}",
        "goal_shared_notify": "🎯 {partner_name} భాగస్వామ్య లక్ష్యం సెట్ చేశారు: {text}",
        "goals_header": "🎯 <b>మీ లక్ష్యాలు</b>\n\n",
        "goals_personal": "<b>వ్యక్తిగత:</b>\n",
        "goals_shared": "<b>భాగస్వామ్య:</b>\n",
        "goals_empty": "యాక్టివ్ లక్ష్యాలు లేవు. /goal వాడి సెట్ చేయండి!",
        "goal_achieved": "✅ లక్ష్యం సాధించబడింది!",
        "goal_dropped": "❌ లక్ష్యం తొలగించబడింది.",
        "goal_usage": "వాడకం: /goal <మీ లక్ష్యం>",
        "together_usage": "వాడకం: /together <భాగస్వామ్య లక్ష్యం>",
        "together_no_partner": "ముందు భాగస్వామితో జత చేయండి. /start వాడండి.",

        # Settings (FEAT-004)
        "settings_header": "⚙️ <b>సెట్టింగ్స్</b>\n\nవర్గం ఎంచుకోండి:",
        "settings_voice": "🗣 వాయిస్",
        "settings_dampening": "🎚 డాంపెనింగ్",
        "settings_goals": "🎯 లక్ష్యాలు",
        "settings_profile": "👤 ప్రొఫైల్",
        "settings_mood": "🎭 మూడ్ ప్రీసెట్స్",
        "settings_dampening_current": "🎚 ప్రస్తుత డాంపెనింగ్ బేస్: <b>{level:.1f}</b>",
        "settings_dampening_updated": "🎚 డాంపెనింగ్ <b>{level:.1f}</b> కి అప్‌డేట్ అయింది.",
        "settings_profile_current": (
            "👤 <b>మీ ప్రొఫైల్</b>\n"
            "రకం: {relationship_type}\n"
            "వ్యవధి: {duration_years} సంవత్సరాలు\n"
            "పిల్లలు: {has_kids}\n"
            "వయస్సు: {age_range}\n\n"
            "ఎడిట్ చేయడానికి ఫీల్డ్ నొక్కండి:"
        ),
        "settings_profile_updated": "✅ ప్రొఫైల్ అప్‌డేట్: {field} = {value}",

        # Setup keys (FEAT-004)
        "setup_my_key_stored": "🔑 మీ వ్యక్తిగత Gemini కీ సేవ్ అయింది: {api_key}",
        "setup_my_key_cleared": "🔑 మీ వ్యక్తిగత Gemini కీ తొలగించబడింది.",
        "setup_our_key_stored": "🔑 కపుల్ Gemini కీ సేవ్ అయింది: {api_key}",
        "setup_our_key_cleared": "🔑 కపుల్ Gemini కీ తొలగించబడింది.",
        "setup_tier_info": (
            "🔑 <b>API కీ స్థితి</b>\n"
            "మీ కీ: {user_key}\n"
            "కపుల్ కీ: {couple_key}\n"
            "యాక్టివ్ టైర్: <b>{active_tier}</b>"
        ),

        # Remember (FEAT-001 batch4)
        "remember_header": "💭 <b>ఒక భాగస్వామ్య జ్ఞాపకం</b>",
        "remember_empty": "ఇంకా భాగస్వామ్య జ్ఞాపకాలు లేవు. నాతో మాట్లాడి కొన్ని క్షణాలు పంచుకోండి!",
        "remember_item": "✨ {text}",

        # Nudges (FEAT-001 batch4)
        "nudge_appreciation": "💛 మీ {partner_name} గురించి ఇప్పుడు మీకు నచ్చే ఒక విషయం ఏమిటి?",
        "nudge_generic": "💬 విషయాలు ఎలా ఉన్నాయి? ఏదైనా మనసులో ఉందా?",

        # Horsemen / appreciation (FEAT-001 batch4)
        "horsemen_tip": "💡 {tip}",
        "appreciation_nudge": "💛 మీ సంబంధం గురించి ఒక మంచి విషయం గురించి ఆలోచించండి.",
        "appreciation_trend_positive": "ఈ వారం మీ సానుకూల సంభాషణలు బాగున్నాయి! 🌟",
        "appreciation_trend_negative": "ఈ వారం కొంచెం ఒత్తిడిగా ఉంది. మంచి క్షణాలపై దృష్టి పెట్టండి. 💛",
        "conflicts_header": "⚡ <b>కమ్యూనికేషన్ నమూనాలు</b>",
        "conflicts_empty": "ఇప్పుడు ఓపెన్ టాపిక్స్ లేవు. అంతా బాగుంది! 🌟",
        "conflicts_item": "• <b>{theme}</b> — {duration} నుండి ఓపెన్ ({relay_count} రిలేలు)\n",
        "conflicts_resolved_note": "✅ పరిష్కరించబడింది: {theme}",
        "checkin_conflicts_summary": "ఈ వారం, {total} చర్చా అంశాలలో {resolved} పరిష్కరించబడ్డాయి. ఇంకా ఓపెన్: {open_themes}.",

        # Feedback loop (FEAT-003 batch4)
        "feedback_ask": "🔄 మీ రిలే \"{topic}\" గురించి డెలివరీ అయింది. బాగా చేరిందా?",
        "feedback_yes": "👍 అవును",
        "feedback_not_sure": "🤷 తెలియదు",
        "feedback_no": "👎 కాదు",
        "feedback_thanks": "ఫీడ్‌బ్యాక్ కోసం ధన్యవాదాలు! ఇది మెరుగుపరచడంలో సహాయపడుతుంది. 🙏",

        # A/B rewrite (FEAT-003 batch4)
        "ab_option_header": "📝 మీ భాగస్వామి కోసం రెండు వెర్షన్లు:\n\n",
        "ab_option_a": "<b>ఆప్షన్ A</b> (తేలికైన):\n{draft}\n\n",
        "ab_option_b": "<b>ఆప్షన్ B</b> (మరింత మృదువైన):\n{draft}",
        "btn_send_a": "📤 A పంపు",
        "btn_send_b": "📤 B పంపు",

        # Read receipts (FEAT-003 batch4)
        "history_acknowledged": " ✓",
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
