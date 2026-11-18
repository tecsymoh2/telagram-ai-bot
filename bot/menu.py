"""Button menu: every feature reachable by tapping."""
from . import config, db, menudata, styledata
from . import telegram as tg

BOTTOM_TEXT = {"📋 Menu": "/menu", "🆕 New chat": "/reset", "🔎 Tools": "/menu tools", "⭐ Premium": "/premium",
               "⚙️ Settings": "/menu set", "❓ Help": "/menu help"}


def reply_keyboard():
    """Persistent bottom keyboard with the colours the admin chose."""
    def k(text, key):
        s = tg.STYLE_MAP.get(key, styledata.DEFAULTS[key])
        return {"text": text} if s == "default" else {"text": text, "style": s}
    return {"keyboard": [[k("📋 Menu", "k_menu"), k("🆕 New chat", "k_new"), k("🔎 Tools", "k_tools")],
                         [k("⭐ Premium", "k_premium"), k("⚙️ Settings", "k_settings"), k("❓ Help", "k_help")]],
            "resize_keyboard": True, "is_persistent": True}


def _grid(buttons, per_row=2):
    return [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]


def main_view(level):
    btns = [{"text": s["label"], "callback_data": f"mn:{sid}"} for sid, s in menudata.SECTION.items() if sid != "adm" or level >= 1]
    return "📋 Menu – tap what you want to do.\nYou can also just type a message, or send a voice note, photo or file.", _grid(btns)


def section_view(sid, level):
    s = menudata.SECTION[sid]
    btns = [{"text": e["label"], "callback_data": f"mr:{e['id']}"} for e in menudata.ENTRY.values() if e["sec"] == sid and e["level"] <= level]
    return s["title"], _grid(btns) + [[{"text": "⬅️ Menu", "callback_data": "mn:home"}]]


def on_callback(c, cq, user, level):
    """Handles menu taps. Returns True if the callback belonged to the menu."""
    kind, _, val = (cq.get("data") or "").partition(":")
    if kind not in ("mn", "mr", "mcx"):
        return False
    msg = cq.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id", user["id"])
    mid = msg.get("message_id")

    def show(text, kb):
        r = tg.call("editMessageText", chat_id=chat_id, message_id=mid, text=text,
                    reply_markup={"inline_keyboard": tg.style_keyboard(kb)}) if mid else {}
        if not r.get("ok") and "not modified" not in str(r.get("description", "")):
            tg.send(chat_id, text, keyboard=kb)

    tg.answer_callback(cq["id"])
    if kind == "mn":
        if val in menudata.SECTION and (val != "adm" or level >= 1):
            show(*section_view(val, level))
        else:
            show(*main_view(level))
        return True
    if kind == "mcx":
        db.set_user_field(c, user["id"], "pending_action", None)
        show(*main_view(level))
        return True
    e = menudata.ENTRY.get(val)
    if not e or e["level"] > level:
        return True
    if e["mode"] in ("photo", "info"):
        tg.send(chat_id, e["prompt"], keyboard=[[{"text": "⬅️ Menu", "callback_data": "mn:home"}]])
    elif e["mode"] == "ask":
        db.set_user_field(c, user["id"], "pending_action", f"cmd:{e['id']}")
        tg.send(chat_id, e["prompt"], keyboard=[[{"text": "❌ Cancel", "callback_data": "mcx"}]])
    else:
        from . import handlers
        fake = {"message_id": mid or 0, "chat": msg.get("chat") or {"id": chat_id, "type": "private"}, "from": cq["from"]}
        handlers.run_command(c, user, chat_id, fake, e["cmd"], e["arg"], user["id"] in config.ADMIN_IDS)
    return True
