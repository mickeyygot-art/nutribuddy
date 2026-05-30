# Pre-Push Checklist — Before Making Repo Public

## 🔴 Security (do BEFORE pushing to public GitHub)

- [ ] **Rotate Anthropic API key** → console.anthropic.com → API Keys → delete old key → create new one
- [ ] **Rotate LINE Channel Access Token** → LINE Developers Console → Messaging API tab → Reissue token
- [ ] **Confirm .env is in .gitignore** (already set — double check it's not staged)
- [ ] **Confirm no secrets in .env.example** (should only have placeholder values)
- [ ] **Set Railway env vars** with the NEW rotated keys — not the old ones shared in chat

## ✅ Already Safe to Push
- `main.py` — no hardcoded secrets, all env vars
- `requirements.txt` — no secrets
- `Procfile` — no secrets
- `railway.toml` — no secrets
- `.env.example` — placeholder values only
- `.gitignore` — .env is excluded

## After Rotating Keys
Update Railway environment variables:
- `ANTHROPIC_API_KEY` → new key
- `LINE_CHANNEL_ACCESS_TOKEN` → new token
- `LINE_CHANNEL_SECRET` → stays the same (not shared publicly)
