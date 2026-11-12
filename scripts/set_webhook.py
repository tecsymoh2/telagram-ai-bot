"""Usage: python scripts/set_webhook.py https://YOUR-DOMAIN.vercel.app/api"""
import os
import sys

import httpx

token = os.environ["TELEGRAM_BOT_TOKEN"]
secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
r = httpx.post(
    f"https://api.telegram.org/bot{token}/setWebhook",
    data={"url": sys.argv[1], "secret_token": secret, "allowed_updates": '["message","callback_query"]'},
)
print(r.json())
