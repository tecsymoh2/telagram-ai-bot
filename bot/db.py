import random

import psycopg
from psycopg.rows import dict_row

from .config import DATABASE_URL
from .schema import SCHEMA

_schema_ready = False


def ensure_schema(c):
    """Create tables automatically on first run (idempotent)."""
    global _schema_ready
    if not _schema_ready:
        c.execute(SCHEMA)
        _schema_ready = True


def connect():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True, connect_timeout=10)


def is_duplicate(c, update_id):
    row = c.execute(
        "INSERT INTO processed_updates(update_id) VALUES (%s) ON CONFLICT DO NOTHING RETURNING update_id",
        (update_id,),
    ).fetchone()
    if random.random() < 0.02:
        c.execute("DELETE FROM processed_updates WHERE created_at < now() - interval '2 days'")
    return row is None


def upsert_user(c, u):
    return c.execute(
        """INSERT INTO users (id, username, first_name) VALUES (%s,%s,%s)
           ON CONFLICT (id) DO UPDATE SET username=EXCLUDED.username,
             first_name=EXCLUDED.first_name, last_seen=now()
           RETURNING *""",
        (u["id"], u.get("username"), u.get("first_name")),
    ).fetchone()


def active_conversation(c, user_id):
    row = c.execute(
        "SELECT id FROM conversations WHERE user_id=%s AND active ORDER BY id DESC LIMIT 1", (user_id,)
    ).fetchone()
    if row:
        return row["id"]
    return c.execute(
        "INSERT INTO conversations (user_id) VALUES (%s) RETURNING id", (user_id,)
    ).fetchone()["id"]


def reset_conversation(c, user_id):
    c.execute("UPDATE conversations SET active=FALSE WHERE user_id=%s AND active", (user_id,))
    return c.execute(
        "INSERT INTO conversations (user_id) VALUES (%s) RETURNING id", (user_id,)
    ).fetchone()["id"]


def add_message(c, conv_id, user_id, role, content):
    c.execute(
        "INSERT INTO messages (conversation_id, user_id, role, content) VALUES (%s,%s,%s,%s)",
        (conv_id, user_id, role, content),
    )


def history(c, conv_id, limit):
    rows = c.execute(
        """SELECT role, content FROM (
             SELECT id, role, content FROM messages WHERE conversation_id=%s ORDER BY id DESC LIMIT %s
           ) t ORDER BY id""",
        (conv_id, limit),
    ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def recent_user_messages(c, user_id, seconds=60):
    return c.execute(
        """SELECT (SELECT count(*) FROM messages WHERE user_id=%(u)s AND role='user'
                     AND created_at > now() - make_interval(secs => %(s)s))
                + (SELECT count(*) FROM usage WHERE user_id=%(u)s
                     AND created_at > now() - make_interval(secs => %(s)s)) AS n""",
        {"u": user_id, "s": seconds},
    ).fetchone()["n"]


def today_count(c, user_id):
    return c.execute(
        """SELECT (SELECT count(*) FROM messages WHERE user_id=%(u)s AND role='user'
                     AND created_at >= date_trunc('day', now()))
                + (SELECT count(*) FROM usage WHERE user_id=%(u)s
                     AND created_at >= date_trunc('day', now())) AS n""",
        {"u": user_id},
    ).fetchone()["n"]


def log_usage(c, user_id, kind):
    c.execute("INSERT INTO usage (user_id, kind) VALUES (%s,%s)", (user_id, kind))


def usage_today(c, user_id, kind):
    return c.execute(
        "SELECT count(*) AS n FROM usage WHERE user_id=%s AND kind=%s AND created_at >= date_trunc('day', now())",
        (user_id, kind),
    ).fetchone()["n"]


def set_user_field(c, user_id, field, value):
    assert field in {"model", "persona", "is_banned", "daily_limit", "temperature", "pending_action", "tz_offset"}
    c.execute(f"UPDATE users SET {field}=%s WHERE id=%s", (value, user_id))


def add_note(c, user_id, text):
    c.execute("INSERT INTO notes (user_id, content) VALUES (%s,%s)", (user_id, text))


def list_notes(c, user_id):
    return c.execute("SELECT id, content FROM notes WHERE user_id=%s ORDER BY id", (user_id,)).fetchall()


def delete_note(c, user_id, note_id=None):
    if note_id is None:
        return c.execute("DELETE FROM notes WHERE user_id=%s", (user_id,)).rowcount
    return c.execute("DELETE FROM notes WHERE user_id=%s AND id=%s", (user_id, note_id)).rowcount


def user_stats(c, user_id):
    return c.execute(
        "SELECT count(*) FILTER (WHERE role='user') AS sent, count(*) AS total, "
        "min(created_at) AS first FROM messages WHERE user_id=%s",
        (user_id,),
    ).fetchone()


def global_stats(c):
    return c.execute(
        """SELECT (SELECT count(*) FROM users) AS users,
                  (SELECT count(*) FROM users WHERE last_seen > now() - interval '1 day') AS active_24h,
                  (SELECT count(*) FROM users WHERE is_banned) AS banned,
                  (SELECT count(*) FROM messages) AS messages,
                  (SELECT count(*) FROM messages WHERE created_at > now() - interval '1 day') AS messages_24h"""
    ).fetchone()


def all_user_ids(c):
    return [r["id"] for r in c.execute("SELECT id FROM users WHERE NOT is_banned").fetchall()]


def get_setting(c, key, default=None):
    r = c.execute("SELECT value FROM settings WHERE key=%s", (key,)).fetchone()
    return r["value"] if r else default


def set_setting(c, key, value):
    if value is None:
        c.execute("DELETE FROM settings WHERE key=%s", (key,))
    else:
        c.execute(
            "INSERT INTO settings(key,value) VALUES (%s,%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
            (key, value),
        )


def find_user(c, ident):
    ident = ident.strip().lstrip("@")
    if ident.isdigit():
        return c.execute("SELECT * FROM users WHERE id=%s", (int(ident),)).fetchone()
    return c.execute("SELECT * FROM users WHERE lower(username)=lower(%s)", (ident,)).fetchone()


def list_users(c, limit=10, offset=0):
    return c.execute("SELECT * FROM users ORDER BY last_seen DESC LIMIT %s OFFSET %s", (limit, offset)).fetchall()


def top_users(c, limit=10):
    return c.execute(
        """SELECT u.id, u.username, u.first_name, count(m.id) AS n
           FROM users u JOIN messages m ON m.user_id=u.id AND m.role='user'
           GROUP BY u.id ORDER BY n DESC LIMIT %s""",
        (limit,),
    ).fetchall()


def pop_last_exchange(c, conv_id):
    """Remove the last user+assistant pair and return the user's text (for /regenerate)."""
    rows = c.execute(
        "SELECT id, role, content FROM messages WHERE conversation_id=%s ORDER BY id DESC LIMIT 2", (conv_id,)
    ).fetchall()
    if len(rows) < 2 or rows[0]["role"] != "assistant" or rows[1]["role"] != "user":
        return None
    c.execute("DELETE FROM messages WHERE id = ANY(%s)", ([rows[0]["id"], rows[1]["id"]],))
    return rows[1]["content"]


def recent_messages(c, conv_id, limit=10):
    rows = c.execute(
        "SELECT role, content FROM messages WHERE conversation_id=%s ORDER BY id DESC LIMIT %s", (conv_id, limit)
    ).fetchall()
    return list(reversed(rows))


def add_feedback(c, user_id, text):
    c.execute("INSERT INTO feedback (user_id, content) VALUES (%s,%s)", (user_id, text))


def admin_ids_from_db(c):
    return [r["id"] for r in c.execute("SELECT id FROM users").fetchall()]


# ---------- plans / payments / premium ----------
def get_plans(c):
    import json
    from .config import DEFAULT_PLANS
    raw = get_setting(c, "plans")
    try:
        return json.loads(raw) if raw else dict(DEFAULT_PLANS)
    except Exception:
        return dict(DEFAULT_PLANS)


def save_plans(c, plans):
    import json
    set_setting(c, "plans", json.dumps(plans))


def add_payment(c, pid, user_id, plan, amount, days, phone):
    return c.execute(
        """INSERT INTO payments (id, user_id, plan, amount, days, phone) VALUES (%s,%s,%s,%s,%s,%s)
           ON CONFLICT (id) DO NOTHING RETURNING *""",
        (pid, user_id, plan, amount, days, phone),
    ).fetchone()


def get_payment(c, pid):
    return c.execute("SELECT * FROM payments WHERE id=%s", (pid,)).fetchone()


def get_payment_by_seq(c, seq):
    return c.execute("SELECT * FROM payments WHERE seq=%s", (seq,)).fetchone()


def recent_payments(c, limit=15):
    return c.execute("SELECT * FROM payments ORDER BY created_at DESC LIMIT %s", (limit,)).fetchall()


def revenue(c):
    return c.execute(
        """SELECT coalesce(sum(amount) FILTER (WHERE applied),0) AS total,
                  coalesce(sum(amount) FILTER (WHERE applied AND created_at > now() - interval '30 days'),0) AS last30,
                  count(*) FILTER (WHERE applied) AS paid
           FROM payments"""
    ).fetchone()


def premium_count(c):
    return c.execute("SELECT count(*) AS n FROM users WHERE premium_until > now()").fetchone()["n"]


def grant_premium(c, user_id, days):
    return c.execute(
        """UPDATE users SET premium_until = GREATEST(coalesce(premium_until, now()), now()) + make_interval(days => %s)
           WHERE id=%s RETURNING premium_until""",
        (days, user_id),
    ).fetchone()


def revoke_premium(c, user_id):
    c.execute("UPDATE users SET premium_until=NULL WHERE id=%s", (user_id,))


# ---------- reminders ----------
def add_reminder(c, user_id, chat_id, text, due_at):
    return c.execute(
        "INSERT INTO reminders (user_id, chat_id, text, due_at) VALUES (%s,%s,%s,%s) RETURNING id",
        (user_id, chat_id, text, due_at),
    ).fetchone()["id"]


def list_reminders(c, user_id):
    return c.execute(
        "SELECT id, text, due_at FROM reminders WHERE user_id=%s AND NOT sent ORDER BY due_at LIMIT 20", (user_id,)
    ).fetchall()


def cancel_reminder(c, user_id, rid):
    return c.execute("DELETE FROM reminders WHERE user_id=%s AND id=%s AND NOT sent", (user_id, rid)).rowcount


def due_reminders(c, limit=50):
    return c.execute(
        """UPDATE reminders SET sent=TRUE WHERE id IN (
             SELECT id FROM reminders WHERE NOT sent AND due_at <= now() ORDER BY due_at LIMIT %s FOR UPDATE SKIP LOCKED)
           RETURNING *""",
        (limit,),
    ).fetchall()


def count_pending_reminders(c):
    return c.execute("SELECT count(*) AS n FROM reminders WHERE NOT sent").fetchone()["n"]
