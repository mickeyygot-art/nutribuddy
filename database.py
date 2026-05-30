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
