import httpx

from .config import BOT_TOKEN

API = f"https://api.telegram.org/bot{BOT_TOKEN}"
FILE_API = f"https://api.telegram.org/file/bot{BOT_TOKEN}"
_client = httpx.Client(timeout=30)
_me = None


def call(method, **params):
    return _client.post(f"{API}/{method}", json=params).json()


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
            params["reply_markup"] = {"inline_keyboard": keyboard}
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
