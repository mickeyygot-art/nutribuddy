# tracking.py
"""
NutriBuddy PostHog analytics module.
All analytics calls go through this module — never call posthog directly from main.py.
"""
import os
from datetime import datetime
import pytz
import posthog

BKK = pytz.timezone("Asia/Bangkok")

# ── Configuration ──────────────────────────────────────────────────────────────

posthog.api_key = os.environ.get("POSTHOG_API_KEY", "")
posthog.host = os.environ.get("POSTHOG_HOST", "https://us.i.posthog.com")
posthog.flush_at = 20
posthog.flush_interval = 10

_DISABLED = os.environ.get("POSTHOG_DISABLED", "").lower() == "true"

# Known internal/test LINE user IDs — excluded from all analytics.
# Add your own LINE user ID here during development.
INTERNAL_LINE_USER_IDS: set[str] = set(
    filter(None, os.environ.get("POSTHOG_INTERNAL_IDS", "").split(","))
)


def _guard(line_user_id: str) -> bool:
    """Return True if this user should be excluded from tracking."""
    return _DISABLED or not posthog.api_key or line_user_id in INTERNAL_LINE_USER_IDS


# ── Identity ───────────────────────────────────────────────────────────────────

def identify_user(
    line_user_id: str,
    *,
    goal: str,
    language: str,
    meals_logged_count: int = 0,
    last_active_at: datetime | None = None,
) -> None:
    """Set or update person properties for a user. Call whenever a trait changes."""
    if _guard(line_user_id):
        return
    posthog.identify(
        line_user_id,
        properties={
            "goal": goal,
            "language": language,
            "meals_logged_count": meals_logged_count,
            "last_active_at": (last_active_at or datetime.now(BKK)).isoformat(),
        },
    )


# ── Events ─────────────────────────────────────────────────────────────────────

def track_user_joined(line_user_id: str) -> None:
    """
    LINE FollowEvent — user added the bot.
    Sets created_at once via $set_once so a re-follow doesn't overwrite it.
    """
    if _guard(line_user_id):
        return
    now = datetime.now(BKK).isoformat()
    posthog.capture(
        line_user_id,
        "user.joined",
        properties={
            "$set_once": {"created_at": now},
            "$set": {"last_active_at": now},
        },
    )


def track_goal_set(
    line_user_id: str,
    *,
    goal: str,
    previous_goal: str | None,
    is_initial_set: bool,
    set_method: str,
) -> None:
    """User sets or changes their health goal."""
    if _guard(line_user_id):
        return
    posthog.capture(
        line_user_id,
        "goal.set",
        properties={
            "goal": goal,
            "previous_goal": previous_goal,
            "is_initial_set": is_initial_set,
            "set_method": set_method,   # "digit_reply" | "phrase"
            "$set": {"goal": goal},     # Keep person trait current
        },
    )


def track_meal_logged(
    line_user_id: str,
    *,
    source: str,
    meal_type: str | None,
    dish_identified: bool,
    is_first_meal: bool,
    new_meals_count: int,
) -> None:
    """
    Meal successfully recorded in DB.
    This is the primary value action — the retention and engagement metric.
    """
    if _guard(line_user_id):
        return
    posthog.capture(
        line_user_id,
        "meal.logged",
        properties={
            "source": source,               # "photo" | "text"
            "meal_type": meal_type,         # "breakfast"|"lunch"|"dinner"|"snack"|"late_snack"|None
            "dish_identified": dish_identified,
            "is_first_meal": is_first_meal,
            "$set": {
                "meals_logged_count": new_meals_count,
                "last_active_at": datetime.now(BKK).isoformat(),
            },
        },
    )


def track_summary_sent(
    line_user_id: str,
    *,
    summary_type: str,
    meals_in_period: int,
    is_nudge: bool,
    days_logged: int | None = None,
) -> None:
    """Bot pushes a daily or weekly summary to the user."""
    if _guard(line_user_id):
        return
    props: dict = {
        "summary_type": summary_type,   # "daily" | "weekly"
        "meals_in_period": meals_in_period,
        "is_nudge": is_nudge,
    }
    if days_logged is not None:
        props["days_logged"] = days_logged
    posthog.capture(line_user_id, "summary.sent", properties=props)


def track_dashboard_viewed(
    line_user_id: str,
    *,
    meals_in_period: int,
    had_meals: bool,
) -> None:
    """User requests their in-chat 7-day summary."""
    if _guard(line_user_id):
        return
    posthog.capture(
        line_user_id,
        "dashboard.viewed",
        properties={
            "meals_in_period": meals_in_period,
            "had_meals": had_meals,
        },
    )


def track_user_blocked(line_user_id: str, *, block_reason: str) -> None:
    """User receives 6-hour block after off-topic strikes."""
    if _guard(line_user_id):
        return
    posthog.capture(
        line_user_id,
        "user.blocked",
        properties={"block_reason": block_reason},  # "strike_limit" | "rapid_fire"
    )


def track_user_unblocked(line_user_id: str) -> None:
    """User sends unblock keyword and regains access."""
    if _guard(line_user_id):
        return
    posthog.capture(line_user_id, "user.unblocked")


# ── Lifecycle ──────────────────────────────────────────────────────────────────

def update_last_active(line_user_id: str) -> None:
    """
    Update last_active_at trait on every incoming webhook message.
    Lightweight identify() call — does not send an event.
    """
    if _guard(line_user_id):
        return
    posthog.identify(
        line_user_id,
        properties={"last_active_at": datetime.now(BKK).isoformat()},
    )


def shutdown() -> None:
    """Flush remaining events and close the PostHog client. Call on SIGTERM."""
    if not _DISABLED and posthog.api_key:
        posthog.shutdown()
