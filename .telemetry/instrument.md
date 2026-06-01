# Instrumentation Guide

## Target: PostHog Python SDK

Generated from tracking-plan.yaml v1 on 2026-05-31.

Stack: Python 3.12 / FastAPI / Railway. All tracking calls are server-side only — there is no browser client. LINE is the user-facing interface.

---

## SDK Setup

### Dependencies

```bash
pip install posthog
```

Add to `requirements.txt`:
```
posthog
```

### Initialization

The `posthog` Python package exposes a module-level client — configure it once at startup and call it anywhere.

```python
# tracking.py — configure at import time
import posthog

posthog.api_key = os.environ["POSTHOG_API_KEY"]
posthog.host = "https://us.i.posthog.com"   # EU: "https://eu.i.posthog.com"
posthog.flush_at = 20       # Send in batches of 20
posthog.flush_interval = 10  # Or every 10 seconds, whichever comes first
```

### Environment Variables

| Variable | Purpose | Required |
|----------|---------|----------|
| `POSTHOG_API_KEY` | PostHog project API key | Yes |
| `POSTHOG_HOST` | Cloud endpoint (default: us.i.posthog.com) | No |
| `POSTHOG_DISABLED` | Set to `"true"` in dev to suppress all calls | No |

---

## Identity

### identify()

**Syntax (Python):**
```python
posthog.identify(distinct_id, properties=None)
```

`distinct_id` is the LINE user ID (`Uxxxxxxx...`). No hashing needed — it contains no PII.

`properties` maps directly to PostHog person properties (`$set`). For set-once properties (like `created_at`), use a `capture()` call with `$set_once` instead.

**User Traits:**

| Trait | Type | PII | Notes |
|-------|------|-----|-------|
| `goal` | string enum | No | Updated on every goal change |
| `language` | string enum | No | `th` or `en`; updated when user switches |
| `last_active_at` | ISO 8601 string | No | Updated on every incoming message |
| `meals_logged_count` | integer | No | Incremented on every `meal.logged` |

`created_at` is set once via `$set_once` in the `user.joined` capture call — see Events section.

**When to Call:**
- After `user.joined` — set initial traits (`goal`, `language`, `last_active_at`)
- After `goal.set` — update `goal` trait
- After language detection — update `language` trait if changed
- After every successful `meal.logged` — increment `meals_logged_count`
- On every incoming webhook message — update `last_active_at`

**Template:**
```python
# After onboarding (user.joined) — set initial person properties
posthog.identify(
    line_user_id,
    properties={
        "goal": "no_goal",
        "language": "th",
        "last_active_at": datetime.now(BKK).isoformat(),
        "meals_logged_count": 0,
    }
)
```

### group()

**Not applicable.** NutriBuddy is a B2C single-player product with no accounts, workspaces, or organizations. There is no group hierarchy. No `group_identify()` calls are needed.

---

## Events

### track() / capture()

**Syntax (Python):**
```python
posthog.capture(distinct_id, event, properties=None)
```

`distinct_id` is always the LINE user ID. `event` is the event name from the tracking plan. `properties` is a dict of event-specific properties.

**No SDK constraints** for NutriBuddy's use case. PostHog Python stores full event properties. No encoding tricks needed.

**Template — lifecycle event:**
```python
# user.joined
posthog.capture(
    line_user_id,
    "user.joined",
    properties={
        "$set_once": {"created_at": datetime.now(BKK).isoformat()}
    }
)
```

**Template — primary value action:**
```python
# meal.logged
posthog.capture(
    line_user_id,
    "meal.logged",
    properties={
        "source": "photo",                 # or "text"
        "meal_type": "lunch",              # or None if unknown
        "dish_identified": True,           # False = unknown dish
        "is_first_meal": False,
        "$set": {"meals_logged_count": new_count},   # Update trait inline
    }
)
```

### Group-Level Attribution

Not applicable — no group hierarchy. All events are user-level only.

---

## Complete Tracking Module

Drop `tracking.py` into the `nutribuddy/` directory alongside `main.py`.

```python
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
```

---

## Architecture

### Client vs Server

All calls are server-side. NutriBuddy has no browser client — LINE is the interface. Every `posthog.capture()` and `posthog.identify()` call is made from `main.py` (webhook handlers) or the scheduler functions.

### Batching

The posthog-python SDK buffers events in memory and flushes:
- When the buffer reaches `flush_at` events (20), or
- Every `flush_interval` seconds (10)

For a low-volume beta bot this is fine. Events may lag up to 10 seconds before appearing in PostHog.

### Shutdown / Flush

Add `posthog.shutdown()` to FastAPI's lifespan handler so buffered events are flushed before Railway stops the container:

```python
# In main.py — add lifespan to FastAPI app
from contextlib import asynccontextmanager
import tracking

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    tracking.shutdown()

app = FastAPI(lifespan=lifespan)
```

### Error Handling

The PostHog Python SDK swallows network errors internally and logs them — it will not raise exceptions into your webhook handlers. You do not need try/except around tracking calls. Events that fail to deliver are dropped (no retry queue at this scale). If delivery reliability becomes critical, upgrade to a managed queue.

---

## Wiring into main.py

Below are the exact call sites in `main.py` where each tracking function should be added.

### handle_follow (user.joined)

```python
@handler.add(FollowEvent)
def handle_follow(event):
    user = get_or_create_user(event.source.user_id)
    _reply(event.reply_token, ONBOARDING_MSG_1)
    time.sleep(1)
    _push(event.source.user_id, ONBOARDING_MSG_2)
    # ── Analytics ──
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
```

### handle_text — goal change

```python
# After update_user_goal() succeeds:
try:
    is_initial = user["goal"] == "no_goal"   # rough proxy — refine if needed
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
```

### handle_text — meal logged via text

```python
# After log_meal() succeeds for each entry in triage["meals"]:
try:
    from database import get_meal_count  # add this helper (see note below)
    new_count = get_meal_count(user_id)
    tracking.track_meal_logged(
        line_user_id,
        source="text",
        meal_type=entry.get("meal_type"),
        dish_identified=bool(dish),
        is_first_meal=(new_count == 1),
        new_meals_count=new_count,
    )
except Exception as e:
    print(f"Analytics error (meal.logged/text): {e}")
```

### handle_image — meal logged via photo

```python
# After log_meal() succeeds:
try:
    from database import get_meal_count
    new_count = get_meal_count(user_id)
    tracking.track_meal_logged(
        line_user_id,
        source="photo",
        meal_type=None,   # photo path derives meal_type from clock, not stored before log
        dish_identified=bool(dish_name),
        is_first_meal=(new_count == 1),
        new_meals_count=new_count,
    )
except Exception as e:
    print(f"Analytics error (meal.logged/photo): {e}")
```

### handle_text — dashboard

```python
# After the Claude dashboard reply:
try:
    tracking.track_dashboard_viewed(
        line_user_id,
        meals_in_period=len(meals_7),
        had_meals=bool(meals_7),
    )
except Exception as e:
    print(f"Analytics error (dashboard.viewed): {e}")
```

### handle_text — block triggers

```python
# Where block_triggered is logged to event_log, also call:
tracking.track_user_blocked(line_user_id, block_reason="strike_limit")
# or for rapid-fire path:
tracking.track_user_blocked(line_user_id, block_reason="rapid_fire")

# After clear_block():
tracking.track_user_unblocked(line_user_id)
```

### send_daily_summaries / send_weekly_summaries

```python
# After _push() succeeds:
try:
    tracking.track_summary_sent(
        user["line_user_id"],
        summary_type="daily",           # or "weekly"
        meals_in_period=len(meals),
        is_nudge=not bool(meals),
        days_logged=days_with_meals,    # weekly only
    )
except Exception as e:
    print(f"Analytics error (summary.sent): {e}")
```

### Required DB helper

Add to `database.py`:

```python
def get_meal_count(user_id: str) -> int:
    """Return total number of meals logged by this user."""
    result = supabase.table("meals").select("id", count="exact").eq("user_id", user_id).execute()
    return result.count or 0
```

---

## Verification

### Confirming Delivery

1. Open your PostHog project → **Activity → Live Events** — events appear in real time as they come in.
2. Search by distinct_id (your LINE user ID) to confirm your test events arrive with the right properties.
3. Check **Persons** tab — your user should appear with all traits after the first `identify()` call.

### Expected Latency

Events are buffered in-process and flushed every 10 seconds (or at 20 events). In practice: events arrive in PostHog within 10–30 seconds of the webhook call.

### Success vs Failure

The SDK logs delivery errors to stdout but does not raise. Watch Railway logs for lines like:
```
error uploading: 400 Bad Request
```
A 400 typically means a malformed payload (wrong property type). A 401 means a bad API key. 200 = success (no log line).

### Development Testing

**Option 1 — Disable tracking locally:**
```bash
POSTHOG_DISABLED=true uvicorn main:app --reload
```

**Option 2 — Separate PostHog project:**
Create a "NutriBuddy Dev" project in PostHog. Set `POSTHOG_API_KEY` to the dev project key in your local `.env`. Production and dev data are completely isolated.

**Option 3 — Internal user exclusion:**
Add your personal LINE user ID to `POSTHOG_INTERNAL_IDS` in Railway env vars. All your test interactions will be silently skipped.

---

## Rollout Strategy

**Phase 1 — Lifecycle events (do this first)**

Add `tracking.py` to the repo. Wire `user.joined` and `goal.set`. Deploy to Railway. Test by adding yourself to the bot (or a test LINE account that is NOT in `INTERNAL_LINE_USER_IDS`). Confirm in PostHog Live Events.

**Phase 2 — Core value events**

Wire `meal.logged` in both `handle_text` and `handle_image`. This is the retention signal — verify `is_first_meal` fires exactly once per user. Check PostHog Persons to confirm `meals_logged_count` increments correctly.

**Phase 3 — Engagement events**

Wire `summary.sent` in both cron functions, `dashboard.viewed`, `user.blocked`, and `user.unblocked`. These are lower-frequency — verify by triggering each manually (send off-topic messages, request the dashboard, wait for the daily summary cron).

**Phase 4 — Monitoring (first week)**

Watch Railway logs for `error uploading` lines. In PostHog: check event volume matches expected user count × sessions. If `meal.logged` count looks low, check the `get_meal_count` helper is being called correctly.

---

## SDK-Specific Constraints

- **No browser SDK needed.** NutriBuddy is server-only. Do not use `posthog-js`.
- **Group analytics not used.** NutriBuddy has no B2B groups. The PostHog group analytics paid add-on is not needed.
- **Events are batched, not real-time.** 10-second flush interval means Railway could lose up to 10 seconds of events if the container is killed without a SIGTERM. The lifespan shutdown handler mitigates this.
- **`$set_once` for `created_at`.** PostHog's `$set_once` prevents a re-follow event from overwriting the original `created_at`. This is handled in `track_user_joined()` via the `properties` dict.
- **`$set` inline with events.** PostHog Python supports `$set` and `$set_once` as special keys inside `capture()` properties. This lets you update person traits atomically with the event — no separate `identify()` call needed for trait updates that always accompany an event (like updating `meals_logged_count` on `meal.logged`).

## Coverage Gaps

None for this stack. The posthog-python SDK covers all required patterns (identify, capture, $set, $set_once, shutdown). No unsupported environments.
