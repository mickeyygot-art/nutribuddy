import os
from datetime import datetime, timezone, timedelta
from supabase import create_client, Client
import pytz
from typing import Optional

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


def log_meal(user_id: str, description: str):
    supabase.table("meals").insert({
        "user_id": user_id,
        "description": description[:200],
        "meal_type": _meal_type_from_time(),
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
