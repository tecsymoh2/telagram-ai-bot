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
           RETURNING *, (xmax = 0) AS is_new""",
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
    return c.execute(
        "INSERT INTO messages (conversation_id, user_id, role, content) VALUES (%s,%s,%s,%s) RETURNING id",
        (conv_id, user_id, role, content),
    ).fetchone()["id"]


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
    assert field in {"model", "persona", "is_banned", "daily_limit", "temperature", "pending_action", "tz_offset", "language", "referred_by"}
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


# ---------- groups ----------
def get_chat(c, chat_id, title=None):
    return c.execute(
        """INSERT INTO chats (chat_id, title) VALUES (%s,%s)
           ON CONFLICT (chat_id) DO UPDATE SET title=coalesce(EXCLUDED.title, chats.title) RETURNING *""",
        (chat_id, title),
    ).fetchone()


def set_chat_field(c, chat_id, field, value):
    assert field in {"mode", "persona", "welcome"}
    c.execute(f"UPDATE chats SET {field}=%s WHERE chat_id=%s", (value, chat_id))


# ---------- documents ----------
def save_document(c, user_id, name, content):
    c.execute(
        """INSERT INTO documents (user_id, name, content) VALUES (%s,%s,%s)
           ON CONFLICT (user_id) DO UPDATE SET name=EXCLUDED.name, content=EXCLUDED.content, created_at=now()""",
        (user_id, name, content),
    )


def get_document(c, user_id):
    return c.execute("SELECT name, content FROM documents WHERE user_id=%s", (user_id,)).fetchone()


def clear_document(c, user_id):
    return c.execute("DELETE FROM documents WHERE user_id=%s", (user_id,)).rowcount


# ---------- todos ----------
def add_todo(c, user_id, text):
    return c.execute("INSERT INTO todos (user_id, text) VALUES (%s,%s) RETURNING id", (user_id, text)).fetchone()["id"]


def list_todos(c, user_id):
    return c.execute("SELECT id, text, done FROM todos WHERE user_id=%s ORDER BY done, id LIMIT 50", (user_id,)).fetchall()


def count_todos(c, user_id):
    return c.execute("SELECT count(*) AS n FROM todos WHERE user_id=%s", (user_id,)).fetchone()["n"]


def finish_todo(c, user_id, tid):
    return c.execute("UPDATE todos SET done=TRUE WHERE user_id=%s AND id=%s", (user_id, tid)).rowcount


def clear_todos(c, user_id):
    return c.execute("DELETE FROM todos WHERE user_id=%s AND done", (user_id,)).rowcount


# ---------- promo codes ----------
def create_code(c, code, days, max_uses):
    c.execute(
        """INSERT INTO promo_codes (code, days, max_uses) VALUES (%s,%s,%s)
           ON CONFLICT (code) DO UPDATE SET days=EXCLUDED.days, max_uses=EXCLUDED.max_uses""",
        (code, days, max_uses),
    )


def list_codes(c):
    return c.execute("SELECT * FROM promo_codes ORDER BY created_at DESC LIMIT 30").fetchall()


def delete_code(c, code):
    return c.execute("DELETE FROM promo_codes WHERE code=%s", (code,)).rowcount


def redeem_code(c, code, user_id):
    """Returns (days, error)."""
    row = c.execute("SELECT * FROM promo_codes WHERE code=%s", (code,)).fetchone()
    if not row:
        return None, "That code doesn't exist."
    got = c.execute(
        "INSERT INTO promo_redemptions (code, user_id) VALUES (%s,%s) ON CONFLICT DO NOTHING RETURNING code",
        (code, user_id),
    ).fetchone()
    if not got:
        return None, "You've already used that code."
    ok = c.execute(
        "UPDATE promo_codes SET used=used+1 WHERE code=%s AND used < max_uses RETURNING days", (code,)
    ).fetchone()
    if not ok:
        c.execute("DELETE FROM promo_redemptions WHERE code=%s AND user_id=%s", (code, user_id))
        return None, "That code has been fully used."
    return ok["days"], None


# ---------- referrals / daily ----------
def referral_count(c, user_id):
    return c.execute("SELECT count(*) AS n FROM users WHERE referred_by=%s", (user_id,)).fetchone()["n"]


def update_daily(c, user_id, day, streak):
    c.execute("UPDATE users SET last_daily=%s, streak=%s WHERE id=%s", (day, streak, user_id))


# ---------- expiry notices ----------
def expiring_users(c):
    return c.execute(
        """SELECT id, premium_until FROM users WHERE premium_until > now()
             AND premium_until < now() + interval '2 days' AND expiry_notified IS DISTINCT FROM premium_until"""
    ).fetchall()


def expired_users(c):
    return c.execute(
        """SELECT id, premium_until FROM users WHERE premium_until <= now()
             AND premium_until > now() - interval '3 days' AND expired_notified IS DISTINCT FROM premium_until"""
    ).fetchall()


def mark_notified(c, user_id, column, value):
    assert column in {"expiry_notified", "expired_notified"}
    c.execute(f"UPDATE users SET {column}=%s WHERE id=%s", (value, user_id))


# ---------- analytics / export ----------
def daily_series(c, days=14):
    return c.execute(
        """SELECT d::date AS day,
             (SELECT count(*) FROM messages m WHERE m.role='user' AND m.created_at::date = d::date) AS msgs,
             (SELECT count(*) FROM users u WHERE u.created_at::date = d::date) AS new_users,
             (SELECT coalesce(sum(amount),0) FROM payments p WHERE p.applied AND p.created_at::date = d::date) AS rev
           FROM generate_series(current_date - (%s::int - 1), current_date, interval '1 day') d ORDER BY d""",
        (days,),
    ).fetchall()


def all_users(c):
    return c.execute("SELECT id, username, first_name, created_at, last_seen, premium_until, is_banned FROM users ORDER BY created_at").fetchall()


# ---------- ratings ----------
def rate_message(c, message_id, user_id, rating):
    own = c.execute("SELECT 1 AS x FROM messages WHERE id=%s AND user_id=%s", (message_id, user_id)).fetchone()
    if not own:
        return False
    c.execute(
        """INSERT INTO ratings (message_id, user_id, rating) VALUES (%s,%s,%s)
           ON CONFLICT (message_id) DO UPDATE SET rating=EXCLUDED.rating""",
        (message_id, user_id, rating),
    )
    return True


def rating_stats(c):
    return c.execute(
        "SELECT count(*) FILTER (WHERE rating=1) AS up, count(*) FILTER (WHERE rating=0) AS down FROM ratings"
    ).fetchone()


# ---------- privacy ----------
def delete_user_data(c, user_id):
    """Removes chat content. Keeps the user row and payment records (needed for premium & accounting)."""
    for table in ("ratings", "messages", "conversations", "notes", "documents", "todos", "reminders", "feedback", "usage"):
        c.execute(f"DELETE FROM {table} WHERE user_id=%s", (user_id,))


# ---------- segments / leaderboard ----------
def user_ids_segment(c, seg):
    where = {
        "all": "NOT is_banned",
        "premium": "NOT is_banned AND premium_until > now()",
        "free": "NOT is_banned AND (premium_until IS NULL OR premium_until <= now())",
        "inactive": "NOT is_banned AND last_seen < now() - interval '14 days'",
    }[seg]
    return [r["id"] for r in c.execute(f"SELECT id FROM users WHERE {where}").fetchall()]


def top_streaks(c, limit=10):
    return c.execute(
        "SELECT first_name, username, streak FROM users WHERE streak > 0 AND last_daily >= current_date - 1 "
        "ORDER BY streak DESC LIMIT %s", (limit,)).fetchall()


def top_inviters(c, limit=10):
    return c.execute(
        """SELECT u.first_name, u.username, count(*) AS n FROM users r JOIN users u ON u.id = r.referred_by
           GROUP BY u.id ORDER BY n DESC LIMIT %s""", (limit,)).fetchall()
