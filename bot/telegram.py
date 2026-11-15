import httpx

from .config import BOT_TOKEN

API = f"https://api.telegram.org/bot{BOT_TOKEN}"
FILE_API = f"https://api.telegram.org/file/bot{BOT_TOKEN}"
_client = httpx.Client(timeout=30)
_me = None


from . import styledata

STYLE_MAP = dict(styledata.DEFAULTS)     # refreshed from the database at the start of every request (see colors.refresh)


def auto_style(btn):
    """Colour an inline button from the admin's choices (an explicit style on the button wins)."""
    if "style" in btn:
        return btn
    key = styledata.key_of(btn.get("callback_data", ""))
    if key:
        style = STYLE_MAP.get(key, styledata.DEFAULTS.get(key, "default"))
        return btn if style == "default" else {**btn, "style": style}
    return btn


def style_keyboard(rows):
    return [[auto_style(b) for b in row] for row in rows]


def _strip_style(markup):
    if isinstance(markup, dict) and "inline_keyboard" in markup:
        markup["inline_keyboard"] = [[{k: v for k, v in b.items() if k != "style"} for b in row] for row in markup["inline_keyboard"]]
    return markup


def call(method, **params):
    r = _client.post(f"{API}/{method}", json=params).json()
    if not r.get("ok") and "reply_markup" in params and "style" in str(r.get("description", "")).lower():
        params["reply_markup"] = _strip_style(params["reply_markup"])
        r = _client.post(f"{API}/{method}", json=params).json()
    return r


def me():
    global _me
    if _me is None:
        _me = call("getMe").get("result", {})
    return _me


def typing(chat_id):
    try:
        call("sendChatAction", chat_id=chat_id, action="typing")
    except Exception:
        pass


def chunks(text, size=4000):
    text = text.strip() or "…"
    while len(text) > size:
        cut = text.rfind("\n", 0, size)
        cut = cut if cut > size // 2 else size
        yield text[:cut]
        text = text[cut:].lstrip()
    yield text


def send(chat_id, text, reply_to=None, keyboard=None):
    parts = list(chunks(text))
    for i, part in enumerate(parts):
        params = {"chat_id": chat_id, "text": part}
        if reply_to and i == 0:
            params["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        if keyboard and i == len(parts) - 1:
            params["reply_markup"] = {"inline_keyboard": style_keyboard(keyboard)}
        call("sendMessage", **params)


def send_document(chat_id, filename, data: bytes, caption=""):
    _client.post(
        f"{API}/sendDocument",
        data={"chat_id": chat_id, "caption": caption},
        files={"document": (filename, data, "text/plain")},
    )


def answer_callback(callback_id, text=""):
    call("answerCallbackQuery", callback_query_id=callback_id, text=text)


def download(file_id):
    info = call("getFile", file_id=file_id).get("result")
    if not info:
        return None
    return _client.get(f"{FILE_API}/{info['file_path']}").content


def send_photo(chat_id, data: bytes, caption=""):
    _client.post(
        f"{API}/sendPhoto",
        data={"chat_id": chat_id, "caption": caption[:1000]},
        files={"photo": ("image.jpg", data, "image/jpeg")},
        timeout=60,
    )


def action(chat_id, act="typing"):
    try:
        call("sendChatAction", chat_id=chat_id, action=act)
    except Exception:
        pass


def delete_message(chat_id, message_id):
    try:
        call("deleteMessage", chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


def send_business(chat_id, connection_id, text):
    """Send as the connected account owner (Telegram Business)."""
    ok = True
    for part in chunks(text):
        r = call("sendMessage", chat_id=chat_id, text=part, business_connection_id=connection_id)
        ok = ok and bool(r.get("ok"))
    return ok


def business_typing(chat_id, connection_id):
    try:
        call("sendChatAction", chat_id=chat_id, action="typing", business_connection_id=connection_id)
    except Exception:
        pass


def send_audio(chat_id, data: bytes, caption="", business_connection_id=None):
    form = {"chat_id": chat_id, "caption": caption[:200]}
    if business_connection_id:
        form["business_connection_id"] = business_connection_id
    r = _client.post(f"{API}/sendAudio", data=form, files={"audio": ("voice.wav", data, "audio/wav")}, timeout=60)
    return bool(r.json().get("ok"))


def send_sticker(chat_id, data: bytes):
    return _client.post(f"{API}/sendSticker", data={"chat_id": chat_id}, files={"sticker": ("sticker.webp", data, "image/webp")}, timeout=60)


def send_document_bytes(chat_id, filename, data: bytes, caption="", mime="application/octet-stream"):
    return _client.post(f"{API}/sendDocument", data={"chat_id": chat_id, "caption": caption},
                        files={"document": (filename, data, mime)}, timeout=60)
