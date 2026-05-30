# NutriBuddy — Developer Context

You are the developer agent for NutriBuddy, an AI health coaching bot built on LINE Messaging API + Claude API, deployed on Railway, with Supabase as the database.

**Your role:** Read, write, debug, test, and deploy code. Do not make product decisions — those come from the product agent (separate Cowork session) and are tracked in Linear.

---

## Project Structure

```
nutribuddy/
├── main.py           # FastAPI app — webhook handlers, scheduler, LINE logic
├── database.py       # Supabase client + all DB helper functions
├── schema.sql        # Database schema (run once in Supabase SQL Editor)
├── requirements.txt  # Python dependencies
├── Procfile          # Railway start command
├── railway.toml      # Railway deployment config
├── DEVELOPMENT.md    # Full dev guide — READ THIS before touching anything
├── .env.example      # Env var template (never commit real .env)
└── CLAUDE.md         # This file
```

---

## Stack

- **Python 3.12**, FastAPI, Uvicorn
- **LINE Messaging API v3** (`line-bot-sdk>=3.0`)
- **Anthropic Claude API** — `claude-sonnet-4-6` (responses + vision), `claude-haiku-4-5-20251001` (off-topic classification)
- **Supabase** (PostgreSQL) — users, meals, off_topic_log tables
- **APScheduler** — background cron for 8pm daily summary (13:00 UTC)
- **Railway** — hosting + auto-deploy from GitHub `main` branch

---

## Environment Variables (all required)

| Variable | Purpose |
|---|---|
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE bot messaging |
| `LINE_CHANNEL_SECRET` | LINE webhook signature verification |
| `ANTHROPIC_API_KEY` | Claude API |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_KEY` | Supabase service_role key (not anon) |
| `CRON_SECRET` | Protects `/cron/daily-summary` endpoint |

Never hardcode these. All live in Railway environment variables.

---

## Key Behaviours to Understand

1. **Every text message** → check blocked → check goal change → classify off-topic (Haiku) → Claude response (Sonnet)
2. **Every image** → check blocked → Claude vision with `DISH: [name]` extraction → store dish name only in DB
3. **Follow event** → send fixed onboarding message → create user in DB
4. **Daily 8pm** → APScheduler calls `send_daily_summaries()` → structured 4-part template per user
5. **Off-topic guard** → 3 strikes → 6hr block stored in `off_topic_log` table

---

## Before You Write Any Code

Read `DEVELOPMENT.md` — it has the planning checklist, security rules, API limits, branch strategy, and known limitations. Follow the planning steps section before implementing any task.

---

## Running Locally

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in real values
uvicorn main:app --reload --port 8000
# Then use ngrok to expose for LINE webhook testing
```

## Running Tests

```bash
python tests/test_logic.py
```

---

## Deployment

Push to `main` → Railway auto-deploys. Check logs at Railway dashboard.
Health check: `GET /health`
Manual summary test: `POST /cron/daily-summary` with header `X-Cron-Secret: YOUR_SECRET`
