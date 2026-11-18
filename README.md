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

## Reply in your personal chats (Telegram Business)
1. @BotFather → your bot → Bot Settings → Business Mode → turn on.
2. Re-open `/api?action=setup` once after deploying.
3. In Telegram (needs Premium): Settings → Business → Chatbots → pick the bot, choose chats, allow "Reply to messages".
4. In the bot: `/business`, `/bizmode draft|auto|off`, `/bizprompt <instructions>`, `/bizdisclose on|off`.
Draft mode (default) shows you a suggested reply with Send/Skip buttons. When you type in a chat yourself, the bot pauses there for 30 minutes.

## v4 additions
- **Business mode extras:** working hours + away message (`/bizhours`, `/bizaway`), FAQ training (`/bizfaq` text or file), appointment booking with
  conflict checks and reminders (`/bizbooking`, `/appointments`, `/cancelappt`), per-customer memory, team inbox for hard questions (`/bizteam` in a group),
  experimental voice replies (`/bizvoice`, needs a Groq voice model; set `GROQ_TTS_MODEL` / `GROQ_TTS_VOICE` if the defaults change).
- **Group moderation:** `/antilink`, `/badwords`, `/warnlimit` (bot must be a group admin).
- **Image tools:** `/sticker`, `/enhance`, `/removebg` (needs a remove.bg key: `/setremovebg`).
- **Staff roles:** `/addstaff <id> <manager|support>`; **backups:** `/backup`, `/setbackup`; **alerts:** errors and failed health checks are sent to owners.
The cron job (`/cronurl`) now also sends appointment reminders, backups and health checks.

## Button menu & colours
- `/menu` (or the bottom keyboard) opens every feature as buttons: Chat, Tools, Create, Organise, Settings, Premium, Rewards, Business, Groups, Help, Admin.
  Buttons that need text ask for it first; staff-only buttons appear by role.
- `/colors` (managers/owner): recolour the main menu, a whole section at once (`cat_…`), or any single button (`b_…`), the bottom keyboard, answer buttons and more.
  Shortcuts: `/setcolor cat_tools green`, `/setcolor b_srch red`, `/setcolor b_srch inherit`, `/resetcolors`.

## Local
```
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn api.index:app --reload
```
Groq model names change; verify `GROQ_MODEL`, `ALLOWED_MODELS` and `GROQ_VISION_MODEL` in your Groq console.
