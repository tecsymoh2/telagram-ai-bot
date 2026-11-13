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

## Premium, dashboard, reminders
- **Premium** is paid by M-Pesa STK Push through the Techland API (https://techland-stk.vercel.app/docs). Set the key in Telegram with `/setkey <key>` (stored in the DB, replace anytime). Plans: `/setplan weekly 50 7`.
- In Techland, set the webhook URL to `https://YOUR-DOMAIN/api?action=techland` and save the secret with `/setwebhooksecret`. Users can also tap "I've paid" to check.
- **Dashboard**: send `/dashboard` to the bot for a one-hour admin link.
- **Reminders** need a scheduler: `/cronurl` gives a URL to call every minute (free on cron-job.org).

## More features
- **Referrals** `/invite`, **daily streak** `/daily` (7 days = +1 premium day), **promo codes** (`/newcode`, `/redeem`)
- **Document chat**: send a PDF/DOCX/TXT file, then ask questions (`/doc`, `/cleardoc`)
- **Groups**: `/groupmode`, `/grouppersona`, `/welcome`, `/groupinfo` (group admins). Mode "all" needs privacy mode off in @BotFather.
- **To-do list**, `/quiz`, `/language`, **premium expiry notices** (sent by the cron job), admin `/exportusers` CSV, dashboard charts.

## Even more
- 👍 👎 🔄 buttons under answers (ratings shown on the dashboard), `/deleteme` privacy erase
- `/read <link>`, `/weather`, `/convert`, `/qr`, `/password`, `/dice`, `/coin`, `/leaderboard`
- Admin: `/lock image|search|doc on` (premium only), `/broadcastto premium|free|inactive|all`, `/setstart`

## Local
```
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn api.index:app --reload
```
Groq model names change; verify `GROQ_MODEL`, `ALLOWED_MODELS` and `GROQ_VISION_MODEL` in your Groq console.
