import hmac
import json
import os
import sys
from urllib.parse import parse_qs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, Request, Response  # noqa: E402
from fastapi.responses import HTMLResponse  # noqa: E402

from bot import config, dashboard, db, handlers, security, techland  # noqa: E402
from bot import telegram as tg  # noqa: E402

app = FastAPI()

COMMANDS = [
    ("start", "Welcome"), ("help", "Show commands"), ("premium", "Plans & upgrade"),
    ("invite", "Invite friends, earn Premium"), ("daily", "Daily check-in streak"),
    ("redeem", "Use a promo code"), ("search", "Search the web"), ("imagine", "Generate an image"),
    ("quiz", "Make a quiz"), ("remind", "Set a reminder"), ("reminders", "Your reminders"),
    ("todo", "To-do list"), ("doc", "Current document"), ("cleardoc", "Remove document"),
    ("translate", "Translate text"), ("explain", "Explain simply"), ("improve", "Polish writing"),
    ("tldr", "Short summary"), ("reset", "New conversation"), ("regenerate", "Redo last answer"),
    ("history", "Last 10 messages"), ("model", "Choose AI model"), ("persona", "Choose personality"),
    ("language", "Set reply language"), ("creativity", "Set creativity 0-1"), ("timezone", "Set your timezone"),
    ("remember", "Save a fact about you"), ("memories", "List saved facts"), ("forget", "Delete saved facts"),
    ("summary", "Summarize this chat"), ("export", "Download chat history"), ("stats", "Your usage"),
    ("referrals", "Friends you invited"), ("groupinfo", "Group settings"),
    ("read", "Summarize a web page"), ("weather", "Weather forecast"), ("convert", "Convert currency"),
    ("qr", "Make a QR code"), ("password", "Generate a password"), ("dice", "Roll dice"), ("coin", "Flip a coin"),
    ("leaderboard", "Top streaks & inviters"), ("deleteme", "Erase my data"),
    ("feedback", "Send feedback"), ("status", "Bot health"), ("about", "About"),
]


def run_setup(request: Request):
    """Creates tables, sets webhook + command menu. Open https://YOUR-DOMAIN/api?action=setup once."""
    missing = [k for k, v in {
        "TELEGRAM_BOT_TOKEN": config.BOT_TOKEN, "GROQ_API_KEY": config.GROQ_API_KEY,
        "DATABASE_URL": config.DATABASE_URL}.items() if not v]
    if missing:
        return {"ok": False, "error": "Missing environment variables", "missing": missing}
    result = {}
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    try:
        with db.connect() as c:
            db.ensure_schema(c)
            db.set_setting(c, "base_url", f"https://{host}")
            result["payments_key"] = "set" if techland.configured(c) else "not set (use /setkey in Telegram)"
        result["database"] = "ready"
    except Exception as e:
        return {"ok": False, "error": f"Database connection failed: {e}"}
    result["webhook"] = tg.call(
        "setWebhook", url=f"https://{host}/api", secret_token=config.WEBHOOK_SECRET,
        allowed_updates=["message", "callback_query"], drop_pending_updates=True)
    tg.call("setMyCommands", commands=[{"command": c, "description": d} for c, d in COMMANDS])
    result["bot"] = tg.me().get("username")
    result["ok"] = bool(result["webhook"].get("ok"))
    return result


@app.get("/{path:path}")
def get_any(request: Request, path: str = ""):
    q = request.query_params
    action = q.get("action", "")
    if action == "setup" or path.rstrip("/").endswith("setup"):
        return run_setup(request)

    if action == "dashboard":
        uid, exp, sig = q.get("uid", ""), q.get("exp", ""), q.get("sig", "")
        if not security.verify_dashboard(uid, exp, sig):
            return HTMLResponse("<h3>Link invalid or expired. Send /dashboard to the bot for a new one.</h3>", status_code=403)
        with db.connect() as c:
            db.ensure_schema(c)
            return HTMLResponse(dashboard.render(c, uid, exp, sig))

    if action == "cron":
        if not hmac.compare_digest(q.get("key", ""), security.cron_key()):
            return Response(status_code=403)
        with db.connect() as c:
            db.ensure_schema(c)
            from datetime import datetime, timezone
            out = {"ok": True, "reminders": handlers.process_reminders(c)}
            if datetime.now(timezone.utc).minute % 10 == 0:  # expiry checks every 10 minutes are plenty
                out["expiry_notices"] = handlers.process_expiries(c)
            return out

    return {"ok": True, "service": "telegram-ai-bot", "next": "Open /api?action=setup once to finish installation"}


@app.post("/{path:path}")
async def post_any(request: Request, path: str = ""):
    action = request.query_params.get("action", "")
    raw = await request.body()

    # ---- admin dashboard form actions ----
    if action == "dash":
        form = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
        uid, exp, sig = form.get("uid", ""), form.get("exp", ""), form.get("sig", "")
        if not security.verify_dashboard(uid, exp, sig):
            return HTMLResponse("<h3>Link invalid or expired.</h3>", status_code=403)
        with db.connect() as c:
            notice = dashboard.apply_action(c, form)
            return HTMLResponse(dashboard.render(c, uid, exp, sig, notice))

    # ---- payment provider webhook ----
    if action == "techland" or request.headers.get("x-techland-signature"):
        try:
            with db.connect() as c:
                secret = db.get_setting(c, "techland_webhook_secret")
                if secret and not techland.verify_signature(
                        raw, request.headers.get("x-techland-timestamp", "0"),
                        request.headers.get("x-techland-signature", ""), secret):
                    return Response(status_code=403)
                payload = json.loads(raw or b"{}")
                data = payload.get("data") or {}
                pid = data.get("id") or data.get("payment_id") or payload.get("id")
                if pid:
                    handlers.sync_payment(c, pid)  # re-verified against the API, never trusted from the payload
        except Exception as e:
            print("techland webhook failed:", repr(e))
        return {"ok": True}

    # ---- Telegram webhook ----
    got = request.headers.get("x-telegram-bot-api-secret-token", "")
    if not hmac.compare_digest(got, config.WEBHOOK_SECRET):
        return Response(status_code=403)
    try:
        update = json.loads(raw)
        with db.connect() as c:
            db.ensure_schema(c)
            if db.is_duplicate(c, update["update_id"]):
                return {"ok": True}
            handlers.handle_update(c, update)
    except Exception as e:  # never return 5xx, or Telegram retries endlessly
        print("update failed:", repr(e))
    return {"ok": True}
