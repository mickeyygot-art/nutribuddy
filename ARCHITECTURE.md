# NutriBuddy — System Architecture

**Owner:** Architect Agent · **Status:** MVP / Beta · **Updated:** May 2026

LINE-native AI health coaching bot. Users interact entirely through LINE — no app download. Processes food photos and text, coaches on nutrition, logs meals, sends daily/weekly summaries. Maintain this doc whenever infra, data flow, or boundaries change.

---

## System Diagram

```
USER (LINE App)
  │ HTTPS
  ▼
LINE Messaging API + CDN  (routing, push, image delivery)
  │ POST /webhook
  ▼
Railway — single FastAPI + Uvicorn service (Python 3.12)
  ├── POST /webhook              LINE event dispatcher
  ├── GET  /health
  ├── POST /cron/daily-summary   manual trigger (CRON_SECRET)
  ├── POST /cron/weekly-summary  manual trigger (CRON_SECRET)
  ├── APScheduler (in-process)
  │     ├── daily  13:00 UTC      → send_daily_summaries()
  │     └── weekly Mon 01:00 UTC  → send_weekly_summaries()
  └── in-memory: conversation_history {line_user_id: [last 10 msgs]}  (resets on deploy)
  │                                    │
  ▼                                    ▼
Anthropic API                     Supabase (PostgreSQL)
  ├── claude-sonnet-4-6             users · meals
  │   text / vision / summaries     off_topic_log · event_log
  └── claude-haiku-4-5
      classifiers + extractors
```

---

## Data Flows

**Photo:** webhook → verify signature → get_or_create_user → is_blocked? → download image (LINE CDN) → base64 → Sonnet vision (`DISH: [name]\n\n[coaching]`) → parse dish → log_meal (or UNKNOWN nudge if no dish) → append history → reply.

**Text:** webhook → verify → get_or_create_user → lang detect → >500 chars? TOO_LONG → is_blocked / unblock keyword → detect_goal → is_conversational? skip classifier → classify_off_topic (Haiku) → 1/2/3 strikes (warn → warn → 6hr block) → classify_meal_report + extract_dish (Haiku) → log_meal → detect_date_intent (Haiku) → fetch history → build goal+meal prompt → Sonnet w/ history → clean_for_line → reply.

**Follow (onboarding):** webhook → get_or_create_user (defaults goal=no_goal, language=th) → reply ONBOARDING_MSG_1 → sleep(1) → push ONBOARDING_MSG_2.

**Daily summary (20:00 BKK = 13:00 UTC):** get_all_users → per user: get_today_meals → no meals? push nudge : group by type → goal-aware 4-part prompt (has_dinner flag) → Sonnet (250 tok) → push.

**Weekly summary (Mon 08:00 BKK = Mon 01:00 UTC):** get_all_users → per user: get_week_meals → no meals? push fixed : compute days_logged + top-3 dishes → ≤2 days inject warm prefix → goal-specific guidance → 4-part prompt → Sonnet (280 tok) → push.

---

## Database Entity Diagram

```
users                                    meals
├── id              UUID PK              ├── id          UUID PK
├── line_user_id    TEXT UNIQUE          ├── user_id     FK → users.id CASCADE
├── goal            TEXT                 ├── description TEXT (≤200 chars)
│   lose_weight|eat_clean|               ├── meal_type   breakfast|lunch|dinner|
│   build_muscle|no_goal                 │               snack|late_snack (time-derived)
├── language        th|en                ├── source      photo|text
├── created_at      TIMESTAMPTZ          └── logged_at   TIMESTAMPTZ
└── last_active_at  TIMESTAMPTZ
                                         event_log
off_topic_log                            ├── id          UUID PK
├── id              UUID PK              ├── user_id     FK → users.id CASCADE
├── user_id         FK UNIQUE CASCADE    ├── event_type  daily_summary_sent|
├── count           INTEGER (resets@3)   │   weekly_summary_sent|block_triggered|
├── blocked_until   TIMESTAMPTZ (NULL=ok)│   dashboard_requested|unblock
└── updated_at      TIMESTAMPTZ          └── created_at  TIMESTAMPTZ
```

Relationships: `users ──< meals` · `users ──| off_topic_log` · `users ──< event_log`

---

## Claude API Usage

| Trigger | Model | Tokens | Purpose |
|---|---|---|---|
| Text | haiku-4-5 | 5 | Off-topic classify (YES/NO) |
| Text | haiku-4-5 | 5 | Meal report classify (MEAL/NOT) |
| Text | haiku-4-5 | 30 | Dish name extract |
| Text | haiku-4-5 | 20 | Date intent detect |
| Text | sonnet-4-6 | 300 | Conversational reply |
| Photo | sonnet-4-6 | 300 | Vision + coaching |
| Daily | sonnet-4-6 | 250 | Per-user daily summary |
| Weekly | sonnet-4-6 | 280 | Per-user weekly summary |

Per active user/day: text ≈ up to 4 Haiku + 1 Sonnet; photo = 1 Sonnet; daily = 1 Sonnet; weekly ≈ 0.14 Sonnet.

---

## Infrastructure & Security

| Component | Service | Tier |
|---|---|---|
| App server | Railway | Free/Hobby — auto-deploy from `main` |
| Database | Supabase | Free — 500MB |
| AI | Anthropic | Pay-per-use, console budget cap |
| Messaging | LINE | Free — 500 push/mo, replies unlimited |
| Scheduler | APScheduler | In-process |

- LINE webhook: X-Line-Signature HMAC-SHA256 verified on every POST.
- `/cron/*`: X-Cron-Secret checked against `CRON_SECRET`.
- DB: service_role key, server-side only.
- Secrets: Railway env vars only — never in code/git.
- User identity: LINE user ID only — no PII stored.

---

## Known Limitations

| Issue | Impact | Fix when |
|---|---|---|
| APScheduler in-process | Summaries fail silently if process restarts at trigger time | >100 users |
| `get_all_users()` fetches all rows | Memory spike | >500 users |
| Conversation history in-memory | Resets on deploy | When users complain |
| No image size validation | Memory spike on large uploads | Before public launch |
| No structured logging | Hard to diagnose prod failures | Soon |
| Single Railway service | No horizontal scaling | >1000 users |

---

## Scaling Roadmap

**0–200:** No changes. Monitor Supabase storage + Railway memory.
**200–1000:** Paginate `get_all_users()`; move scheduler to Railway cron service; Redis for conversation history; upgrade LINE plan if push >500/mo.
**1000+:** Split API + scheduler services; queue webhook processing; index `meals(user_id, logged_at)` + `off_topic_log(user_id)`; read replicas for analytics (event_log already in place).

---

## Decision Log

| Decision | Rationale |
|---|---|
| Railway over AWS/GCP | Zero DevOps, auto-deploy from GitHub |
| Supabase over self-hosted PG | Managed, free tier sufficient for beta |
| APScheduler in-process | Simpler for MVP, acceptable beta risk |
| Haiku for all classifiers | Cost — classification needs 5–30 tokens |
| In-memory conversation history | No DB overhead; deploy reset acceptable |
| Single Railway service | Sufficient <200 users, simplest path |
| Store dish name, not nutrition data | LLM nutrition estimates unreliable |
| meal_type from clock time | No user input; good enough for coaching |
