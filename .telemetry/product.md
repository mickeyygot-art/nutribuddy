# Product: NutriBuddy

**Last updated:** 2026-05-31
**Method:** codebase scan + conversation

## Product Identity
- **One-liner:** Thai users send food photos or text messages on LINE, the bot identifies dishes, logs meals, and coaches them toward their health goal — with daily and weekly summaries pushed automatically.
- **Category:** ai-ml-tool (consumer health)
- **Product type:** B2C — no organizations or accounts. Every user is an independent individual.
- **Collaboration:** single-player — each user has a private 1:1 with the bot. No shared state between users.

## Business Model
- **Monetization:** Free only (beta)
- **Pricing tiers:** None currently
- **Billing integration:** None detected

## Tech Stack
- **Primary language:** Python 3.12
- **Framework:** FastAPI + Uvicorn
- **Database:** Supabase (PostgreSQL)
- **Background jobs:** APScheduler (in-process) — daily summary at 13:00 UTC, weekly summary Mondays 01:00 UTC
- **HTTP client patterns:** LINE SDK (linebot.v3), Anthropic Python SDK, Supabase Python client
- **Module organization:** Single-module (main.py + database.py), deployed on Railway

## Value Mapping

### Primary Value Action
**meal.logged** — A user successfully logs a meal (photo or text). If this drops to zero, the product has failed.

### Core Features (directly deliver value)
1. **Photo meal logging** — User sends a food photo; Sonnet vision identifies the dish and delivers goal-aware coaching in ≤2 sentences.
2. **Text meal logging** — User types what they ate; Haiku triage extracts dish name and meal type, logs it silently, Sonnet replies.
3. **Goal-aware coaching** — Every reply is shaped by the user's active goal (lose_weight / eat_clean / build_muscle / no_goal).
4. **Daily summary** — Pushed at 20:00 BKK; recaps the day's meals and gives one actionable tip for tomorrow.
5. **Weekly summary** — Pushed Monday 08:00 BKK; reviews the week's logging consistency and top dishes with goal-specific guidance.
6. **In-chat dashboard** — User requests their 7-day summary on demand; Sonnet generates a plain-text personal recap.

### Supporting Features (enable core actions)
1. **Onboarding** — Follow event triggers two-message welcome sequence; user sets their health goal immediately.
2. **Goal setting** — User can change their goal at any time via phrase or digit; stored and applied to all future replies.
3. **Meal history lookup** — User can ask what they ate on a past day (up to 30 days back); date is resolved by Haiku.
4. **Off-topic moderation** — 3-strike system with 6-hour block; prevents bot misuse and keeps AI cost bounded.
5. **Language detection** — Auto-detects Thai vs English per message; all copy exists in both languages.
6. **Admin dashboard** — Web UI at `/admin` shows user counts, meal stats, event log, and estimated API cost.

## Entity Model

### Users
- **ID format:** UUID (internal, `gen_random_uuid()`); externally identified by LINE user ID (opaque string, e.g. `Uxxxxxxx`)
- **Roles:** end user only (no admin role in user table — admin access is header-secret gated at the API level)
- **Multi-account:** no — one LINE user ID maps to exactly one user row
- **PII stored:** none — LINE user ID is an opaque platform identifier, not personally identifiable. No email, name, or phone stored.

### No Account / Organization Entity
NutriBuddy has no concept of teams, organizations, or shared workspaces. There is no second entity type.

## Group Hierarchy

None. NutriBuddy is user-level tracking only. Every event belongs to a user; there is no group above the user to attribute events to.

## Current State
- **Existing analytics tracking:** None — no SDK calls to any analytics destination in the codebase
- **App-level event log:** `event_log` table in Supabase records five event types (`daily_summary_sent`, `weekly_summary_sent`, `block_triggered`, `dashboard_requested`, `unblock`) — these are operational logs, not analytics
- **Documentation:** partial — ARCHITECTURE.md is thorough; no tracking documentation
- **Known issues:** No visibility into funnel (onboarding → first meal → retention), no goal conversion data, no cohort analysis

## Integration Targets
| Destination | Purpose | Priority |
|-------------|---------|----------|
| PostHog | User behavior analytics, funnel analysis, retention cohorts | Primary |

**PostHog notes:** PostHog supports anonymous user tracking without PII — LINE user ID used as `distinct_id`. Group analytics available but not needed (no groups). Free tier generous for early-stage B2C products.

**Internal user exclusion:** Yes — test/admin interactions should be filtered. Implementation: maintain a set of known internal LINE user IDs; skip `posthog.capture()` for those IDs. No email domain to filter on (no email stored).

## Codebase Observations
- **Feature areas inferred from routes/handlers:** webhook (text + image), onboarding (follow event), daily summary cron, weekly summary cron, admin dashboard, health check
- **Entity model inferred from schema:** `users`, `meals`, `off_topic_log`, `event_log` — clean four-table schema, no join tables
- **AI usage pattern:** Haiku for classification (5–30 tokens), Sonnet for generation (250–300 tokens) — cost-aware dual-model design
- **Scaling notes:** Architecture doc flags >200 users as the point where scheduler and pagination need rework — relevant to analytics volume expectations
