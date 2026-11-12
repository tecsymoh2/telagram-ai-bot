# Telegram AI Bot

Webhook bot for Vercel: Groq (chat, vision, Whisper) + Neon Postgres memory.

## Features
- Persistent users, conversations and message history
- `/start /help /reset /about /status /admin`
- `/model` and `/persona` pickers (inline buttons, saved per user)
- `/remember /memories /forget` – long-term notes injected into every prompt
- `/summary`, `/export` (history as .txt), `/stats`
- Voice messages (Whisper) and photo analysis (vision model)
- Group chats: replies to @mentions, replies and commands
- Per-minute and daily rate limits, duplicate-update protection
- Tools: `/translate /explain /improve /tldr /regenerate /history /creativity /feedback`
- Admin (from Telegram, unlimited for admins): `/users /user /top /limit /unlimited /ban /unban /reply /broadcast /maintenance /setprompt`
- Webhook secret verification, long-reply splitting

## Deploy (3 steps)
1. Import this repo into Vercel.
2. Add env vars: `TELEGRAM_BOT_TOKEN`, `GROQ_API_KEY`, `DATABASE_URL` (Neon connection string). Optional: `ADMIN_IDS`.
3. Deploy, then open `https://YOUR-DOMAIN.vercel.app/api?action=setup` once.

`/setup` creates the database tables, registers the webhook (with an auto-generated secret) and installs the Telegram command menu. No SQL or curl needed.

## Local
```
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn api.index:app --reload
```
Groq model names change; verify `GROQ_MODEL`, `ALLOWED_MODELS` and `GROQ_VISION_MODEL` in your Groq console.
