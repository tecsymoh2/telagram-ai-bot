import time

from . import ai, config, db
from . import telegram as tg

START_TIME = time.time()

HELP = """🤖 Commands

💬 Chat
/reset – fresh conversation
/regenerate – redo the last answer
/history – last 10 messages
/summary – summarize this chat
/export – download chat as .txt

🛠 Tools (reply to a message or add text)
/translate <language> <text>
/explain <text> – explain simply
/improve <text> – fix & polish writing
/tldr <text> – short summary

⚙️ Settings
/model – choose AI model
/persona – choose personality
/creativity <0-1> – how creative replies are

🧠 Memory
/remember <text> · /memories · /forget [id]

ℹ️ Other
/stats · /status · /feedback <text> · /about

Also send 🎤 voice notes and 📷 photos."""

ADMIN_HELP = """🛠 Admin (no limits for you)

/admin – global stats
/users [page] – list users
/user <id|@name> – user details
/top – most active users
/limit <id|@name> <n|default> – daily limit
/unlimited <id|@name> – remove limit
/ban · /unban <id|@name>
/reply <id|@name> <text> – message a user
/broadcast <text> – message everyone
/maintenance on|off – pause the bot for users
/setprompt <text|reset> – change system prompt live"""

TOOLS = {
    "/explain": "Explain the following clearly and simply, with an example if useful:\n\n{t}",
    "/improve": "Improve the following text: fix grammar, spelling and clarity while keeping the meaning and tone. Return only the improved text.\n\n{t}",
    "/tldr": "Summarize the following in 2-4 short bullet points:\n\n{t}",
}


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
    is_admin = user["id"] in config.ADMIN_IDS

    if text.startswith("/"):
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        return run_command(c, user, chat_id, msg, cmd, arg.strip(), is_admin)

    if db.get_setting(c, "maintenance") == "1" and not is_admin:
        return tg.send(chat_id, "🔧 The bot is under maintenance. Please try again soon.", msg["message_id"])

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


def limit_for(user):
    return user["daily_limit"] if user.get("daily_limit") is not None else config.DAILY_LIMIT


def check_limits(c, user, chat_id, reply_to):
    """Returns True if the user may proceed."""
    uid = user["id"]
    if uid in config.ADMIN_IDS:
        return True
    if db.recent_user_messages(c, uid) >= config.RATE_LIMIT_PER_MIN:
        tg.send(chat_id, "⏳ Slow down a little — try again in a minute.", reply_to)
        return False
    if db.today_count(c, uid) >= limit_for(user):
        tg.send(chat_id, f"📊 Daily limit of {limit_for(user)} messages reached. Come back tomorrow!", reply_to)
        return False
    return True


def system_for(c, user):
    return ai.build_system(user, db.list_notes(c, user["id"]), db.get_setting(c, "system_prompt"))


def temp_for(user):
    return user["temperature"] if user.get("temperature") is not None else 0.7


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


def one_shot(c, user, chat_id, reply_to, prompt):
    """A single AI call that does not touch conversation history."""
    if not check_limits(c, user, chat_id, reply_to):
        return
    tg.typing(chat_id)
    try:
        out = ai.chat(user["model"] or config.MODEL, system_for(c, user), [{"role": "user", "content": prompt}],
                      temperature=temp_for(user))
    except ai.AIError as e:
        return tg.send(chat_id, f"⚠️ {e}", reply_to)
    except Exception:
        return tg.send(chat_id, "⚠️ Something went wrong. Please try again.", reply_to)
    # count it toward usage
    db.add_message(c, db.active_conversation(c, user["id"]), user["id"], "user", f"[tool] {prompt[:200]}")
    tg.send(chat_id, out, reply_to)


def target_user(c, reply, arg):
    ident = arg.split(" ")[0] if arg else ""
    u = db.find_user(c, ident) if ident else None
    if not u:
        reply("User not found. Use their numeric ID or @username (they must have used the bot).")
    return u


def run_command(c, user, chat_id, msg, cmd, arg, is_admin):
    uid, mid = user["id"], msg["message_id"]
    reply = lambda t, **kw: tg.send(chat_id, t, mid, **kw)

    if db.get_setting(c, "maintenance") == "1" and not is_admin and cmd not in ("/start", "/help"):
        return reply("🔧 The bot is under maintenance. Please try again soon.")

    # ---------- basics ----------
    if cmd == "/start":
        name = user.get("first_name") or "there"
        return reply(f"👋 Hi {name}! I'm your AI assistant. Send a message, voice note, or photo.\n\nType /help to see what I can do.")
    if cmd == "/help":
        return reply(HELP + (("\n\n" + ADMIN_HELP) if is_admin else ""))
    if cmd == "/about":
        return reply("🤖 Telegram AI Bot\nPowered by Groq (LLMs, vision, Whisper) with Neon Postgres memory, hosted on Vercel.")
    if cmd == "/reset":
        db.reset_conversation(c, uid)
        return reply("🧹 Conversation cleared. Fresh start!")

    # ---------- settings ----------
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
            return reply(f"Usage: /creativity 0.0–1.0 (now {temp_for(user)}). Low = precise, high = creative.")
        db.set_user_field(c, uid, "temperature", v)
        return reply(f"✅ Creativity set to {v}")

    # ---------- memory ----------
    if cmd == "/remember":
        if not arg:
            return reply("Usage: /remember I'm vegetarian and live in Lagos")
        db.add_note(c, uid, arg[:500])
        return reply("🧠 Got it, I'll remember that.")
    if cmd == "/memories":
        notes = db.list_notes(c, uid)
        if not notes:
            return reply("No memories yet. Use /remember <text>.")
        return reply("🧠 Your memories:\n" + "\n".join(f"{n['id']}. {n['content']}" for n in notes))
    if cmd == "/forget":
        n = db.delete_note(c, uid, int(arg)) if arg.isdigit() else db.delete_note(c, uid)
        return reply(f"🗑 Deleted {n} memor{'y' if n == 1 else 'ies'}.")

    # ---------- chat tools ----------
    if cmd == "/regenerate":
        conv = db.active_conversation(c, uid)
        last = db.pop_last_exchange(c, conv)
        if not last or last.startswith("[Photo]") or last.startswith("[tool]"):
            return reply("Nothing I can regenerate.")
        return respond(c, user, chat_id, mid, last)

    if cmd == "/history":
        rows = db.recent_messages(c, db.active_conversation(c, uid), 10)
        if not rows:
            return reply("No messages yet.")
        lines = [("🧑 " if r["role"] == "user" else "🤖 ") + (r["content"][:200] + ("…" if len(r["content"]) > 200 else "")) for r in rows]
        return reply("🕘 Last messages\n\n" + "\n\n".join(lines))

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

    replied_text = (msg.get("reply_to_message") or {}).get("text") or (msg.get("reply_to_message") or {}).get("caption") or ""

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

    # ---------- info ----------
    if cmd == "/stats":
        s = db.user_stats(c, uid)
        first = s["first"].strftime("%Y-%m-%d") if s["first"] else "—"
        lim = "unlimited" if is_admin else limit_for(user)
        return reply(f"📊 Your stats\nMessages sent: {s['sent']}\nTotal messages: {s['total']}\n"
                     f"Today: {db.today_count(c, uid)}/{lim}\nFirst message: {first}\n"
                     f"Model: {user['model'] or config.MODEL}\nPersona: {user['persona']}\nCreativity: {temp_for(user)}")

    if cmd == "/status":
        t = time.time()
        c.execute("SELECT 1")
        ms = int((time.time() - t) * 1000)
        return reply(f"✅ Online\nDatabase: ok ({ms} ms)\nDefault model: {config.MODEL}\nUptime (this instance): {int(time.time() - START_TIME)}s")

    if cmd == "/feedback":
        if not arg:
            return reply("Usage: /feedback <your message>")
        db.add_feedback(c, uid, arg[:1000])
        who = f"@{user['username']}" if user.get("username") else user.get("first_name") or ""
        for a in config.ADMIN_IDS:
            try:
                tg.call("sendMessage", chat_id=a, text=f"💬 Feedback from {who} ({uid}):\n{arg[:1000]}\n\nReply: /reply {uid} <text>")
            except Exception:
                pass
        return reply("🙏 Thanks, your feedback was sent.")

    # ---------- admin ----------
    ADMIN_CMDS = ("/admin", "/users", "/user", "/top", "/limit", "/unlimited", "/ban", "/unban",
                  "/reply", "/broadcast", "/maintenance", "/setprompt")
    if cmd in ADMIN_CMDS:
        if not is_admin:
            return reply("⛔ Admins only.")
        return run_admin(c, chat_id, reply, cmd, arg)

    return reply("Unknown command. Try /help")


def fmt_user(u):
    name = f"@{u['username']}" if u.get("username") else (u.get("first_name") or "—")
    return f"{name} ({u['id']})"


def run_admin(c, chat_id, reply, cmd, arg):
    if cmd == "/admin":
        s = db.global_stats(c)
        mode = "ON 🔧" if db.get_setting(c, "maintenance") == "1" else "off"
        custom = "custom" if db.get_setting(c, "system_prompt") else "default"
        return reply(f"🛠 Admin panel\nUsers: {s['users']} (active 24h: {s['active_24h']}, banned: {s['banned']})\n"
                     f"Messages: {s['messages']} (24h: {s['messages_24h']})\nMaintenance: {mode}\nSystem prompt: {custom}\n\n{ADMIN_HELP}")

    if cmd == "/users":
        page = int(arg) if arg.isdigit() and int(arg) > 0 else 1
        rows = db.list_users(c, 15, (page - 1) * 15)
        if not rows:
            return reply("No users on this page.")
        lines = [f"{'🚫' if u['is_banned'] else '•'} {fmt_user(u)}" for u in rows]
        return reply(f"👥 Users (page {page}, newest activity first)\n" + "\n".join(lines) + f"\n\nNext: /users {page + 1}")

    if cmd == "/top":
        rows = db.top_users(c, 10)
        return reply("🏆 Most active\n" + "\n".join(f"{i + 1}. {fmt_user(u)} – {u['n']} msgs" for i, u in enumerate(rows)) if rows else "No data yet.")

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

    # commands that need a target user
    u = target_user(c, reply, arg)
    if not u:
        return
    rest = arg.partition(" ")[2].strip()

    if cmd == "/user":
        s = db.user_stats(c, u["id"])
        lim = "default" if u["daily_limit"] is None else u["daily_limit"]
        return reply(f"👤 {fmt_user(u)}\nName: {u.get('first_name') or '—'}\nBanned: {u['is_banned']}\n"
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
    if cmd == "/reply":
        if not rest:
            return reply("Usage: /reply <id|@name> <text>")
        ok = tg.call("sendMessage", chat_id=u["id"], text="💬 Admin: " + rest).get("ok")
        return reply("✅ Sent." if ok else "⚠️ Could not deliver (user may have blocked the bot).")


def handle_callback(c, cq):
    data = cq.get("data", "")
    user = db.upsert_user(c, cq["from"])
    kind, _, value = data.partition(":")
    if kind == "model" and value in config.ALLOWED_MODELS:
        db.set_user_field(c, user["id"], "model", value)
        tg.answer_callback(cq["id"], f"Model: {value}")
    elif kind == "persona" and value in config.PERSONAS:
        db.set_user_field(c, user["id"], "persona", value)
        tg.answer_callback(cq["id"], f"Persona: {value}")
    else:
        return tg.answer_callback(cq["id"])
    msg = cq.get("message")
    if msg:
        tg.send(msg["chat"]["id"], f"✅ {kind.capitalize()} set to {value}")
