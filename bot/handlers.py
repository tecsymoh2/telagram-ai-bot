import re
import time
from datetime import datetime, timedelta, timezone

from . import ai, config, db, security, techland
from . import telegram as tg

START_TIME = time.time()

HELP = """🤖 Commands

💬 Chat
/reset · /regenerate · /history · /summary · /export

🔎 Smart tools
/search <question> – web search
/imagine <prompt> – generate an image
/translate <language> <text>
/explain · /improve · /tldr (text or reply)

⏰ Reminders
/remind 30m <text> or /remind 18:30 <text>
/reminders · /cancelremind <id> · /timezone <+3>

⭐ Premium
/premium – plans & status · /subscribe <plan> <phone>

⚙️ Settings
/model · /persona · /creativity <0-1>

🧠 Memory
/remember <text> · /memories · /forget [id]

ℹ️ /stats · /status · /feedback <text> · /about

Also send 🎤 voice notes and 📷 photos."""

ADMIN_HELP = """🛠 Admin (no limits for you)

📊 /admin · /dashboard (web panel link)
👥 /users [page] · /user <id|@name> · /top
🚦 /limit <user> <n|default> · /unlimited <user>
⛔ /ban · /unban <user>
✉️ /reply <user> <text> · /broadcast <text>
🔧 /maintenance on|off · /setprompt <text|reset>

⭐ Premium & payments
/grant <user> <days> · /revoke <user>
/plans · /setplan <name> <price> <days> · /delplan <name>
/payments · /setpremiumlimit <n>
/setkey <api key> · /setbase <url> · /setwebhooksecret <secret>
/cronurl – link for reminders scheduler"""

TOOLS = {
    "/explain": "Explain the following clearly and simply, with an example if useful:\n\n{t}",
    "/improve": "Improve the following text: fix grammar, spelling and clarity while keeping the meaning and tone. Return only the improved text.\n\n{t}",
    "/tldr": "Summarize the following in 2-4 short bullet points:\n\n{t}",
}


# ------------------------------------------------------------------ helpers
def is_admin_id(uid):
    return uid in config.ADMIN_IDS


def is_premium(u):
    pu = u.get("premium_until")
    return is_admin_id(u["id"]) or bool(pu and pu > datetime.now(timezone.utc))


def premium_limit(c):
    v = db.get_setting(c, "premium_limit")
    return int(v) if v and v.isdigit() else config.PREMIUM_DAILY_LIMIT


def limit_for(c, user):
    if user.get("daily_limit") is not None:
        return user["daily_limit"]
    return premium_limit(c) if is_premium(user) else config.DAILY_LIMIT


def temp_for(user):
    return user["temperature"] if user.get("temperature") is not None else 0.7


def fmt_user(u):
    name = f"@{u['username']}" if u.get("username") else (u.get("first_name") or "—")
    return f"{name} ({u['id']})"


def system_for(c, user):
    return ai.build_system(user, db.list_notes(c, user["id"]), db.get_setting(c, "system_prompt"))


def check_limits(c, user, chat_id, reply_to):
    uid = user["id"]
    if is_admin_id(uid):
        return True
    if db.recent_user_messages(c, uid) >= config.RATE_LIMIT_PER_MIN:
        tg.send(chat_id, "⏳ Slow down a little — try again in a minute.", reply_to)
        return False
    lim = limit_for(c, user)
    if db.today_count(c, uid) >= lim:
        extra = "" if is_premium(user) else "\n⭐ Upgrade with /premium for a much higher limit."
        tg.send(chat_id, f"📊 Daily limit of {lim} messages reached. Come back tomorrow!{extra}", reply_to)
        return False
    return True


def feature_ok(c, user, chat_id, reply_to, kind):
    if is_admin_id(user["id"]):
        return True
    free, prem = {"image": (config.FREE_IMAGE_LIMIT, config.PREMIUM_IMAGE_LIMIT),
                  "search": (config.FREE_SEARCH_LIMIT, config.PREMIUM_SEARCH_LIMIT)}[kind]
    premium = is_premium(user)
    lim = prem if premium else free
    if db.usage_today(c, user["id"], kind) >= lim:
        extra = "" if premium else "\n⭐ Upgrade with /premium for more."
        tg.send(chat_id, f"You've used your {lim} daily {kind} requests.{extra}", reply_to)
        return False
    return check_limits(c, user, chat_id, reply_to)


# ------------------------------------------------------------------ updates
def handle_update(c, update):
    if "callback_query" in update:
        return handle_callback(c, update["callback_query"])
    msg = update.get("message")
    if not msg or "from" not in msg or msg["from"].get("is_bot"):
        return

    chat = msg["chat"]
    chat_id = chat["id"]
    is_group = chat["type"] in ("group", "supergroup")
    text = msg.get("text") or msg.get("caption") or ""

    if is_group:
        bot = tg.me()
        uname = bot.get("username", "")
        replied = (msg.get("reply_to_message") or {}).get("from", {}).get("id") == bot.get("id")
        mentioned = f"@{uname}".lower() in text.lower()
        if not (text.startswith("/") or mentioned or replied):
            return
        text = text.replace(f"@{uname}", "").strip()

    user = db.upsert_user(c, msg["from"])
    if user["is_banned"]:
        return
    admin = is_admin_id(user["id"])

    if text.startswith("/"):
        if user.get("pending_action"):
            db.set_user_field(c, user["id"], "pending_action", None)
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        return run_command(c, user, chat_id, msg, cmd, arg.strip(), admin)

    if db.get_setting(c, "maintenance") == "1" and not admin:
        return tg.send(chat_id, "🔧 The bot is under maintenance. Please try again soon.", msg["message_id"])

    # waiting for a phone number to start a premium payment
    pending = user.get("pending_action") or ""
    if pending.startswith("buy:") and text and not is_group:
        return start_checkout(c, user, chat_id, msg["message_id"], pending[4:], text)

    prefix = ""
    if msg.get("voice") or msg.get("audio"):
        tg.typing(chat_id)
        media = msg.get("voice") or msg.get("audio")
        try:
            text = ai.transcribe(tg.download(media["file_id"]))
        except ai.AIError as e:
            return tg.send(chat_id, f"⚠️ {e}", msg["message_id"])
        if not text:
            return tg.send(chat_id, "I couldn't hear anything in that voice message.", msg["message_id"])
        prefix = f"🎤 “{text}”\n\n"

    image = tg.download(msg["photo"][-1]["file_id"]) if msg.get("photo") else None
    if not text and image is None:
        return tg.send(chat_id, "Send me text, a voice message, or a photo 🙂", msg["message_id"])
    respond(c, user, chat_id, msg["message_id"], text, image, prefix)


def respond(c, user, chat_id, reply_to, text, image=None, prefix=""):
    uid = user["id"]
    if not check_limits(c, user, chat_id, reply_to):
        return
    tg.typing(chat_id)
    conv = db.active_conversation(c, uid)
    hist = db.history(c, conv, config.HISTORY_LIMIT)
    system = system_for(c, user)
    model = user["model"] or config.MODEL
    stored = f"[Photo] {text}".strip() if image else text
    try:
        if image:
            answer = ai.vision(system, hist, text, image, temp_for(user))
        else:
            answer = ai.chat(model, system, [*hist, {"role": "user", "content": text}], temperature=temp_for(user))
    except ai.AIError as e:
        return tg.send(chat_id, f"⚠️ {e}", reply_to)
    except Exception:
        return tg.send(chat_id, "⚠️ Something went wrong. Please try again.", reply_to)
    db.add_message(c, conv, uid, "user", stored)
    db.add_message(c, conv, uid, "assistant", answer)
    tg.send(chat_id, prefix + answer, reply_to)


def one_shot(c, user, chat_id, reply_to, prompt, kind="tool", model=None, system=None, gate=True):
    if gate and not (feature_ok(c, user, chat_id, reply_to, kind) if kind in ("image", "search")
                     else check_limits(c, user, chat_id, reply_to)):
        return
    tg.typing(chat_id)
    try:
        out = ai.chat(model or user["model"] or config.MODEL, system or system_for(c, user),
                      [{"role": "user", "content": prompt}], 1200, temp_for(user))
    except ai.AIError as e:
        return tg.send(chat_id, f"⚠️ {e}", reply_to)
    except Exception:
        return tg.send(chat_id, "⚠️ Something went wrong. Please try again.", reply_to)
    db.log_usage(c, user["id"], kind)
    tg.send(chat_id, out, reply_to)


# ------------------------------------------------------------------ premium
def premium_text(c, user):
    plans = db.get_plans(c)
    lines = []
    if is_admin_id(user["id"]):
        lines.append("⭐ You're an admin: all premium features, no limits.")
    elif is_premium(user):
        lines.append(f"⭐ Premium active until {user['premium_until']:%Y-%m-%d %H:%M} UTC")
    else:
        lines.append("You're on the free plan.")
    lines.append(f"\nPremium gives you:\n• {premium_limit(c)} messages/day (free: {config.DAILY_LIMIT})"
                 f"\n• {config.PREMIUM_IMAGE_LIMIT} images/day (free: {config.FREE_IMAGE_LIMIT})"
                 f"\n• {config.PREMIUM_SEARCH_LIMIT} web searches/day (free: {config.FREE_SEARCH_LIMIT})")
    if plans:
        lines.append("\nPlans (M-Pesa):")
        lines += [f"• {n}: KES {p['price']} for {p['days']} days" for n, p in plans.items()]
    return "\n".join(lines), plans


def start_checkout(c, user, chat_id, reply_to, plan, phone_text):
    uid = user["id"]
    plans = db.get_plans(c)
    p = plans.get(plan)
    if not p:
        db.set_user_field(c, uid, "pending_action", None)
        return tg.send(chat_id, "That plan doesn't exist. See /premium.", reply_to)
    phone = techland.normalize_phone(phone_text)
    if not phone:
        return tg.send(chat_id, "Please send a valid M-Pesa number like 0712345678 (or /cancel).", reply_to)
    ext = f"u{uid}-{plan}-{int(time.time())}"
    try:
        data = techland.create_payment(c, phone, p["price"], "PREMIUM", ext, ext)
    except techland.PayError as e:
        db.set_user_field(c, uid, "pending_action", None)
        return tg.send(chat_id, f"⚠️ {e}", reply_to)
    row = db.add_payment(c, data["id"], uid, plan, p["price"], p["days"], phone[-9:])
    db.set_user_field(c, uid, "pending_action", None)
    seq = row["seq"] if row else (db.get_payment(c, data["id"]) or {}).get("seq")
    tg.send(chat_id, f"📲 Check your phone and enter your M-Pesa PIN to pay KES {p['price']}.\n"
                     "Premium activates automatically. If it doesn't, tap the button below.",
            reply_to, keyboard=[[{"text": "✅ I've paid – check", "callback_data": f"chk:{seq}"}]])


def apply_payment(c, pid, receipt=None):
    row = c.execute("UPDATE payments SET status='SUCCESS', applied=TRUE, receipt=%s WHERE id=%s AND NOT applied RETURNING *",
                    (receipt, pid)).fetchone()
    if not row:
        return None
    until = db.grant_premium(c, row["user_id"], row["days"])["premium_until"]
    tg.call("sendMessage", chat_id=row["user_id"],
            text=f"🎉 Payment received! Premium active until {until:%Y-%m-%d %H:%M} UTC."
                 + (f"\nReceipt: {receipt}" if receipt else ""))
    for a in config.ADMIN_IDS:
        try:
            tg.call("sendMessage", chat_id=a, text=f"💰 New premium: user {row['user_id']} · {row['plan']} · KES {row['amount']}")
        except Exception:
            pass
    return row


def sync_payment(c, pid):
    """Re-check a payment with the payment API (never trust webhook content) and apply it."""
    row = db.get_payment(c, pid)
    if not row:
        return None, "Payment not found."
    if row["applied"]:
        return "SUCCESS", "Already active."
    try:
        p = techland.get_payment(c, pid)
    except techland.PayError as e:
        return row["status"], str(e)
    status = p.get("status", "PENDING")
    if status == "SUCCESS":
        apply_payment(c, pid, p.get("receipt"))
    elif status in ("FAILED", "CANCELLED"):
        c.execute("UPDATE payments SET status=%s, message=%s WHERE id=%s", (status, p.get("message"), pid))
    return status, p.get("message")


# ------------------------------------------------------------------ reminders
def parse_when(token, tz_offset):
    """'30m', '2h', '1d2h', or 'HH:MM' (user's local time). Returns UTC datetime or None."""
    now = datetime.now(timezone.utc)
    m = re.fullmatch(r"(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?", token.lower())
    if m and any(m.groups()):
        d, h, mi = (int(x or 0) for x in m.groups())
        delta = timedelta(days=d, hours=h, minutes=mi)
        return now + delta if delta.total_seconds() > 0 else None
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", token)
    if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
        local = now + timedelta(hours=tz_offset)
        target = local.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
        if target <= local:
            target += timedelta(days=1)
        return target - timedelta(hours=tz_offset)
    return None


def process_reminders(c):
    n = 0
    for r in db.due_reminders(c):
        try:
            tg.call("sendMessage", chat_id=r["chat_id"], text=f"⏰ Reminder: {r['text']}")
            n += 1
        except Exception:
            pass
    return n


# ------------------------------------------------------------------ commands
def run_command(c, user, chat_id, msg, cmd, arg, admin):
    uid, mid = user["id"], msg["message_id"]
    reply = lambda t, **kw: tg.send(chat_id, t, mid, **kw)
    private = msg["chat"]["type"] == "private"

    if db.get_setting(c, "maintenance") == "1" and not admin and cmd not in ("/start", "/help"):
        return reply("🔧 The bot is under maintenance. Please try again soon.")

    if cmd == "/start":
        return reply(f"👋 Hi {user.get('first_name') or 'there'}! I'm your AI assistant. Send a message, voice note, or photo.\n\nType /help to see what I can do.")
    if cmd == "/help":
        return reply(HELP + (("\n\n" + ADMIN_HELP) if admin else ""))
    if cmd == "/about":
        return reply("🤖 Telegram AI Bot\nGroq AI (chat, vision, voice, search) · Neon Postgres · Vercel.")
    if cmd == "/cancel":
        db.set_user_field(c, uid, "pending_action", None)
        return reply("Cancelled.")
    if cmd == "/reset":
        db.reset_conversation(c, uid)
        return reply("🧹 Conversation cleared. Fresh start!")

    # ---- premium ----
    if cmd == "/premium":
        text, plans = premium_text(c, user)
        kb = [[{"text": f"{n} · KES {p['price']}", "callback_data": f"buy:{n}"}] for n, p in plans.items()] if private else None
        return reply(text + ("" if private else "\n\nOpen a private chat with me to subscribe."), keyboard=kb)
    if cmd == "/subscribe":
        if not private:
            return reply("Please subscribe in a private chat with me.")
        plan, _, phone = arg.partition(" ")
        if plan not in db.get_plans(c):
            return reply("Usage: /subscribe <plan> <phone>. See /premium for plans.")
        if not phone.strip():
            db.set_user_field(c, uid, "pending_action", f"buy:{plan}")
            return reply("Send your M-Pesa number (e.g. 0712345678), or /cancel.")
        return start_checkout(c, user, chat_id, mid, plan, phone)

    # ---- smart tools ----
    if cmd == "/search":
        if not arg:
            return reply("Usage: /search <question>")
        return one_shot(c, user, chat_id, mid, arg, "search", config.SEARCH_MODEL,
                        "You are a research assistant. Use web search for current, accurate information. "
                        "Answer concisely and mention your sources.")
    if cmd == "/imagine":
        if not arg:
            return reply("Usage: /imagine a lion wearing a crown, digital art")
        if not feature_ok(c, user, chat_id, mid, "image"):
            return
        tg.action(chat_id, "upload_photo")
        try:
            img = ai.generate_image(arg)
        except ai.AIError as e:
            return reply(f"⚠️ {e}")
        except Exception:
            return reply("⚠️ Image generation failed. Please try again.")
        db.log_usage(c, uid, "image")
        return tg.send_photo(chat_id, img, f"🎨 {arg[:200]}")

    # ---- reminders ----
    if cmd == "/remind":
        when, _, body = arg.partition(" ")
        due = parse_when(when, user["tz_offset"]) if when else None
        if not due or not body.strip():
            return reply("Usage: /remind 30m call mum\nor /remind 18:30 take medicine (your time, see /timezone)")
        if len(db.list_reminders(c, uid)) >= 20:
            return reply("You have too many pending reminders (max 20).")
        rid = db.add_reminder(c, uid, chat_id, body.strip()[:500], due)
        local = due + timedelta(hours=user["tz_offset"])
        return reply(f"⏰ Reminder #{rid} set for {local:%a %d %b %H:%M} (UTC{user['tz_offset']:+d}).")
    if cmd == "/reminders":
        rows = db.list_reminders(c, uid)
        if not rows:
            return reply("No pending reminders.")
        return reply("⏰ Your reminders\n" + "\n".join(
            f"#{r['id']} · {(r['due_at'] + timedelta(hours=user['tz_offset'])):%d %b %H:%M} · {r['text']}" for r in rows))
    if cmd == "/cancelremind":
        ok = arg.isdigit() and db.cancel_reminder(c, uid, int(arg))
        return reply("🗑 Reminder cancelled." if ok else "Usage: /cancelremind <id> (see /reminders)")
    if cmd == "/timezone":
        try:
            off = int(arg.replace("+", ""))
            assert -12 <= off <= 14
        except Exception:
            return reply(f"Usage: /timezone +3  (hours from UTC, now {user['tz_offset']:+d})")
        db.set_user_field(c, uid, "tz_offset", off)
        return reply(f"🕒 Timezone set to UTC{off:+d}.")

    # ---- settings ----
    if cmd == "/model":
        if arg in config.ALLOWED_MODELS:
            db.set_user_field(c, uid, "model", arg)
            return reply(f"✅ Model set to {arg}")
        current = user["model"] or config.MODEL
        kb = [[{"text": ("✅ " if m == current else "") + m, "callback_data": f"model:{m}"}] for m in config.ALLOWED_MODELS]
        return reply("Choose a model:", keyboard=kb)
    if cmd == "/persona":
        if arg in config.PERSONAS:
            db.set_user_field(c, uid, "persona", arg)
            return reply(f"✅ Persona set to {arg}")
        names = list(config.PERSONAS)
        kb = [[{"text": ("✅ " if n == user["persona"] else "") + n, "callback_data": f"persona:{n}"} for n in names[i:i + 2]]
              for i in range(0, len(names), 2)]
        return reply("Choose a persona:", keyboard=kb)
    if cmd == "/creativity":
        try:
            v = float(arg)
            assert 0 <= v <= 1
        except Exception:
            return reply(f"Usage: /creativity 0.0–1.0 (now {temp_for(user)}).")
        db.set_user_field(c, uid, "temperature", v)
        return reply(f"✅ Creativity set to {v}")

    # ---- memory ----
    if cmd == "/remember":
        if not arg:
            return reply("Usage: /remember I'm vegetarian and live in Lagos")
        db.add_note(c, uid, arg[:500])
        return reply("🧠 Got it, I'll remember that.")
    if cmd == "/memories":
        notes = db.list_notes(c, uid)
        return reply("🧠 Your memories:\n" + "\n".join(f"{n['id']}. {n['content']}" for n in notes)
                     if notes else "No memories yet. Use /remember <text>.")
    if cmd == "/forget":
        n = db.delete_note(c, uid, int(arg)) if arg.isdigit() else db.delete_note(c, uid)
        return reply(f"🗑 Deleted {n} memor{'y' if n == 1 else 'ies'}.")

    # ---- chat tools ----
    if cmd == "/regenerate":
        last = db.pop_last_exchange(c, db.active_conversation(c, uid))
        if not last or last.startswith("[Photo]"):
            return reply("Nothing I can regenerate.")
        return respond(c, user, chat_id, mid, last)
    if cmd == "/history":
        rows = db.recent_messages(c, db.active_conversation(c, uid), 10)
        if not rows:
            return reply("No messages yet.")
        return reply("🕘 Last messages\n\n" + "\n\n".join(
            ("🧑 " if r["role"] == "user" else "🤖 ") + (r["content"][:200] + ("…" if len(r["content"]) > 200 else "")) for r in rows))
    if cmd == "/summary":
        hist = db.history(c, db.active_conversation(c, uid), 40)
        if not hist:
            return reply("Nothing to summarize yet.")
        convo = "\n".join(f"{m['role']}: {m['content']}" for m in hist)
        return one_shot(c, user, chat_id, mid, "Summarize this conversation in a few short bullet points:\n\n" + convo)
    if cmd == "/export":
        hist = db.history(c, db.active_conversation(c, uid), 1000)
        if not hist:
            return reply("Nothing to export yet.")
        body = "\n\n".join(f"{m['role'].upper()}: {m['content']}" for m in hist)
        return tg.send_document(chat_id, "conversation.txt", body.encode(), "💾 Your conversation")

    rt = msg.get("reply_to_message") or {}
    replied_text = rt.get("text") or rt.get("caption") or ""
    if cmd == "/translate":
        lang, _, body = arg.partition(" ")
        body = body.strip() or replied_text
        if not lang or not body:
            return reply("Usage: /translate <language> <text>  (or reply to a message)")
        return one_shot(c, user, chat_id, mid, f"Translate the following text to {lang}. Return only the translation.\n\n{body}")
    if cmd in TOOLS:
        body = arg or replied_text
        if not body:
            return reply(f"Usage: {cmd} <text>  (or reply to a message)")
        return one_shot(c, user, chat_id, mid, TOOLS[cmd].format(t=body))

    # ---- info ----
    if cmd == "/stats":
        s = db.user_stats(c, uid)
        first = s["first"].strftime("%Y-%m-%d") if s["first"] else "—"
        lim = "unlimited" if admin else limit_for(c, user)
        plan = "admin" if admin else ("premium ⭐" if is_premium(user) else "free")
        return reply(f"📊 Your stats\nPlan: {plan}\nMessages sent: {s['sent']}\n"
                     f"Today: {db.today_count(c, uid)}/{lim}\nFirst message: {first}\n"
                     f"Model: {user['model'] or config.MODEL}\nPersona: {user['persona']}\nCreativity: {temp_for(user)}")
    if cmd == "/status":
        t = time.time()
        c.execute("SELECT 1")
        return reply(f"✅ Online\nDatabase: ok ({int((time.time() - t) * 1000)} ms)\nModel: {config.MODEL}\n"
                     f"Uptime (this instance): {int(time.time() - START_TIME)}s")
    if cmd == "/feedback":
        if not arg:
            return reply("Usage: /feedback <your message>")
        db.add_feedback(c, uid, arg[:1000])
        for a in config.ADMIN_IDS:
            try:
                tg.call("sendMessage", chat_id=a, text=f"💬 Feedback from {fmt_user(user)}:\n{arg[:1000]}\n\nReply: /reply {uid} <text>")
            except Exception:
                pass
        return reply("🙏 Thanks, your feedback was sent.")

    ADMIN_CMDS = ("/admin", "/dashboard", "/users", "/user", "/top", "/limit", "/unlimited", "/ban", "/unban",
                  "/reply", "/broadcast", "/maintenance", "/setprompt", "/grant", "/revoke", "/plans", "/setplan",
                  "/delplan", "/payments", "/setpremiumlimit", "/setkey", "/setbase", "/setwebhooksecret", "/cronurl")
    if cmd in ADMIN_CMDS:
        if not admin:
            return reply("⛔ Admins only.")
        return run_admin(c, chat_id, msg, reply, cmd, arg)
    return reply("Unknown command. Try /help")


# ------------------------------------------------------------------ admin
def target_user(c, reply, arg):
    ident = arg.split(" ")[0] if arg else ""
    u = db.find_user(c, ident) if ident else None
    if not u:
        reply("User not found. Use their numeric ID or @username (they must have used the bot).")
    return u


def run_admin(c, chat_id, msg, reply, cmd, arg):
    uid = msg["from"]["id"]

    if cmd == "/admin":
        s = db.global_stats(c)
        rev = db.revenue(c)
        return reply(
            f"🛠 Admin panel\nUsers: {s['users']} (active 24h: {s['active_24h']}, banned: {s['banned']})\n"
            f"Messages: {s['messages']} (24h: {s['messages_24h']})\n"
            f"Premium users: {db.premium_count(c)} · Revenue: KES {rev['total']} (30d: {rev['last30']})\n"
            f"Payments API key: {'✅ set' if techland.configured(c) else '❌ not set (/setkey)'}\n"
            f"Maintenance: {'ON 🔧' if db.get_setting(c, 'maintenance') == '1' else 'off'} · "
            f"Prompt: {'custom' if db.get_setting(c, 'system_prompt') else 'default'}\n\n" + ADMIN_HELP)

    if cmd == "/dashboard":
        base = db.get_setting(c, "base_url")
        if not base:
            return reply("Open https://YOUR-DOMAIN/api?action=setup once, then try again.")
        exp = int(time.time()) + 3600
        return reply(f"🖥 Admin dashboard (link valid 1 hour, don't share):\n{base}/api?action=dashboard&uid={uid}&exp={exp}&sig={security.sign_dashboard(uid, exp)}")

    if cmd == "/cronurl":
        base = db.get_setting(c, "base_url")
        if not base:
            return reply("Open https://YOUR-DOMAIN/api?action=setup once, then try again.")
        return reply("⏰ Reminders need a scheduler. Create a free job on cron-job.org that calls this URL every minute:\n"
                     f"{base}/api?action=cron&key={security.cron_key()}\n\nKeep it private.")

    if cmd == "/users":
        page = int(arg) if arg.isdigit() and int(arg) > 0 else 1
        rows = db.list_users(c, 15, (page - 1) * 15)
        if not rows:
            return reply("No users on this page.")
        lines = [f"{'🚫' if u['is_banned'] else ('⭐' if is_premium(u) else '•')} {fmt_user(u)}" for u in rows]
        return reply(f"👥 Users (page {page})\n" + "\n".join(lines) + f"\n\nNext: /users {page + 1}")

    if cmd == "/top":
        rows = db.top_users(c, 10)
        return reply("🏆 Most active\n" + "\n".join(f"{i + 1}. {fmt_user(u)} – {u['n']} msgs" for i, u in enumerate(rows))
                     if rows else "No data yet.")

    if cmd == "/broadcast":
        if not arg:
            return reply("Usage: /broadcast <text>")
        ok = 0
        for target in db.all_user_ids(c):
            try:
                if tg.call("sendMessage", chat_id=target, text="📢 " + arg).get("ok"):
                    ok += 1
            except Exception:
                pass
        return reply(f"Sent to {ok} users.")

    if cmd == "/maintenance":
        if arg.lower() not in ("on", "off"):
            return reply("Usage: /maintenance on|off")
        db.set_setting(c, "maintenance", "1" if arg.lower() == "on" else "0")
        return reply(f"🔧 Maintenance {arg.lower()}.")

    if cmd == "/setprompt":
        if not arg:
            return reply("Usage: /setprompt <text> or /setprompt reset")
        if arg.lower() == "reset":
            db.set_setting(c, "system_prompt", None)
            return reply("✅ System prompt reset to default.")
        db.set_setting(c, "system_prompt", arg[:3000])
        return reply("✅ System prompt updated for everyone.")

    # ----- payments config -----
    if cmd == "/plans":
        plans = db.get_plans(c)
        return reply("⭐ Plans\n" + ("\n".join(f"• {n}: KES {p['price']} / {p['days']} days" for n, p in plans.items()) or "none")
                     + f"\n\nPremium daily limit: {premium_limit(c)}\nAdd/change: /setplan <name> <price> <days>")
    if cmd == "/setplan":
        parts = arg.split()
        if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit() or not re.fullmatch(r"[a-z0-9_]{1,20}", parts[0].lower()):
            return reply("Usage: /setplan <name> <price KES> <days>\nExample: /setplan weekly 50 7")
        price, days = int(parts[1]), int(parts[2])
        if not (1 <= price <= 250000 and 1 <= days <= 3650):
            return reply("Price must be 1–250000 KES and days 1–3650.")
        plans = db.get_plans(c)
        plans[parts[0].lower()] = {"price": price, "days": days}
        db.save_plans(c, plans)
        return reply(f"✅ Plan {parts[0].lower()}: KES {price} for {days} days.")
    if cmd == "/delplan":
        plans = db.get_plans(c)
        if arg.lower() not in plans:
            return reply("Plan not found. See /plans.")
        del plans[arg.lower()]
        db.save_plans(c, plans)
        return reply("🗑 Plan deleted.")
    if cmd == "/setpremiumlimit":
        if not arg.isdigit():
            return reply("Usage: /setpremiumlimit <messages per day>")
        db.set_setting(c, "premium_limit", arg)
        return reply(f"✅ Premium users now get {arg} messages/day.")
    if cmd == "/payments":
        rows = db.recent_payments(c, 12)
        if not rows:
            return reply("No payments yet.")
        icon = {"SUCCESS": "✅", "PENDING": "⏳", "FAILED": "❌", "CANCELLED": "🚫"}
        return reply("💳 Recent payments\n" + "\n".join(
            f"{icon.get(r['status'], '•')} {r['created_at']:%d %b %H:%M} · user {r['user_id']} · {r['plan']} · KES {r['amount']}" for r in rows))
    if cmd == "/setkey":
        if len(arg) < 12:
            return reply("Usage: /setkey tl_live_xxxxxxxx")
        db.set_setting(c, "techland_key", arg.strip())
        tg.delete_message(chat_id, msg["message_id"])  # remove the key from the chat
        return tg.send(chat_id, "🔑 Payment API key saved (your message was deleted for safety).")
    if cmd == "/setbase":
        if not arg.startswith("https://"):
            return reply("Usage: /setbase https://techland-stk.vercel.app")
        db.set_setting(c, "techland_base", arg.rstrip("/"))
        return reply("✅ Payment API base URL saved.")
    if cmd == "/setwebhooksecret":
        if len(arg) < 8:
            return reply("Usage: /setwebhooksecret <secret from your Techland dashboard>")
        db.set_setting(c, "techland_webhook_secret", arg.strip())
        tg.delete_message(chat_id, msg["message_id"])
        base = db.get_setting(c, "base_url") or "https://YOUR-DOMAIN"
        return tg.send(chat_id, f"🔐 Webhook secret saved. In Techland set the webhook URL to:\n{base}/api?action=techland")

    # ----- commands needing a target user -----
    u = target_user(c, reply, arg)
    if not u:
        return
    rest = arg.partition(" ")[2].strip()

    if cmd == "/user":
        s = db.user_stats(c, u["id"])
        lim = "default" if u["daily_limit"] is None else u["daily_limit"]
        prem = f"until {u['premium_until']:%Y-%m-%d}" if is_premium(u) and u.get("premium_until") else "no"
        return reply(f"👤 {fmt_user(u)}\nName: {u.get('first_name') or '—'}\nBanned: {u['is_banned']} · Premium: {prem}\n"
                     f"Model: {u['model'] or 'default'} · Persona: {u['persona']}\nDaily limit: {lim}\n"
                     f"Messages sent: {s['sent']} (today {db.today_count(c, u['id'])})\n"
                     f"Joined: {u['created_at']:%Y-%m-%d} · Last seen: {u['last_seen']:%Y-%m-%d %H:%M}")
    if cmd in ("/ban", "/unban"):
        db.set_user_field(c, u["id"], "is_banned", cmd == "/ban")
        return reply(f"{'🚫 Banned' if cmd == '/ban' else '✅ Unbanned'} {fmt_user(u)}")
    if cmd == "/unlimited":
        db.set_user_field(c, u["id"], "daily_limit", 1000000)
        return reply(f"♾ {fmt_user(u)} now has no daily limit.")
    if cmd == "/limit":
        if rest.lower() == "default":
            db.set_user_field(c, u["id"], "daily_limit", None)
            return reply(f"✅ {fmt_user(u)} back to the default limit.")
        if not rest.isdigit():
            return reply("Usage: /limit <id|@name> <number|default>")
        db.set_user_field(c, u["id"], "daily_limit", int(rest))
        return reply(f"✅ {fmt_user(u)} daily limit = {rest}")
    if cmd == "/grant":
        if not rest.isdigit() or not (1 <= int(rest) <= 3650):
            return reply("Usage: /grant <id|@name> <days>")
        until = db.grant_premium(c, u["id"], int(rest))["premium_until"]
        try:
            tg.call("sendMessage", chat_id=u["id"], text=f"🎁 You've been given Premium until {until:%Y-%m-%d}!")
        except Exception:
            pass
        return reply(f"⭐ {fmt_user(u)} premium until {until:%Y-%m-%d}.")
    if cmd == "/revoke":
        db.revoke_premium(c, u["id"])
        return reply(f"Premium removed from {fmt_user(u)}.")
    if cmd == "/reply":
        if not rest:
            return reply("Usage: /reply <id|@name> <text>")
        ok = tg.call("sendMessage", chat_id=u["id"], text="💬 Admin: " + rest).get("ok")
        return reply("✅ Sent." if ok else "⚠️ Could not deliver (user may have blocked the bot).")


# ------------------------------------------------------------------ callbacks
def handle_callback(c, cq):
    data = cq.get("data", "")
    user = db.upsert_user(c, cq["from"])
    if user["is_banned"]:
        return tg.answer_callback(cq["id"])
    kind, _, value = data.partition(":")
    msg = cq.get("message")
    chat_id = msg["chat"]["id"] if msg else user["id"]

    if kind == "model" and value in config.ALLOWED_MODELS:
        db.set_user_field(c, user["id"], "model", value)
        tg.answer_callback(cq["id"], f"Model: {value}")
        return tg.send(chat_id, f"✅ Model set to {value}")
    if kind == "persona" and value in config.PERSONAS:
        db.set_user_field(c, user["id"], "persona", value)
        tg.answer_callback(cq["id"], f"Persona: {value}")
        return tg.send(chat_id, f"✅ Persona set to {value}")
    if kind == "buy":
        p = db.get_plans(c).get(value)
        if not p:
            return tg.answer_callback(cq["id"], "Plan not available")
        db.set_user_field(c, user["id"], "pending_action", f"buy:{value}")
        tg.answer_callback(cq["id"])
        return tg.send(chat_id, f"💳 {value}: KES {p['price']} for {p['days']} days.\n"
                                "Send your M-Pesa number (e.g. 0712345678), or /cancel.")
    if kind == "chk" and value.isdigit():
        row = db.get_payment_by_seq(c, int(value))
        if not row or row["user_id"] != user["id"]:
            return tg.answer_callback(cq["id"], "Payment not found")
        status, message = sync_payment(c, row["id"])
        if status == "SUCCESS":
            return tg.answer_callback(cq["id"], "Premium active! 🎉")
        if status in ("FAILED", "CANCELLED"):
            tg.answer_callback(cq["id"])
            return tg.send(chat_id, f"❌ Payment {status.lower()}. {message or ''}\nTry again with /premium.")
        return tg.answer_callback(cq["id"], "Still waiting for payment…")
    tg.answer_callback(cq["id"])
