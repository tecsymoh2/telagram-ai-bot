# Telegram AI Bot

Python Telegram webhook bot for Vercel. Uses Groq for AI responses and Neon PostgreSQL for persistent conversation memory.

## Features
- Persistent user profiles, conversations, and message history
- Commands: `/start`, `/help`, `/reset`, `/about`, `/status`, `/admin`
- Optional Telegram webhook secret verification
- Configurable model and system prompt

## Deploy to Vercel
Import this repository into Vercel and add environment variables: `DATABASE_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, `GROQ_API_KEY`. Optional: `GROQ_MODEL`, `ADMIN_IDS`, `SYSTEM_PROMPT`. Deploy. Webhook URL: `https://YOUR-VERCEL-DOMAIN/api`.

Register webhook locally (replace placeholders; never commit credentials):
```bash
curl -X POST "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook" \
  -d "url=https://YOUR-VERCEL-DOMAIN/api" \
  -d "secret_token=<TELEGRAM_WEBHOOK_SECRET>"
```

## Neon
Database name: `telegram_ai_bot`. Run `schema.sql` if tables need recreating. Use the Neon connection string as `DATABASE_URL`; keep it private.

## Local development
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn api.index:app --reload
```

Check Groq model availability and limits before production. Add rate limits and usage controls before broad public release.
