import hmac
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, Request, Response  # noqa: E402

from bot import config, db, handlers  # noqa: E402
from bot import telegram as tg  # noqa: E402

app = FastAPI()

COMMANDS = [
    ("start", "Welcome"), ("help", "Show commands"), ("reset", "New conversation"),
    ("model", "Choose AI model"), ("persona", "Choose personality"),
    ("remember", "Save a fact about you"), ("memories", "List saved facts"),
    ("forget", "Delete saved facts"), ("summary", "Summarize this chat"),
    ("export", "Download chat history"), ("stats", "Your usage"),
    ("status", "Bot health"), ("about", "About this bot"),
]


def run_setup(request: Request):
    """Creates tables, sets webhook + command menu. Open https://YOUR-DOMAIN/api?action=setup once."""
    missing = [k for k, v in {
        "TELEGRAM_BOT_TOKEN": config.BOT_TOKEN, "GROQ_API_KEY": config.GROQ_API_KEY,
        "DATABASE_URL": config.DATABASE_URL}.items() if not v]
    if missing:
        return {"ok": False, "error": "Missing environment variables", "missing": missing}
    result = {}
    try:
        with db.connect() as c:
            db.ensure_schema(c)
        result["database"] = "ready"
    except Exception as e:
        return {"ok": False, "error": f"Database connection failed: {e}"}
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    url = f"https://{host}/api"
    result["webhook"] = tg.call(
        "setWebhook", url=url, secret_token=config.WEBHOOK_SECRET,
        allowed_updates=["message", "callback_query"], drop_pending_updates=True)
    tg.call("setMyCommands", commands=[{"command": c, "description": d} for c, d in COMMANDS])
    result["bot"] = tg.me().get("username")
    result["ok"] = bool(result["webhook"].get("ok"))
    return result


@app.get("/{path:path}")
def get_any(request: Request, path: str = ""):
    if request.query_params.get("action") == "setup" or path.rstrip("/").endswith("setup"):
        return run_setup(request)
    return {"ok": True, "service": "telegram-ai-bot", "next": "Open /api?action=setup once to finish installation"}


@app.post("/{path:path}")
async def webhook(request: Request, path: str = ""):
    got = request.headers.get("x-telegram-bot-api-secret-token", "")
    if not hmac.compare_digest(got, config.WEBHOOK_SECRET):
        return Response(status_code=403)

    update = await request.json()
    try:
        with db.connect() as c:
            db.ensure_schema(c)
            if db.is_duplicate(c, update["update_id"]):
                return {"ok": True}
            handlers.handle_update(c, update)
    except Exception as e:  # never return 5xx, or Telegram will retry endlessly
        print("update failed:", repr(e))
    return {"ok": True}
