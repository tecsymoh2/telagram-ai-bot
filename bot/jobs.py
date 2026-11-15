"""Scheduled work run by the cron endpoint: reminders, expiry notices, appointments, backups, health checks."""
import csv
import io
import zipfile
from datetime import datetime, timezone

from . import ai, alerts, business, config, db, handlers, techland
from . import telegram as tg


def backup_zip(c):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for table in ("users", "payments", "business_appointments", "feedback", "promo_codes"):
            rows = db.all_rows(c, table)
            out = io.StringIO()
            if rows:
                w = csv.DictWriter(out, fieldnames=list(rows[0].keys()))
                w.writeheader()
                for r in rows:
                    w.writerow({k: ("" if v is None else v) for k, v in r.items()})
            z.writestr(f"{table}.csv", out.getvalue())
    return buf.getvalue()


def backup_if_due(c):
    days = db.get_setting(c, "backup_days")
    now = datetime.now(timezone.utc)
    if not str(days or "").isdigit() or int(days) < 1 or now.hour != 23:     # 23:00 UTC = 02:00 East Africa
        return False
    last = db.get_setting(c, "last_backup")
    if last:
        try:
            if (now.date() - datetime.strptime(last, "%Y-%m-%d").date()).days < int(days):
                return False
        except ValueError:
            pass
    db.set_setting(c, "last_backup", f"{now:%Y-%m-%d}")
    data = backup_zip(c)
    for uid in config.ADMIN_IDS:
        tg.send_document_bytes(uid, f"backup_{now:%Y%m%d}.zip", data, "🗄 Automatic backup", "application/zip")
    return True


def health(c):
    ok = True
    try:
        ai._client.get(f"{ai.BASE}/models", timeout=15).raise_for_status()
    except Exception as e:
        ok = False
        alerts.alert(c, "groq", f"AI provider check failed: {type(e).__name__}. Replies may not work.", 3, 3600)
    if techland.configured(c):
        try:
            techland.ping(c)
        except Exception as e:
            ok = False
            alerts.alert(c, "techland", f"Payment API check failed: {e}", 3, 3600)
    return ok


def run_all(c):
    out = {"reminders": handlers.process_reminders(c), "appointments": business.process_appointments(c)}
    now = datetime.now(timezone.utc)
    if now.minute % 10 == 0:
        out["expiry_notices"] = handlers.process_expiries(c)
        out["health"] = health(c)
    out["backup"] = backup_if_due(c)
    return out
