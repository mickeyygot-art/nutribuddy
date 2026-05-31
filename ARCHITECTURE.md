# NutriBuddy — System Architecture

**Owner:** Architect Agent
**Last updated:** May 2026
**Status:** MVP / Beta

This document is maintained by the architect agent. Update it whenever infrastructure, data flow, or system boundaries change.

---

## System Overview

NutriBuddy is a LINE-native AI health coaching bot. Users interact entirely through LINE Messaging API — no separate app download required. The system processes food photos and text messages, provides nutritional coaching, logs meals, and sends proactive daily and weekly summaries.

---

## System Diagram

```
┌─────────────────────────────────────────────────────────┐
│                        USER                             │
│                    (LINE App)                           │
└────────────────────────┬────────────────────────────────┘
                         │ HTTPS
                         ▼
┌─────────────────────────────────────────────────────────┐
│                LINE Messaging API                        │
│         (Message routing, push notifications)           │
│                    LINE CDN                             │
│            (Image content delivery)                     │
└────────────────────────┬────────────────────────────────┘
                         │ POST /webhook
                         ▼
┌─────────────────────────────────────────────────────────┐
│              Railway (Single Service)                    │
│                                                         │
│   FastAPI + Uvicorn (Python 3.12)                       │
│   ├── POST /webhook    (LINE event dispatcher)          │
│   ├── GET  /health     (health check)                   │
│   ├── POST /cron/daily-summary   (manual trigger)       │
│   ├── POST /cron/weekly-summary  (manual trigger)       │
│   └── APScheduler (background, in-process)             │
│       ├── Daily  13:00 UTC → send_daily_summaries()    │
│       └── Weekly Mon 01:00 UTC → send_weekly_summaries()│
│                                                         │
│   In-memory state:                                      │
│   └── conversation_history  {line_user_id: [msg, ...]} │
│       (last 10 messages per user, resets on deploy)    │
└──────────┬──────────────────────┬───────────────────────┘
           │                      │
           ▼                      ▼
┌──────────────────┐   ┌─────────────────────┐
│  Anthropic API   │   │   Supabase           │
│                  │   │   (PostgreSQL)       │
│  claude-sonnet-4-6   │                      │
│  · text replies  │   │   users              │
│  · vision        │   │   meals              │
│  · daily summary │   │   off_topic_log      │
│  · weekly summary│   │                      │
│                  │   └─────────────────────┘
│  claude-haiku-4-5│
│  · off-topic     │
│    classifier    │
│  · meal report   │
│    classifier    │
│  · dish name     │
│    extractor     │
│  · date intent   │
│    detector      │
└──────────────────┘
```

---

## Data Flow Diagrams

### Photo message flow
```
User sends photo
→ LINE delivers POST /webhook
→ Signature verified (X-Line-Signature / LINE_CHANNEL_SECRET)
→ get_or_create_user() → Supabase
→ is_blocked() → Supabase off_topic_log
  → If blocked: reply BLOCKED message, return
→ Download image bytes from LINE CDN (MessagingApiBlob)
→ Base64 encode image
→ Claude Sonnet vision call
  → System: SYSTEM_PROMPT (goal-aware)
  → User: image + structured vision_prompt
  → Returns: "DISH: [full dish name]\n\n[coaching text]"
→ Parse: extract dish_name from first line
→ If dish_name found:
  → log_meal(user_id, dish_name) → Supabase meals
→ If dish_name not found:
  → append UNKNOWN_DISH nudge to coaching_text, skip DB write
→ Append to in-memory conversation_history (trim to 10)
→ _reply(reply_token, coaching_text) → LINE Messaging API
```

### Text message flow
```
User sends text
→ LINE delivers POST /webhook
→ Signature verified
→ get_or_create_user() → Supabase
→ Language detection (Thai char range check)
  → update_user_language() if changed → Supabase
→ Input length guard (>500 chars → reply TOO_LONG, return)
→ is_blocked() → Supabase off_topic_log
→ is_unblock_command() check
  → If unblock keyword AND currently blocked:
    → clear_block() → Supabase, reply UNBLOCK, return
→ If blocked: reply BLOCKED, return
→ detect_goal() keyword match
  → If goal found: update_user_goal() → Supabase, reply confirmation, return
→ is_conversational() check (≤10 chars OR in whitelist)
  → If conversational: skip off-topic classifier
→ If not conversational: classify_off_topic() → Claude Haiku
  → If off-topic:
    → increment_off_topic() → Supabase
    → count=1 → reply OFFTOPIC warning
    → count=2 → reply WARN_2 (last warning)
    → count≥3 → set 6hr block, reply BLOCKED, return
→ classify_meal_report() → Claude Haiku
  → If MEAL: extract_dish_from_text() → Claude Haiku
    → log_meal(user_id, dish_name) → Supabase
→ detect_date_intent() → Claude Haiku
  → If TOO_OLD: reply HISTORY_LIMIT, return
  → If date found: get_meals_by_date_range() → Supabase
→ get_today_meals() → Supabase (always, for context)
→ Build system prompt (goal + meal context)
→ Append user message to in-memory conversation_history
→ Claude Sonnet call with full history
→ Append assistant reply to conversation_history (trim to 10)
→ _reply(reply_token, clean_for_line(reply_text)) → LINE
```

### Follow event (onboarding) flow
```
User follows bot
→ LINE delivers POST /webhook (FollowEvent)
→ get_or_create_user(line_user_id) → Supabase
  → Insert with defaults: goal=no_goal, language=th
→ _reply(reply_token, ONBOARDING_MSG_1)  ← goal selection prompt
→ sleep(1)
→ _push(line_user_id, ONBOARDING_MSG_2)  ← features overview
```

### Daily summary flow (20:00 Bangkok = 13:00 UTC)
```
APScheduler fires send_daily_summaries()
→ get_all_users() → Supabase (all rows — see scaling note)
→ For each user:
  → get_today_meals(user_id) → Supabase
  → If no meals:
    → _push(line_user_id, fixed nudge message)
    → continue
  → Group meals by meal_type
  → Build structured prompt (goal-aware, 4-sentence template)
    → has_dinner flag modifies template:
       · With dinner:    [what ate] [goal progress] [tomorrow tip]
       · Without dinner: [what ate] [goal progress] [dinner wish] [tomorrow tip]
  → Claude Sonnet call (max_tokens=250)
  → _push(line_user_id, summary)
```

### Weekly summary flow (Monday 08:00 Bangkok = Monday 01:00 UTC)
```
APScheduler fires send_weekly_summaries()
→ get_all_users() → Supabase
→ For each user:
  → get_week_meals(user_id) → Supabase (last 7 days)
  → If no meals:
    → _push(line_user_id, WEEKLY_NO_MEALS fixed message)
    → continue
  → Compute days_with_meals, top 3 dishes by frequency
  → days_with_meals ≤ 2 → inject warm low-log prefix
  → Select goal-specific guidance (lose_weight/eat_clean/build_muscle/no_goal)
  → Build 4-part structured prompt
  → Claude Sonnet call (max_tokens=280)
  → _push(line_user_id, weekly_summary)
```

---

## Database Entity Diagram

```
users
├── id              UUID PK (gen_random_uuid)
├── line_user_id    TEXT UNIQUE NOT NULL   ← LINE's stable user identifier
├── goal            TEXT DEFAULT 'no_goal' ← lose_weight|eat_clean|build_muscle|no_goal
├── language        TEXT DEFAULT 'th'      ← th|en (auto-detected per message)
└── created_at      TIMESTAMPTZ DEFAULT NOW()

meals
├── id              UUID PK
├── user_id         UUID FK → users.id ON DELETE CASCADE
├── description     TEXT  ← dish name (max 200 chars)
├── meal_type       TEXT  ← breakfast|lunch|dinner|snack|late_snack (time-derived)
└── logged_at       TIMESTAMPTZ DEFAULT NOW()

off_topic_log
├── id              UUID PK
├── user_id         UUID FK → users.id ON DELETE CASCADE UNIQUE
├── count           INTEGER DEFAULT 0  ← resets to 0 after block triggered
├── blocked_until   TIMESTAMPTZ        ← NULL = not blocked
└── updated_at      TIMESTAMPTZ DEFAULT NOW()
```

**Relationships:**
```
users ──< meals           (one user → many meal records)
users ──| off_topic_log   (one user → zero or one off-topic record)
```

---

## Claude API Usage Map

| Trigger | Model | Max tokens | Purpose |
|---|---|---|---|
| Text message | claude-haiku-4-5-20251001 | 5 | Off-topic classification (YES/NO) |
| Text message | claude-haiku-4-5-20251001 | 5 | Meal report classification (MEAL/NOT) |
| Text message | claude-haiku-4-5-20251001 | 30 | Dish name extraction |
| Text message | claude-haiku-4-5-20251001 | 20 | Date intent detection (YYYY-MM-DD/NO) |
| Text message | claude-sonnet-4-6 | 300 | Conversational reply |
| Photo message | claude-sonnet-4-6 | 300 | Vision + coaching (DISH: extraction) |
| Daily summary | claude-sonnet-4-6 | 250 | Per-user daily summary |
| Weekly summary | claude-sonnet-4-6 | 280 | Per-user weekly summary |

**Cost estimate (per active user per day):**
- Text message: up to 4 Haiku calls + 1 Sonnet call
- Photo message: 1 Sonnet call
- Daily summary: 1 Sonnet call (if meals logged)
- Weekly summary: 1 Sonnet call / 7 days = ~0.14 Sonnet calls/day

---

## Infrastructure

| Component | Service | Tier | Notes |
|---|---|---|---|
| Application server | Railway | Free/Hobby | Auto-deploy from GitHub main |
| Database | Supabase | Free | 500MB limit |
| AI API | Anthropic | Pay-per-use | Budget limit set in console |
| Messaging | LINE Messaging API | Free (500 push/mo) | Reply tokens unlimited |
| Scheduler | APScheduler (in-process) | — | Runs inside Railway service |

---

## Security Boundaries

| Boundary | Mechanism |
|---|---|
| LINE webhook authenticity | X-Line-Signature HMAC-SHA256 verified on every POST /webhook |
| Manual cron trigger | X-Cron-Secret header checked against CRON_SECRET env var |
| Database access | service_role key, server-side only — never exposed to clients |
| Secrets | All in Railway environment variables — never in code or git |
| User identity | LINE user ID only — no PII (name, phone, email) stored |

---

## Known Limitations & Technical Debt

| Issue | Impact | Fix when |
|---|---|---|
| APScheduler in-process | Summary fails silently if process restarts at 20:00 or 08:00 Monday | >100 users |
| `get_all_users()` fetches all rows at once | Memory spike at scale | >500 users |
| Conversation history in-memory | Resets on every deploy; users lose context | When users complain |
| No image size validation | Memory spike on large image uploads | Before public launch |
| No structured logging / error tracking | Hard to diagnose prod failures | Soon |
| Single Railway service | No horizontal scaling | >1000 concurrent users |
| `source` column absent from meals table | Schema diagram and code don't record photo vs text source | Next schema migration |
| No `last_active_at` on users | Can't filter inactive users for summaries | Before scaling summaries |
| No `event_log` table | Can't audit summary delivery or block events | Before analytics |

---

## Scaling Roadmap

**0–200 users:** No infrastructure changes. Monitor Supabase storage and Railway memory.

**200–1000 users:**
- Paginate `get_all_users()` (`.range(0, 99)`) in summary loops
- Move APScheduler to Railway cron service (separate service, survives restarts)
- Redis for conversation history (survives deploys, shared across instances)
- Upgrade LINE plan if push message volume exceeds 500/month

**1000+ users:**
- Separate Railway services: API server + scheduler worker
- Queue webhook processing (Railway Redis or managed queue)
- Database indexes on `meals.user_id + logged_at`, `off_topic_log.user_id`
- Add `event_log` table for delivery audit trail
- Read replicas for analytics queries

---

## Decision Log

| Decision | Rationale | Date |
|---|---|---|
| Railway over AWS/GCP | Zero DevOps for MVP, auto-deploy from GitHub | May 2026 |
| Supabase over self-hosted PostgreSQL | Managed DB, free tier sufficient for beta | May 2026 |
| APScheduler in-process over Railway cron | Simpler for MVP, acceptable risk at beta scale | May 2026 |
| Claude Haiku for all classifiers | Cost — classification requires max 5–30 tokens, not Sonnet quality | May 2026 |
| In-memory conversation history | No DB overhead; reset on deploy acceptable for MVP | May 2026 |
| Single Railway service | Sufficient for <200 users, simplest deployment path | May 2026 |
| Dish name stored, not nutritional data | Nutrition estimates from LLM are unreliable; store facts only | May 2026 |
| meal_type inferred from clock time | No user input needed; close enough for coaching context | May 2026 |
