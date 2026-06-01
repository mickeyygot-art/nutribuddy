import os
import re
import json
import base64
import time
import urllib.request
import urllib.error
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, Request, HTTPException
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi, MessagingApiBlob,
    ReplyMessageRequest, PushMessageRequest, TextMessage,
    FlexMessage, FlexContainer,
)
from linebot.v3.webhooks import (
    MessageEvent, TextMessageContent, ImageMessageContent, AudioMessageContent,
    FollowEvent, PostbackEvent,
)
import urllib.parse
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
    get_lapsed_users, mark_winback_sent, get_meal_count, get_meal_dates,
    compute_streak, bkk_date_key, get_recent_meals, update_user_profile,
    set_checkin_pending, clear_checkin_pending, insert_checkin, get_recent_checkins,
    get_month_meals,
)
import subprocess
import tempfile
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

# YOL-69: onboarding is single-language. New users default to Thai (DB default);
# the EN strings are served only to users whose stored language is already 'en'.
ONBOARDING_MSG_1_TH = """สวัสดี! ฉันคือ NutriBuddy เพื่อนด้านสุขภาพของคุณบน LINE 🥑

ก่อนเริ่ม — เป้าหมายของคุณคืออะไร?
1️⃣ ลดน้ำหนัก
2️⃣ กินอาหารคลีนมากขึ้น
3️⃣ เพิ่มกล้ามเนื้อ
4️⃣ ยังไม่มีเป้าหมาย ขอแค่รู้ว่ากินอะไรอยู่"""

ONBOARDING_MSG_1_EN = """Hi! I'm NutriBuddy, your health buddy on LINE 🥑

To start — what's your goal?
1️⃣ Lose weight
2️⃣ Eat cleaner
3️⃣ Build muscle
4️⃣ No goal yet, just want to see what I'm eating"""

ONBOARDING_MSG_2_TH = """นี่คือสิ่งที่ NutriBuddy ทำได้

ส่งรูปอาหาร — ฉันจะบอกว่าโภชนาการเป็นยังไงและมีคำแนะนำสำหรับมื้อต่อไป
บอกชื่อเมนู — พิมพ์หรือส่งเสียงก็ได้ ไม่ต้องมีรูปเสมอไป
ถามประวัติ — "เมื่อเช้ากินอะไร" หรือ "เมื่อวานกินอะไรบ้าง"
เปลี่ยนเป้าหมาย — พิมพ์ "เปลี่ยนเป้าหมาย" ได้ตลอดเวลา
สรุปรายวัน — ทุกคืน 20.00 น. ฉันจะส่งสรุปมื้ออาหารวันนี้ให้
สรุปรายสัปดาห์ — ทุกวันจันทร์ 8.00 น. ฉันจะส่งภาพรวมของสัปดาห์ที่ผ่านมา"""

ONBOARDING_MSG_2_EN = """Here's what NutriBuddy can do

Send a food photo — I'll tell you how it looks nutritionally, with a tip for next time
Say a dish — type or send a voice note, no photo needed
Ask your history — "what did I eat this morning?" or "what did I eat yesterday?"
Change your goal — type "change goal" anytime
Daily recap — every night at 8pm I'll sum up today's meals
Weekly recap — every Monday 8am I'll send your week in review"""

def _onboarding_1(lang): return ONBOARDING_MSG_1_EN if lang == "en" else ONBOARDING_MSG_1_TH
def _onboarding_2(lang): return ONBOARDING_MSG_2_EN if lang == "en" else ONBOARDING_MSG_2_TH

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

# YOL-51: win-back nudge (warm, no guilt)
WINBACK_TH = "คิดถึงนะ 🌿 ไม่ได้คุยกันสองสามวันเลย — วันนี้กินอะไรอร่อยๆ บ้าง? ส่งรูปมาให้ NutriBuddy ดูได้เลย"
WINBACK_EN = "Missed you 🌿 It's been a few days — what'd you eat today? Send me a photo anytime."

# YOL-60: opt-in weekly outcome check-in (appended after the Monday summary)
CHECKIN_PROMPT_TH = "อีกนิดนะ 🌿 อาทิตย์นี้รู้สึกยังไงบ้าง? พลังงานเป็นไง (1-5) และรู้สึกเข้าใกล้เป้าหมายขึ้นไหม? ตอบสั้นๆ หรือข้ามก็ได้นะ"
CHECKIN_PROMPT_EN = "One more thing 🌿 How are you feeling this week? Energy (1-5), and do you feel closer to your goal? A short reply or skip — totally up to you."
CHECKIN_THANKS_TH = "ขอบคุณที่เล่าให้ฟังนะ 🌿"
CHECKIN_THANKS_EN = "Thanks for sharing that 🌿"

# YOL-62: voice fallback when transcription is unavailable
VOICE_FALLBACK_TH = "ขอโทษนะ ตอนนี้ยังฟังเสียงไม่ค่อยชัด — พิมพ์ชื่อเมนูหรือส่งรูปอาหารมาได้เลย 🍽️"
VOICE_FALLBACK_EN = "Sorry, I couldn't catch that voice note — type the dish name or send a photo instead 🍽️"

# YOL-52/53: milestone copy (templated — no Claude call). {n} = the milestone number.
MILESTONE_VOLUME_TH = {
    10: "นี่คือมื้อที่ 10 ที่เราบันทึกด้วยกันแล้วนะ ดีใจที่ได้ดูแลมื้ออาหารไปด้วยกัน 🎉",
    30: "30 มื้อแล้ว! เห็นความตั้งใจของคุณเลย ภูมิใจนะ 🎉",
    100: "ครบ 100 มื้อแล้วนะ นี่คือนิสัยที่ดีจริงๆ สุดยอดไปเลย 🎉",
}
MILESTONE_VOLUME_EN = {
    10: "That's your 10th meal logged with me — love that we're doing this together 🎉",
    30: "30 meals in! Your consistency really shows 🎉",
    100: "100 meals logged — this is a real habit now. Amazing 🎉",
}
MILESTONE_STREAK_TH = {
    3: "บันทึกติดกัน 3 วันแล้วนะ เริ่มเป็นจังหวะที่ดีเลย 🔥",
    7: "ครบ 7 วันติด! คุณกำลังสร้างนิสัยที่ดีจริงๆ 🔥",
    14: "สองสัปดาห์ติดต่อกันแล้ว เก่งมากเลยนะ 🔥",
    30: "30 วันติด! นี่คือความมุ่งมั่นระดับสุดยอดเลย 🔥",
}
MILESTONE_STREAK_EN = {
    3: "3 days in a row — you're finding a nice rhythm 🔥",
    7: "7-day streak! You're building a real habit 🔥",
    14: "Two full weeks in a row — that's impressive 🔥",
    30: "30-day streak! Incredible commitment 🔥",
}

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

# YOL-63: short labels for the Flex goal buttons (LINE button label cap = 20 chars). YOL-69: per-language.
GOAL_BUTTON_LABEL = {
    "lose_weight":  "🥗 ลดน้ำหนัก",
    "eat_clean":    "🌿 กินคลีน",
    "build_muscle": "💪 เพิ่มกล้ามเนื้อ",
    "no_goal":      "✨ ยังไม่มีเป้าหมาย",
}
GOAL_BUTTON_LABEL_EN = {
    "lose_weight":  "🥗 Lose weight",
    "eat_clean":    "🌿 Eat clean",
    "build_muscle": "💪 Build muscle",
    "no_goal":      "✨ Just exploring",
}

# YOL-63: warm, goal-specific, personalized confirmation after a goal tap. {name} optional.
GOAL_CONFIRM_TH = {
    "lose_weight":  "เยี่ยมเลย{name}! ตั้งเป้าหมายลดน้ำหนักให้แล้ว 🥑 ส่งรูปมื้อแรกมาได้เลย",
    "eat_clean":    "ดีมากเลย{name}! เป้าหมายกินคลีนพร้อมแล้ว 🥗 ส่งมื้อแรกมาดูกันเลย",
    "build_muscle": "สุดยอด{name}! ตั้งเป้าเพิ่มกล้ามเนื้อแล้ว 💪 ส่งมื้อแรกมาได้เลย",
    "no_goal":      "ยังไม่มีเป้าหมายก็ไม่เป็นไรเลย{name} — แค่อยากกินอย่างมีสติก็เริ่มได้ 🌿",
}
GOAL_CONFIRM_EN = {
    "lose_weight":  "Awesome{name}! Lose-weight goal is set 🥑 Send me your first meal anytime",
    "eat_clean":    "Love it{name}! Eat-clean goal is ready 🥗 Send your first meal and let's see",
    "build_muscle": "Great{name}! Build-muscle goal is set 💪 Send me your first meal anytime",
    "no_goal":      "No goal yet is perfectly fine{name} — just eating a little more mindfully is a great start 🌿",
}

# YOL-64: re-open the goal card on demand. Kept explicit so coaching questions that merely
# mention "goal" (e.g. "is this good for my goal?") don't hijack into the menu.
GOAL_MENU_KEYWORDS = {
    "เปลี่ยนเป้าหมาย", "ตั้งเป้าหมาย", "เลือกเป้าหมาย", "เมนูเป้าหมาย",
    "change goal", "change my goal", "set goal", "set my goal", "goal menu",
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
# YOL-61: proactive pattern-spotting — once/day per user {line_user_id: 'YYYY-MM-DD'}
pattern_spot_day: dict[str, str] = {}


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


def is_goal_menu_request(text: str) -> bool:
    """YOL-64: True if the user wants to (re)open the goal card."""
    t = text.lower().strip()
    return any(kw in t for kw in GOAL_MENU_KEYWORDS)


def parse_postback(data: str) -> dict:
    """YOL-63: parse a postback querystring like 'action=set_goal&goal=lose_weight'."""
    try:
        return {k: v[0] for k, v in urllib.parse.parse_qs(data or "").items()}
    except Exception:
        return {}


def build_journey_flex(display_name: str = "", current_goal: str | None = None, lang: str = "th") -> dict:
    """YOL-63/69: single-language onboarding journey bubble that doubles as the goal selector.
    Footer buttons are postback actions (clean chat; stable goal enum in payload)."""
    name = f" {display_name}" if display_name else ""
    th = lang != "en"
    greeting = f"สวัสดี{name}! 🥑" if th else f"Hi{name}! 🥑"
    sub = "มาเริ่มต้นดูแลมื้ออาหารไปด้วยกัน" if th else "Let's build healthier habits together"
    steps = [
        "➕ เพิ่มเพื่อน NutriBuddy", "🎯 ตั้งเป้าหมายของคุณ",
        "📷 บันทึกมื้อ — ถ่าย พิมพ์ หรือพูด", "💬 รับคำแนะนำทันที",
        "📊 สรุปรายวันและรายสัปดาห์", "🌿 สุขภาพดีขึ้นทีละนิด",
    ] if th else [
        "➕ Add NutriBuddy", "🎯 Set your goal",
        "📷 Log a meal — snap, type, or say it", "💬 Instant coaching",
        "📊 Daily & weekly recaps", "🌿 Healthier habits, gently",
    ]
    choose = "เลือกเป้าหมายของคุณ" if th else "Choose your goal"
    labels = GOAL_BUTTON_LABEL if th else GOAL_BUTTON_LABEL_EN

    body_contents = [
        {"type": "text", "text": greeting, "weight": "bold", "size": "xl", "color": "#16a34a"},
        {"type": "text", "text": sub, "size": "sm", "color": "#888888", "wrap": True, "margin": "sm"},
    ]
    if current_goal:
        cur = "เป้าหมายตอนนี้" if th else "Current goal"
        body_contents.append({
            "type": "text", "margin": "md", "size": "sm", "color": "#16a34a", "wrap": True,
            "text": f"{cur}: {GOAL_LABEL.get(current_goal, current_goal)}",
        })
    body_contents.append({"type": "separator", "margin": "lg"})
    for s in steps:
        body_contents.append({"type": "text", "text": s, "size": "sm", "wrap": True, "margin": "md", "color": "#333333"})
    body_contents.append({"type": "separator", "margin": "lg"})
    body_contents.append({"type": "text", "text": choose, "weight": "bold", "size": "md", "margin": "lg", "wrap": True})

    # All four goals are equal first-class options — identical styling so none looks
    # pre-selected. The selected goal is reflected in the header on re-open + the reply.
    buttons = [{
        "type": "button", "style": "secondary", "height": "sm", "margin": "sm",
        "action": {"type": "postback", "label": labels[g], "data": f"action=set_goal&goal={g}"},
    } for g in ("lose_weight", "eat_clean", "build_muscle", "no_goal")]

    return {
        "type": "bubble",
        "body": {"type": "box", "layout": "vertical", "contents": body_contents},
        "footer": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": buttons},
    }


def build_month_stats(meals: list) -> dict:
    """YOL-68: meal-derived stats for the Wrapped recap (pure; streak/outcome added by caller)."""
    from collections import Counter
    counts = Counter(m["description"] for m in meals)
    return {
        "total_meals": len(meals),
        "distinct_dishes": len(counts),
        "days_logged": len({bkk_date_key(m["logged_at"]) for m in meals}),
        "top_dishes": [d for d, _ in counts.most_common(3)],
    }


def build_wrapped_flex(stats: dict, streak: int, takeaway: str, lang: str = "th") -> dict:
    """YOL-68: shareable 'Your Food Month' recap bubble (single-language per YOL-69)."""
    th = lang != "en"
    title = "สรุปอาหารเดือนนี้ 🥑" if th else "Your Food Month 🥑"
    L = {
        "dishes": "เมนูที่ลอง" if th else "dishes tried",
        "days": "วันที่บันทึก" if th else "days logged",
        "streak": "ต่อเนื่องสูงสุด" if th else "streak",
        "unit_day": "วัน" if th else "days",
        "fav": "เมนูเด็ดของเดือนนี้" if th else "Your top dishes",
        "share": "กดค้างที่การ์ดเพื่อส่งต่อให้เพื่อน 💚" if th else "Long-press this card to share with a friend 💚",
    }

    def stat_row(num, label):
        return {"type": "box", "layout": "vertical", "flex": 1, "contents": [
            {"type": "text", "text": str(num), "size": "xxl", "weight": "bold", "color": "#16a34a", "align": "center"},
            {"type": "text", "text": label, "size": "xs", "color": "#888888", "align": "center", "wrap": True},
        ]}

    body = [
        {"type": "text", "text": title, "weight": "bold", "size": "lg", "color": "#16a34a", "wrap": True},
        {"type": "box", "layout": "horizontal", "margin": "lg", "contents": [
            stat_row(stats["distinct_dishes"], L["dishes"]),
            stat_row(stats["days_logged"], L["days"]),
            stat_row(f'{streak} {L["unit_day"]}', L["streak"]),
        ]},
    ]
    if stats["top_dishes"]:
        body.append({"type": "separator", "margin": "lg"})
        body.append({"type": "text", "text": L["fav"], "weight": "bold", "size": "sm", "margin": "lg", "color": "#333333"})
        for i, d in enumerate(stats["top_dishes"], 1):
            body.append({"type": "text", "text": f"{i}. {d}", "size": "sm", "margin": "sm", "wrap": True})
    if takeaway:
        body.append({"type": "separator", "margin": "lg"})
        body.append({"type": "text", "text": takeaway, "size": "sm", "margin": "lg", "wrap": True, "color": "#333333"})

    return {
        "type": "bubble",
        "body": {"type": "box", "layout": "vertical", "contents": body},
        "footer": {"type": "box", "layout": "vertical", "contents": [
            {"type": "text", "text": L["share"], "size": "xs", "color": "#16a34a", "align": "center", "wrap": True}
        ]},
    }


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


def streak_milestone(streak: int):
    return streak if streak in (3, 7, 14, 30) else None


def volume_milestone(count: int):
    return count if count in (10, 30, 100) else None


def summary_angle(seed: int) -> str:
    """YOL-54: rotate the summary's flavour so it isn't identical every day."""
    return ("a practical tip", "a surprising insight from their data", "pure encouragement")[seed % 3]


def summary_asks_question(seed: int) -> bool:
    """YOL-54: only ~1 in 3 summaries ends with an optional question (avoid nagging)."""
    return seed % 3 == 0


def was_truncated(stop_reason) -> bool:
    """YOL-65: definitive truncation signal — the model was cut at max_tokens.
    Replaces the unreliable Thai-particle heuristic for inferring completeness."""
    return stop_reason == "max_tokens"


def generate_complete(prompt: str, base_tokens: int, ceiling_tokens: int) -> str:
    """YOL-65: generate text that is never cut mid-word (critical for Thai, which has no
    spaces). Detects truncation via stop_reason and REGENERATES at a higher ceiling rather
    than space-stitching a continuation (which stranded fragments like 'พรุ พรุ่งนี้').
    Only trims to a boundary as a last resort if even the ceiling run is cut off."""
    resp = claude.messages.create(
        model="claude-sonnet-4-6", max_tokens=base_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    if was_truncated(resp.stop_reason):
        resp = claude.messages.create(
            model="claude-sonnet-4-6", max_tokens=ceiling_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
    text = resp.content[0].text
    if was_truncated(resp.stop_reason):  # still cut at the ceiling (rare) — last resort
        text = trim_to_complete(text) or text
    return text


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


def is_profile_stale(profile_updated_at, now) -> bool:
    """YOL-59: True if the coaching profile is missing or older than 24h."""
    if not profile_updated_at:
        return True
    try:
        ts = datetime.fromisoformat(str(profile_updated_at).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return True
    return (now - ts) >= timedelta(hours=24)


def profile_context(profile) -> str:
    """YOL-59: system-prompt snippet that injects the learned profile, or '' if none."""
    if not profile:
        return ""
    return ("\n\nWHAT YOU REMEMBER ABOUT THIS USER (use to personalize warmly, never to judge):\n"
            + profile)


def maybe_update_profile(user: dict, line_user_id: str):
    """YOL-59: refresh the learned profile via one cheap Haiku pass, only when stale
    (>24h). Learns silently from recent meals + recent things the user said."""
    if not is_profile_stale(user.get("profile_updated_at"), datetime.now(timezone.utc)):
        return
    try:
        meals = get_recent_meals(user["id"], 20)
        if not meals:
            return
        meal_list = ", ".join(m["description"] for m in meals)
        recent_msgs = " | ".join(
            h["content"] for h in conversation_history.get(line_user_id, [])
            if h.get("role") == "user"
        )[:500]
        goal_label = GOAL_LABEL.get(user.get("goal", "no_goal"), "no specific goal")
        existing = user.get("coaching_profile") or "none"
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=130,
            messages=[{"role": "user", "content":
                f"You maintain a compact coaching profile for a Thai food/health chatbot user.\n"
                f"Goal: {goal_label}\n"
                f"Recent meals (newest first): {meal_list}\n"
                f"Recent things the user said: {recent_msgs or 'none'}\n"
                f"Existing profile: {existing}\n\n"
                f"Write an updated profile: 3-5 very short facts about their eating patterns, "
                f"preferences, recurring dishes, and any stated dietary avoidances (e.g. 'no pork'). "
                f"Food and behavior ONLY — no names, no PII. Plain text, max 60 words. "
                f"Reply with ONLY the profile text."}],
        )
        profile = clean_for_line(resp.content[0].text)[:600]
        update_user_profile(user["id"], profile)
    except Exception as e:
        print(f"Profile update error for {user.get('id')}: {e}")


def is_checkin_pending(pending_at, now) -> bool:
    """YOL-60: True if a check-in was asked within the last 48h (awaiting a reply)."""
    if not pending_at:
        return False
    try:
        ts = datetime.fromisoformat(str(pending_at).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    return (now - ts) < timedelta(hours=48)


def parse_checkin(text: str) -> dict:
    """YOL-60: extract {is_checkin, energy(1-5|None), goal_progress, weight} from a reply."""
    SAFE = {"is_checkin": False, "energy": None, "goal_progress": None, "weight": None}
    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=60,
            messages=[{"role": "user", "content":
                "A health bot asked the user how they're feeling this week (energy + goal progress). "
                "Parse their reply into JSON only:\n"
                '{"is_checkin": true/false, "energy": 1-5 or null, "goal_progress": "better|same|worse" or null, "weight": number-in-kg or null}\n'
                "is_checkin = false if they ignored it / asked something unrelated. "
                "weight only if explicitly stated. Reply with ONLY the JSON.\n\n"
                f"Reply: {text}"}],
        )
        import json as _json
        s = resp.content[0].text.strip()
        a, b = s.find("{"), s.rfind("}")
        if a == -1 or b == -1:
            return dict(SAFE)
        data = _json.loads(s[a:b + 1])
        e = data.get("energy")
        e = e if isinstance(e, int) and 1 <= e <= 5 else None
        return {
            "is_checkin": bool(data.get("is_checkin")),
            "energy": e,
            "goal_progress": data.get("goal_progress") if data.get("goal_progress") in ("better", "same", "worse") else None,
            "weight": data.get("weight") if isinstance(data.get("weight"), (int, float)) else None,
        }
    except Exception as e:
        print(f"parse_checkin error: {e}")
        return dict(SAFE)


def pattern_spot_allowed(line_user_id: str) -> bool:
    """YOL-61: allow a proactive pattern observation at most once per BKK day per user."""
    today = datetime.now(BKK).date().isoformat()
    if pattern_spot_day.get(line_user_id) == today:
        return False
    pattern_spot_day[line_user_id] = today
    return True


def check_milestone(user_id: str, lang: str, is_first_today: bool) -> str | None:
    """YOL-52/53: return a templated milestone celebration, or None. Volume milestones
    fire on any qualifying log; streak milestones only on the first meal of the day."""
    try:
        vm = volume_milestone(get_meal_count(user_id))
        if vm:
            return (MILESTONE_VOLUME_TH if lang == "th" else MILESTONE_VOLUME_EN)[vm]
        if is_first_today:
            keys = {bkk_date_key(x) for x in get_meal_dates(user_id, 45)}
            sm = streak_milestone(compute_streak(keys, datetime.now(BKK).date()))
            if sm:
                return (MILESTONE_STREAK_TH if lang == "th" else MILESTONE_STREAK_EN)[sm]
    except Exception as e:
        print(f"Milestone check error for {user_id}: {e}")
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


def _reply_flex(reply_token: str, alt_text: str, bubble: dict):
    """YOL-63: reply with a Flex bubble."""
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message(
            ReplyMessageRequest(reply_token=reply_token, messages=[
                FlexMessage(alt_text=alt_text, contents=FlexContainer.from_dict(bubble))
            ])
        )


def _push_flex(line_user_id: str, alt_text: str, bubble: dict):
    """YOL-68: push a Flex bubble (scheduled recap)."""
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).push_message(
            PushMessageRequest(to=line_user_id, messages=[
                FlexMessage(alt_text=alt_text, contents=FlexContainer.from_dict(bubble))
            ])
        )


def _display_name(line_user_id: str) -> str:
    """YOL-63: fetch the LINE display name for a personalized greeting. Not stored (no PII)."""
    try:
        with ApiClient(configuration) as api_client:
            return MessagingApi(api_client).get_profile(line_user_id).display_name or ""
    except Exception as e:
        print(f"display_name fetch error: {e}")
        return ""


def transcribe_audio(audio_bytes: bytes, duration_ms: int = 0) -> str | None:
    """YOL-62: transcribe a LINE voice note (m4a) via Google Speech-to-Text.
    Transcodes m4a→FLAC with ffmpeg first (Google STT doesn't accept AAC). Returns
    None if GOOGLE_API_KEY is unset, audio is too long, or anything fails (graceful)."""
    key = os.environ.get("GOOGLE_API_KEY", "")
    if not key:
        return None
    if duration_ms and duration_ms > 60000:  # cap at 60s — guardrail
        return None
    try:
        with tempfile.NamedTemporaryFile(suffix=".m4a") as src, \
             tempfile.NamedTemporaryFile(suffix=".flac") as dst:
            src.write(audio_bytes)
            src.flush()
            subprocess.run(
                ["ffmpeg", "-y", "-i", src.name, "-ac", "1", "-ar", "16000", dst.name],
                check=True, capture_output=True, timeout=30,
            )
            with open(dst.name, "rb") as f:
                flac = f.read()
        body = json.dumps({
            "config": {
                "encoding": "FLAC",
                "sampleRateHertz": 16000,
                "languageCode": "th-TH",
                "alternativeLanguageCodes": ["en-US"],
            },
            "audio": {"content": base64.b64encode(flac).decode("utf-8")},
        }).encode("utf-8")
        req = urllib.request.Request(
            f"https://speech.googleapis.com/v1/speech:recognize?key={key}",
            data=body, headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
        parts = [
            x["alternatives"][0]["transcript"]
            for x in data.get("results", []) if x.get("alternatives")
        ]
        text = " ".join(parts).strip()
        return text or None
    except Exception as e:
        print(f"Transcription error: {e}")
        return None


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

def _apply_goal(user: dict, line_user_id: str, goal: str, lang: str) -> str:
    """Set the goal, fire analytics, return a warm goal-specific confirmation. Shared by
    the text path and the Flex postback (YOL-63)."""
    is_initial = user.get("goal") == "no_goal"
    update_user_goal(line_user_id, goal)
    user["goal"] = goal
    try:
        tracking.track_goal_set(
            line_user_id, goal=goal,
            previous_goal=user.get("goal") if not is_initial else None,
            is_initial_set=is_initial, set_method="flex_postback",
        )
        tracking.identify_user(line_user_id, goal=goal, language=lang)
    except Exception as e:
        print(f"Analytics error (goal.set): {e}")
    table = GOAL_CONFIRM_TH if lang == "th" else GOAL_CONFIRM_EN
    return table.get(goal, table["no_goal"])


@handler.add(PostbackEvent)
def handle_postback(event):
    """YOL-63: handle Flex goal-button taps (postback). Always confirm warmly — a postback
    shows nothing in the chat, so a silent state change would be confusing."""
    line_user_id = event.source.user_id
    user = get_or_create_user(line_user_id)
    try:
        update_last_active(user["id"])
    except Exception:
        pass
    data = parse_postback(event.postback.data)
    action = data.get("action")
    if action == "set_goal" and data.get("goal") in GOAL_LABEL:
        lang = user.get("language", "th")
        name = _display_name(line_user_id)
        confirm = _apply_goal(user, line_user_id, data["goal"], lang)
        confirm = confirm.format(name=(f" {name}" if name else ""))
        _reply(event.reply_token, confirm)
    elif action == "open_goal_menu":  # YOL-64: Rich Menu button
        lang = user.get("language", "th")
        bubble = build_journey_flex(_display_name(line_user_id), current_goal=user.get("goal"), lang=lang)
        try:
            _reply_flex(event.reply_token, "เลือกเป้าหมายของคุณ" if lang != "en" else "Choose your goal", bubble)
        except Exception as e:
            print(f"Goal menu flex error: {e}")


@handler.add(FollowEvent)
def handle_follow(event):
    user = get_or_create_user(event.source.user_id)
    lang = user.get("language", "th")  # YOL-69: new users default Thai (DB default)
    # YOL-63: personalized single-language journey card that doubles as the goal selector.
    try:
        name = _display_name(event.source.user_id)
        bubble = build_journey_flex(name, lang=lang)
        alt = "ยินดีต้อนรับสู่ NutriBuddy! เลือกเป้าหมายของคุณ" if lang != "en" else "Welcome to NutriBuddy! Choose your goal"
        _reply_flex(event.reply_token, alt, bubble)
        time.sleep(1)
        _push(event.source.user_id, _onboarding_2(lang))  # capabilities + how to re-open goal menu
    except Exception as e:
        print(f"Onboarding flex error: {e}")  # fall back to text onboarding (YOL-26)
        _reply(event.reply_token, _onboarding_1(lang))
        time.sleep(1)
        _push(event.source.user_id, _onboarding_2(lang))
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

    # YOL-29: Rate limit — silently drop if >10 messages/60s
    if is_rate_limited(line_user_id):
        return

    user = get_or_create_user(line_user_id)
    try:
        update_last_active(user["id"])  # YOL-35
    except Exception as e:
        print(f"last_active update error: {e}")

    process_text(event, user, event.message.text)


def process_text(event, user: dict, text: str):
    """Shared text pipeline — reused by handle_text and voice transcripts (YOL-62)."""
    line_user_id = user["line_user_id"]
    user_id = user["id"]
    lang = "th" if is_thai(text) else "en"

    if user["language"] != lang:
        update_user_language(line_user_id, lang)
        user["language"] = lang

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

    # YOL-60: capture an outcome check-in reply (before goal detection, since a bare
    # energy digit like "4" would otherwise be misread as a goal selection).
    if is_checkin_pending(user.get("checkin_pending_at"), datetime.now(timezone.utc)):
        parsed = parse_checkin(text)
        try:
            clear_checkin_pending(user_id)
        except Exception:
            pass
        if parsed["is_checkin"]:
            try:
                insert_checkin(user_id, parsed["energy"], parsed["goal_progress"], parsed["weight"])
                log_event(user_id, "checkin_completed")
            except Exception as e:
                print(f"checkin store error for {user_id}: {e}")
            _reply(event.reply_token, CHECKIN_THANKS_TH if lang == "th" else CHECKIN_THANKS_EN)
            return
        # not a check-in answer → fall through to normal handling

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

    # YOL-64: re-open the goal card on demand (keyword path; Rich Menu uses a postback)
    if is_goal_menu_request(text):
        try:
            bubble = build_journey_flex(_display_name(line_user_id), current_goal=user.get("goal"), lang=lang)
            _reply_flex(event.reply_token, "เลือกเป้าหมายของคุณ" if lang != "en" else "Choose your goal", bubble)
        except Exception as e:
            print(f"Goal menu flex error: {e}")
            _reply(event.reply_token,
                   "บอกเป้าหมายได้เลยนะ: ลดน้ำหนัก / กินคลีน / เพิ่มกล้ามเนื้อ / ยังไม่มีเป้าหมาย"
                   if lang != "en" else "Just tell me your goal: lose weight / eat clean / build muscle / no goal")
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
        # YOL-65: Thai-safe completion (stop_reason regeneration, no mid-word cut)
        _reply(event.reply_token, generate_complete(prompt, base_tokens=400, ceiling_tokens=600))
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
    is_first_today = not get_today_meals(user_id) if triage["meals"] else False
    try:
        for entry in triage["meals"]:
            dish = (entry.get("dish") or "").strip()[:200]
            if dish:
                log_meal(user_id, dish, source="text", meal_type=entry.get("meal_type") or None)
                logged_dishes.append(dish)
    except Exception as e:
        print(f"Text meal log error for {user_id}: {e}")  # Non-fatal

    # YOL-44 follow-through takes priority; else YOL-52/53 milestone
    celebration = None
    if logged_dishes:
        try:
            celebration = check_follow_through(user, logged_dishes)
        except Exception as e:
            print(f"Follow-through error for {user_id}: {e}")
        if not celebration:
            celebration = check_milestone(user_id, lang, is_first_today)

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
    system += profile_context(user.get("coaching_profile"))  # YOL-59
    if meal_context_parts:
        system += "\n\nMEAL CONTEXT (use this when answering questions about what was eaten):\n" + "\n".join(meal_context_parts)

    # YOL-61: proactive pattern-spotting on a meal log — folded into this same reply,
    # capped once/day per user. Default is silence; only speak up if truly noteworthy.
    if logged_dishes and pattern_spot_allowed(line_user_id):
        try:
            recent_list = ", ".join(m["description"] for m in get_recent_meals(user_id, 6))
            system += ("\n\nRECENT MEALS (newest first): " + recent_list +
                       "\nIf AND ONLY IF you notice a genuinely noteworthy short-term pattern "
                       "(several similar meals in a row, a run of goal-fit choices, or a sudden shift), "
                       "add ONE warm, forward-looking observation or offer (e.g. 'want a lighter dinner idea?'). "
                       "Otherwise don't mention patterns at all. Never shame.")
        except Exception as e:
            print(f"Pattern-spot context error for {user_id}: {e}")

    # YOL-19: Call Claude with prior history + this turn, then commit BOTH turns only on
    # success — appending the user turn before the call corrupts history if the call throws.
    history = conversation_history.setdefault(line_user_id, [])
    resp = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,  # YOL-65: headroom so a 2-sentence Thai reply isn't cut mid-word
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

    maybe_update_profile(user, line_user_id)  # YOL-59: refresh if stale (post-reply, no added latency)


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
        max_tokens=400,  # YOL-65: Thai headroom for DISH line + coaching response
        system=SYSTEM_PROMPT.format(goal=goal_label) + profile_context(user.get("coaching_profile")),  # YOL-59
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
        is_first_today = not get_today_meals(user_id)  # before logging this one
        try:
            log_meal(user_id, dish_name, source="photo")
        except Exception as e:
            print(f"Meal log error for {user_id}: {e}")
        # YOL-44: follow-through celebration takes priority; else YOL-52/53 milestone
        try:
            celebration = check_follow_through(user, [dish_name])
        except Exception as e:
            print(f"Follow-through error for {user_id}: {e}")
        if not celebration:
            celebration = check_milestone(user_id, lang, is_first_today)

    # YOL-19: Add photo + reply to conversation history
    history = conversation_history.setdefault(line_user_id, [])
    history.append({"role": "user", "content": "[sent a food photo]"})
    history.append({"role": "assistant", "content": coaching_text})
    if len(history) > 10:
        conversation_history[line_user_id] = history[-10:]

    _reply(event.reply_token, coaching_text)
    if celebration:
        _push(line_user_id, celebration)

    if dish_name:
        maybe_update_profile(user, line_user_id)  # YOL-59: refresh if stale


# ── AUDIO / VOICE (YOL-62) ────────────────────────────────────────────────────

@handler.add(MessageEvent, message=AudioMessageContent)
def handle_audio(event):
    line_user_id = event.source.user_id

    # YOL-29: rate limit
    if is_rate_limited(line_user_id):
        return

    user = get_or_create_user(line_user_id)
    try:
        update_last_active(user["id"])  # YOL-35
    except Exception as e:
        print(f"last_active update error: {e}")
    lang = user.get("language", "th")

    if is_blocked(user["id"]):
        _reply(event.reply_token, BLOCKED_TH if lang == "th" else BLOCKED_EN)
        return

    # Download the voice note (m4a), transcribe, then reuse the text pipeline.
    try:
        with ApiClient(configuration) as api_client:
            audio_bytes = MessagingApiBlob(api_client).get_message_content(message_id=event.message.id)
    except Exception as e:
        print(f"Audio download error for {user['id']}: {e}")
        _reply(event.reply_token, VOICE_FALLBACK_TH if lang == "th" else VOICE_FALLBACK_EN)
        return

    duration = getattr(event.message, "duration", 0) or 0
    transcript = transcribe_audio(audio_bytes, duration)  # raw audio discarded after this
    if not transcript:
        _reply(event.reply_token, VOICE_FALLBACK_TH if lang == "th" else VOICE_FALLBACK_EN)
        return

    process_text(event, user, transcript)  # YOL-62: route through the existing text path


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

            # YOL-52: current logging streak (BKK)
            try:
                streak = compute_streak(
                    {bkk_date_key(x) for x in get_meal_dates(user["id"], 45)},
                    datetime.now(BKK).date(),
                )
            except Exception:
                streak = 0
            # YOL-54: rotate angle + sometimes ask an optional question
            seed = datetime.now(BKK).timetuple().tm_yday
            angle = summary_angle(seed)
            ask_q = summary_asks_question(seed)

            focus = {
                "lose_weight":  "Name a specific lower-cal swap to try tomorrow.",
                "eat_clean":    "Name a specific vegetable to add tomorrow.",
                "build_muscle": "Name a specific protein source to add tomorrow.",
                "no_goal":      "Note a positive pattern and suggest one simple habit.",
            }.get(goal, "Suggest one specific, practical thing for tomorrow.")

            streak_line = (f"The user has a {streak}-day logging streak — if 3 or more, mention it warmly in Part A."
                           if streak >= 3 else "")
            question_line = ("End Part B with a light, genuinely optional question inviting a reply "
                             "(e.g. about eating out or tomorrow's plan). Never pressure." if ask_q else "")
            profile = user.get("coaching_profile")  # YOL-59
            profile_line = f"What you remember about this user: {profile}" if profile else ""

            prompt = f"""User logged {n} meal(s) today. Dishes: {', '.join(dishes)}.
User goal: {goal_label}
User language: {lang_word}
Yesterday's suggestion (if any): {last_suggestion}
{profile_line}
{streak_line}

Write TWO parts separated by a line containing only ===
Part A: a warm opener, 1 sentence, mentions they logged {n} meal(s), ends with 1 emoji.
Part B: 2-3 sentences with the flavour of {angle}. Observe a pattern from today's dishes, connect to their goal, then give ONE specific actionable suggestion for tomorrow — name a real dish or ingredient. {focus} Do not repeat yesterday's suggestion. {question_line} End with 1 emoji.

Tone: data-light storyteller, warm friend, no numbers, no calories, no lecturing.
Do NOT list the meals yourself. Plain text only, no markdown. Reply in {lang_word}."""

            # YOL-65: higher ceiling + stop_reason-based regeneration so Thai never cuts mid-word
            raw = generate_complete(prompt, base_tokens=400, ceiling_tokens=700)
            opener, narrative = split_opener_narrative(raw)
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
            # YOL-54: sometimes invite a reply
            ask_q = summary_asks_question(datetime.now(BKK).timetuple().tm_yday)
            question_line = ("End Part B with a light, genuinely optional question inviting a reply "
                             "about the week ahead. Never pressure." if ask_q else "")
            focus = {
                "lose_weight":  "Spot a weekly pattern and name one swap to try.",
                "eat_clean":    "Celebrate any clean choices and name one vegetable to add this week.",
                "build_muscle": "Note protein consistency and name one dish to add more of.",
                "no_goal":      "Celebrate consistency and suggest one simple habit for the week.",
            }.get(goal, "Suggest one specific thing to try this week.")

            profile = user.get("coaching_profile")  # YOL-59
            profile_line = f"What you remember about this user: {profile}" if profile else ""

            prompt = f"""User logged meals on {days} of 7 days this week.
Top dishes (by frequency): {', '.join(top_dishes)}.
User goal: {goal_label}
User language: {lang_word}
{profile_line}

Write TWO parts separated by a line containing only ===
Part A: a warm opener, 1 sentence, mentions they logged {days} of 7 days, ends with 1 emoji. Tone: {tone}.
Part B: 2-3 sentences. Observe a weekly eating pattern, call out one positive thing, then give ONE specific suggestion for the coming week — name a real dish or ingredient. {focus} {question_line} End with 1 emoji.

Tone: data-light storyteller, warm friend, no numbers, no calories, no lecturing.
Do NOT list the dishes yourself. Plain text only, no markdown. Reply in {lang_word}."""

            # YOL-65: higher ceiling + stop_reason-based regeneration (Thai-safe)
            raw = generate_complete(prompt, base_tokens=500, ceiling_tokens=800)
            opener, narrative = split_opener_narrative(raw)
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

            # YOL-60: opt-in outcome check-in (gentle, optional — only for engaged users)
            try:
                _push(line_user_id, CHECKIN_PROMPT_TH if lang == "th" else CHECKIN_PROMPT_EN)
                set_checkin_pending(user["id"])
            except Exception as e:
                print(f"Check-in prompt error for {user['id']}: {e}")
        except Exception as e:
            print(f"Weekly summary error for {user.get('line_user_id')}: {e}")


# ── WIN-BACK NUDGE (YOL-51) — 17:00 Bangkok = 10:00 UTC ───────────────────────

def send_winback_nudges():
    # PLAN (YOL-51): once per lapse, nudge users idle 3–5 days. Warm, no guilt.
    #   Skip blocked users. Mark sent so they get exactly one nudge per quiet streak.
    for user in get_lapsed_users():
        try:
            if is_blocked(user["id"]):
                continue
            lang = user.get("language", "th")
            # YOL-59: personalize the nudge with the learned profile when available
            profile = user.get("coaching_profile")
            msg = WINBACK_TH if lang == "th" else WINBACK_EN
            if profile:
                try:
                    resp = claude.messages.create(
                        model="claude-sonnet-4-6",
                        max_tokens=90,
                        messages=[{"role": "user", "content":
                            f"Write a warm, no-guilt win-back message to a user who's been quiet a few days. "
                            f"1-2 short sentences, reference something they enjoy from this profile, and invite "
                            f"them to log a meal. End with 1 emoji. Reply in {'Thai' if lang == 'th' else 'English'}.\n\n"
                            f"Profile: {profile}"}],
                    )
                    msg = resp.content[0].text
                except Exception as e:
                    print(f"Win-back personalize error for {user['id']}: {e}")
            _push(user["line_user_id"], msg)
            mark_winback_sent(user["id"])
            try:
                log_event(user["id"], "winback_sent")
            except Exception:
                pass
        except Exception as e:
            print(f"Win-back error for {user.get('line_user_id')}: {e}")


# ── MONTHLY WRAPPED RECAP (YOL-68) — 1st of month, 09:00 Bangkok = 02:00 UTC ──

MONTHLY_QUIET_TH = "เดือนนี้เงียบไปนิดนะ 🌿 ไม่เป็นไรเลย เดือนหน้าเริ่มใหม่ด้วยกัน — ส่งมื้อแรกมาได้ทุกเมื่อ"
MONTHLY_QUIET_EN = "A quiet month 🌿 That's totally okay — let's start fresh next month. Send me your first meal anytime"


def send_monthly_recaps():
    # PLAN (YOL-68): a shareable 'Your Food Month' Flex card per user.
    #   No-shame: a quiet month gets a warm encouraging note, never a 'you failed' score.
    for user in get_all_users():
        try:
            lang = user.get("language", "th")
            line_user_id = user["line_user_id"]
            meals = get_month_meals(user["id"], 30)

            if not meals:
                _push(line_user_id, MONTHLY_QUIET_TH if lang == "th" else MONTHLY_QUIET_EN)
                continue

            stats = build_month_stats(meals)
            streak = compute_streak(
                {bkk_date_key(x) for x in get_meal_dates(user["id"], 45)},
                datetime.now(BKK).date(),
            )
            goal_label = GOAL_LABEL.get(user.get("goal", "no_goal"), "no specific goal")
            lang_word = "Thai" if lang == "th" else "English"
            takeaway = generate_complete(
                f"Write ONE warm, personal sentence celebrating this user's food month. "
                f"They tried {stats['distinct_dishes']} different dishes across {stats['days_logged']} days; "
                f"favorites: {', '.join(stats['top_dishes'])}. Goal: {goal_label}. "
                f"Celebrate variety, consistency, and effort — NEVER mention calories or 'failed' days, "
                f"no numbers-as-judgment. Warm friend tone, reply in {lang_word}, end with 1 emoji.",
                base_tokens=150, ceiling_tokens=300,
            )
            bubble = build_wrapped_flex(stats, streak, clean_for_line(takeaway), lang)
            alt = "สรุปอาหารเดือนนี้ของคุณ 🥑" if lang == "th" else "Your food month 🥑"
            _push_flex(line_user_id, alt, bubble)
            try:
                log_event(user["id"], "monthly_recap_sent")
            except Exception:
                pass
        except Exception as e:
            print(f"Monthly recap error for {user.get('line_user_id')}: {e}")


# ── SCHEDULER (20:00 Bangkok = 13:00 UTC) ─────────────────────────────────────

scheduler = BackgroundScheduler(timezone=pytz.utc)
scheduler.add_job(send_daily_summaries, "cron", hour=13, minute=0)
scheduler.add_job(send_weekly_summaries, "cron", day_of_week="mon", hour=1, minute=0)
scheduler.add_job(send_winback_nudges, "cron", hour=10, minute=0)  # 17:00 BKK
scheduler.add_job(send_monthly_recaps, "cron", day=1, hour=2, minute=0)  # 1st, 09:00 BKK
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

    # YOL-60: outcome signal (the north-star proxy) — last 30 days of check-ins
    month_ago = (now - timedelta(days=30)).isoformat()
    checkins = supabase.table("checkins").select("*").gte("created_at", month_ago).execute().data
    energies = [c["energy"] for c in checkins if isinstance(c.get("energy"), (int, float))]
    avg_energy = round(sum(energies) / len(energies), 1) if energies else None
    progress_dist = dict(Counter(c["goal_progress"] for c in checkins if c.get("goal_progress")))

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
            "winback_sent_7d": event_counts.get("winback_sent", 0),
            "checkins_completed_7d": event_counts.get("checkin_completed", 0),
        },
        "outcomes": {
            "checkins_30d": len(checkins),
            "avg_energy_30d": avg_energy,
            "goal_progress_dist_30d": progress_dist,
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


@app.post("/cron/winback")
def trigger_winback(request: Request):
    """Manual trigger for win-back nudges — protected by CRON_SECRET header."""
    expected = os.environ.get("CRON_SECRET", "")
    if not expected or request.headers.get("X-Cron-Secret", "") != expected:
        raise HTTPException(status_code=403, detail="Forbidden")
    send_winback_nudges()
    return {"status": "sent"}


@app.post("/cron/monthly-recap")
def trigger_monthly_recap(request: Request):
    """Manual trigger for the Wrapped monthly recap — protected by CRON_SECRET header."""
    expected = os.environ.get("CRON_SECRET", "")
    if not expected or request.headers.get("X-Cron-Secret", "") != expected:
        raise HTTPException(status_code=403, detail="Forbidden")
    send_monthly_recaps()
    return {"status": "sent"}
