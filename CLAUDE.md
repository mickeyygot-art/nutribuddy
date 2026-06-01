# NutriBuddy — CLAUDE.md

Read this first. All agents (product, developer, architect) must follow these principles.

---

## Mission
Help people in Thailand and SEA build healthier eating habits — frictionless, personal, encouraging. Not calorie counting.
**NutriBuddy wins if users get measurably healthier. Everything else is a proxy.**

## Design Principles
1. **Outcome-first** — every feature must connect to a real health outcome, not just better logging.
2. **Thai & SEA first** — default user speaks Thai, eats Thai food, uses LINE daily. Generic solutions are not enough.
3. **No shame, no guilt** — celebrate what's good first. Coaching is warm, forward-looking. Non-negotiable.
4. **Frictionless** — zero extra steps for users. Silent auto-detection beats asking questions.
5. **Design for the user who almost quit** — not the motivated early adopter. Every retention mechanic serves that person.

## Security (no exceptions)
- All secrets in Railway env vars only — never in code, comments, or commits.
- Rotate immediately on any exposure.
- Every LINE webhook must pass HMAC-SHA256 signature verification.
- `/admin`, `/cron/*` endpoints must require a secret header. No public access.
- Store only: LINE user ID, goal, language, meal descriptions. No PII.

## Quality Standards
- Run `python tests/test_logic.py` before every commit. All tests must pass.
- No silent failures — catch and log errors, never swallow them.
- `clean_for_line()` on every outgoing message — LINE doesn't render markdown.
- `max_tokens` must be high enough to complete a full sentence.

## Scalability
- Build for today, design for tomorrow. Don't over-engineer for 10k users when we have 15.
- Log every architectural shortcut in `ARCHITECTURE.md` → Known Limitations with a "fix when" threshold.
- Keep `database.py` (data layer) and `main.py` (app logic) separate.

## Agent Roles
| Agent | Owns | Does not own |
|---|---|---|
| Product (Cowork) | What to build, Linear issues, UX copy | Code structure |
| Developer (Claude Code) | Implementation, tests, commits | Product decisions |
| Architect (Claude Code) | System design, DB schema, scaling | Features, UX |

Handoff: Product creates Linear issue → Developer/Architect implements → commits as `feat/fix: description (YOL-XX)`.
Disagreements: flag to product agent with reason. Never silently ignore.

## Reading Order
1. `CLAUDE.md` — this file
2. `ARCHITECTURE.md` — system design & data flow
3. `DEVELOPMENT.md` — dev guide, planning steps, security rules
4. `schema.sql` — database schema
5. Linear — current backlog

---

# Developer Agent

**Role:** Read, write, debug, test, deploy. No product decisions — those come from Linear issues.

## Stack
- Python 3.12, FastAPI, Uvicorn
- LINE Messaging API v3 (`line-bot-sdk>=3.0`)
- Claude API — `claude-sonnet-4-6` (responses + vision), `claude-haiku-4-5-20251001` (classifiers)
- Supabase (PostgreSQL) — users, meals, off_topic_log, event_log
- APScheduler — daily 8pm (13:00 UTC) + weekly Monday 8am (01:00 UTC)
- Railway — auto-deploy from `main` branch

## Environment Variables
| Variable | Purpose |
|---|---|
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE messaging |
| `LINE_CHANNEL_SECRET` | Webhook signature verification |
| `ANTHROPIC_API_KEY` | Claude API |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_KEY` | service_role key (not anon) |
| `CRON_SECRET` | Protects `/cron/*` endpoints |
| `ADMIN_SECRET` | Protects `/admin` endpoint |

## Key Behaviours
1. **Text** → blocked check → goal change → off-topic classifier (Haiku) → Sonnet reply
2. **Image** → blocked check → Sonnet vision → extract `DISH: [name]` → log to DB
3. **Follow** → create user in DB → send 2-part onboarding message
4. **Daily 8pm** → `send_daily_summaries()` → 4-part template per user
5. **Weekly Monday 8am** → `send_weekly_summaries()` → 4-part weekly recap per user
6. **Off-topic** → 3 strikes → 6hr block in `off_topic_log`

## Commands
```bash
# Local dev
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt && cp .env.example .env
uvicorn main:app --reload --port 8000  # + ngrok for webhook

# Test
python tests/test_logic.py

# Deploy
git push origin main  # Railway auto-deploys
```

**Before writing any code — read `DEVELOPMENT.md` for the full planning checklist.**
