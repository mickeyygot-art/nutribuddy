import os
import base64
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
    log_meal, get_today_meals, is_blocked, increment_off_topic,
    get_all_users,
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

Rules:
- Reply in 2-3 short sentences MAX. No bullet points, no bold text, max 1 emoji.
- Always reply in the same language the user writes in (Thai or English).
- Only discuss food, nutrition, health goals, and eating habits. Nothing else.
- Do NOT browse the internet. Do NOT answer off-topic questions.
- Tone: quick text from a knowledgeable friend. Warm, never clinical.
- No shame or guilt. Celebrate what's good first, then one small suggestion.
- You know Thai/SEA food well: som tam, khao man gai, pad thai, tom yum, etc.
- Personalize advice based on the user's goal: {goal}"""

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

    # On-topic: Claude
    goal_label = GOAL_LABEL.get(user["goal"], "no specific goal")
    resp = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=200,
        system=SYSTEM_PROMPT.format(goal=goal_label),
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

    resp = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=200,
        system=SYSTEM_PROMPT.format(goal=goal_label),
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
            {"type": "text", "text": "Here's what I'm eating."},
        ]}],
    )

    reply_text = resp.content[0].text
    _reply(event.reply_token, reply_text)
    log_meal(user_id, reply_text)


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

            meal_list = ", ".join(m["description"][:40] for m in meals)
            has_dinner = any(m["meal_type"] == "dinner" for m in meals)
            goal_label = GOAL_LABEL.get(user["goal"], "no specific goal")

            prompt = (
                f"User goal: {goal_label}. Today they ate: {meal_list}. "
                f"{'They have not logged dinner yet.' if not has_dinner else ''} "
                f"Write a warm 2-sentence day summary + 1 tip for tomorrow. "
                f"{'Add a kind dinner wish.' if not has_dinner else ''} "
                f"Reply in {'Thai' if lang == 'th' else 'English'}. Max 3 sentences, 1 emoji max."
            )
            resp = claude.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=200,
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
def trigger_summary():
    """Manual trigger for testing."""
    send_daily_summaries()
    return {"status": "sent"}
