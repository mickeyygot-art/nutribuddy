import os
import base64
import time
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, HTTPException
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi, MessagingApiBlob,
    ReplyMessageRequest, PushMessageRequest, TextMessage,
)
from linebot.v3.webhooks import (
    MessageEvent, TextMessageContent, ImageMessageContent, FollowEvent,
)
from linebot.v3.exceptions import InvalidSignatureError
import anthropic
from apscheduler.schedulers.background import BackgroundScheduler
import pytz

from database import (
    get_or_create_user, update_user_goal, update_user_language,
    log_meal, get_today_meals, get_meals_by_date_range, get_week_meals,
    is_blocked, clear_block, force_block, increment_off_topic, get_all_users,
    update_last_meal_type, update_last_active, log_event, supabase,
)

app = FastAPI()

configuration = Configuration(access_token=os.environ["LINE_CHANNEL_ACCESS_TOKEN"].strip())
handler = WebhookHandler(os.environ["LINE_CHANNEL_SECRET"].strip())
claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"].strip())

BKK = pytz.timezone("Asia/Bangkok")

# ── COPY ──────────────────────────────────────────────────────────────────────

ONBOARDING_MSG_1 = """สวัสดี! ฉันคือ NutriBuddy เพื่อนด้านสุขภาพของคุณบน LINE 🥑

ก่อนเริ่ม — เป้าหมายของคุณคืออะไร?
1️⃣ ลดน้ำหนัก
2️⃣ กินอาหารคลีนมากขึ้น
3️⃣ เพิ่มกล้ามเนื้อ
4️⃣ ยังไม่มีเป้าหมาย ขอแค่รู้ว่ากินอะไรอยู่

(You can also chat with me in English anytime!)"""

ONBOARDING_MSG_2 = """นี่คือสิ่งที่ NutriBuddy ทำได้

ส่งรูปอาหาร — ฉันจะบอกว่าโภชนาการเป็นยังไงและมีคำแนะนำสำหรับมื้อต่อไป
บอกชื่อเมนู — พิมพ์ก็ได้ ไม่ต้องมีรูปเสมอไป
ถามประวัติ — "เมื่อเช้ากินอะไร" หรือ "เมื่อวานกินอะไรบ้าง"
เปลี่ยนเป้าหมาย — บอกได้เลยตลอดเวลา
สรุปรายวัน — ทุกคืน 20.00 น. ฉันจะส่งสรุปมื้ออาหารวันนี้ให้
สรุปรายสัปดาห์ — ทุกวันจันทร์ 8.00 น. ฉันจะส่งภาพรวมของสัปดาห์ที่ผ่านมา"""

BLOCKED_TH = "NutriBuddy พักอยู่ 6 ชั่วโมงนะ กลับมาคุยเรื่องอาหารด้วยกันทีหลังได้เลย 🌿"
BLOCKED_EN = "NutriBuddy is resting for 6 hours. Come back and let's talk food! 🌿"

WARN_2_TH = "ดูเหมือนเราออกนอกเรื่องอาหารกันไปสักหน่อยแล้ว 😅 NutriBuddy ช่วยได้แค่เรื่องโภชนาการนะ ถ้าถามนอกเรื่องอีก ฉันจะหยุดตอบชั่วคราว 6 ชั่วโมง"
WARN_2_EN = "We've gone off-topic a couple of times! NutriBuddy only covers food and nutrition. One more and I'll take a 6-hour break 😅"

OFFTOPIC_TH = "NutriBuddy ช่วยเรื่องอาหารและสุขภาพเท่านั้นนะ — ส่งรูปอาหารมาได้เลย! 🍽️"
OFFTOPIC_EN = "NutriBuddy only covers food and health — send me a food photo! 🍽️"

HISTORY_LIMIT_TH = "NutriBuddy เก็บประวัติอาหารได้แค่ 30 วันย้อนหลังนะ"
HISTORY_LIMIT_EN = "NutriBuddy keeps meal history for the last 30 days only."

TOO_LONG_TH = "ข้อความยาวเกินไปนิดนึงนะ ลองส่งสั้นๆ หรือส่งรูปอาหารมาได้เลย 🍽️"
TOO_LONG_EN = "That message is a bit long! Try a shorter question or just send a food photo 🍽️"

UNBLOCK_TH = "ยินดีต้อนรับกลับนะ! ส่งรูปอาหารมาได้เลย 🍽️"
UNBLOCK_EN = "Welcome back! Send me a food photo anytime 🍽️"

UNKNOWN_DISH_TH = "(ไม่แน่ใจชื่อเมนูนี้ — ช่วยบอกชื่อด้วยได้มั้ย?)"
UNKNOWN_DISH_EN = "(Not sure what this dish is — could you tell me the name?)"

WEEKLY_NO_MEALS_TH = "สัปดาห์นี้ยังไม่ได้ส่งรูปอาหารมาเลยนะ — สัปดาห์หน้าลองเริ่มด้วยมื้อเดียวก็ได้ 🍽️"
WEEKLY_NO_MEALS_EN = "No meals logged this week — next week, try starting with just one photo 🍽️"

UNBLOCK_KEYWORDS = {
    "เริ่มใหม่", "ขอโทษ", "ยกเลิก", "unblock",
    "start", "restart", "sorry",
}

# YOL-31: Meal keyword → meal_type mapping (longest keys checked first)
MEAL_KEYWORDS = [
    ("late night", "late_snack"),
    ("อาหารเช้า", "breakfast"), ("มื้อเช้า", "breakfast"),
    ("อาหารกลางวัน", "lunch"), ("มื้อกลางวัน", "lunch"),
    ("อาหารเย็น", "dinner"), ("มื้อเย็น", "dinner"),
    ("กลางวัน", "lunch"), ("เที่ยง", "lunch"),
    ("เช้า", "breakfast"), ("breakfast", "breakfast"), ("morning", "breakfast"),
    ("เย็น", "dinner"), ("dinner", "dinner"), ("evening", "dinner"),
    ("ของว่าง", "snack"), ("สแนค", "snack"), ("snack", "snack"), ("midday", "lunch"),
    ("ดึก", "late_snack"), ("lunch", "lunch"),
]

CONVERSATIONAL_WHITELIST = {
    "โอเค", "ok", "okay", "ได้", "ครับ", "ค่ะ", "นะ",
    "yes", "no", "ใช่", "ไม่", "ขอบคุณ", "thanks",
    "บอกไปแล้ว", "บอกแล้ว", "แล้ว", "เข้าใจ",
}

DASHBOARD_KEYWORDS = {
    "ดูสรุป", "สรุปของฉัน", "ดูประวัติ", "กินอะไรไปบ้าง", "สถิติ",
    "my summary", "show summary", "my stats", "dashboard", "my history",
}

NO_MEALS_DASHBOARD_TH = "ยังไม่มีข้อมูลอาหารใน 7 วันที่ผ่านมาเลยนะ ลองส่งรูปอาหารมาให้ดูได้เลย 🍽️"
NO_MEALS_DASHBOARD_EN = "No meals logged in the last 7 days yet — try sending a food photo! 🍽️"

# Bare digits only match when the whole message IS that digit (onboarding reply).
# Matching them as substrings silently changed goals on messages like "กินข้าว 2 จาน".
GOAL_DIGITS = {
    "1": "lose_weight", "2": "eat_clean", "3": "build_muscle", "4": "no_goal",
}

# Phrase keys match as substrings (intentional — "อยากลดน้ำหนัก" should work).
GOAL_PHRASES = {
    "ลดน้ำหนัก": "lose_weight",   "lose weight": "lose_weight",
    "กินสะอาด": "eat_clean",       "eat clean": "eat_clean",
    "กินอาหารคลีน": "eat_clean",   "อาหารคลีน": "eat_clean",
    "เพิ่มกล้าม": "build_muscle",  "build muscle": "build_muscle",
    "ยังไม่มีเป้าหมาย": "no_goal", "no goal": "no_goal",
}

GOAL_LABEL = {
    "lose_weight":  "ลดน้ำหนัก / lose weight",
    "eat_clean":    "กินสะอาดขึ้น / eat clean",
    "build_muscle": "เพิ่มกล้ามเนื้อ / build muscle",
    "no_goal":      "ยังไม่มีเป้าหมายเฉพาะ / no specific goal",
}

SYSTEM_PROMPT = """You are NutriBuddy, a friendly health coach on LINE for Thai users.

FORMATTING — LINE does not support markdown. Violating these will break the message:
- NEVER use **, *, __, _, #, or any markdown symbols
- NEVER use bullet points or numbered lists
- Plain text only. Write naturally like an SMS or LINE message.
- Max 1 emoji per reply, placed at the end of a sentence only.

LENGTH — always finish your sentence before stopping:
- Maximum 2 sentences for photo responses
- Maximum 2 sentences for text responses
- Each sentence must be complete. Never cut off mid-thought.
- If you can only fit one complete sentence, write one sentence.

CONTENT:
- Always reply in the same language the user writes in (Thai or English)
- Only discuss food, nutrition, health goals, and eating habits. Nothing else.
- Tone: quick text from a knowledgeable friend. Warm, never clinical.
- Celebrate what is good first, then one small suggestion.
- You know Thai/SEA food well: som tam, khao man gai, pad thai, tom yum, etc.
- User's goal: {goal}"""

# ── HELPERS ───────────────────────────────────────────────────────────────────

# In-memory conversation history: {line_user_id: [{role, content}, ...]} max 10 entries
conversation_history: dict[str, list] = {}

# YOL-29: Rate limiting — {line_user_id: [datetime, ...]}
message_timestamps: dict[str, list] = {}
# YOL-29: Rapid off-topic detection — {user_id: [datetime, ...]}
off_topic_timestamps: dict[str, list] = {}


def is_thai(text: str) -> bool:
    return any("฀" <= c <= "๿" for c in text)


def detect_goal(text: str) -> str | None:
    t = text.lower().strip()
    if t in GOAL_DIGITS:            # exact standalone digit (onboarding reply)
        return GOAL_DIGITS[t]
    for key, goal in GOAL_PHRASES.items():
        if key in t:
            return goal
    return None


def clean_for_line(text: str) -> str:
    """Strip markdown and limit to 1 emoji. Applied to every outgoing message."""
    import re
    # Remove markdown
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'\*(.*?)\*', r'\1', text)
    text = re.sub(r'__(.*?)__', r'\1', text)
    text = re.sub(r'_(.*?)_', r'\1', text)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[-*]\s+', '', text, flags=re.MULTILINE)

    # Keep only the first emoji found, remove the rest
    emoji_pattern = re.compile(
        "[\U00002600-\U000027BF"
        "\U0001F300-\U0001F9FF"
        "\U0001FA00-\U0001FA9F"
        "\U00002702-\U000027B0"
        "\U0000FE00-\U0000FE0F"
        "\U0001F1E0-\U0001F1FF]+",
        flags=re.UNICODE
    )
    emojis_found = emoji_pattern.findall(text)
    if len(emojis_found) > 1:
        first_emoji = emojis_found[0]
        # Remove all emojis, then add the first one back at the end
        text = emoji_pattern.sub('', text).strip()
        text = text + ' ' + first_emoji

    return text.strip()


def is_dashboard_request(text: str) -> bool:
    """YOL-33: Returns True if user is requesting their personal summary."""
    t = text.lower().strip()
    return any(kw in t for kw in DASHBOARD_KEYWORDS)


def detect_meal_keyword(text: str) -> str | None:
    """YOL-31: Returns meal_type if a meal-time keyword is found, else None."""
    t = text.lower()
    for kw, meal_type in MEAL_KEYWORDS:
        if kw in t:
            return meal_type
    return None


def is_rate_limited(line_user_id: str) -> bool:
    """YOL-29: Returns True if user sent >10 messages in the last 60s. Silently drops if True."""
    now = datetime.now()
    cutoff = now - timedelta(seconds=60)
    ts = message_timestamps.setdefault(line_user_id, [])
    ts.append(now)
    message_timestamps[line_user_id] = [t for t in ts if t > cutoff]
    return len(message_timestamps[line_user_id]) > 10


def is_rapid_off_topic(user_id: str) -> bool:
    """YOL-29: Returns True if 3+ off-topic strikes within 120s (rapid fire batch)."""
    now = datetime.now()
    cutoff = now - timedelta(seconds=120)
    ts = off_topic_timestamps.setdefault(user_id, [])
    ts.append(now)
    off_topic_timestamps[user_id] = [t for t in ts if t > cutoff]
    return len(off_topic_timestamps[user_id]) >= 3


def is_unblock_command(text: str) -> bool:
    t = text.lower().strip()
    return any(kw in t for kw in UNBLOCK_KEYWORDS)


def is_conversational(text: str) -> bool:
    """YOL-25: Returns True if message is a short acknowledgement — skip off-topic classifier."""
    t = text.strip()
    if len(t) <= 10:
        return True
    return t.lower() in CONVERSATIONAL_WHITELIST


def parse_triage_json(raw: str) -> dict:
    """Parse the triage model output robustly (handles ```json fences / stray prose).

    Falls back to a safe default (on-topic, no meals, no history) so a bad parse
    never blocks a legitimate user — worst case they just get a normal reply.
    """
    import json
    SAFE = {"on_topic": True, "meals": [], "history_date": None}
    if not raw:
        return dict(SAFE)
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s[:4].lower() == "json":
            s = s[4:]
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end == -1 or end < start:
        return dict(SAFE)
    try:
        data = json.loads(s[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return dict(SAFE)
    if not isinstance(data, dict):
        return dict(SAFE)
    return {
        "on_topic": bool(data.get("on_topic", True)),
        "meals": data.get("meals") if isinstance(data.get("meals"), list) else [],
        "history_date": data.get("history_date") or None,
    }


def cap_history_date(date_str, today):
    """30-day window guard. Returns 'TOO_OLD' | 'YYYY-MM-DD' | None."""
    if not date_str:
        return None
    try:
        asked = datetime.strptime(str(date_str), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    if asked < today - timedelta(days=30):
        return "TOO_OLD"
    return str(date_str)


def triage_message(text: str) -> dict:
    """Single Haiku call replacing 4 separate classifiers (off-topic, meal report,
    meal extraction, date intent). Returns:
        {on_topic: bool, meals: [{dish, meal_type}], history_date: 'YYYY-MM-DD'|None}
    """
    today_bkk = datetime.now(BKK).date()
    resp = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=250,
        messages=[{"role": "user", "content":
            f"You triage messages for a Thai food/health chatbot. Today is {today_bkk} (Bangkok).\n"
            f"Return ONLY a JSON object, no other text:\n"
            f'{{"on_topic": true/false, "meals": [{{"dish": "name", "meal_type": "breakfast|lunch|dinner|snack|late_snack|null"}}], "history_date": "YYYY-MM-DD" or null}}\n'
            f"- on_topic: is it about food, nutrition, health, or eating? (greetings/acknowledgements count as on_topic)\n"
            f"- meals: dishes the user reports having EATEN. Empty list if it's a question or no meal mentioned. Use null meal_type if not stated.\n"
            f"- history_date: if they ask what they ate on a past day, resolve it (e.g. 'yesterday', 'last Monday') to a date. Use the earliest day for a range. null otherwise.\n\n"
            f"Message: {text}"}]
    )
    return parse_triage_json(resp.content[0].text)


def build_meal_history_context(meals: list, date_str: str) -> str:
    # PLAN:
    # 1. If no meals, return a "no meals logged" string for the date.
    # 2. Group by meal_type, list dish names, return formatted string.
    if not meals:
        return f"(No meals logged for {date_str})"
    by_type: dict = {}
    for m in meals:
        by_type.setdefault(m["meal_type"], []).append(m["description"])
    parts = ", ".join(
        f"{mtype.capitalize()}: {', '.join(dishes)}"
        for mtype, dishes in by_type.items()
    )
    return f"Meal history for {date_str}: {parts}"


def _reply(reply_token: str, text: str):
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message(
            ReplyMessageRequest(reply_token=reply_token, messages=[TextMessage(text=clean_for_line(text))])
        )


def _push(line_user_id: str, text: str):
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).push_message(
            PushMessageRequest(to=line_user_id, messages=[TextMessage(text=clean_for_line(text))])
        )


# ── WEBHOOK ───────────────────────────────────────────────────────────────────

@app.post("/webhook")
async def webhook(request: Request):
    signature = request.headers.get("X-Line-Signature", "")
    body = await request.body()
    try:
        handler.handle(body.decode("utf-8"), signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    return {"status": "ok"}


# ── FOLLOW (onboarding) ───────────────────────────────────────────────────────

@handler.add(FollowEvent)
def handle_follow(event):
    get_or_create_user(event.source.user_id)
    _reply(event.reply_token, ONBOARDING_MSG_1)
    time.sleep(1)
    _push(event.source.user_id, ONBOARDING_MSG_2)


# ── TEXT ──────────────────────────────────────────────────────────────────────

@handler.add(MessageEvent, message=TextMessageContent)
def handle_text(event):
    line_user_id = event.source.user_id
    text = event.message.text

    # YOL-29: Rate limit — silently drop if >10 messages/60s
    if is_rate_limited(line_user_id):
        return

    user = get_or_create_user(line_user_id)
    user_id = user["id"]
    lang = "th" if is_thai(text) else "en"

    if user["language"] != lang:
        update_user_language(line_user_id, lang)

    # YOL-35: Track last active timestamp
    try:
        update_last_active(user_id)
    except Exception as e:
        print(f"last_active update error: {e}")

    # YOL-21: Input length guard — cap at 500 chars before any Claude call
    if len(text) > 500:
        _reply(event.reply_token, TOO_LONG_TH if lang == "th" else TOO_LONG_EN)
        return

    # YOL-20: Unblock command — check BEFORE blocked gate to allow escape
    currently_blocked = is_blocked(user_id)
    if is_unblock_command(text) and currently_blocked:
        clear_block(user_id)
        try:
            log_event(user_id, "unblock")
        except Exception:
            pass
        _reply(event.reply_token, UNBLOCK_TH if lang == "th" else UNBLOCK_EN)
        return

    # Blocked?
    if currently_blocked:
        _reply(event.reply_token, BLOCKED_TH if lang == "th" else BLOCKED_EN)
        return

    # Goal change?
    goal = detect_goal(text)
    if goal:
        update_user_goal(line_user_id, goal)
        label = GOAL_LABEL[goal]
        msg = (f"บันทึกเป้าหมายใหม่แล้วนะ: {label} 💪"
               if lang == "th" else f"Goal updated: {label} 💪")
        _reply(event.reply_token, msg)
        return

    # YOL-33: In-chat dashboard — always valid, checked before off-topic classifier
    if is_dashboard_request(text):
        try:
            log_event(user_id, "dashboard_requested")
        except Exception:
            pass
        meals_7 = get_week_meals(user_id)
        if not meals_7:
            _reply(event.reply_token, NO_MEALS_DASHBOARD_TH if lang == "th" else NO_MEALS_DASHBOARD_EN)
            return
        from collections import Counter
        days = len({m["logged_at"][:10] for m in meals_7})
        dish_counts = Counter(m["description"] for m in meals_7)
        top_dishes = ", ".join(d for d, _ in dish_counts.most_common(3))
        by_type = {}
        for m in meals_7:
            by_type.setdefault(m["meal_type"], 0)
            by_type[m["meal_type"]] += 1
        missing = [mt for mt in ["breakfast", "lunch", "dinner"] if by_type.get(mt, 0) < 3]
        goal_label = GOAL_LABEL.get(user["goal"], "no specific goal")
        lang_word = "Thai" if lang == "th" else "English"
        prompt = f"""Personal 7-day food summary for a NutriBuddy user.
Days logged: {days}/7
Most eaten: {top_dishes}
Meal types often skipped (< 3 days this week): {', '.join(missing) if missing else 'none'}
Goal: {goal_label}

Write a short plain-text summary in this format (no labels, no markdown, no bullets):
Line 1: days logged warm sentence
Line 2: most eaten dishes
Line 3: one sentence on missed meal types or eating pattern
Line 4: one goal-relevant tip 🌿

Rules: max 4 sentences, max 1 emoji at the end, plain text only, reply in {lang_word}."""
        resp = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=250,
            messages=[{"role": "user", "content": prompt}],
        )
        _reply(event.reply_token, resp.content[0].text)
        return

    # Single triage call (off-topic? meals? history date?) — replaces 4 Haiku calls.
    # YOL-25: short / conversational acks skip triage entirely → straight to Sonnet.
    if is_conversational(text):
        triage = {"on_topic": True, "meals": [], "history_date": None}
    else:
        triage = triage_message(text)

    # YOL-12/29: Off-topic handling
    if not triage["on_topic"]:
        if is_rapid_off_topic(user_id):          # YOL-29: 3+ strikes within 120s → block now
            force_block(user_id)
            try:
                log_event(user_id, "block_triggered")
            except Exception:
                pass
            msg = BLOCKED_TH if lang == "th" else BLOCKED_EN
        else:
            count = increment_off_topic(user_id)
            if count >= 3:
                try:
                    log_event(user_id, "block_triggered")
                except Exception:
                    pass
                msg = BLOCKED_TH if lang == "th" else BLOCKED_EN
            elif count == 2:
                msg = WARN_2_TH if lang == "th" else WARN_2_EN
            else:
                msg = OFFTOPIC_TH if lang == "th" else OFFTOPIC_EN
        _reply(event.reply_token, msg)
        return

    # YOL-24/32: Silently log all meals reported in the message
    logged_any = False
    try:
        for entry in triage["meals"]:
            dish = (entry.get("dish") or "").strip()[:200]
            if dish:
                log_meal(user_id, dish, source="text", meal_type=entry.get("meal_type") or None)
                logged_any = True
    except Exception as e:
        print(f"Text meal log error for {user_id}: {e}")  # Non-fatal

    # YOL-31: Retroactive meal-type correction from a follow-up message (e.g. photo then
    # "อาหารเช้านะ"). Only when triage extracted no meals of its own, so we don't fight it.
    if not logged_any:
        meal_kw = detect_meal_keyword(text)
        if meal_kw:
            try:
                update_last_meal_type(user_id, meal_kw)
            except Exception as e:
                print(f"Meal type update error for {user_id}: {e}")

    # YOL-16: Meal history — inject requested day + today's meals as context
    today_date = datetime.now(BKK).date()
    today_str = str(today_date)
    hist_date = cap_history_date(triage["history_date"], today_date)
    if hist_date == "TOO_OLD":
        _reply(event.reply_token, HISTORY_LIMIT_TH if lang == "th" else HISTORY_LIMIT_EN)
        return

    meal_context_parts = []
    if hist_date:
        asked_dt = BKK.localize(datetime.strptime(hist_date, "%Y-%m-%d"))
        history_meals = get_meals_by_date_range(user_id, asked_dt, asked_dt + timedelta(days=1))
        meal_context_parts.append(build_meal_history_context(history_meals, hist_date))

    if hist_date != today_str:
        today_meals = get_today_meals(user_id)
        if today_meals:
            meal_context_parts.append(build_meal_history_context(today_meals, today_str))

    # On-topic: Claude with conversation history (YOL-19)
    goal_label = GOAL_LABEL.get(user["goal"], "no specific goal")
    system = SYSTEM_PROMPT.format(goal=goal_label)
    if meal_context_parts:
        system += "\n\nMEAL CONTEXT (use this when answering questions about what was eaten):\n" + "\n".join(meal_context_parts)

    # YOL-19: Call Claude with prior history + this turn, then commit BOTH turns only on
    # success — appending the user turn before the call corrupts history if the call throws.
    history = conversation_history.setdefault(line_user_id, [])
    resp = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        system=system,
        messages=history + [{"role": "user", "content": text}],
    )
    reply_text = resp.content[0].text

    history.append({"role": "user", "content": text})
    history.append({"role": "assistant", "content": reply_text})
    if len(history) > 10:
        conversation_history[line_user_id] = history[-10:]

    _reply(event.reply_token, reply_text)


# ── IMAGE ─────────────────────────────────────────────────────────────────────

@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image(event):
    line_user_id = event.source.user_id

    # YOL-29: Rate limit
    if is_rate_limited(line_user_id):
        return

    user = get_or_create_user(line_user_id)
    user_id = user["id"]
    lang = user["language"]

    # YOL-35: Track last active
    try:
        update_last_active(user_id)
    except Exception as e:
        print(f"last_active update error: {e}")

    if is_blocked(user_id):
        _reply(event.reply_token, BLOCKED_TH if lang == "th" else BLOCKED_EN)
        return

    with ApiClient(configuration) as api_client:
        image_bytes = MessagingApiBlob(api_client).get_message_content(message_id=event.message.id)

    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    goal_label = GOAL_LABEL.get(user["goal"], "no specific goal")

    # YOL-23: Updated vision prompt — extract full variant name including cooking method + sides
    vision_prompt = (
        "What dish is this? Start your reply with 'DISH: [full dish name including cooking method and visible sides]' on the first line. "
        "Examples: 'ข้าวมันไก่ทอด', 'ข้าวมันไก่ต้ม + ไข่ต้ม', 'กะเพราหมูสับไข่ดาว', 'Grilled salmon + steamed rice'. "
        "Be specific about cooking method (ทอด/ต้ม/ย่าง/ผัด / fried/steamed/grilled/stir-fried) when visible. "
        "Then a blank line, then your coaching response."
    )

    resp = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        system=SYSTEM_PROMPT.format(goal=goal_label),
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
            {"type": "text", "text": vision_prompt},
        ]}],
    )

    full_response = resp.content[0].text
    lines = full_response.strip().splitlines()

    # YOL-23: Extract full variant dish name; handle unknown gracefully
    dish_name = None
    coaching_text = full_response
    if lines and lines[0].upper().startswith("DISH:"):
        dish_name = lines[0][5:].strip()
        coaching_text = "\n".join(lines[2:]).strip() if len(lines) > 2 else full_response

    if not dish_name:
        # YOL-23: Unknown dish — append nudge, skip DB logging
        nudge = UNKNOWN_DISH_TH if lang == "th" else UNKNOWN_DISH_EN
        coaching_text = coaching_text + "\n" + nudge
    else:
        try:
            log_meal(user_id, dish_name, source="photo")
        except Exception as e:
            print(f"Meal log error for {user_id}: {e}")

    # YOL-19: Add photo + reply to conversation history
    history = conversation_history.setdefault(line_user_id, [])
    history.append({"role": "user", "content": "[sent a food photo]"})
    history.append({"role": "assistant", "content": coaching_text})
    if len(history) > 10:
        conversation_history[line_user_id] = history[-10:]

    _reply(event.reply_token, coaching_text)


# ── DAILY SUMMARY ─────────────────────────────────────────────────────────────

def send_daily_summaries():
    for user in get_all_users():
        try:
            meals = get_today_meals(user["id"])
            lang = user["language"]
            line_user_id = user["line_user_id"]

            if not meals:
                msg = ("วันนี้ยังไม่ได้บันทึกอาหารเลยนะ ถ้าเย็นนี้มีมื้ออร่อย ส่งรูปมาให้ดูได้เลย 🍽️"
                       if lang == "th" else
                       "No meals logged today — if you're having dinner tonight, send a photo and let's see it 🍽️")
                _push(line_user_id, msg)
                continue

            # Build structured meal summary by type
            by_type = {}
            for m in meals:
                by_type.setdefault(m["meal_type"], []).append(m["description"])
            meal_lines = "\n".join(
                f"- {mtype.capitalize()}: {', '.join(dishes)}"
                for mtype, dishes in by_type.items()
            )
            has_dinner = "dinner" in by_type
            goal_label = GOAL_LABEL.get(user["goal"], "no specific goal")
            lang_word = "Thai" if lang == "th" else "English"

            prompt = f"""Daily summary for a NutriBuddy user.
Goal: {goal_label}
Meals logged today:
{meal_lines}
{"⚠️ No dinner logged yet." if not has_dinner else ""}

Write a structured summary following this exact template:
- What you ate today — one warm sentence listing the meals (use the dish names above)
- Goal progress — one sentence connecting today's eating to their goal
{"- Dinner wish — one kind, brief sentence wishing them a healthy dinner" if not has_dinner else ""}
- Tomorrow tip — one specific, practical suggestion for tomorrow

STRICT FORMATTING — LINE does not render markdown:
- NEVER use **, *, __, _, #, or any markdown symbols
- NEVER use bullet points or numbered lists in the reply
- Plain text only, like an SMS message
- Max 1 emoji total, at the end of a sentence only
- Max 4 short sentences total
- Warm friend tone, reply in {lang_word}"""

            resp = claude.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=250,
                messages=[{"role": "user", "content": prompt}],
            )
            _push(line_user_id, resp.content[0].text)
            try:
                log_event(user["id"], "daily_summary_sent")
            except Exception:
                pass
        except Exception as e:
            print(f"Summary error for {user.get('line_user_id')}: {e}")


# ── WEEKLY SUMMARY (Monday 08:00 Bangkok = Monday 01:00 UTC) ──────────────────

def send_weekly_summaries():
    # PLAN (YOL-27):
    # 1. 0 meals → re-engagement, no Claude call
    # 2. 1-2 days → warm low-logging prefix injected into prompt
    # 3. Otherwise → full 4-part structured prompt with goal-specific guidance
    from collections import Counter
    for user in get_all_users():
        try:
            meals = get_week_meals(user["id"])
            lang = user["language"]
            line_user_id = user["line_user_id"]

            if not meals:
                msg = WEEKLY_NO_MEALS_TH if lang == "th" else WEEKLY_NO_MEALS_EN
                _push(line_user_id, msg)
                continue

            days_with_meals = len({m["logged_at"][:10] for m in meals})
            dish_counts = Counter(m["description"] for m in meals)
            top_dishes = ", ".join(d for d, _ in dish_counts.most_common(3))
            goal = user.get("goal", "no_goal")
            goal_label = GOAL_LABEL.get(goal, "no specific goal")
            lang_word = "Thai" if lang == "th" else "English"

            # Goal-specific guidance for Part 3 (observation) and Part 4 (tip)
            goal_guidance = {
                "lose_weight":  "Part 3: note balance of fried vs lighter meals. Part 4: suggest a lower-cal swap or smaller portion.",
                "eat_clean":    "Part 3: note vegetable frequency this week. Part 4: suggest adding vegetables or reducing processed food.",
                "build_muscle": "Part 3: note protein consistency across the week. Part 4: suggest a protein source to add next week.",
                "no_goal":      "Part 3: give a general positive observation about variety or habits. Part 4: suggest a general positive habit (hydration, color variety, etc.).",
            }.get(goal, "Part 3: general positive observation. Part 4: practical eating habit suggestion.")

            # Warm prefix for low-logging weeks (1-2 days)
            low_log_note = ""
            if days_with_meals <= 2:
                low_log_note = (
                    f"Note: user only logged {days_with_meals} day(s) this week. "
                    "Frame Part 1 warmly as a good start, not a failure. "
                    f"TH prefix example: 'สัปดาห์นี้เริ่มบันทึกแล้ว {days_with_meals} วัน — ดีมากที่เริ่มต้น!'"
                )

            prompt = f"""Weekly summary for a NutriBuddy user.
Goal: {goal_label}
Days logged this week: {days_with_meals}/7
Most eaten dishes: {top_dishes}
{low_log_note}

Write exactly 4 sentences — one per part, in order:
Part 1 — Days logged: warm sentence noting they logged {days_with_meals} out of 7 days (frame as progress).
Part 2 — What they ate most: one sentence naming the top dishes ({top_dishes}).
Part 3 — Goal observation: {goal_guidance.split("Part 3:")[1].split(".")[0].strip()}
Part 4 — One focus tip for this week: {goal_guidance.split("Part 4:")[1].strip()} This part must always be included.

STRICT FORMATTING — LINE does not render markdown:
- NEVER use **, *, __, _, #, or any markdown symbols
- NEVER use bullet points or numbered lists
- Plain text only, like an SMS message
- Max 1 emoji total, placed at the end of the last sentence only
- Warm friend tone, reply in {lang_word}"""

            resp = claude.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=280,
                messages=[{"role": "user", "content": prompt}],
            )
            _push(line_user_id, resp.content[0].text)
            try:
                log_event(user["id"], "weekly_summary_sent")
            except Exception:
                pass
        except Exception as e:
            print(f"Weekly summary error for {user.get('line_user_id')}: {e}")


# ── SCHEDULER (20:00 Bangkok = 13:00 UTC) ─────────────────────────────────────

scheduler = BackgroundScheduler(timezone=pytz.utc)
scheduler.add_job(send_daily_summaries, "cron", hour=13, minute=0)
scheduler.add_job(send_weekly_summaries, "cron", day_of_week="mon", hour=1, minute=0)
scheduler.start()


# ── HEALTH & MANUAL TRIGGER ───────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "healthy", "service": "NutriBuddy"}


@app.post("/cron/daily-summary")
def trigger_summary(request: Request):
    """Manual trigger — protected by CRON_SECRET header."""
    expected = os.environ.get("CRON_SECRET", "")
    if not expected or request.headers.get("X-Cron-Secret", "") != expected:
        raise HTTPException(status_code=403, detail="Forbidden")
    send_daily_summaries()
    return {"status": "sent"}


# ── ADMIN DASHBOARD (YOL-36) ──────────────────────────────────────────────────

def _check_admin(request: Request):
    """Raise 403 if ADMIN_SECRET header or query param doesn't match."""
    secret = os.environ.get("ADMIN_SECRET", "")
    provided = (
        request.headers.get("X-Admin-Secret", "") or
        request.query_params.get("secret", "")
    )
    if not secret or provided != secret:
        raise HTTPException(status_code=403, detail="Forbidden")


@app.get("/admin")
def admin_page(request: Request):
    from fastapi.responses import FileResponse
    _check_admin(request)
    return FileResponse("admin.html")


@app.get("/admin/stats")
def admin_stats(request: Request):
    _check_admin(request)
    from datetime import timezone as _tz
    now = datetime.now(_tz.utc)
    week_ago = (now - timedelta(days=7)).isoformat()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

    users = supabase.table("users").select("*").execute().data
    total_users = len(users)
    new_this_week = sum(1 for u in users if u.get("created_at", "") >= week_ago)
    active_today = sum(1 for u in users if (u.get("last_active_at") or "") >= today_start)
    active_this_week = sum(1 for u in users if (u.get("last_active_at") or "") >= week_ago)
    goal_breakdown = {}
    for u in users:
        goal_breakdown[u.get("goal", "no_goal")] = goal_breakdown.get(u.get("goal", "no_goal"), 0) + 1

    meals_all = supabase.table("meals").select("*").execute().data
    users_with_meals = {m.get("user_id") for m in meals_all}
    drop_off = sum(1 for u in users if u["id"] not in users_with_meals)  # registered, never logged
    meals_today = [m for m in meals_all if m.get("logged_at", "") >= today_start]
    meals_week = [m for m in meals_all if m.get("logged_at", "") >= week_ago]
    from collections import Counter
    dish_counts = Counter(m["description"] for m in meals_week)
    top_dishes = [{"dish": d, "count": c} for d, c in dish_counts.most_common(10)]
    type_dist = dict(Counter(m["meal_type"] for m in meals_all))
    text_meals = sum(1 for m in meals_all if m.get("source") == "text")
    photo_meals = sum(1 for m in meals_all if m.get("source") != "text")

    events = supabase.table("event_log").select("*").gte("created_at", week_ago).execute().data
    event_counts = dict(Counter(e["event_type"] for e in events))

    active_users_count = max(active_this_week, 1)
    avg_meals_per_user = round(len(meals_week) / active_users_count, 1)
    api_cost_estimate = round(len(meals_today) * 0.003, 4)

    return {
        "users": {
            "total": total_users,
            "new_this_week": new_this_week,
            "active_today": active_today,
            "active_this_week": active_this_week,
            "drop_off": drop_off,
            "goal_breakdown": goal_breakdown,
        },
        "meals": {
            "today": len(meals_today),
            "this_week": len(meals_week),
            "all_time": len(meals_all),
            "avg_per_active_user_per_day": avg_meals_per_user,
            "photo_count": photo_meals,
            "text_count": text_meals,
            "top_dishes_7d": top_dishes,
            "type_distribution": type_dist,
        },
        "engagement": {
            "daily_summaries_sent_7d": event_counts.get("daily_summary_sent", 0),
            "weekly_summaries_sent_7d": event_counts.get("weekly_summary_sent", 0),
            "dashboard_requests_7d": event_counts.get("dashboard_requested", 0),
            "blocks_triggered_7d": event_counts.get("block_triggered", 0),
            "unblocks_7d": event_counts.get("unblock", 0),
        },
        "system": {
            "estimated_api_cost_today_usd": api_cost_estimate,
            "generated_at": now.isoformat(),
        },
    }


@app.post("/cron/weekly-summary")
def trigger_weekly_summary(request: Request):
    """Manual trigger for weekly summary — protected by CRON_SECRET header."""
    expected = os.environ.get("CRON_SECRET", "")
    if not expected or request.headers.get("X-Cron-Secret", "") != expected:
        raise HTTPException(status_code=403, detail="Forbidden")
    send_weekly_summaries()
    return {"status": "sent"}
