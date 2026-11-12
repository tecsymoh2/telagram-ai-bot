import time

from . import ai, config, db
from . import telegram as tg

START_TIME = time.time()

HELP = """🤖 Commands

/start – welcome
/help – this list
/reset – start a fresh conversation
/model – choose AI model
/persona – choose a personality
/remember <text> – save a fact about you
/memories – list saved facts
/forget [id] – delete one or all facts
/summary – summarize this conversation
/export – download chat history
/stats – your usage
/status – bot health
/about – about this bot

You can also send 🎤 voice messages (transcribed) and 📷 photos (analyzed)."""

ADMIN_HELP = """🛠 Admin

/admin – global stats
/broadcast <text> – message all users
/ban <user_id> · /unban <user_id>"""


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

    # In groups, only respond to commands, @mentions, or replies to the bot.
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

    # Voice → text
    prefix = ""
    if msg.get("voice") or msg.get("audio"):
        tg.typing(chat_id)
        media = msg.get("voice") or msg.get("audio")
        try:
            audio = tg.download(media["file_id"])
            text = ai.transcribe(audio)
        except ai.AIError as e:
            return tg.send(chat_id, f"⚠️ {e}", msg["message_id"])
        if not text:
            return tg.send(chat_id, "I couldn't hear anything in that voice message.", msg["message_id"])
        prefix = f"🎤 “{text}”\n\n"

    image = None
    if msg.get("photo"):
        image = tg.download(msg["photo"][-1]["file_id"])

    if not text and image is None:
        return tg.send(chat_id, "Send me text, a voice message, or a photo 🙂", msg["message_id"])

    respond(c, user, chat_id, msg["message_id"], text, image, prefix)


def respond(c, user, chat_id, reply_to, text, image=None, prefix=""):
    uid = user["id"]
    is_admin = uid in config.ADMIN_IDS
    if not is_admin:
        if db.recent_user_messages(c, uid) >= config.RATE_LIMIT_PER_MIN:
            return tg.send(chat_id, "⏳ Slow down a little — try again in a minute.", reply_to)
        if db.today_count(c, uid) >= config.DAILY_LIMIT:
            return tg.send(chat_id, f"📊 Daily limit of {config.DAILY_LIMIT} messages reached. Come back tomorrow!", reply_to)

    tg.typing(chat_id)
    conv = db.active_conversation(c, uid)
    hist = db.history(c, conv, config.HISTORY_LIMIT)
    system = ai.build_system(user, db.list_notes(c, uid))
    model = user["model"] or config.MODEL
    stored = f"[Photo] {text}".strip() if image else text

    try:
        if image:
            answer = ai.vision(system, hist, text, image)
        else:
            answer = ai.chat(model, system, [*hist, {"role": "user", "content": text}])
    except ai.AIError as e:
        return tg.send(chat_id, f"⚠️ {e}", reply_to)
    except Exception:
        return tg.send(chat_id, "⚠️ Something went wrong. Please try again.", reply_to)

    db.add_message(c, conv, uid, "user", stored)
    db.add_message(c, conv, uid, "assistant", answer)
    tg.send(chat_id, prefix + answer, reply_to)


def run_command(c, user, chat_id, msg, cmd, arg, is_admin):
    uid, mid = user["id"], msg["message_id"]
    reply = lambda t, **kw: tg.send(chat_id, t, mid, **kw)

    if cmd == "/start":
        name = user.get("first_name") or "there"
        return reply(f"👋 Hi {name}! I'm your AI assistant. Just send a message, voice note, or photo.\n\nType /help to see what I can do.")
    if cmd == "/help":
        return reply(HELP + (("\n\n" + ADMIN_HELP) if is_admin else ""))
    if cmd == "/about":
        return reply("🤖 Telegram AI Bot\nPowered by Groq (LLMs, vision, Whisper) with Neon Postgres memory, hosted on Vercel.")
    if cmd == "/reset":
        db.reset_conversation(c, uid)
        return reply("🧹 Conversation cleared. Fresh start!")

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
        if arg.isdigit():
            n = db.delete_note(c, uid, int(arg))
        else:
            n = db.delete_note(c, uid)
        return reply(f"🗑 Deleted {n} memor{'y' if n == 1 else 'ies'}.")

    if cmd == "/summary":
        hist = db.history(c, db.active_conversation(c, uid), 40)
        if not hist:
            return reply("Nothing to summarize yet.")
        tg.typing(chat_id)
        convo = "\n".join(f"{m['role']}: {m['content']}" for m in hist)
        try:
            out = ai.chat(user["model"] or config.MODEL, "You summarize conversations in a few short bullet points.",
                          [{"role": "user", "content": convo}], 400)
        except ai.AIError as e:
            return reply(f"⚠️ {e}")
        return reply("📝 Summary\n\n" + out)

    if cmd == "/export":
        hist = db.history(c, db.active_conversation(c, uid), 1000)
        if not hist:
            return reply("Nothing to export yet.")
        body = "\n\n".join(f"{m['role'].upper()}: {m['content']}" for m in hist)
        return tg.send_document(chat_id, "conversation.txt", body.encode(), "💾 Your conversation")

    if cmd == "/stats":
        s = db.user_stats(c, uid)
        first = s["first"].strftime("%Y-%m-%d") if s["first"] else "—"
        return reply(f"📊 Your stats\nMessages sent: {s['sent']}\nTotal messages: {s['total']}\n"
                     f"Today: {db.today_count(c, uid)}/{config.DAILY_LIMIT}\nFirst message: {first}\n"
                     f"Model: {user['model'] or config.MODEL}\nPersona: {user['persona']}")

    if cmd == "/status":
        t = time.time()
        c.execute("SELECT 1")
        ms = int((time.time() - t) * 1000)
        return reply(f"✅ Online\nDatabase: ok ({ms} ms)\nDefault model: {config.MODEL}\nUptime (this instance): {int(time.time() - START_TIME)}s")

    # ---- admin ----
    if cmd in ("/admin", "/broadcast", "/ban", "/unban"):
        if not is_admin:
            return reply("⛔ Admins only.")
        if cmd == "/admin":
            s = db.global_stats(c)
            return reply(f"🛠 Admin panel\nUsers: {s['users']} (active 24h: {s['active_24h']}, banned: {s['banned']})\n"
                         f"Messages: {s['messages']} (24h: {s['messages_24h']})\n\n" + ADMIN_HELP)
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
        if not arg.isdigit():
            return reply(f"Usage: {cmd} <user_id>")
        db.set_user_field(c, int(arg), "is_banned", cmd == "/ban")
        return reply(f"{'🚫 Banned' if cmd == '/ban' else '✅ Unbanned'} {arg}")

    return reply("Unknown command. Try /help")


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
