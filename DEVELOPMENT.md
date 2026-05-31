# NutriBuddy — Development Guide

Developer agent: read, write, debug, test, deploy. No product decisions — those come from the product agent via Linear.

---

## Agent Roles & Handoff

- **Product (Cowork):** strategy, features, roadmap, Linear issues. No code.
- **Developer (Claude Code):** reads issues, implements, tests, commits. No product calls.
- **Flow:** Product writes issue with plan → Developer implements, commits `feat/fix: desc (YOL-XX)` → marks Done + comments what shipped.

---

## Planning Steps — Required Before Any Code

No exceptions, even for small changes.

1. **Read the Linear issue fully** — what, why, definition of done. Unclear? Ask product before proceeding.
2. **Identify affected files** — `main.py`, `database.py`, `schema.sql`, `requirements.txt`. New table/column → goes in `schema.sql` and must run in Supabase before code ships.
3. **Check API cost impact** — new Claude calls? How many per user/day? Update spending estimate below.
4. **Write a `# PLAN:` comment block** at the top of the function before implementing.
5. **Implement against the plan** — update comments if you deviate.
6. **Test before committing** — run relevant test in `tests/test_logic.py`; write one first if missing.
7. **Commit** — `git commit -m "feat: [description] (YOL-XX)"`
8. **Update Linear** — mark Done, brief comment on what was built.

---

## Stack

Python 3.12 · FastAPI · Uvicorn · LINE Messaging API v3 · Anthropic Claude (sonnet-4-6 responses+vision, haiku-4-5 classifiers) · Supabase (PostgreSQL) · APScheduler (in-process) · Railway (auto-deploy from `main`).

---

## Security — Non-Negotiable

- **Never** commit API keys, even to a private repo. All secrets in Railway env vars only.
- Rotate immediately if any key is exposed in chat, logs, or commits.
- Use `.env.example` for docs — never `.env`.

**Required env vars:**

| Variable | Source |
|---|---|
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Console → Messaging API |
| `LINE_CHANNEL_SECRET` | LINE Console → Basic settings |
| `ANTHROPIC_API_KEY` | console.anthropic.com → API Keys |
| `SUPABASE_URL` | Supabase → Settings → API |
| `SUPABASE_KEY` | Supabase → Settings → API → service_role key |
| `CRON_SECRET` | Random string — protects `/cron/*` |

**Key rotation (if exposed):** regenerate at Anthropic / LINE / Supabase → update Railway Variables → redeploy.

---

## Protecting `/cron/*` Endpoints

Both summary triggers send push messages, so they must be guarded:

```python
@app.post("/cron/daily-summary")
def trigger_summary(request: Request):
    if request.headers.get("X-Cron-Secret", "") != os.environ.get("CRON_SECRET", ""):
        raise HTTPException(status_code=403, detail="Forbidden")
    send_daily_summaries()
    return {"status": "sent"}
```

---

## API Usage Limits

**Anthropic:** text = up to 4 Haiku + 1 Sonnet; image = 1 Sonnet; daily/weekly summary = 1 Sonnet per active user. Set a hard spending limit (recommend $20/mo during beta).
**LINE:** free tier = 500 push/mo (summaries count; replies unlimited). Upgrade before user count > ~150.
**Supabase:** free tier = 500MB DB, 2GB bandwidth. Monitor in Reports.

---

## Local Development

```bash
git clone https://github.com/mickeyygot-art/nutribuddy.git && cd nutribuddy
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in real keys
uvicorn main:app --reload --port 8000
ngrok http 8000               # copy https URL → LINE Console → Webhook URL → /webhook
```

**Tests:** `python tests/test_logic.py`
**DB:** schema in `schema.sql`, run once in Supabase SQL Editor. Reset (dev only): `TRUNCATE users, meals, off_topic_log, event_log CASCADE;` — never on production.

---

## Deployment (Railway)

Push to `main` → auto-deploys. Check logs in Railway → Deployments.

- Health: `GET /health`
- Manual summary: `curl -X POST .../cron/daily-summary -H "X-Cron-Secret: $SECRET"`

**Branches:** `feature/xxx` → `dev` (test here) → PR into `main`. Never push directly to `main`.

---

## Known Limitations (fix before scaling)

| Issue | Impact | Fix |
|---|---|---|
| `get_all_users()` fetches all rows | Memory at >500 users | Add `.range(0, 99)` pagination |
| No image size validation | Memory on large uploads | Check content-length before download |
| No structured logging | Hard to debug prod | Add `logging` / Sentry |
| APScheduler in-process | Summary fails if process restarts | Move to Railway cron service |

---

## Daily Summary Template (20:00 BKK)

Sent to all users with ≥1 meal logged. Max 4 short sentences, 1 emoji, warm friend tone, plain text (no markdown).

- **Dinner logged:** [what you ate] · [goal progress] · [tomorrow tip]
- **No dinner yet:** [what you ate] · [goal progress] · [dinner wish] · [tomorrow tip]
- **No meals:** fixed message, no Claude call (TH/EN).

**Goal-specific tip focus:** lose_weight → lower-cal swap/smaller portion · eat_clean → add veg/cut processed · build_muscle → add protein · no_goal → general habit (hydration, color variety).

---

## New Feature Checklist

- [ ] New DB table/column? → `schema.sql` + run migration in Supabase
- [ ] New Claude calls? → estimate cost, update spending limit
- [ ] New endpoint with side effects? → add auth
- [ ] Stores user data? → update privacy policy before launch
- [ ] Changes system prompt? → test Thai + English before deploy
