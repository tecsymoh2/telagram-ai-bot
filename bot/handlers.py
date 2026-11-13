import re
import time
from datetime import datetime, timedelta, timezone

import csv
import io

import random
import secrets
import string

from . import ai, config, db, files, security, techland, utils, web
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
/quiz <topic> – make a quiz
/read <link> – summarize a web page
/weather <city> · /convert 100 usd kes
/qr <text> · /password [length] · /dice [2d6] · /coin

📄 Documents
Send a PDF/DOCX/TXT file, then ask questions.
/doc – current file · /cleardoc

⏰ Reminders & tasks
/remind 30m <text> · /reminders · /cancelremind <id>
/todo add <text> · /todo · /todo done <id> · /todo clear
/timezone <+3>

⭐ Premium & rewards
/premium · /subscribe <plan> <phone>
/invite – earn free premium days
/referrals · /daily · /redeem <code> · /leaderboard

⚙️ Settings
/model · /persona · /creativity <0-1> · /language <name>

🧠 Memory
/remember <text> · /memories · /forget [id]

👥 Groups (group admins)
/groupmode mention|all|off · /grouppersona <name>
/welcome <text|off> · /groupinfo

ℹ️ /stats · /status · /feedback <text> · /about
🔒 /deleteme – erase your chat data

Tip: tap 👍 👎 🔄 under answers to rate or redo them."""

ADMIN_HELP = """🛠 Admin (no limits for you)

📊 /admin · /dashboard (web panel link)
👥 /users [page] · /user <id|@name> · /top · /exportusers
🚦 /limit <user> <n|default> · /unlimited <user>
⛔ /ban · /unban <user>
✉️ /reply <user> <text> · /broadcast <text>
📣 /broadcastto <all|premium|free|inactive> <text>
🔒 /lock <image|search|doc> on|off (premium only) · /setstart <text|reset>
🔧 /maintenance on|off · /setprompt <text|reset>

⭐ Premium & payments
/grant <user> <days> · /revoke <user>
/plans · /setplan <name> <price> <days> · /delplan <name>
/payments · /setpremiumlimit <n>
/setkey <api key> · /setbase <url> · /setwebhooksecret <secret>

🎁 Growth
/newcode <CODE> <days> <max uses> · /codes · /delcode <CODE>
/setrefdays <referrer days> [invitee days]
/cronurl – scheduler link (reminders + expiry notices)"""

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


def system_for(c, user, with_doc=False, persona=None):
    u = dict(user)
    if persona:
        u["persona"] = persona
    system = ai.build_system(u, db.list_notes(c, user["id"]), db.get_setting(c, "system_prompt"))
    if with_doc:
        d = db.get_document(c, user["id"])
        if d:
            system += (f'\n\nThe user uploaded a document named "{d["name"]}". Use it when relevant:\n'
                       f'<document>\n{d["content"]}\n</document>')
    return system


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
    if db.get_setting(c, f"lock_{kind}") == "1" and not is_premium(user):
        tg.send(chat_id, f"🔒 {kind.capitalize()} is a Premium feature. Upgrade with /premium.", reply_to)
        return False
    free, prem = {"image": (config.FREE_IMAGE_LIMIT, config.PREMIUM_IMAGE_LIMIT),
                  "search": (config.FREE_SEARCH_LIMIT, config.PREMIUM_SEARCH_LIMIT),
                  "doc": (config.FREE_DOC_LIMIT, config.PREMIUM_DOC_LIMIT)}[kind]
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
    if msg and msg.get("new_chat_members") and msg["chat"]["type"] in ("group", "supergroup"):
        return welcome_members(c, msg)
    if not msg or "from" not in msg or msg["from"].get("is_bot"):
        return

    chat = msg["chat"]
    chat_id = chat["id"]
    is_group = chat["type"] in ("group", "supergroup")
    text = msg.get("text") or msg.get("caption") or ""

    group_persona = None
    if is_group:
        bot = tg.me()
        uname = bot.get("username", "")
        chat_row = db.get_chat(c, chat_id, chat.get("title"))
        group_persona = chat_row["persona"]
        replied = (msg.get("reply_to_message") or {}).get("from", {}).get("id") == bot.get("id")
        mentioned = f"@{uname}".lower() in text.lower()
        is_cmd = text.startswith("/")
        if chat_row["mode"] == "off" and not is_cmd:
            return
        if not (is_cmd or mentioned or replied or (chat_row["mode"] == "all" and text)):
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

    if msg.get("document"):
        return handle_document(c, user, chat_id, msg, text, group_persona)

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
    respond(c, user, chat_id, msg["message_id"], text, image, prefix, group_persona)


def welcome_members(c, msg):
    chat_id = msg["chat"]["id"]
    row = db.get_chat(c, chat_id, msg["chat"].get("title"))
    if not row["welcome"]:
        return
    bot_id = tg.me().get("id")
    for m in msg["new_chat_members"]:
        if m.get("is_bot") or m.get("id") == bot_id:
            continue
        name = m.get("first_name") or "friend"
        tg.send(chat_id, row["welcome"].replace("{name}", name))


def handle_document(c, user, chat_id, msg, caption, persona=None):
    mid = msg["message_id"]
    doc = msg["document"]
    name = doc.get("file_name") or "file"
    if (doc.get("file_size") or 0) > 5_000_000:
        return tg.send(chat_id, "That file is too large (max 5 MB).", mid)
    if not feature_ok(c, user, chat_id, mid, "doc"):
        return
    tg.typing(chat_id)
    try:
        text = files.extract_text(name, tg.download(doc["file_id"]))
    except ValueError as e:
        return tg.send(chat_id, str(e), mid)
    except Exception:
        return tg.send(chat_id, "I couldn't read that file. Is it a scanned/protected PDF?", mid)
    text = text.strip()
    if not text:
        return tg.send(chat_id, "I couldn't find any text in that file.", mid)
    cap = 20000 if is_premium(user) else 8000
    db.save_document(c, user["id"], name, text[:cap])
    db.log_usage(c, user["id"], "doc")
    if caption:
        return respond(c, user, chat_id, mid, caption, persona=persona)
    note = " (shortened to fit)" if len(text) > cap else ""
    tg.send(chat_id, f"📄 Loaded “{name}”{note}. Ask me anything about it!\n/cleardoc to remove it.", mid)


def respond(c, user, chat_id, reply_to, text, image=None, prefix="", persona=None):
    uid = user["id"]
    if not check_limits(c, user, chat_id, reply_to):
        return
    tg.typing(chat_id)
    conv = db.active_conversation(c, uid)
    hist = db.history(c, conv, config.HISTORY_LIMIT)
    system = system_for(c, user, True, persona)
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
    answer_id = db.add_message(c, conv, uid, "assistant", answer)
    kb = [[{"text": "👍", "callback_data": f"fb:1:{answer_id}"}, {"text": "👎", "callback_data": f"fb:0:{answer_id}"},
           {"text": "🔄", "callback_data": "rg"}]] if chat_id > 0 else None
    tg.send(chat_id, prefix + answer, reply_to, keyboard=kb)


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
                 f"\n• {config.PREMIUM_SEARCH_LIMIT} web searches/day (free: {config.FREE_SEARCH_LIMIT})"
                 f"\n• {config.PREMIUM_DOC_LIMIT} document uploads/day (free: {config.FREE_DOC_LIMIT}) with longer files")
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


def process_expiries(c):
    n = 0
    for u in db.expiring_users(c):
        tg.call("sendMessage", chat_id=u["id"],
                text=f"⏳ Your Premium ends on {u['premium_until']:%Y-%m-%d %H:%M} UTC. Renew anytime with /premium.")
        db.mark_notified(c, u["id"], "expiry_notified", u["premium_until"])
        n += 1
    for u in db.expired_users(c):
        tg.call("sendMessage", chat_id=u["id"],
                text="Your Premium has expired. You're back on the free plan. Get it back with /premium 💛")
        db.mark_notified(c, u["id"], "expired_notified", u["premium_until"])
        n += 1
    return n


def can_manage_chat(chat_id, uid):
    if is_admin_id(uid):
        return True
    r = tg.call("getChatMember", chat_id=chat_id, user_id=uid).get("result") or {}
    return r.get("status") in ("creator", "administrator")


# ------------------------------------------------------------------ commands
def run_command(c, user, chat_id, msg, cmd, arg, admin):
    uid, mid = user["id"], msg["message_id"]
    reply = lambda t, **kw: tg.send(chat_id, t, mid, **kw)
    private = msg["chat"]["type"] == "private"

    if db.get_setting(c, "maintenance") == "1" and not admin and cmd not in ("/start", "/help"):
        return reply("🔧 The bot is under maintenance. Please try again soon.")

    if cmd == "/start":
        if arg.startswith("ref_") and arg[4:].isdigit() and user.get("is_new") and private:
            apply_referral(c, user, int(arg[4:]))
        custom = db.get_setting(c, "start_text")
        if custom:
            return reply(custom.replace("{name}", user.get("first_name") or "there"))
        return reply(f"👋 Hi {user.get('first_name') or 'there'}! I'm your AI assistant. Send a message, voice note, photo, or document.\n\nType /help to see what I can do.")
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

    # ---- growth: invite, daily, promo ----
    if cmd == "/invite":
        d = int(db.get_setting(c, "ref_days") or config.DEFAULT_REF_DAYS)
        di = int(db.get_setting(c, "ref_invitee_days") or config.DEFAULT_REF_INVITEE_DAYS)
        link = f"https://t.me/{tg.me().get('username')}?start=ref_{uid}"
        return reply(f"🎁 Invite friends!\nYou get {d} days of Premium for each friend who joins, and they get {di} day(s) free.\n\nYour link:\n{link}")
    if cmd == "/referrals":
        return reply(f"👥 Friends you've invited: {db.referral_count(c, uid)}")
    if cmd == "/daily":
        today = (datetime.now(timezone.utc) + timedelta(hours=user["tz_offset"])).date()
        last = user.get("last_daily")
        if last == today:
            return reply(f"✅ Already claimed today. Current streak: {user['streak']} 🔥")
        streak = user["streak"] + 1 if last == today - timedelta(days=1) else 1
        db.update_daily(c, uid, today, streak)
        extra = ""
        if streak % 7 == 0:
            until = db.grant_premium(c, uid, 1)["premium_until"]
            extra = f"\n🎉 7-day streak! +1 day of Premium (until {until:%Y-%m-%d})."
        return reply(f"🔥 Daily check-in: day {streak} in a row. Come back tomorrow!{extra}")
    if cmd == "/redeem":
        if not arg:
            return reply("Usage: /redeem <code>")
        days, err = db.redeem_code(c, arg.strip().upper())
        if err:
            return reply("⚠️ " + err)
        until = db.grant_premium(c, uid, days)["premium_until"]
        return reply(f"🎉 Code accepted! +{days} days of Premium (until {until:%Y-%m-%d}).")

    # ---- productivity ----
    if cmd == "/todo":
        sub_cmd, _, rest = arg.partition(" ")
        rest = rest.strip()
        if sub_cmd == "add" and rest:
            if db.count_todos(c, uid) >= 50:
                return reply("Your list is full (50). Use /todo clear to remove finished tasks.")
            return reply(f"✅ Added #{db.add_todo(c, uid, rest[:300])}")
        if sub_cmd == "done" and rest.isdigit():
            return reply("☑️ Done!" if db.finish_todo(c, uid, int(rest)) else "Task not found.")
        if sub_cmd == "clear":
            return reply(f"🧹 Removed {db.clear_todos(c, uid)} finished task(s).")
        rows = db.list_todos(c, uid)
        if not rows:
            return reply("Your to-do list is empty.\nAdd: /todo add buy milk")
        return reply("📝 To-do\n" + "\n".join(f"{'☑️' if r['done'] else '⬜'} #{r['id']} {r['text']}" for r in rows)
                     + "\n\n/todo done <id> · /todo clear")
    if cmd == "/quiz":
        if not arg:
            return reply("Usage: /quiz <topic>")
        return one_shot(c, user, chat_id, mid, f"Create a fun 5-question multiple-choice quiz about: {arg}. "
                        "Number the questions, give options A-D, and put the answer key at the end under 'Answers'.")
    if cmd == "/language":
        if not arg:
            return reply(f"Usage: /language Swahili  (or /language reset). Now: {user.get('language') or 'automatic'}")
        db.set_user_field(c, uid, "language", None if arg.lower() == "reset" else arg[:30])
        return reply("🌍 Language reset to automatic." if arg.lower() == "reset" else f"🌍 I'll reply in {arg[:30]}.")

    # ---- documents ----
    if cmd == "/doc":
        d = db.get_document(c, uid)
        return reply(f"📄 Current document: “{d['name']}” ({len(d['content'])} chars). Ask me about it, or /cleardoc."
                     if d else "No document loaded. Send me a PDF, DOCX or text file.")
    if cmd == "/cleardoc":
        return reply("🗑 Document removed." if db.clear_document(c, uid) else "No document loaded.")

    # ---- groups ----
    if cmd in ("/groupmode", "/grouppersona", "/welcome", "/groupinfo"):
        if private:
            return reply("These commands work inside a group.")
        row = db.get_chat(c, chat_id, msg["chat"].get("title"))
        if cmd == "/groupinfo":
            return reply(f"👥 Group settings\nMode: {row['mode']}\nPersona: {row['persona'] or 'default'}\n"
                         f"Welcome: {row['welcome'] or 'off'}\n\nMode 'all' needs privacy mode turned off in @BotFather.")
        if not can_manage_chat(chat_id, uid):
            return reply("⛔ Only group admins can change this.")
        if cmd == "/groupmode":
            if arg not in ("mention", "all", "off"):
                return reply("Usage: /groupmode mention|all|off\n• mention: reply to @mentions and replies\n• all: reply to every message\n• off: only commands")
            db.set_chat_field(c, chat_id, "mode", arg)
            return reply(f"✅ Group mode: {arg}")
        if cmd == "/grouppersona":
            if arg.lower() == "reset":
                db.set_chat_field(c, chat_id, "persona", None)
                return reply("✅ Group persona reset.")
            if arg not in config.PERSONAS:
                return reply("Usage: /grouppersona <" + "|".join(config.PERSONAS) + "|reset>")
            db.set_chat_field(c, chat_id, "persona", arg)
            return reply(f"✅ Group persona: {arg}")
        if not arg:
            return reply("Usage: /welcome Welcome {name}! (or /welcome off)")
        db.set_chat_field(c, chat_id, "welcome", None if arg.lower() == "off" else arg[:500])
        return reply("✅ Welcome message " + ("disabled." if arg.lower() == "off" else "saved."))

    # ---- utilities ----
    if cmd == "/read":
        url = arg or next(iter(re.findall(r"https?://\S+", (msg.get("reply_to_message") or {}).get("text", ""))), "")
        url = url.split()[0] if url else ""
        if not url:
            return reply("Usage: /read https://example.com/article")
        if not check_limits(c, user, chat_id, mid):
            return
        tg.typing(chat_id)
        try:
            page = web.fetch_text(url)
        except web.FetchError as e:
            return reply(f"⚠️ {e}")
        except Exception:
            return reply("⚠️ I couldn't open that link.")
        return one_shot(c, user, chat_id, mid, "Summarize this web page in 5 short bullet points, then add one line on why it matters:\n\n" + page, gate=False)
    if cmd == "/weather":
        if not arg:
            return reply("Usage: /weather Nairobi")
        if not check_limits(c, user, chat_id, mid):
            return
        try:
            out = utils.weather(arg)
        except Exception:
            return reply("⚠️ Weather service is unavailable right now.")
        db.log_usage(c, uid, "tool")
        return reply(out or "I couldn't find that place.")
    if cmd == "/convert":
        m = re.fullmatch(r"([\d.,]+)\s*([A-Za-z]{3})\s*(?:to|in|->)?\s*([A-Za-z]{3})", arg.strip())
        if not m:
            return reply("Usage: /convert 100 usd kes")
        if not check_limits(c, user, chat_id, mid):
            return
        try:
            res = utils.convert(float(m.group(1).replace(",", "")), m.group(2), m.group(3))
        except Exception:
            res = None
        if not res:
            return reply("⚠️ I couldn't convert that. Check the currency codes (e.g. USD, KES, EUR).")
        db.log_usage(c, uid, "tool")
        return reply(f"💱 {m.group(1)} {m.group(2).upper()} = {res[0]:,.2f} {m.group(3).upper()}\n(rate {res[1]:,.4f})")
    if cmd == "/qr":
        if not arg:
            return reply("Usage: /qr https://example.com")
        if not check_limits(c, user, chat_id, mid):
            return
        png = utils.qr_png(arg)
        if not png:
            return reply("⚠️ QR service unavailable. Try again later.")
        db.log_usage(c, uid, "tool")
        return tg.send_photo(chat_id, png, "🔳 " + arg[:100])
    if cmd == "/password":
        n = int(arg) if arg.isdigit() else 16
        n = max(8, min(n, 64))
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*-_"
        pw = "".join(secrets.choice(alphabet) for _ in range(n))
        return reply(f"🔐 {pw}\n\nGenerated on the server and not stored. Delete this message after copying.")
    if cmd == "/dice":
        m = re.fullmatch(r"(\d{1,2})d(\d{1,3})", arg.lower()) if arg else None
        n, sides = (int(m.group(1)), int(m.group(2))) if m else (1, 6)
        if n < 1 or sides < 2:
            return reply("Usage: /dice 2d6")
        rolls = [random.randint(1, sides) for _ in range(n)]
        return reply(f"🎲 {n}d{sides}: {', '.join(map(str, rolls))}" + (f" = {sum(rolls)}" if n > 1 else ""))
    if cmd == "/coin":
        return reply("🪙 " + random.choice(["Heads", "Tails"]))
    if cmd == "/leaderboard":
        streaks, inviters = db.top_streaks(c, 5), db.top_inviters(c, 5)
        nm = lambda r: r.get("first_name") or "Anonymous"
        out = ["🏆 Leaderboard", "", "🔥 Longest active streaks"]
        out += [f"{i + 1}. {nm(r)} – {r['streak']} days" for i, r in enumerate(streaks)] or ["No streaks yet. Start with /daily!"]
        out += ["", "👥 Top inviters"]
        out += [f"{i + 1}. {nm(r)} – {r['n']} friends" for i, r in enumerate(inviters)] or ["No invites yet. Share /invite!"]
        return reply("\n".join(out))
    if cmd == "/deleteme":
        return reply("⚠️ This permanently erases your chats, memories, documents, reminders and to-dos. "
                     "Your Premium and payment records stay. Continue?",
                     keyboard=[[{"text": "Yes, erase my data", "callback_data": "del:yes"}, {"text": "Cancel", "callback_data": "del:no"}]])

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
                  "/delplan", "/payments", "/setpremiumlimit", "/setkey", "/setbase", "/setwebhooksecret", "/cronurl",
                  "/setrefdays", "/newcode", "/codes", "/delcode", "/exportusers", "/lock", "/broadcastto", "/setstart")
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

    # ----- controls -----
    if cmd == "/lock":
        parts = arg.lower().split()
        if len(parts) != 2 or parts[0] not in ("image", "search", "doc") or parts[1] not in ("on", "off"):
            return reply("Usage: /lock <image|search|doc> on|off\n(on = premium users only)")
        db.set_setting(c, f"lock_{parts[0]}", "1" if parts[1] == "on" else "0")
        return reply(f"🔒 {parts[0]} is now {'premium-only' if parts[1] == 'on' else 'available to everyone (with daily limits)'}.")
    if cmd == "/broadcastto":
        seg, _, text = arg.partition(" ")
        if seg not in ("all", "premium", "free", "inactive") or not text.strip():
            return reply("Usage: /broadcastto <all|premium|free|inactive> <text>")
        ids = db.user_ids_segment(c, seg)
        ok = 0
        for target in ids:
            try:
                if tg.call("sendMessage", chat_id=target, text="📢 " + text.strip()).get("ok"):
                    ok += 1
            except Exception:
                pass
        return reply(f"Sent to {ok}/{len(ids)} users ({seg}).")
    if cmd == "/setstart":
        if not arg:
            return reply("Usage: /setstart <text> (use {name}) or /setstart reset")
        db.set_setting(c, "start_text", None if arg.lower() == "reset" else arg[:1500])
        return reply("✅ Start message " + ("reset." if arg.lower() == "reset" else "saved."))

    # ----- growth -----
    if cmd == "/setrefdays":
        parts = arg.split()
        if not parts or not all(x.isdigit() for x in parts) or len(parts) > 2:
            return reply("Usage: /setrefdays <referrer days> [invitee days]  (0 disables)")
        db.set_setting(c, "ref_days", parts[0])
        if len(parts) == 2:
            db.set_setting(c, "ref_invitee_days", parts[1])
        return reply(f"✅ Referral reward: referrer {parts[0]} day(s)" + (f", invitee {parts[1]} day(s)." if len(parts) == 2 else "."))
    if cmd == "/newcode":
        parts = arg.split()
        if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit() or not re.fullmatch(r"[A-Za-z0-9_-]{3,24}", parts[0]):
            return reply("Usage: /newcode <CODE> <days> <max uses>\nExample: /newcode WELCOME7 7 100")
        db.create_code(c, parts[0].upper(), int(parts[1]), int(parts[2]))
        return reply(f"🎁 Code {parts[0].upper()}: {parts[1]} days, {parts[2]} uses. Users redeem with /redeem {parts[0].upper()}")
    if cmd == "/codes":
        rows = db.list_codes(c)
        return reply("🎁 Promo codes\n" + "\n".join(f"{r['code']}: {r['days']}d · {r['used']}/{r['max_uses']} used" for r in rows)
                     if rows else "No codes yet. Create one with /newcode.")
    if cmd == "/delcode":
        return reply("🗑 Deleted." if db.delete_code(c, arg.strip().upper()) else "Code not found.")
    if cmd == "/exportusers":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "username", "first_name", "joined", "last_seen", "premium_until", "banned"])
        for u in db.all_users(c):
            w.writerow([u["id"], u["username"], u["first_name"], u["created_at"], u["last_seen"], u["premium_until"], u["is_banned"]])
        return tg.send_document(chat_id, "users.csv", buf.getvalue().encode(), "👥 All users")

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


def apply_referral(c, user, referrer_id):
    if referrer_id == user["id"]:
        return
    ref = db.find_user(c, str(referrer_id))
    if not ref or ref["is_banned"]:
        return
    db.set_user_field(c, user["id"], "referred_by", referrer_id)
    days = int(db.get_setting(c, "ref_days") or config.DEFAULT_REF_DAYS)
    inv_days = int(db.get_setting(c, "ref_invitee_days") or config.DEFAULT_REF_INVITEE_DAYS)
    if days and db.referral_count(c, referrer_id) <= config.MAX_REWARDED_REFERRALS:
        until = db.grant_premium(c, referrer_id, days)["premium_until"]
        try:
            tg.call("sendMessage", chat_id=referrer_id,
                    text=f"🎉 {user.get('first_name') or 'Someone'} joined with your link! +{days} days of Premium (until {until:%Y-%m-%d}).")
        except Exception:
            pass
    if inv_days:
        db.grant_premium(c, user["id"], inv_days)
        tg.send(user["id"], f"🎁 Welcome bonus: {inv_days} day(s) of Premium, thanks to your friend's invite!")


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
    if kind == "fb":
        score, _, mid_s = value.partition(":")
        ok = mid_s.isdigit() and db.rate_message(c, int(mid_s), user["id"], 1 if score == "1" else 0)
        tg.answer_callback(cq["id"], "Thanks for the feedback! 🙏" if ok else "")
        if ok and msg:
            tg.call("editMessageReplyMarkup", chat_id=chat_id, message_id=msg["message_id"],
                    reply_markup={"inline_keyboard": [[{"text": "🔄", "callback_data": "rg"}]]})
        return
    if kind == "rg":
        tg.answer_callback(cq["id"], "Regenerating…")
        if msg:
            tg.call("editMessageReplyMarkup", chat_id=chat_id, message_id=msg["message_id"], reply_markup={"inline_keyboard": []})
        last = db.pop_last_exchange(c, db.active_conversation(c, user["id"]))
        if not last or last.startswith("[Photo]"):
            return tg.send(chat_id, "Nothing I can regenerate.")
        return respond(c, user, chat_id, None, last)
    if kind == "del":
        tg.answer_callback(cq["id"])
        if value == "yes":
            db.delete_user_data(c, user["id"])
            return tg.send(chat_id, "🗑 Done. Your chat data has been erased.")
        return tg.send(chat_id, "Cancelled. Nothing was deleted.")
    tg.answer_callback(cq["id"])
