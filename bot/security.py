import hashlib
import hmac

from . import config


def _mac(msg: str) -> str:
    return hmac.new(config.WEBHOOK_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()


def sign_dashboard(uid, exp):
    return _mac(f"dash:{uid}:{exp}")[:40]


def verify_dashboard(uid, exp, sig):
    try:
        import time
        return int(exp) > time.time() and hmac.compare_digest(sign_dashboard(uid, exp), sig) \
            and int(uid) in config.ADMIN_IDS
    except Exception:
        return False


def cron_key():
    return _mac("cron")[:24]
