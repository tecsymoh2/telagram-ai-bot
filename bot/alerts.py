"""Rate-limited alerts to the owners (and optionally staff)."""
import time

from . import config, db, roles
from . import telegram as tg

_mem = {}


def alert(c, key, text, min_level=3, cooldown=1800):
    now = int(time.time())
    try:
        last = db.get_setting(c, f"alert_{key}")
        if last and str(last).isdigit() and now - int(last) < cooldown:
            return False
        db.set_setting(c, f"alert_{key}", str(now))
        targets = roles.staff_ids(c, min_level)
    except Exception:           # the database itself may be down
        if now - _mem.get(key, 0) < cooldown:
            return False
        _mem[key] = now
        targets = config.ADMIN_IDS
    for uid in targets:
        try:
            tg.send(uid, "🚨 " + text[:1500])
        except Exception:
            pass
    return True
