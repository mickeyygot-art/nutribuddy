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
    GOAL_MAP = {
        "1": "lose_weight", "ลดน้ำหนัก": "lose_weight", "lose weight": "lose_weight",
        "2": "eat_clean",   "กินสะอาด": "eat_clean",   "eat clean": "eat_clean",
        "3": "build_muscle","เพิ่มกล้าม": "build_muscle","build muscle": "build_muscle",
        "4": "no_goal",     "ยังไม่มี": "no_goal",     "no goal": "no_goal",
    }
    t = text.lower().strip()
    for key, goal in GOAL_MAP.items():
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
