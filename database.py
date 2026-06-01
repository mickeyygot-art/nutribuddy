import os
from datetime import datetime, timezone, timedelta
from supabase import create_client, Client
import pytz

supabase: Client = create_client(
    os.environ["SUPABASE_URL"].strip(),
    os.environ["SUPABASE_KEY"].strip(),
)

BKK = pytz.timezone("Asia/Bangkok")


# ── USERS ─────────────────────────────────────────────────────────────────────

def get_or_create_user(line_user_id: str) -> dict:
    result = supabase.table("users").select("*").eq("line_user_id", line_user_id).execute()
    if result.data:
        return result.data[0]
    new = supabase.table("users").insert({
        "line_user_id": line_user_id,
        "goal": "no_goal",
        "language": "th",
    }).execute()
    return new.data[0]


def update_user_goal(line_user_id: str, goal: str):
    supabase.table("users").update({"goal": goal}).eq("line_user_id", line_user_id).execute()


def update_user_language(line_user_id: str, language: str):
    supabase.table("users").update({"language": language}).eq("line_user_id", line_user_id).execute()


def get_all_users() -> list:
    return supabase.table("users").select("*").execute().data


def update_last_active(user_id: str):
    """YOL-35: Stamp last_active_at on every incoming message."""
    supabase.table("users").update(
        {"last_active_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", user_id).execute()


def log_event(user_id: str, event_type: str):
    """YOL-35: Append an event row for engagement metrics."""
    supabase.table("event_log").insert(
        {"user_id": user_id, "event_type": event_type}
    ).execute()


def update_user_suggestion(user_id: str, suggestion: str):
    """YOL-43: Store the latest coaching move + timestamp for follow-through detection."""
    supabase.table("users").update({
        "last_suggestion": suggestion,
        "last_suggestion_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", user_id).execute()


def clear_user_suggestion(user_id: str):
    """YOL-44: Clear the stored suggestion so a follow-through only celebrates once."""
    supabase.table("users").update(
        {"last_suggestion": None}
    ).eq("id", user_id).execute()


def get_lapsed_users() -> list:
    """YOL-51: Users idle 3–5 days who haven't been nudged this lapse window.

    Eligible = last_active_at in [now-5d, now-3d] AND (never nudged OR the last
    nudge predates their last activity, i.e. it was a previous lapse)."""
    now = datetime.now(timezone.utc)
    lo = (now - timedelta(days=5)).isoformat()
    hi = (now - timedelta(days=3)).isoformat()
    rows = (
        supabase.table("users")
        .select("*")
        .gte("last_active_at", lo)
        .lte("last_active_at", hi)
        .execute()
        .data
    )
    eligible = []
    for u in rows:
        wb = u.get("last_winback_at")
        if not wb or wb < u.get("last_active_at", ""):
            eligible.append(u)
    return eligible


def mark_winback_sent(user_id: str):
    """YOL-51: Record that a win-back nudge was sent (one per lapse window)."""
    supabase.table("users").update(
        {"last_winback_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", user_id).execute()


def get_meal_count(user_id: str) -> int:
    """YOL-53: Total meals ever logged by this user."""
    result = supabase.table("meals").select("id", count="exact").eq("user_id", user_id).execute()
    return result.count or 0


def get_meal_dates(user_id: str, days: int = 45) -> list:
    """YOL-52: logged_at values for the last `days` days (for streak calculation)."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = (
        supabase.table("meals")
        .select("logged_at")
        .eq("user_id", user_id)
        .gte("logged_at", since)
        .execute()
        .data
    )
    return [r["logged_at"] for r in rows]


def get_month_meals(user_id: str, days: int = 30) -> list:
    """YOL-68: all meals in the last `days` days (for the Wrapped recap)."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return (
        supabase.table("meals")
        .select("description, meal_type, logged_at")
        .eq("user_id", user_id)
        .gte("logged_at", since)
        .order("logged_at")
        .execute()
        .data
    )


def get_recent_meals(user_id: str, limit: int = 20) -> list:
    """YOL-59: most recent meals (newest first) for the profile-learning pass."""
    return (
        supabase.table("meals")
        .select("description, meal_type, logged_at")
        .eq("user_id", user_id)
        .order("logged_at", desc=True)
        .limit(limit)
        .execute()
        .data
    )


def update_user_profile(user_id: str, profile: str):
    """YOL-59: store the learned coaching profile + refresh timestamp."""
    supabase.table("users").update({
        "coaching_profile": profile,
        "profile_updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", user_id).execute()


def set_checkin_pending(user_id: str):
    """YOL-60: mark that we just asked the outcome check-in (awaiting a reply)."""
    supabase.table("users").update(
        {"checkin_pending_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", user_id).execute()


def clear_checkin_pending(user_id: str):
    """YOL-60: stop intercepting replies as a check-in answer."""
    supabase.table("users").update(
        {"checkin_pending_at": None}
    ).eq("id", user_id).execute()


def insert_checkin(user_id: str, energy, goal_progress, weight):
    """YOL-60: store one self-reported wellbeing check-in."""
    supabase.table("checkins").insert({
        "user_id": user_id,
        "energy": energy,
        "goal_progress": goal_progress,
        "weight": weight,
    }).execute()


def get_recent_checkins(user_id: str, limit: int = 6) -> list:
    """YOL-60: recent check-ins (newest first) for trend context."""
    return (
        supabase.table("checkins")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
        .data
    )


# ── MEALS ─────────────────────────────────────────────────────────────────────

def _meal_type_from_time() -> str:
    hour = datetime.now(BKK).hour
    if 6 <= hour <= 10:
        return "breakfast"
    elif 11 <= hour <= 14:
        return "lunch"
    elif 15 <= hour <= 17:
        return "snack"
    elif 18 <= hour <= 21:
        return "dinner"
    return "late_snack"


def log_meal(user_id: str, description: str, source: str = "photo", meal_type: str | None = None):
    supabase.table("meals").insert({
        "user_id": user_id,
        "description": description[:200],
        "meal_type": meal_type or _meal_type_from_time(),
        "source": source,
    }).execute()


def get_meals_by_date_range(user_id: str, from_date: datetime, to_date: datetime) -> list:
    # PLAN:
    # 1. Hard cap: clamp from_date to at most 30 days ago (Bangkok midnight)
    # 2. Convert both bounds to UTC ISO strings for Supabase query
    # 3. Return meals ordered by logged_at
    thirty_days_ago = (
        datetime.now(BKK)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        - timedelta(days=30)
    )
    if from_date.tzinfo is None:
        from_date = BKK.localize(from_date)
    if to_date.tzinfo is None:
        to_date = BKK.localize(to_date)
    if from_date < thirty_days_ago:
        from_date = thirty_days_ago

    from_utc = from_date.astimezone(timezone.utc).isoformat()
    to_utc = to_date.astimezone(timezone.utc).isoformat()

    return (
        supabase.table("meals")
        .select("*")
        .eq("user_id", user_id)
        .gte("logged_at", from_utc)
        .lt("logged_at", to_utc)
        .order("logged_at")
        .execute()
        .data
    )


def update_last_meal_type(user_id: str, meal_type: str):
    """YOL-31: Update most recent meal's meal_type IF logged within last 60 seconds."""
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
    result = (
        supabase.table("meals")
        .select("id")
        .eq("user_id", user_id)
        .gte("logged_at", cutoff)
        .order("logged_at", desc=True)
        .limit(1)
        .execute()
    )
    if result.data:
        supabase.table("meals").update({"meal_type": meal_type}).eq("id", result.data[0]["id"]).execute()


def get_today_meals(user_id: str) -> list:
    today_start = (
        datetime.now(BKK)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .astimezone(timezone.utc)
        .isoformat()
    )
    return (
        supabase.table("meals")
        .select("*")
        .eq("user_id", user_id)
        .gte("logged_at", today_start)
        .order("logged_at")
        .execute()
        .data
    )


# ── OFF-TOPIC GUARD ───────────────────────────────────────────────────────────

def is_blocked(user_id: str) -> bool:
    result = supabase.table("off_topic_log").select("*").eq("user_id", user_id).execute()
    if not result.data:
        return False
    record = result.data[0]
    if record.get("blocked_until"):
        blocked_until = datetime.fromisoformat(record["blocked_until"].replace("Z", "+00:00"))
        if datetime.now(timezone.utc) < blocked_until:
            return True
        # Block expired — reset
        supabase.table("off_topic_log").update(
            {"count": 0, "blocked_until": None}
        ).eq("user_id", user_id).execute()
    return False


def clear_block(user_id: str):
    """Reset off-topic count and remove block — called by unblock command."""
    supabase.table("off_topic_log").update(
        {"count": 0, "blocked_until": None}
    ).eq("user_id", user_id).execute()


def force_block(user_id: str):
    """Immediately set 6hr block regardless of current count — used by rapid-fire detection."""
    blocked_until = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
    result = supabase.table("off_topic_log").select("id").eq("user_id", user_id).execute()
    if result.data:
        supabase.table("off_topic_log").update(
            {"count": 0, "blocked_until": blocked_until}
        ).eq("user_id", user_id).execute()
    else:
        supabase.table("off_topic_log").insert(
            {"user_id": user_id, "count": 0, "blocked_until": blocked_until}
        ).execute()


def increment_off_topic(user_id: str) -> int:
    """Returns new count. If count reaches 3, sets 6hr block and resets count."""
    result = supabase.table("off_topic_log").select("*").eq("user_id", user_id).execute()
    if not result.data:
        supabase.table("off_topic_log").insert({"user_id": user_id, "count": 1}).execute()
        return 1

    count = result.data[0]["count"] + 1
    update = {"count": count, "updated_at": datetime.now(timezone.utc).isoformat()}

    if count >= 3:
        update["blocked_until"] = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
        update["count"] = 0

    supabase.table("off_topic_log").update(update).eq("user_id", user_id).execute()
    return count


# ── WEEKLY MEALS ──────────────────────────────────────────────────────────────

def bkk_date_key(iso: str) -> str:
    """Convert a UTC ISO timestamp to its Bangkok calendar date 'YYYY-MM-DD'."""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(BKK).date().isoformat()


def compute_streak(date_keys: set, today) -> int:
    """YOL-52: consecutive days (BKK) with >=1 meal, allowing ~1 grace day per 7.
    An unlogged 'today' is treated as pending (doesn't break the streak)."""
    day = today
    if day.isoformat() not in date_keys:
        day = today - timedelta(days=1)
    streak, grace = 0, 0
    while True:
        if day.isoformat() in date_keys:
            streak += 1
            day = day - timedelta(days=1)
        elif streak > 0 and grace < (streak // 7 + 1):
            grace += 1
            day = day - timedelta(days=1)
        else:
            break
    return streak


def get_liff_summary(line_user_id: str):
    """YOL-50/52: Last-7-day summary + current streak for the LIFF dashboard. None if not found."""
    res = supabase.table("users").select("id").eq("line_user_id", line_user_id).execute()
    if not res.data:
        return None
    user_id = res.data[0]["id"]
    meals = get_week_meals(user_id)  # ascending by logged_at
    days_logged = len({m["logged_at"][:10] for m in meals})
    streak = compute_streak(
        {bkk_date_key(x) for x in get_meal_dates(user_id, 45)},
        datetime.now(BKK).date(),
    )
    return {
        "days_logged": days_logged,
        "streak": streak,
        "meals": [{"dish": m["description"], "logged_at": m["logged_at"]} for m in meals],
        "top_dishes": get_week_top_dishes(user_id, 3),
    }


def get_week_top_dishes(user_id: str, n: int = 3) -> list:
    """YOL-49: Return the top-n dish names by frequency over the past 7 days."""
    from collections import Counter
    meals = get_week_meals(user_id)
    counts = Counter(m["description"] for m in meals)
    return [dish for dish, _ in counts.most_common(n)]


def get_week_meals(user_id: str) -> list:
    # PLAN:
    # 1. Compute Bangkok midnight 7 days ago → convert to UTC
    # 2. Fetch all meals from that point to now, ordered by logged_at
    week_start = (
        datetime.now(BKK)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        - timedelta(days=7)
    ).astimezone(timezone.utc).isoformat()

    return (
        supabase.table("meals")
        .select("*")
        .eq("user_id", user_id)
        .gte("logged_at", week_start)
        .order("logged_at")
        .execute()
        .data
    )
