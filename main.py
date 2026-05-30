import os
import base64
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
    log_meal, get_today_meals, get_meals_by_date_range,
    is_blocked, increment_off_topic, get_all_users,
)

app = FastAPI()

configuration = Configuration(access_token=os.environ["LINE_CHANNEL_ACCESS_TOKEN"].strip())
handler = WebhookHandler(os.environ["LINE_CHANNEL_SECRET"].strip())
claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"].strip())

BKK = pytz.timezone("Asia/Bangkok")

# ── COPY ──────────────────────────────────────────────────────────────────────

ONBOARDING_MSG = """สวัสดี! 👋 ฉันคือ NutriBuddy เพื่อนด้านสุขภาพของคุณบน LINE

ส่งรูปอาหารมาให้ฉันดูได้เลย แล้วฉันจะบอกว่าโภชนาการเป็นยังไง พร้อมคำแนะนำเล็กๆ น้อยๆ สำหรับมื้อต่อไป 🍽️

(You can also chat with me in English anytime!)

ก่อนเริ่ม — เป้าหมายของคุณคืออะไร?
1️⃣ ลดน้ำหนัก
2️⃣ กินสะอาดขึ้น
3️⃣ เพิ่มกล้ามเนื้อ
4️⃣ ยังไม่มีเป้าหมาย ขอแค่รู้ว่ากินอะไรอยู่"""

BLOCKED_TH = "NutriBuddy พักอยู่ 6 ชั่วโมงนะ กลับมาคุยเรื่องอาหารด้วยกันทีหลังได้เลย 🌿"
BLOCKED_EN = "NutriBuddy is resting for 6 hours. Come back and let's talk food! 🌿"

WARN_2_TH = "ดูเหมือนเราออกนอกเรื่องอาหารกันไปสักหน่อยแล้ว 😅 NutriBuddy ช่วยได้แค่เรื่องโภชนาการนะ ถ้าถามนอกเรื่องอีก ฉันจะหยุดตอบชั่วคราว 6 ชั่วโมง"
WARN_2_EN = "We've gone off-topic a couple of times! NutriBuddy only covers food and nutrition. One more and I'll take a 6-hour break 😅"

OFFTOPIC_TH = "NutriBuddy ช่วยเรื่องอาหารและสุขภาพเท่านั้นนะ — ส่งรูปอาหารมาได้เลย! 🍽️"
OFFTOPIC_EN = "NutriBuddy only covers food and health — send me a food photo! 🍽️"

HISTORY_LIMIT_TH = "NutriBuddy เก็บประวัติอาหารได้แค่ 30 วันย้อนหลังนะ"
HISTORY_LIMIT_EN = "NutriBuddy keeps meal history for the last 30 days only."

GOAL_MAP = {
    "1": "lose_weight", "ลดน้ำหนัก": "lose_weight", "lose weight": "lose_weight",
    "2": "eat_clean",   "กินสะอาด": "eat_clean",   "eat clean": "eat_clean",
    "3": "build_muscle","เพิ่มกล้าม": "build_muscle","build muscle": "build_muscle",
    "4": "no_goal",     "ยังไม่มี": "no_goal",     "no goal": "no_goal",
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

def is_thai(text: str) -> bool:
    return any("฀" <= c <= "๿" for c in text)


def detect_goal(text: str) -> str | None:
    t = text.lower().strip()
    for key, goal in GOAL_MAP.items():
        if key in t:
            return goal
    return None


def classify_off_topic(text: str) -> bool:
    """Returns True if message is NOT related to food/health."""
    resp = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=5,
        messages=[{"role": "user", "content":
            f"Is this message related to food, nutrition, health, or eating habits? Reply YES or NO only.\n\nMessage: {text}"}]
    )
    return resp.content[0].text.strip().upper() == "NO"


def detect_date_intent(text: str) -> str:
    # PLAN:
    # 1. Ask Haiku: is this a meal-history question? If yes, return YYYY-MM-DD (Bangkok).
    # 2. Validate the returned date — if it parses OK, check 30-day cap.
    # 3. Return "TOO_OLD" | "NO" | "YYYY-MM-DD"
    today_bkk = datetime.now(BKK).date()
    resp = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=20,
        messages=[{"role": "user", "content":
            f"Today is {today_bkk} (Bangkok time). "
            f"Is the user asking about their past meals? "
            f"If yes, reply with only the date in YYYY-MM-DD format (resolve 'yesterday', 'last Monday', etc.). "
            f"If asking about a range/week, use the earliest date in the range. "
            f"If no meal-history question, reply: NO\n\nMessage: {text}"}]
    )
    result = resp.content[0].text.strip()
    if result.upper() == "NO":
        return "NO"
    try:
        asked_date = datetime.strptime(result, "%Y-%m-%d").date()
        cutoff = today_bkk - timedelta(days=30)
        if asked_date < cutoff:
            return "TOO_OLD"
        return result
    except ValueError:
        return "NO"


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
            ReplyMessageRequest(reply_token=reply_token, messages=[TextMessage(text=text)])
        )


def _push(line_user_id: str, text: str):
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).push_message(
            PushMessageRequest(to=line_user_id, messages=[TextMessage(text=text)])
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
    _reply(event.reply_token, ONBOARDING_MSG)


# ── TEXT ──────────────────────────────────────────────────────────────────────

@handler.add(MessageEvent, message=TextMessageContent)
def handle_text(event):
    line_user_id = event.source.user_id
    text = event.message.text
    user = get_or_create_user(line_user_id)
    user_id = user["id"]
    lang = "th" if is_thai(text) else "en"

    if user["language"] != lang:
        update_user_language(line_user_id, lang)

    # Blocked?
    if is_blocked(user_id):
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

    # Off-topic?
    if classify_off_topic(text):
        count = increment_off_topic(user_id)
        if count >= 3:
            msg = BLOCKED_TH if lang == "th" else BLOCKED_EN
        elif count == 2:
            msg = WARN_2_TH if lang == "th" else WARN_2_EN
        else:
            msg = OFFTOPIC_TH if lang == "th" else OFFTOPIC_EN
        _reply(event.reply_token, msg)
        return

    # Meal history intent detection
    # PLAN:
    # 1. Ask Haiku if user is asking about past meals → get date or NO/TOO_OLD
    # 2. TOO_OLD → reply with 30-day limit message, stop
    # 3. Valid date → fetch that day's meals and build context string
    # 4. Always inject today's meals as extra context regardless
    date_intent = detect_date_intent(text)
    if date_intent == "TOO_OLD":
        _reply(event.reply_token, HISTORY_LIMIT_TH if lang == "th" else HISTORY_LIMIT_EN)
        return

    meal_context_parts = []
    if date_intent != "NO":
        asked_dt = BKK.localize(datetime.strptime(date_intent, "%Y-%m-%d"))
        history_meals = get_meals_by_date_range(user_id, asked_dt, asked_dt + timedelta(days=1))
        meal_context_parts.append(build_meal_history_context(history_meals, date_intent))

    # Always include today's meals for freshness
    today_str = str(datetime.now(BKK).date())
    if date_intent != today_str:  # avoid duplicate if user asked about today
        today_meals = get_today_meals(user_id)
        if today_meals:
            meal_context_parts.append(build_meal_history_context(today_meals, today_str))

    # On-topic: Claude
    goal_label = GOAL_LABEL.get(user["goal"], "no specific goal")
    system = SYSTEM_PROMPT.format(goal=goal_label)
    if meal_context_parts:
        system += "\n\nMEAL CONTEXT (use this when answering questions about what was eaten):\n" + "\n".join(meal_context_parts)

    resp = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        system=system,
        messages=[{"role": "user", "content": text}],
    )
    _reply(event.reply_token, resp.content[0].text)


# ── IMAGE ─────────────────────────────────────────────────────────────────────

@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image(event):
    line_user_id = event.source.user_id
    user = get_or_create_user(line_user_id)
    user_id = user["id"]
    lang = user["language"]

    if is_blocked(user_id):
        _reply(event.reply_token, BLOCKED_TH if lang == "th" else BLOCKED_EN)
        return

    with ApiClient(configuration) as api_client:
        image_bytes = MessagingApiBlob(api_client).get_message_content(message_id=event.message.id)

    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    goal_label = GOAL_LABEL.get(user["goal"], "no specific goal")

    # Single call: extract dish name + coaching response together
    resp = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        system=SYSTEM_PROMPT.format(goal=goal_label),
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
            {"type": "text", "text": "What dish is this? Start your reply with 'DISH: [dish name]' on the first line, then a blank line, then your coaching response."},
        ]}],
    )

    full_response = resp.content[0].text
    lines = full_response.strip().splitlines()

    # Extract dish name for DB, coaching text for user
    dish_name = "unknown dish"
    coaching_text = full_response
    if lines and lines[0].upper().startswith("DISH:"):
        dish_name = lines[0][5:].strip()
        coaching_text = "\n".join(lines[2:]).strip() if len(lines) > 2 else full_response

    _reply(event.reply_token, coaching_text)
    try:
        log_meal(user_id, dish_name)  # Store only clean dish name, not full AI response
    except Exception as e:
        print(f"Meal log error for {user_id}: {e}")  # Non-fatal — user got reply, log fails silently


# ── DAILY SUMMARY ─────────────────────────────────────────────────────────────

def send_daily_summaries():
    for user in get_all_users():
        try:
            meals = get_today_meals(user["id"])
            lang = user["language"]
            line_user_id = user["line_user_id"]

            if not meals:
                msg = ("วันนี้ยังไม่ได้ส่งรูปอาหารเลยนะ — พรุ่งนี้ลองส่งมาให้ NutriBuddy ดูได้เลย! 🍽️"
                       if lang == "th" else
                       "No meals logged today — try sending a food photo tomorrow! 🍽️")
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
1. What you ate today — one warm sentence listing the meals (use the dish names above)
2. Goal progress — one sentence connecting today's eating to their goal
{"3. Dinner wish — one kind, brief sentence wishing them a healthy dinner" if not has_dinner else ""}
{"3" if not has_dinner else "3"}. Tomorrow tip — one specific, practical suggestion for tomorrow

Rules: reply in {lang_word}. Max 4 short sentences total. Max 1 emoji. No bullet points in the reply. Warm friend tone."""

            resp = claude.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=250,
                messages=[{"role": "user", "content": prompt}],
            )
            _push(line_user_id, resp.content[0].text)
        except Exception as e:
            print(f"Summary error for {user.get('line_user_id')}: {e}")


# ── SCHEDULER (20:00 Bangkok = 13:00 UTC) ─────────────────────────────────────

scheduler = BackgroundScheduler(timezone=pytz.utc)
scheduler.add_job(send_daily_summaries, "cron", hour=13, minute=0)
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
