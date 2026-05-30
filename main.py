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
    is_blocked, clear_block, increment_off_topic, get_all_users,
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

CONVERSATIONAL_WHITELIST = {
    "โอเค", "ok", "okay", "ได้", "ครับ", "ค่ะ", "นะ",
    "yes", "no", "ใช่", "ไม่", "ขอบคุณ", "thanks",
    "บอกไปแล้ว", "บอกแล้ว", "แล้ว", "เข้าใจ",
}

GOAL_MAP = {
    "1": "lose_weight",    "ลดน้ำหนัก": "lose_weight",      "lose weight": "lose_weight",
    "2": "eat_clean",      "กินสะอาด": "eat_clean",          "eat clean": "eat_clean",
                           "กินอาหารคลีน": "eat_clean",      "อาหารคลีน": "eat_clean",
    "3": "build_muscle",   "เพิ่มกล้าม": "build_muscle",     "build muscle": "build_muscle",
    "4": "no_goal",        "ยังไม่มี": "no_goal",            "no goal": "no_goal",
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


def is_thai(text: str) -> bool:
    return any("฀" <= c <= "๿" for c in text)


def detect_goal(text: str) -> str | None:
    t = text.lower().strip()
    for key, goal in GOAL_MAP.items():
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


def is_unblock_command(text: str) -> bool:
    t = text.lower().strip()
    return any(kw in t for kw in UNBLOCK_KEYWORDS)


def is_conversational(text: str) -> bool:
    """YOL-25: Returns True if message is a short acknowledgement — skip off-topic classifier."""
    t = text.strip()
    if len(t) <= 10:
        return True
    return t.lower() in CONVERSATIONAL_WHITELIST


def classify_meal_report(text: str) -> bool:
    """YOL-24: Returns True if the message is reporting a meal the user ate."""
    resp = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=5,
        messages=[{"role": "user", "content":
            f"Is this message reporting a meal the user just ate or ate earlier today? Reply MEAL or NOT only.\n"
            f"MEAL examples: 'กินข้าวมันไก่มาเมื่อเช้า', 'just had pad thai for lunch', 'เที่ยงกินกะเพรา'\n"
            f"NOT examples: 'is pad thai healthy?', 'what should I eat tonight?', 'ขอบคุณ'\n\n"
            f"Message: {text}"}]
    )
    return resp.content[0].text.strip().upper() == "MEAL"


def extract_dish_from_text(text: str) -> str:
    """YOL-24: Extract dish name only from a meal report message."""
    resp = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=30,
        messages=[{"role": "user", "content":
            f"Extract the dish name only from this message. Reply with just the dish name, nothing else.\n\n"
            f"Message: {text}"}]
    )
    return resp.content[0].text.strip()[:200]


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
    user = get_or_create_user(line_user_id)
    user_id = user["id"]
    lang = "th" if is_thai(text) else "en"

    if user["language"] != lang:
        update_user_language(line_user_id, lang)

    # YOL-21: Input length guard — cap at 500 chars before any Claude call
    if len(text) > 500:
        _reply(event.reply_token, TOO_LONG_TH if lang == "th" else TOO_LONG_EN)
        return

    # YOL-20: Unblock command — check BEFORE blocked gate to allow escape
    currently_blocked = is_blocked(user_id)
    if is_unblock_command(text) and currently_blocked:
        clear_block(user_id)
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

    # YOL-25: Skip off-topic classifier for short / conversational messages
    if not is_conversational(text) and classify_off_topic(text):
        count = increment_off_topic(user_id)
        if count >= 3:
            msg = BLOCKED_TH if lang == "th" else BLOCKED_EN
        elif count == 2:
            msg = WARN_2_TH if lang == "th" else WARN_2_EN
        else:
            msg = OFFTOPIC_TH if lang == "th" else OFFTOPIC_EN
        _reply(event.reply_token, msg)
        return

    # YOL-24: Silently log meal if user is reporting what they ate in text
    try:
        if classify_meal_report(text):
            dish = extract_dish_from_text(text)
            if dish:
                log_meal(user_id, dish)
    except Exception as e:
        print(f"Text meal log error for {user_id}: {e}")  # Non-fatal

    # YOL-16: Meal history intent detection
    date_intent = detect_date_intent(text)
    if date_intent == "TOO_OLD":
        _reply(event.reply_token, HISTORY_LIMIT_TH if lang == "th" else HISTORY_LIMIT_EN)
        return

    meal_context_parts = []
    if date_intent != "NO":
        asked_dt = BKK.localize(datetime.strptime(date_intent, "%Y-%m-%d"))
        history_meals = get_meals_by_date_range(user_id, asked_dt, asked_dt + timedelta(days=1))
        meal_context_parts.append(build_meal_history_context(history_meals, date_intent))

    today_str = str(datetime.now(BKK).date())
    if date_intent != today_str:
        today_meals = get_today_meals(user_id)
        if today_meals:
            meal_context_parts.append(build_meal_history_context(today_meals, today_str))

    # On-topic: Claude with conversation history (YOL-19)
    goal_label = GOAL_LABEL.get(user["goal"], "no specific goal")
    system = SYSTEM_PROMPT.format(goal=goal_label)
    if meal_context_parts:
        system += "\n\nMEAL CONTEXT (use this when answering questions about what was eaten):\n" + "\n".join(meal_context_parts)

    # YOL-19: Append user message and call Claude with full history
    history = conversation_history.setdefault(line_user_id, [])
    history.append({"role": "user", "content": text})

    resp = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        system=system,
        messages=history,
    )
    reply_text = resp.content[0].text

    # YOL-19: Store assistant reply, trim history to last 10 entries (5 pairs)
    history.append({"role": "assistant", "content": reply_text})
    if len(history) > 10:
        conversation_history[line_user_id] = history[-10:]

    _reply(event.reply_token, reply_text)


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
            log_meal(user_id, dish_name)
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
1. What you ate today — one warm sentence listing the meals (use the dish names above)
2. Goal progress — one sentence connecting today's eating to their goal
{"3. Dinner wish — one kind, brief sentence wishing them a healthy dinner" if not has_dinner else ""}
{"3" if not has_dinner else "3"}. Tomorrow tip — one specific, practical suggestion for tomorrow

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


@app.post("/cron/weekly-summary")
def trigger_weekly_summary(request: Request):
    """Manual trigger for weekly summary — protected by CRON_SECRET header."""
    expected = os.environ.get("CRON_SECRET", "")
    if not expected or request.headers.get("X-Cron-Secret", "") != expected:
        raise HTTPException(status_code=403, detail="Forbidden")
    send_weekly_summaries()
    return {"status": "sent"}
