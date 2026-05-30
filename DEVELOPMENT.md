# NutriBuddy — Development Guide

## Planning Steps — Required Before Writing Any Code

Every task from Linear must go through these steps before implementation starts. No exceptions, even for "small" changes.

**Step 1 — Read the Linear issue fully**
Understand what is being asked, why it exists, and what "done" looks like. If the issue is unclear, ask the product agent (Cowork session) before proceeding.

**Step 2 — Identify all affected files**
List every file that will change: `main.py`, `database.py`, `schema.sql`, `requirements.txt`, etc. If a new DB table or column is needed, that goes in `schema.sql` and must be run in Supabase before the code goes live.

**Step 3 — Check for API cost impact**
Does this add new Claude calls? How many per user per day? Update the spending limit estimate in the "API Usage Limits" section below if so.

**Step 4 — Write a brief plan in comments first**
Before writing implementation code, add a `# PLAN:` comment block at the top of the function you're building. Example:
```python
# PLAN:
# 1. Check if user exists in DB
# 2. If new user, insert with default goal=no_goal
# 3. Return user dict either way
```

**Step 5 — Implement against the plan**
Write code that matches the plan comments. If you deviate, update the comments.

**Step 6 — Test before committing**
Run the relevant test in `tests/test_logic.py`. If the feature has no test yet, write one first.

**Step 7 — Commit with Linear issue reference**
```bash
git commit -m "feat: [description] (YOL-XX)"
```

**Step 8 — Update Linear issue**
Mark the issue as Done and add a brief comment on what was implemented.

---

## Stack
- **Runtime**: Python 3.12, FastAPI, Uvicorn
- **AI**: Anthropic Claude API (claude-sonnet-4-6 for responses, claude-haiku-4-5 for classification)
- **Database**: Supabase (PostgreSQL)
- **Messaging**: LINE Messaging API v3
- **Hosting**: Railway
- **Scheduler**: APScheduler (background, in-process)

---

## Security Rules — Non-Negotiable

### API Keys
- **Never** commit real API keys to GitHub, even in a private repo
- All secrets live in Railway environment variables only
- Rotate keys immediately if accidentally exposed in chat, logs, or commits
- Use `.env.example` for documentation — never `.env`

### Key rotation checklist (run if any key is exposed):
1. `console.anthropic.com` → API Keys → delete old → create new
2. LINE Developers Console → Messaging API → Reissue Channel Access Token
3. Supabase → Project Settings → API → regenerate anon/service key
4. Update all three in Railway → Variables → redeploy

### Required environment variables
| Variable | Where to get it |
|---|---|
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Developers Console → Messaging API tab |
| `LINE_CHANNEL_SECRET` | LINE Developers Console → Basic settings |
| `ANTHROPIC_API_KEY` | console.anthropic.com → API Keys |
| `SUPABASE_URL` | Supabase → Project Settings → API → Project URL |
| `SUPABASE_KEY` | Supabase → Project Settings → API → service_role key (for server-side writes) |

---

## API Usage Limits

### Anthropic
- Every text message triggers **2 Claude calls**: 1 Haiku (off-topic check) + 1 Sonnet (response)
- Every image message triggers **1 Sonnet call** (vision)
- Daily summary triggers **1 Sonnet call per active user**
- Set a **spending limit** at console.anthropic.com → Billing → Usage limits
- Recommended: start at $20/month hard limit during beta

### LINE Messaging API
- Free tier: 500 push messages/month (daily summaries count as push)
- Reply messages (webhook responses) are unlimited on free tier
- When user count > 150, upgrade to LINE Official Account paid plan or daily summaries will stop

### Supabase
- Free tier: 500MB database, 2GB bandwidth, 50,000 monthly active users
- Monitor at supabase.com → your project → Reports

---

## Protecting the `/cron/daily-summary` Endpoint

This endpoint triggers push messages to all users. It must be protected.

Add a secret token check:

```python
# In Railway env vars, add: CRON_SECRET=your_random_string_here

@app.post("/cron/daily-summary")
def trigger_summary(request: Request):
    token = request.headers.get("X-Cron-Secret", "")
    if token != os.environ.get("CRON_SECRET", ""):
        raise HTTPException(status_code=403, detail="Forbidden")
    send_daily_summaries()
    return {"status": "sent"}
```

---

## Local Development

```bash
# 1. Clone repo
git clone https://github.com/YOUR_USERNAME/nutribuddy.git
cd nutribuddy

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy and fill in env vars
cp .env.example .env
# Edit .env with your real keys

# 5. Run locally
uvicorn main:app --reload --port 8000

# 6. Expose local server to LINE (for webhook testing)
# Install ngrok: https://ngrok.com
ngrok http 8000
# Copy the https URL → LINE Developers Console → Webhook URL → /webhook
```

---

## Database

Schema is in `schema.sql`. Run it once in Supabase SQL Editor before first deploy.

```sql
-- To reset all data during development:
TRUNCATE users, meals, off_topic_log CASCADE;
```

**Never run TRUNCATE on production.**

---

## Deployment (Railway)

1. Push to `main` branch → Railway auto-deploys
2. Check deploy logs in Railway → your service → Deployments
3. Health check: `GET https://your-app.railway.app/health`
4. Manual summary trigger (with secret): 
   ```bash
   curl -X POST https://your-app.railway.app/cron/daily-summary \
     -H "X-Cron-Secret: YOUR_CRON_SECRET"
   ```

---

## Branch Strategy

```
main          ← production (auto-deploys to Railway)
dev           ← development branch, test here first
feature/xxx   ← individual features, merge into dev
```

Never push directly to `main`. Always PR from `dev`.

---

## Known Limitations (fix before scaling)

| Issue | Impact | Fix |
|---|---|---|
| `get_all_users()` fetches all rows | Memory spike at >500 users | Add `.range(0, 99)` pagination |
| No image size validation | Memory spike on large uploads | Check content-length before download |
| No structured logging | Hard to debug prod errors | Add `logging` module or Sentry |
| APScheduler in-process | Summary fails if process restarts at 20:00 | Move to Railway cron service |

---

## Daily Summary Structure (8pm Bangkok)

Sent every day at 20:00 ICT to all users with at least one meal logged.

### Template (4 sentences max, 1 emoji max, warm friend tone)

**When dinner is logged:**
```
[1] What you ate — warm sentence listing meals by type (dish names only)
[2] Goal progress — one sentence connecting today's eating to their goal
[3] Tomorrow tip — one specific, practical suggestion
```

**When no dinner logged yet:**
```
[1] What you ate — warm sentence listing meals logged so far
[2] Goal progress — one sentence connecting today's eating to their goal
[3] Dinner wish — kind sentence wishing them a healthy dinner tonight
[4] Tomorrow tip — one specific, practical suggestion
```

**When no meals logged at all:**
```
Fixed message (no Claude call):
TH: "วันนี้ยังไม่ได้ส่งรูปอาหารเลยนะ — พรุ่งนี้ลองส่งมาให้ NutriBuddy ดูได้เลย! 🍽️"
EN: "No meals logged today — try sending a food photo tomorrow! 🍽️"
```

### Goal-specific tip guidance (for prompt context)
| Goal | Focus of tip |
|---|---|
| lose_weight | Suggest lower-cal swap or smaller portion for tomorrow |
| eat_clean | Suggest adding vegetables or reducing processed food |
| build_muscle | Suggest protein source to add tomorrow |
| no_goal | General positive habit (hydration, color variety, etc.) |

---

## Adding New Features — Checklist

- [ ] Does it require a new DB table? → Add to `schema.sql`, run migration in Supabase
- [ ] Does it make new Claude API calls? → Estimate cost, update spending limit
- [ ] Does it expose a new endpoint? → Add authentication if it triggers side effects
- [ ] Does it store user data? → Update privacy policy before launch
- [ ] Does it change the system prompt? → Test with Thai and English inputs before deploying
