import os
import re
import json
import base64
import time
import urllib.request
import urllib.error
from contextlib import asynccontextmanager
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
    log_meal, get_today_meals, get_meals_by_date_range, get_week_meals, get_week_top_dishes,
    is_blocked, clear_block, force_block, increment_off_topic, get_all_users,
    update_last_meal_type, update_last_active, log_event, supabase,
    update_user_suggestion, clear_user_suggestion, get_liff_summary,
)
import tracking


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # YOL-45: flush buffered PostHog events before the container stops
    tracking.shutdown()


app = FastAPI(lifespan=lifespan)

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

# YOL-49: weekly no-meals fallback (no Claude call)
WEEKLY_NO_MEALS_TH = "สัปดาห์ที่ผ่านมายังไม่มีบันทึกเลยนะ — สัปดาห์นี้ลองส่งรูปอาหารมาให้ดูได้เลย 🍽️"
WEEKLY_NO_MEALS_EN = "No meals logged last week — try sending a food photo this week 🍽️"

# YOL-48: daily no-meals fallback (no Claude call)
DAILY_NO_MEALS_TH = "วันนี้ยังไม่มีมื้อไหนเลยนะ — ไม่เป็นไร คืนนี้ยังทัน 🌿"
DAILY_NO_MEALS_EN = "Nothing logged today — still time to catch dinner tonight 🌿"

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


# YOL-48/49: sentence-completeness so we never send a truncated, mid-sentence message.
_EMOJI_RE = re.compile(
    "[\U00002600-\U000027BF\U0001F300-\U0001F9FF\U0001FA00-\U0001FAFF"
    "\U00002702-\U000027B0\U0000FE00-\U0000FE0F\U0001F1E0-\U0001F1FF]"
)
_THAI_ENDINGS = ("นะคะ", "นะครับ", "นะ", "ครับ", "ค่ะ", "ค่า", "จ้ะ", "จ้า", "เลย", "น่ะ")


def ends_complete(text: str) -> bool:
    """True if text ends on a sentence boundary (punctuation, emoji, or Thai particle)."""
    s = (text or "").rstrip()
    if not s:
        return False
    if s[-1] in ".!?…":
        return True
    if _EMOJI_RE.match(s[-1]):
        return True
    return any(s.endswith(p) for p in _THAI_ENDINGS)


def trim_to_complete(text: str) -> str:
    """Return text truncated to its last complete sentence boundary. '' if none found."""
    s = (text or "").rstrip()
    if ends_complete(s):
        return s
    best = -1
    for i, ch in enumerate(s):
        if ch in ".!?…" or _EMOJI_RE.match(ch):
            best = i
    for p in _THAI_ENDINGS:
        idx = s.rfind(p)
        if idx != -1:
            best = max(best, idx + len(p) - 1)
    return s[:best + 1].rstrip() if best > 0 else ""


def split_opener_narrative(raw: str):
    """Split a '===' separated model reply into (opener, narrative).
    Falls back to (None, whole) if the separator is missing."""
    if "===" in raw:
        a, _, b = raw.partition("===")
        return a.strip() or None, b.strip()
    return None, raw.strip()


def build_meal_list(dishes: list, lang: str, weekly: bool = False) -> str:
    """Deterministic, hallucination-proof recap list (built in Python, not by Claude)."""
    lines = []
    for i, d in enumerate(dishes, 1):
        if weekly:
            lines.append(f"อันดับ {i}: {d}" if lang == "th" else f"#{i}: {d}")
        else:
            lines.append(f"มื้อที่ {i}: {d}" if lang == "th" else f"Meal {i}: {d}")
    return "\n".join(lines)


def complete_narrative(text: str, lang: str) -> str:
    """YOL-48/49: ensure the narrative is a whole thought — never truncated mid-sentence.
    One continuation call if cut off; otherwise trim to the last complete sentence."""
    if ends_complete(text):
        return text.strip()
    try:
        cont = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=100,
            messages=[{"role": "user", "content":
                "This message was cut off mid-sentence. Write ONLY the few words needed to finish "
                f"the final sentence naturally — nothing else, no repetition.\n\n{text}"}],
        )
        combined = f"{text.rstrip()} {cont.content[0].text.strip()}"
    except Exception as e:
        print(f"narrative continuation error: {e}")
        combined = text
    return trim_to_complete(combined) or combined.strip()


def is_suggestion_fresh(last_at_iso, now) -> bool:
    """YOL-44: True if a stored suggestion exists and is < 36 hours old."""
    if not last_at_iso:
        return False
    try:
        last_at = datetime.fromisoformat(str(last_at_iso).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    return (now - last_at) < timedelta(hours=36)


def check_follow_through(user: dict, dishes: list) -> str | None:
    """YOL-44: If a logged dish matches yesterday's suggestion, return a celebration
    message and clear the suggestion (fires once). Returns None otherwise."""
    from datetime import timezone as _tz
    suggestion = user.get("last_suggestion")
    if not suggestion:
        return None
    if not is_suggestion_fresh(user.get("last_suggestion_at"), datetime.now(_tz.utc)):
        return None
    lang = user.get("language", "th")
    for dish in dishes:
        if not dish:
            continue
        match = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=5,
            messages=[{"role": "user", "content":
                f'Yesterday\'s suggestion: "{suggestion}"\nUser just logged: "{dish}"\n\n'
                f"Does this meal match or reflect the suggestion? Reply YES or NO only."}]
        )
        if match.content[0].text.strip().upper().startswith("YES"):
            resp = claude.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=80,
                messages=[{"role": "user", "content":
                    f'Yesterday you suggested: "{suggestion}"\nUser just logged: "{dish}"\n'
                    f"User language: {lang}\n\n"
                    f"Write a short, genuine celebration (1-2 sentences) acknowledging they followed through. "
                    f"Warm friend tone. Reference both the suggestion and what they ate. 1 emoji max. "
                    f"Reply in {'Thai' if lang == 'th' else 'English'}."}]
            )
            try:
                clear_user_suggestion(user["id"])
            except Exception as e:
                print(f"clear_user_suggestion error for {user['id']}: {e}")
            return resp.content[0].text
    return None


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
    user = get_or_create_user(event.source.user_id)
    _reply(event.reply_token, ONBOARDING_MSG_1)
    time.sleep(1)
    _push(event.source.user_id, ONBOARDING_MSG_2)
    # YOL-45: analytics — user.joined
    try:
        tracking.track_user_joined(event.source.user_id)
        tracking.identify_user(
            event.source.user_id,
            goal=user["goal"],
            language=user["language"],
            meals_logged_count=0,
        )
    except Exception as e:
        print(f"Analytics error (user.joined): {e}")


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
        # YOL-45: analytics — goal.set
        try:
            is_initial = user["goal"] == "no_goal"
            tracking.track_goal_set(
                line_user_id,
                goal=goal,
                previous_goal=user["goal"] if not is_initial else None,
                is_initial_set=is_initial,
                set_method="digit_reply" if text.strip() in GOAL_DIGITS else "phrase",
            )
            tracking.identify_user(line_user_id, goal=goal, language=lang)
        except Exception as e:
            print(f"Analytics error (goal.set): {e}")
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
    logged_dishes = []
    try:
        for entry in triage["meals"]:
            dish = (entry.get("dish") or "").strip()[:200]
            if dish:
                log_meal(user_id, dish, source="text", meal_type=entry.get("meal_type") or None)
                logged_dishes.append(dish)
    except Exception as e:
        print(f"Text meal log error for {user_id}: {e}")  # Non-fatal

    # YOL-44: celebrate if any logged dish matches yesterday's coaching suggestion
    celebration = None
    if logged_dishes:
        try:
            celebration = check_follow_through(user, logged_dishes)
        except Exception as e:
            print(f"Follow-through error for {user_id}: {e}")

    # YOL-31: Retroactive meal-type correction from a follow-up message (e.g. photo then
    # "อาหารเช้านะ"). Only when triage extracted no meals of its own, so we don't fight it.
    if not logged_dishes:
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
        if celebration:
            _push(line_user_id, celebration)
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
    if celebration:
        _push(line_user_id, celebration)


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

    celebration = None
    if not dish_name:
        # YOL-23: Unknown dish — append nudge, skip DB logging
        nudge = UNKNOWN_DISH_TH if lang == "th" else UNKNOWN_DISH_EN
        coaching_text = coaching_text + "\n" + nudge
    else:
        try:
            log_meal(user_id, dish_name, source="photo")
        except Exception as e:
            print(f"Meal log error for {user_id}: {e}")
        # YOL-44: celebrate if this matches yesterday's coaching suggestion
        try:
            celebration = check_follow_through(user, [dish_name])
        except Exception as e:
            print(f"Follow-through error for {user_id}: {e}")

    # YOL-19: Add photo + reply to conversation history
    history = conversation_history.setdefault(line_user_id, [])
    history.append({"role": "user", "content": "[sent a food photo]"})
    history.append({"role": "assistant", "content": coaching_text})
    if len(history) > 10:
        conversation_history[line_user_id] = history[-10:]

    _reply(event.reply_token, coaching_text)
    if celebration:
        _push(line_user_id, celebration)


# ── DAILY SUMMARY ─────────────────────────────────────────────────────────────

def send_daily_summaries():
    # PLAN (YOL-48): structured meal list + narrative suggestion in one complete message.
    #   No meals → fixed fallback, no Claude call.
    #   Meals → opener + chronological dish list (built in Python) + narrative suggestion.
    #   Narrative guaranteed complete (never mid-sentence). Suggestion stored for YOL-44.
    for user in get_all_users():
        try:
            meals = get_today_meals(user["id"])  # ordered by logged_at
            lang = user["language"]
            line_user_id = user["line_user_id"]

            if not meals:
                _push(line_user_id, DAILY_NO_MEALS_TH if lang == "th" else DAILY_NO_MEALS_EN)
                continue

            n = len(meals)
            dishes = [m["description"] for m in meals][:5]  # show max 5
            goal = user.get("goal", "no_goal")
            goal_label = GOAL_LABEL.get(goal, "no specific goal")
            last_suggestion = user.get("last_suggestion") or "none"
            lang_word = "Thai" if lang == "th" else "English"

            focus = {
                "lose_weight":  "Name a specific lower-cal swap to try tomorrow.",
                "eat_clean":    "Name a specific vegetable to add tomorrow.",
                "build_muscle": "Name a specific protein source to add tomorrow.",
                "no_goal":      "Note a positive pattern and suggest one simple habit.",
            }.get(goal, "Suggest one specific, practical thing for tomorrow.")

            prompt = f"""User logged {n} meal(s) today. Dishes: {', '.join(dishes)}.
User goal: {goal_label}
User language: {lang_word}
Yesterday's suggestion (if any): {last_suggestion}

Write TWO parts separated by a line containing only ===
Part A: a warm opener, 1 sentence, mentions they logged {n} meal(s), ends with 1 emoji.
Part B: 2-3 sentences. Observe a pattern from today's dishes, connect to their goal, then give ONE specific actionable suggestion for tomorrow — name a real dish or ingredient. {focus} Do not repeat yesterday's suggestion. End with 1 emoji.

Tone: data-light storyteller, warm friend, no numbers, no calories, no lecturing.
Do NOT list the meals yourself. Plain text only, no markdown. Reply in {lang_word}."""

            resp = claude.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )
            opener, narrative = split_opener_narrative(resp.content[0].text)
            narrative = complete_narrative(narrative, lang)
            if not opener or not ends_complete(opener):
                opener = (f"วันนี้คุณบันทึกมา {n} มื้อนะ 🍽️" if lang == "th"
                          else f"You logged {n} meal(s) today 🍽️")

            list_block = build_meal_list(dishes, lang, weekly=False)
            message = f"{opener}\n\n{list_block}\n\n{narrative}"
            _push(line_user_id, message)

            # YOL-44: store ONLY the narrative (not the recap list) for follow-through
            try:
                update_user_suggestion(user["id"], clean_for_line(narrative))
            except Exception as e:
                print(f"update_user_suggestion error for {user['id']}: {e}")

            try:
                log_event(user["id"], "daily_summary_sent")
            except Exception:
                pass
        except Exception as e:
            print(f"Summary error for {user.get('line_user_id')}: {e}")


# ── WEEKLY SUMMARY (Monday 08:00 Bangkok = Monday 01:00 UTC) ──────────────────

def send_weekly_summaries():
    # PLAN (YOL-49): days logged + top-3 dishes (built in Python) + narrative reflection.
    #   No meals → fixed fallback, no Claude call.
    #   Opener tone scales with days logged (5-7 celebratory, 3-4 encouraging, 1-2 gentle).
    #   Narrative guaranteed complete (never mid-sentence).
    for user in get_all_users():
        try:
            meals = get_week_meals(user["id"])
            lang = user["language"]
            line_user_id = user["line_user_id"]

            if not meals:
                _push(line_user_id, WEEKLY_NO_MEALS_TH if lang == "th" else WEEKLY_NO_MEALS_EN)
                continue

            days = len({m["logged_at"][:10] for m in meals})
            top_dishes = get_week_top_dishes(user["id"], 3)
            goal = user.get("goal", "no_goal")
            goal_label = GOAL_LABEL.get(goal, "no specific goal")
            lang_word = "Thai" if lang == "th" else "English"

            tone = ("celebratory" if days >= 5 else
                    "warm and encouraging" if days >= 3 else
                    "gentle, no guilt")
            focus = {
                "lose_weight":  "Spot a weekly pattern and name one swap to try.",
                "eat_clean":    "Celebrate any clean choices and name one vegetable to add this week.",
                "build_muscle": "Note protein consistency and name one dish to add more of.",
                "no_goal":      "Celebrate consistency and suggest one simple habit for the week.",
            }.get(goal, "Suggest one specific thing to try this week.")

            prompt = f"""User logged meals on {days} of 7 days this week.
Top dishes (by frequency): {', '.join(top_dishes)}.
User goal: {goal_label}
User language: {lang_word}

Write TWO parts separated by a line containing only ===
Part A: a warm opener, 1 sentence, mentions they logged {days} of 7 days, ends with 1 emoji. Tone: {tone}.
Part B: 2-3 sentences. Observe a weekly eating pattern, call out one positive thing, then give ONE specific suggestion for the coming week — name a real dish or ingredient. {focus} End with 1 emoji.

Tone: data-light storyteller, warm friend, no numbers, no calories, no lecturing.
Do NOT list the dishes yourself. Plain text only, no markdown. Reply in {lang_word}."""

            resp = claude.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )
            opener, narrative = split_opener_narrative(resp.content[0].text)
            narrative = complete_narrative(narrative, lang)
            if not opener or not ends_complete(opener):
                opener = (f"สัปดาห์นี้คุณบันทึก {days} จาก 7 วัน 🌿" if lang == "th"
                          else f"You logged {days} of 7 days this week 🌿")

            list_block = build_meal_list(top_dishes, lang, weekly=True)
            message = f"{opener}\n\n{list_block}\n\n{narrative}"
            _push(line_user_id, message)

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


# ── LIFF DASHBOARD (YOL-50) ───────────────────────────────────────────────────

def _line_profile(access_token: str):
    """Call LINE's Profile API with a LIFF access token.
    Returns (userId | None, http_status). status is -1 on a non-HTTP error."""
    try:
        req = urllib.request.Request(
            "https://api.line.me/v2/profile",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data.get("userId"), 200
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")[:200]
        except Exception:
            pass
        print(f"LIFF profile HTTPError {e.code}: {body}")
        return None, e.code
    except Exception as e:
        print(f"LIFF profile error: {e}")
        return None, -1


def _liff_user_id(access_token: str) -> str | None:
    uid, _ = _line_profile(access_token)
    return uid


@app.get("/liff")
def liff_page():
    """Serve the LIFF dashboard, injecting LIFF_ID from env."""
    from fastapi.responses import HTMLResponse
    with open("liff.html", "r", encoding="utf-8") as f:
        html = f.read()
    return HTMLResponse(html.replace("__LIFF_ID__", os.environ.get("LIFF_ID", "")))


@app.get("/api/liff/whoami")
def liff_whoami(request: Request):
    """Temporary diagnostic (YOL-50): does the token resolve, and is there a matching row?"""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    uid, profile_status = _line_profile(auth[7:].strip())
    total = 0
    try:
        total = supabase.table("users").select("id", count="exact").execute().count or 0
    except Exception:
        pass
    return {
        "token_resolved": bool(uid),
        "profile_status": profile_status,
        "user_id_prefix": (uid[:10] + "…") if uid else None,
        "row_exists": (get_liff_summary(uid) is not None) if uid else False,
        "total_users": total,
    }


@app.get("/api/liff/meals")
def liff_meals(request: Request):
    """Return the signed-in LIFF user's 7-day meal summary."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    line_user_id = _liff_user_id(auth[7:].strip())
    if not line_user_id:
        print("LIFF: token did not resolve to a userId (invalid token or missing profile scope)")
        raise HTTPException(status_code=401, detail="Invalid token")
    summary = get_liff_summary(line_user_id)
    if summary is None:
        # userId valid but no matching users row — usually a provider mismatch between
        # the LINE Login (LIFF) channel and the Messaging API bot channel.
        print(f"LIFF: no users row for userId {line_user_id[:8]}… (provider mismatch or never onboarded)")
        raise HTTPException(status_code=401, detail="User not found")
    return summary


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
