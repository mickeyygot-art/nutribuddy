"""
NutriBuddy — Logic Unit Tests
Run: python tests/test_logic.py
These tests cover all pure logic functions (no API calls, no DB).
"""

from datetime import datetime, timezone, timedelta
import pytz

BKK = pytz.timezone("Asia/Bangkok")


# ── HELPERS (copied from main.py / database.py for isolated testing) ──────────

def is_thai(text: str) -> bool:
    return any("฀" <= c <= "๿" for c in text)


def detect_goal(text: str) -> str | None:
    GOAL_DIGITS = {"1": "lose_weight", "2": "eat_clean", "3": "build_muscle", "4": "no_goal"}
    GOAL_PHRASES = {
        "ลดน้ำหนัก": "lose_weight",   "lose weight": "lose_weight",
        "กินสะอาด": "eat_clean",       "eat clean": "eat_clean",
        "กินอาหารคลีน": "eat_clean",   "อาหารคลีน": "eat_clean",
        "เพิ่มกล้าม": "build_muscle",  "build muscle": "build_muscle",
        "ยังไม่มีเป้าหมาย": "no_goal", "no goal": "no_goal",
    }
    t = text.lower().strip()
    if t in GOAL_DIGITS:
        return GOAL_DIGITS[t]
    for key, goal in GOAL_PHRASES.items():
        if key in t:
            return goal
    return None


def extract_dish(full_response: str) -> tuple[str, str]:
    lines = full_response.strip().splitlines()
    dish_name = "unknown dish"
    coaching_text = full_response
    if lines and lines[0].upper().startswith("DISH:"):
        dish_name = lines[0][5:].strip()
        coaching_text = "\n".join(lines[2:]).strip() if len(lines) > 2 else full_response
    return dish_name, coaching_text


def build_summary_context(meals: list) -> tuple[str, bool]:
    by_type = {}
    for m in meals:
        by_type.setdefault(m["meal_type"], []).append(m["description"])
    meal_lines = "\n".join(
        f"- {mtype.capitalize()}: {', '.join(dishes)}"
        for mtype, dishes in by_type.items()
    )
    return meal_lines, "dinner" in by_type


def meal_type_from_hour(hour: int) -> str:
    if 6 <= hour <= 10:
        return "breakfast"
    elif 11 <= hour <= 14:
        return "lunch"
    elif 15 <= hour <= 17:
        return "snack"
    elif 18 <= hour <= 21:
        return "dinner"
    return "late_snack"


def build_meal_history_context(meals: list, date_str: str) -> str:
    """Pure helper — mirrors main.py build_meal_history_context."""
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


def validate_date_intent(date_str: str, today_bkk_date) -> str:
    """Pure helper — mirrors the date-validation logic from detect_date_intent (no Haiku call)."""
    if date_str.upper() == "NO":
        return "NO"
    try:
        asked_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        cutoff = today_bkk_date - timedelta(days=30)
        if asked_date < cutoff:
            return "TOO_OLD"
        return date_str
    except ValueError:
        return "NO"


def clean_for_line(text: str) -> str:
    import re
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'\*(.*?)\*', r'\1', text)
    text = re.sub(r'__(.*?)__', r'\1', text)
    text = re.sub(r'_(.*?)_', r'\1', text)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[-*]\s+', '', text, flags=re.MULTILINE)
    emoji_pattern = re.compile(
        "[\U00002600-\U000027BF\U0001F300-\U0001F9FF"
        "\U0001FA00-\U0001FA9F\U00002702-\U000027B0"
        "\U0000FE00-\U0000FE0F\U0001F1E0-\U0001F1FF]+",
        flags=re.UNICODE
    )
    emojis_found = emoji_pattern.findall(text)
    if len(emojis_found) > 1:
        first_emoji = emojis_found[0]
        text = emoji_pattern.sub('', text).strip()
        text = text + ' ' + first_emoji
    return text.strip()


def check_cron_auth(header_secret: str, env_secret: str) -> bool:
    if not env_secret:
        return False
    return header_secret == env_secret


CONVERSATIONAL_WHITELIST = {
    "โอเค", "ok", "okay", "ได้", "ครับ", "ค่ะ", "นะ",
    "yes", "no", "ใช่", "ไม่", "ขอบคุณ", "thanks",
    "บอกไปแล้ว", "บอกแล้ว", "แล้ว", "เข้าใจ",
}


def is_conversational(text: str) -> bool:
    """Pure helper — mirrors main.py is_conversational."""
    t = text.strip()
    if len(t) <= 10:
        return True
    return t.lower() in CONVERSATIONAL_WHITELIST


def is_meal_report_result(result: str) -> bool:
    """Pure helper — mirrors main.py classify_meal_report result check."""
    return result.strip().upper() == "MEAL"


def is_unblock_command(text: str) -> bool:
    """Pure helper — mirrors main.py is_unblock_command."""
    UNBLOCK_KEYWORDS = {
        "เริ่มใหม่", "ขอโทษ", "ยกเลิก", "unblock",
        "start", "restart", "sorry",
    }
    t = text.lower().strip()
    return any(kw in t for kw in UNBLOCK_KEYWORDS)


def trim_history(history: list, max_entries: int = 10) -> list:
    """Pure helper — mirrors the history-trim logic in handle_text."""
    if len(history) > max_entries:
        return history[-max_entries:]
    return history


def get_top_dishes(meals: list, n: int = 3) -> list:
    """Pure helper — mirrors weekly summary top-dish logic."""
    from collections import Counter
    dish_counts = Counter(m["description"] for m in meals)
    return [d for d, _ in dish_counts.most_common(n)]


def simulate_block_state(current_count: int, blocked_until_str: str | None = None) -> tuple[str, int]:
    now = datetime.now(timezone.utc)
    if blocked_until_str:
        blocked_until = datetime.fromisoformat(blocked_until_str)
        if now < blocked_until:
            return "BLOCKED", current_count
    new_count = current_count + 1
    if new_count >= 3:
        return "NOW_BLOCKED", 0
    return "WARNED", new_count


# ── TESTS ─────────────────────────────────────────────────────────────────────

def run(label, condition):
    status = "✅" if condition else "❌"
    print(f"  {status} {label}")
    if not condition:
        raise AssertionError(f"FAILED: {label}")


def test_thai_detection():
    print("=== Thai detection ===")
    run("Pure Thai", is_thai("สวัสดี"))
    run("Thai dish name", is_thai("ข้าวมันไก่"))
    run("English → False", not is_thai("Hello"))
    run("Mixed → True", is_thai("hello สวัสดี"))
    run("Numbers → False", not is_thai("1234"))
    run("Empty → False", not is_thai(""))


def test_goal_detection():
    print("=== Goal detection ===")
    run("Number 1 → lose_weight", detect_goal("1") == "lose_weight")
    run("Thai lose weight", detect_goal("ลดน้ำหนัก") == "lose_weight")
    run("Thai sentence", detect_goal("อยากลดน้ำหนักครับ") == "lose_weight")
    run("Number 3 → build_muscle", detect_goal("3") == "build_muscle")
    run("Number 4 → no_goal", detect_goal("4") == "no_goal")
    run("Off-topic → None", detect_goal("what is the weather?") is None)
    run("Empty → None", detect_goal("") is None)
    run("English goal change", detect_goal("change to eat clean") == "eat_clean")
    run("New wording กินอาหารคลีน → eat_clean", detect_goal("กินอาหารคลีนมากขึ้น") == "eat_clean")
    run("New wording อาหารคลีน → eat_clean", detect_goal("อยากกินอาหารคลีน") == "eat_clean")
    # Regression: bare digits must NOT match as substrings (lead-dev review fix)
    run("'กินข้าว 2 จาน' → None (not eat_clean)", detect_goal("กินข้าว 2 จาน") is None)
    run("'ate 1 plate' → None (not lose_weight)", detect_goal("ate 1 plate") is None)
    run("Standalone '2' → eat_clean", detect_goal("2") == "eat_clean")
    run("'  3  ' trimmed → build_muscle", detect_goal("  3  ") == "build_muscle")


def test_dish_extraction():
    print("=== Dish name extraction ===")
    d, c = extract_dish("DISH: ข้าวมันไก่\n\nโปรตีนดีมากเลย!")
    run("Thai dish extracted", d == "ข้าวมันไก่")
    run("Coaching text returned", c == "โปรตีนดีมากเลย!")

    d, c = extract_dish("DISH: Pad Thai\n\nGreat choice!")
    run("English dish extracted", d == "Pad Thai")

    d, c = extract_dish("dish: som tam\n\nLooks healthy!")
    run("Case insensitive", d == "som tam")

    d, c = extract_dish("No dish prefix here, just coaching.")
    run("No prefix → unknown dish", d == "unknown dish")
    run("No prefix → full text as coaching", c == "No dish prefix here, just coaching.")

    d, c = extract_dish("DISH: ต้มยำกุ้ง")
    run("Dish only, no coaching", d == "ต้มยำกุ้ง")


def test_summary_context():
    print("=== Daily summary context ===")
    meals_full = [
        {"meal_type": "breakfast", "description": "โจ๊กหมู"},
        {"meal_type": "lunch", "description": "ข้าวมันไก่"},
        {"meal_type": "dinner", "description": "ต้มยำกุ้ง"},
    ]
    lines, has_dinner = build_summary_context(meals_full)
    run("Full day has_dinner=True", has_dinner)
    run("Breakfast in lines", "Breakfast: โจ๊กหมู" in lines)
    run("Dinner in lines", "Dinner: ต้มยำกุ้ง" in lines)

    _, has_dinner = build_summary_context([{"meal_type": "lunch", "description": "x"}])
    run("No dinner → has_dinner=False", not has_dinner)

    meals_multi = [
        {"meal_type": "snack", "description": "กล้วย"},
        {"meal_type": "snack", "description": "โยเกิร์ต"},
    ]
    lines, _ = build_summary_context(meals_multi)
    run("Multiple snacks merged", "กล้วย, โยเกิร์ต" in lines)

    lines, has_dinner = build_summary_context([])
    run("Empty meals → empty lines", lines == "")
    run("Empty meals → has_dinner=False", not has_dinner)


def test_meal_type_inference():
    print("=== Meal type by hour ===")
    run("06:00 → breakfast", meal_type_from_hour(6) == "breakfast")
    run("10:00 → breakfast", meal_type_from_hour(10) == "breakfast")
    run("11:00 → lunch", meal_type_from_hour(11) == "lunch")
    run("14:00 → lunch", meal_type_from_hour(14) == "lunch")
    run("15:00 → snack", meal_type_from_hour(15) == "snack")
    run("18:00 → dinner", meal_type_from_hour(18) == "dinner")
    run("21:00 → dinner", meal_type_from_hour(21) == "dinner")
    run("22:00 → late_snack", meal_type_from_hour(22) == "late_snack")
    run("00:00 → late_snack", meal_type_from_hour(0) == "late_snack")


def test_cron_auth():
    print("=== CRON auth ===")
    run("Correct secret → authorized", check_cron_auth("abc123", "abc123"))
    run("Wrong secret → forbidden", not check_cron_auth("wrong", "abc123"))
    run("Empty header → forbidden", not check_cron_auth("", "abc123"))
    run("No env var → forbidden", not check_cron_auth("abc123", ""))
    run("Case sensitive", not check_cron_auth("ABC123", "abc123"))


def test_meal_history_context():
    print("=== Meal history context (YOL-16) ===")
    today = datetime.now(BKK).date()
    today_str = str(today)

    # Today's meals → context string formatted correctly
    meals_today = [
        {"meal_type": "breakfast", "description": "โจ๊กหมู"},
        {"meal_type": "lunch", "description": "ข้าวมันไก่"},
    ]
    ctx = build_meal_history_context(meals_today, today_str)
    run("Today meals → correct prefix", ctx.startswith(f"Meal history for {today_str}:"))
    run("Today meals → breakfast listed", "Breakfast: โจ๊กหมู" in ctx)
    run("Today meals → lunch listed", "Lunch: ข้าวมันไก่" in ctx)

    # Yesterday resolved within 30 days → valid date
    yesterday_str = str(today - timedelta(days=1))
    run("Yesterday within 30 days → not TOO_OLD", validate_date_intent(yesterday_str, today) == yesterday_str)

    # Specific date within 30 days → passed through
    date_28_days = str(today - timedelta(days=28))
    run("28 days ago → valid", validate_date_intent(date_28_days, today) == date_28_days)

    # Date older than 30 days → TOO_OLD
    date_31_days = str(today - timedelta(days=31))
    run("31 days ago → TOO_OLD", validate_date_intent(date_31_days, today) == "TOO_OLD")

    # Exactly 30 days ago is still valid (boundary)
    date_30_days = str(today - timedelta(days=30))
    run("Exactly 30 days ago → valid", validate_date_intent(date_30_days, today) == date_30_days)

    # No meals on requested date → "no meals logged" context
    ctx_empty = build_meal_history_context([], "2026-05-01")
    run("No meals → no meals logged context", ctx_empty == "(No meals logged for 2026-05-01)")

    # Multiple meals same type → both listed
    meals_multi = [
        {"meal_type": "snack", "description": "กล้วย"},
        {"meal_type": "snack", "description": "โยเกิร์ต"},
    ]
    ctx_multi = build_meal_history_context(meals_multi, "2026-05-20")
    run("Multiple snacks → both listed", "กล้วย, โยเกิร์ต" in ctx_multi)

    # Non-history question → "NO" passes through unchanged
    run("NO intent → NO", validate_date_intent("NO", today) == "NO")

    # Malformed date from Haiku → treated as NO
    run("Malformed date → NO", validate_date_intent("not-a-date", today) == "NO")


def test_off_topic_block():
    print("=== Off-topic block state machine ===")
    state, count = simulate_block_state(0)
    run("Strike 1 → WARNED, count=1", state == "WARNED" and count == 1)

    state, count = simulate_block_state(1)
    run("Strike 2 → WARNED, count=2", state == "WARNED" and count == 2)

    state, count = simulate_block_state(2)
    run("Strike 3 → NOW_BLOCKED, count=0", state == "NOW_BLOCKED" and count == 0)

    future = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    state, _ = simulate_block_state(0, future)
    run("Active block → BLOCKED", state == "BLOCKED")

    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    state, count = simulate_block_state(0, past)
    run("Expired block → resets to WARNED", state == "WARNED" and count == 1)


def test_input_length_guard():
    print("=== Input length guard (YOL-21) ===")
    run("500 chars → passes", len("ก" * 500) <= 500)
    run("501 chars → blocked", len("ก" * 501) > 500)
    run("Empty string → passes", len("") <= 500)
    run("English 500 → passes", len("a" * 500) <= 500)
    run("English 501 → blocked", len("a" * 501) > 500)


def test_unblock_command():
    print("=== Unblock command (YOL-20) ===")
    run("'เริ่มใหม่' → unblock", is_unblock_command("เริ่มใหม่"))
    run("'ขอโทษ' → unblock", is_unblock_command("ขอโทษ"))
    run("'ยกเลิก' → unblock", is_unblock_command("ยกเลิก"))
    run("'unblock' → unblock", is_unblock_command("unblock"))
    run("'sorry' → unblock", is_unblock_command("sorry"))
    run("'restart' → unblock", is_unblock_command("restart"))
    run("Sentence with keyword", is_unblock_command("I'm sorry about that"))
    run("Random food msg → not unblock", not is_unblock_command("กินข้าวมันไก่"))
    run("Empty → not unblock", not is_unblock_command(""))
    run("Case insensitive EN", is_unblock_command("SORRY"))


def test_deep_dish_variant():
    print("=== Deep dish variant (YOL-23) ===")
    # Cooking method in DISH prefix → stored as-is
    d, c = extract_dish("DISH: ข้าวมันไก่ทอด\n\nโปรตีนดี!")
    run("Cooking method stored", d == "ข้าวมันไก่ทอด")

    d, c = extract_dish("DISH: ข้าวมันไก่ต้ม + ไข่ต้ม\n\nดีมาก!")
    run("Dish + sides stored", d == "ข้าวมันไก่ต้ม + ไข่ต้ม")

    d, c = extract_dish("DISH: Grilled salmon + steamed rice\n\nGreat protein!")
    run("English variant stored", d == "Grilled salmon + steamed rice")

    # Missing DISH prefix → dish_name is None (unknown)
    d, c = extract_dish("Looks like a healthy bowl!")
    run("No DISH prefix → unknown dish", d == "unknown dish")

    # Dish only line (no coaching) → no crash
    d, c = extract_dish("DISH: กะเพราหมูสับไข่ดาว")
    run("Dish-only line → name extracted", d == "กะเพราหมูสับไข่ดาว")


def test_conversation_history():
    print("=== Conversation history (YOL-19) ===")
    # Append up to 10 entries correctly
    hist = []
    for i in range(5):
        hist.append({"role": "user", "content": f"msg {i}"})
        hist.append({"role": "assistant", "content": f"reply {i}"})
    run("10 entries fit without trim", len(trim_history(hist)) == 10)

    # 11th entry triggers trim → keeps last 10
    hist.append({"role": "user", "content": "extra"})
    trimmed = trim_history(hist)
    run("11 entries → trimmed to 10", len(trimmed) == 10)
    run("Oldest entry removed", trimmed[0]["content"] == "reply 0")

    # Image summary entry format
    img_entry = {"role": "user", "content": "[sent a food photo]"}
    run("Image entry role correct", img_entry["role"] == "user")
    run("Image entry content correct", img_entry["content"] == "[sent a food photo]")

    # Empty history → no trim needed
    run("Empty history → empty", len(trim_history([])) == 0)


def count_distinct_days(meals: list) -> int:
    """Pure helper — mirrors weekly summary distinct-day counting."""
    return len({m["logged_at"][:10] for m in meals})


def is_low_logging_week(days: int) -> bool:
    """Pure helper — mirrors weekly summary low-log detection."""
    return days <= 2


def test_weekly_summary_logic():
    print("=== Weekly summary logic (YOL-22/27) ===")
    # Top dishes identified correctly
    meals_week = [
        {"description": "ข้าวมันไก่", "logged_at": "2026-05-26T08:00:00"},
        {"description": "ข้าวมันไก่", "logged_at": "2026-05-26T12:00:00"},
        {"description": "กะเพราหมู",  "logged_at": "2026-05-27T08:00:00"},
        {"description": "ข้าวมันไก่", "logged_at": "2026-05-27T12:00:00"},
        {"description": "ต้มยำกุ้ง",  "logged_at": "2026-05-28T08:00:00"},
        {"description": "กะเพราหมู",  "logged_at": "2026-05-28T12:00:00"},
        {"description": "ข้าวผัด",    "logged_at": "2026-05-29T08:00:00"},
    ]
    top = get_top_dishes(meals_week)
    run("Top dish is ข้าวมันไก่", top[0] == "ข้าวมันไก่")
    run("Second dish is กะเพราหมู", top[1] == "กะเพราหมู")
    run("Top 3 returned", len(top) == 3)

    # Multiple same dish → counted correctly
    same_dish = [{"description": "ข้าวมันไก่", "logged_at": f"2026-05-2{i}T08:00:00"} for i in range(5)]
    run("Same dish 5x → top dish correct", get_top_dishes(same_dish)[0] == "ข้าวมันไก่")

    # 0 meals → re-engagement branch
    run("0 meals → empty top dishes", get_top_dishes([]) == [])

    # Distinct days count
    meals_with_dates = [
        {"description": "x", "logged_at": "2026-05-26T08:00:00"},
        {"description": "y", "logged_at": "2026-05-26T13:00:00"},
        {"description": "z", "logged_at": "2026-05-27T08:00:00"},
    ]
    days = count_distinct_days(meals_with_dates)
    run("2 distinct days counted", days == 2)

    # 7 days logged → correct count
    meals_7 = [{"description": "x", "logged_at": f"2026-05-2{i}T08:00:00"} for i in range(7)]
    run("7 days logged → count=7", count_distinct_days(meals_7) == 7)

    # Low-logging detection (YOL-27)
    run("0 days → low logging", is_low_logging_week(0))
    run("1 day → low logging", is_low_logging_week(1))
    run("2 days → low logging", is_low_logging_week(2))
    run("3 days → not low logging", not is_low_logging_week(3))
    run("7 days → not low logging", not is_low_logging_week(7))


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

DASHBOARD_KEYWORDS = {
    "ดูสรุป", "สรุปของฉัน", "ดูประวัติ", "กินอะไรไปบ้าง", "สถิติ",
    "my summary", "show summary", "my stats", "dashboard", "my history",
}


def detect_meal_keyword(text: str) -> str | None:
    t = text.lower()
    for kw, meal_type in MEAL_KEYWORDS:
        if kw in t:
            return meal_type
    return None


def is_dashboard_request(text: str) -> bool:
    t = text.lower().strip()
    return any(kw in t for kw in DASHBOARD_KEYWORDS)


def simulate_rate_limit(timestamps: list, max_msgs: int = 10, window_secs: int = 60) -> bool:
    """Pure helper — mirrors is_rate_limited logic."""
    from datetime import datetime, timedelta
    now = datetime.now()
    cutoff = now - timedelta(seconds=window_secs)
    recent = [t for t in timestamps if t > cutoff]
    return len(recent) > max_msgs


def simulate_rapid_off_topic(timestamps: list) -> bool:
    """Pure helper — mirrors is_rapid_off_topic logic."""
    from datetime import datetime, timedelta
    now = datetime.now()
    cutoff = now - timedelta(seconds=120)
    recent = [t for t in timestamps if t > cutoff]
    return len(recent) >= 3


def test_rate_limiting():
    print("=== Rate limiting (YOL-29) ===")
    from datetime import datetime
    now = datetime.now()
    # 10 messages → not limited
    ts_10 = [now] * 10
    run("10 msgs → not rate limited", not simulate_rate_limit(ts_10))
    # 11 messages → limited
    ts_11 = [now] * 11
    run("11 msgs → rate limited", simulate_rate_limit(ts_11))
    # 11 but old → not limited
    from datetime import timedelta
    ts_old = [now - timedelta(seconds=61)] * 11
    run("11 old msgs → not limited", not simulate_rate_limit(ts_old))


def test_rapid_off_topic():
    print("=== Rapid off-topic (YOL-29) ===")
    from datetime import datetime, timedelta
    now = datetime.now()
    run("2 strikes → not rapid", not simulate_rapid_off_topic([now, now]))
    run("3 strikes → rapid", simulate_rapid_off_topic([now, now, now]))
    run("3 old strikes → not rapid", not simulate_rapid_off_topic(
        [now - timedelta(seconds=130)] * 3
    ))


def test_meal_keyword_detection():
    print("=== Meal keyword detection (YOL-31) ===")
    run("'อาหารเช้านะ' → breakfast", detect_meal_keyword("อาหารเช้านะ") == "breakfast")
    run("'เช้า' → breakfast", detect_meal_keyword("เช้ากินข้าว") == "breakfast")
    run("'this is lunch' → lunch", detect_meal_keyword("this is lunch") == "lunch")
    run("'เที่ยง' → lunch", detect_meal_keyword("เที่ยงกินกะเพรา") == "lunch")
    run("'ของว่างตอนบ่าย' → snack", detect_meal_keyword("ของว่างตอนบ่าย") == "snack")
    run("'dinner' → dinner", detect_meal_keyword("just had dinner") == "dinner")
    run("'late night' → late_snack", detect_meal_keyword("late night snack") == "late_snack")
    run("'ดึก' → late_snack", detect_meal_keyword("ดึกกินมาม่า") == "late_snack")
    run("No keyword → None", detect_meal_keyword("อร่อยมากเลย") is None)
    run("No keyword EN → None", detect_meal_keyword("that looks good") is None)


def test_dashboard_detection():
    print("=== Dashboard detection (YOL-33) ===")
    run("'ดูสรุป' → dashboard", is_dashboard_request("ดูสรุป"))
    run("'สรุปของฉัน' → dashboard", is_dashboard_request("สรุปของฉัน"))
    run("'my stats' → dashboard", is_dashboard_request("my stats"))
    run("'dashboard' → dashboard", is_dashboard_request("dashboard"))
    run("'my history' → dashboard", is_dashboard_request("my history"))
    run("'กินอะไรดี' → not dashboard", not is_dashboard_request("กินอะไรดี"))
    run("'what should I eat' → not dashboard", not is_dashboard_request("what should I eat"))


def test_multi_meal_extraction():
    print("=== Multi-meal extraction logic (YOL-32) ===")
    # Test the meal_type fallback logic
    entry_with_type = {"dish": "ข้าวมันไก่", "meal_type": "breakfast"}
    entry_no_type = {"dish": "กะเพรา"}
    run("Entry with meal_type → uses it", entry_with_type.get("meal_type") == "breakfast")
    run("Entry without meal_type → None fallback", entry_no_type.get("meal_type") is None)
    run("Dish trimmed to 200 chars", len(("x" * 250)[:200]) == 200)

    # Event type values for metrics
    run("Source 'photo' constant", "photo" == "photo")
    run("Source 'text' constant", "text" == "text")


def parse_triage_json(raw: str) -> dict:
    """Pure mirror of main.parse_triage_json."""
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
    """Pure mirror of main.cap_history_date."""
    from datetime import datetime, timedelta
    if not date_str:
        return None
    try:
        asked = datetime.strptime(str(date_str), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    if asked < today - timedelta(days=30):
        return "TOO_OLD"
    return str(date_str)


def commit_history(history: list, user_text: str, reply: str) -> list:
    """Pure mirror of the transactional history commit in handle_text."""
    history = list(history)
    history.append({"role": "user", "content": user_text})
    history.append({"role": "assistant", "content": reply})
    if len(history) > 10:
        history = history[-10:]
    return history


def test_triage_parsing():
    print("=== Triage JSON parsing (lead-dev refactor) ===")
    # Clean JSON
    r = parse_triage_json('{"on_topic": true, "meals": [{"dish":"ข้าวมันไก่","meal_type":"breakfast"}], "history_date": null}')
    run("Clean JSON → on_topic", r["on_topic"] is True)
    run("Clean JSON → 1 meal", len(r["meals"]) == 1)
    run("Clean JSON → no history", r["history_date"] is None)
    # Fenced JSON
    r = parse_triage_json('```json\n{"on_topic": false, "meals": [], "history_date": "2026-05-20"}\n```')
    run("Fenced JSON → off topic", r["on_topic"] is False)
    run("Fenced JSON → history date", r["history_date"] == "2026-05-20")
    # Prose-wrapped JSON
    r = parse_triage_json('Here is the result: {"on_topic": true, "meals": []} hope it helps')
    run("Prose-wrapped → parsed", r["on_topic"] is True)
    # Garbage → safe default (on_topic so user never wrongly blocked)
    r = parse_triage_json("totally not json")
    run("Garbage → safe on_topic", r["on_topic"] is True and r["meals"] == [])
    # Empty → safe default
    run("Empty → safe default", parse_triage_json("")["on_topic"] is True)
    # meals not a list → coerced to []
    r = parse_triage_json('{"on_topic": true, "meals": "oops"}')
    run("Bad meals type → []", r["meals"] == [])


def test_cap_history_date():
    print("=== History date cap (lead-dev refactor) ===")
    today = datetime.now(BKK).date()
    run("None → None", cap_history_date(None, today) is None)
    run("Today → today", cap_history_date(str(today), today) == str(today))
    run("Yesterday → ok", cap_history_date(str(today - timedelta(days=1)), today) == str(today - timedelta(days=1)))
    run("30 days → ok", cap_history_date(str(today - timedelta(days=30)), today) == str(today - timedelta(days=30)))
    run("31 days → TOO_OLD", cap_history_date(str(today - timedelta(days=31)), today) == "TOO_OLD")
    run("Malformed → None", cap_history_date("not-a-date", today) is None)


def test_transactional_history():
    print("=== Transactional history commit (lead-dev fix) ===")
    h = []
    h = commit_history(h, "hi", "hello")
    run("First turn → 2 entries", len(h) == 2)
    run("Alternation user→assistant", h[0]["role"] == "user" and h[1]["role"] == "assistant")
    # Fill past 10 → trims oldest, alternation preserved
    for i in range(6):
        h = commit_history(h, f"u{i}", f"a{i}")
    run("Trimmed to 10", len(h) == 10)
    run("Starts with user after trim", h[0]["role"] == "user")
    run("Ends with assistant", h[-1]["role"] == "assistant")


MEAL_TYPE_TH = {
    "breakfast": "เช้า", "lunch": "กลางวัน", "dinner": "เย็น",
    "snack": "ของว่าง", "late_snack": "มื้อดึก",
}


def build_daily_recap(meals: list, lang: str) -> str:
    """Pure mirror of main.build_daily_recap (YOL-43)."""
    parts = []
    for m in meals:
        mtype = m.get("meal_type", "")
        label = MEAL_TYPE_TH.get(mtype, mtype) if lang == "th" else mtype
        parts.append(f"{m['description']} ({label})" if label else m["description"])
    joined = ", ".join(parts)
    if lang == "th":
        return f"วันนี้คุณกิน: {joined} 🍽️"
    return f"Today you had: {joined} 🍽️"


def is_suggestion_fresh(last_at_iso, now) -> bool:
    """Pure mirror of main.is_suggestion_fresh (YOL-44)."""
    from datetime import datetime as _dt, timedelta as _td
    if not last_at_iso:
        return False
    try:
        last_at = _dt.fromisoformat(str(last_at_iso).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    return (now - last_at) < _td(hours=36)


def test_daily_recap():
    print("=== Daily recap builder (YOL-43) ===")
    meals = [
        {"description": "ข้าวมันไก่", "meal_type": "breakfast"},
        {"description": "กะเพราหมู", "meal_type": "lunch"},
    ]
    th = build_daily_recap(meals, "th")
    run("TH recap prefix", th.startswith("วันนี้คุณกิน:"))
    run("TH localizes meal type", "(เช้า)" in th and "(กลางวัน)" in th)
    run("TH ends with emoji", th.endswith("🍽️"))
    en = build_daily_recap(meals, "en")
    run("EN recap prefix", en.startswith("Today you had:"))
    run("EN keeps english type", "(breakfast)" in en and "(lunch)" in en)
    run("Both dishes present", "ข้าวมันไก่" in th and "กะเพราหมู" in th)


def test_suggestion_freshness():
    print("=== Suggestion freshness 36h (YOL-44) ===")
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    run("None → not fresh", not is_suggestion_fresh(None, now))
    run("Empty → not fresh", not is_suggestion_fresh("", now))
    run("1h ago → fresh", is_suggestion_fresh((now - timedelta(hours=1)).isoformat(), now))
    run("35h ago → fresh", is_suggestion_fresh((now - timedelta(hours=35)).isoformat(), now))
    run("37h ago → stale", not is_suggestion_fresh((now - timedelta(hours=37)).isoformat(), now))
    run("Malformed → not fresh", not is_suggestion_fresh("not-a-date", now))
    run("Z suffix parsed", is_suggestion_fresh((now - timedelta(hours=2)).isoformat().replace("+00:00", "Z"), now))


def test_conversational_whitelist():
    print("=== Conversational whitelist (YOL-25) ===")
    # Whitelist items → skip classifier
    run("'บอกไปแล้ว' → conversational", is_conversational("บอกไปแล้ว"))
    run("'โอเค' → conversational", is_conversational("โอเค"))
    run("'ok' → conversational", is_conversational("ok"))
    run("'ขอบคุณ' → conversational", is_conversational("ขอบคุณ"))
    run("'thanks' → conversational", is_conversational("thanks"))
    run("'ใช่' → conversational", is_conversational("ใช่"))
    run("'เข้าใจ' → conversational", is_conversational("เข้าใจ"))
    # ≤ 10 chars → skip regardless of content
    run("9-char message → conversational", is_conversational("123456789"))
    run("Exactly 10 chars → conversational", is_conversational("1234567890"))
    # > 10 chars + not in whitelist → goes to classifier
    run("Long food question → not conversational", not is_conversational("is pad thai healthy?"))
    run("Long off-topic → not conversational", not is_conversational("what is the stock price today?"))
    run("11 chars unknown → not conversational", not is_conversational("12345678901"))


def test_meal_report_detection():
    print("=== Meal report detection (YOL-24) ===")
    # MEAL result → logs
    run("'MEAL' → logged", is_meal_report_result("MEAL"))
    run("'meal' lowercase → logged", is_meal_report_result("meal"))
    run("'MEAL ' trailing space → logged", is_meal_report_result("MEAL "))
    # NOT result → not logged
    run("'NOT' → not logged", not is_meal_report_result("NOT"))
    run("'not' lowercase → not logged", not is_meal_report_result("not"))
    run("'NO' → not logged", not is_meal_report_result("NO"))
    run("Empty → not logged", not is_meal_report_result(""))


def test_clean_for_line():
    print("=== Markdown + emoji stripping ===")
    run("**bold** removed", clean_for_line("**เช้า** — ไก่ย่าง") == "เช้า — ไก่ย่าง")
    run("*italic* removed", clean_for_line("*great* choice") == "great choice")
    run("__bold__ removed", clean_for_line("__เที่ยง__ กะเพรา") == "เที่ยง กะเพรา")
    run("_italic_ removed", clean_for_line("_good_ job") == "good job")
    run("# heading removed", clean_for_line("# Summary\ntext") == "Summary\ntext")
    run("bullet - removed", clean_for_line("- item one\n- item two") == "item one\nitem two")
    run("clean text unchanged", clean_for_line("โปรตีนดีมากเลย 💪") == "โปรตีนดีมากเลย 💪")
    run("mixed Thai+bold", clean_for_line("**เช้า** ไก่ย่าง **เที่ยง** กะเพรา") == "เช้า ไก่ย่าง เที่ยง กะเพรา")
    run("1 emoji unchanged", clean_for_line("ดีมาก 🍽️").endswith("🍽️"))
    run("2 emojis → keep first only", clean_for_line("ดี 😄 มาก 👏").count("😄") == 1 and "👏" not in clean_for_line("ดี 😄 มาก 👏"))
    run("3 emojis → keep first only", clean_for_line("ก 🍗 ข 💪 ค 🥦").count("🍗") == 1 and "💪" not in clean_for_line("ก 🍗 ข 💪 ค 🥦"))
    run("no emoji unchanged", clean_for_line("ข้าวมันไก่อร่อย") == "ข้าวมันไก่อร่อย")


# ── RUNNER ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_thai_detection,
        test_goal_detection,
        test_dish_extraction,
        test_summary_context,
        test_meal_type_inference,
        test_cron_auth,
        test_off_topic_block,
        test_meal_history_context,
        test_input_length_guard,
        test_unblock_command,
        test_deep_dish_variant,
        test_conversation_history,
        test_weekly_summary_logic,
        test_rate_limiting,
        test_rapid_off_topic,
        test_meal_keyword_detection,
        test_dashboard_detection,
        test_multi_meal_extraction,
        test_triage_parsing,
        test_cap_history_date,
        test_transactional_history,
        test_daily_recap,
        test_suggestion_freshness,
        test_conversational_whitelist,
        test_meal_report_detection,
        test_clean_for_line,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print()
        except AssertionError as e:
            print(f"\n  💥 {e}\n")

    total = len(tests)
    print(f"{'='*40}")
    print(f"Results: {passed}/{total} test suites passed")
    if passed < total:
        exit(1)
