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
                + (SELECT count(*) FROM usage WHERE user_id=%(u)s AND kind <> 'biz'
                     AND created_at > now() - make_interval(secs => %(s)s)) AS n""",
        {"u": user_id, "s": seconds},
    ).fetchone()["n"]


def today_count(c, user_id):
    return c.execute(
        """SELECT (SELECT count(*) FROM messages WHERE user_id=%(u)s AND role='user'
                     AND created_at >= date_trunc('day', now()))
                + (SELECT count(*) FROM usage WHERE user_id=%(u)s AND kind <> 'biz'
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
    assert field in {"model", "persona", "is_banned", "daily_limit", "temperature", "pending_action", "tz_offset", "language", "referred_by", "voice_reply"}
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
    assert field in {"mode", "persona", "welcome", "antilink", "badwords", "warn_limit"}
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
    ids = [r["id"] for r in c.execute("SELECT id FROM business_connections WHERE user_id=%s", (user_id,)).fetchall()]
    if ids:
        for table in ("business_messages", "business_chats"):
            c.execute(f"DELETE FROM {table} WHERE connection_id = ANY(%s)", (ids,))
        c.execute("DELETE FROM business_drafts WHERE owner_id=%s", (user_id,))


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


# ---------- business (Telegram Business chatbot mode) ----------
def upsert_business_connection(c, cid, owner_id, user_chat_id, can_reply, is_enabled):
    return c.execute(
        """INSERT INTO business_connections (id, user_id, user_chat_id, can_reply, is_enabled)
           VALUES (%s,%s,%s,%s,%s)
           ON CONFLICT (id) DO UPDATE SET user_chat_id=EXCLUDED.user_chat_id,
             can_reply=EXCLUDED.can_reply, is_enabled=EXCLUDED.is_enabled RETURNING *""",
        (cid, owner_id, user_chat_id, can_reply, is_enabled),
    ).fetchone()


def get_business_connection(c, cid):
    return c.execute("SELECT * FROM business_connections WHERE id=%s", (cid,)).fetchone()


def user_connections(c, owner_id):
    return c.execute("SELECT * FROM business_connections WHERE user_id=%s AND is_enabled", (owner_id,)).fetchall()


def count_business(c):
    return c.execute("SELECT count(*) AS n FROM business_connections WHERE is_enabled").fetchone()["n"]


def get_biz_settings(c, owner_id):
    c.execute("INSERT INTO business_settings (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (owner_id,))
    return c.execute("SELECT * FROM business_settings WHERE user_id=%s", (owner_id,)).fetchone()


def set_biz_setting(c, owner_id, field, value):
    assert field in {"mode", "instructions", "disclose", "hours_start", "hours_end", "away_message", "faq", "booking", "voice", "team_chat_id"}
    get_biz_settings(c, owner_id)
    c.execute(f"UPDATE business_settings SET {field}=%s WHERE user_id=%s", (value, owner_id))


def get_biz_chat(c, cid, chat_id):
    return c.execute(
        """INSERT INTO business_chats (connection_id, chat_id) VALUES (%s,%s)
           ON CONFLICT (connection_id, chat_id) DO UPDATE SET connection_id=EXCLUDED.connection_id RETURNING *""",
        (cid, chat_id),
    ).fetchone()


def pause_biz_chat(c, cid, chat_id, minutes):
    c.execute(
        """INSERT INTO business_chats (connection_id, chat_id, paused_until)
           VALUES (%s,%s, now() + make_interval(mins => %s))
           ON CONFLICT (connection_id, chat_id) DO UPDATE SET paused_until=EXCLUDED.paused_until""",
        (cid, chat_id, minutes),
    )


def mark_biz_disclosed(c, cid, chat_id):
    c.execute("UPDATE business_chats SET disclosed=TRUE WHERE connection_id=%s AND chat_id=%s", (cid, chat_id))


def add_biz_message(c, cid, chat_id, role, content):
    c.execute(
        "INSERT INTO business_messages (connection_id, chat_id, role, content) VALUES (%s,%s,%s,%s)",
        (cid, chat_id, role, content[:4000]),
    )
    import random
    if random.random() < 0.02:  # keep customer data only 30 days
        c.execute("DELETE FROM business_messages WHERE created_at < now() - interval '30 days'")


def biz_history(c, cid, chat_id, limit=12):
    rows = c.execute(
        """SELECT role, content FROM (SELECT id, role, content FROM business_messages
             WHERE connection_id=%s AND chat_id=%s ORDER BY id DESC LIMIT %s) t ORDER BY id""",
        (cid, chat_id, limit),
    ).fetchall()
    return [{"role": "user" if r["role"] == "user" else "assistant", "content": r["content"]} for r in rows]


def biz_bot_replies_last_hour(c, cid, chat_id):
    return c.execute(
        "SELECT count(*) AS n FROM business_messages WHERE connection_id=%s AND chat_id=%s AND role='bot' "
        "AND created_at > now() - interval '1 hour'", (cid, chat_id)).fetchone()["n"]


def add_draft(c, cid, chat_id, owner_id, text):
    return c.execute(
        "INSERT INTO business_drafts (connection_id, chat_id, owner_id, text) VALUES (%s,%s,%s,%s) RETURNING id",
        (cid, chat_id, owner_id, text)).fetchone()["id"]


def get_draft(c, draft_id):
    return c.execute("SELECT * FROM business_drafts WHERE id=%s", (draft_id,)).fetchone()


def delete_draft(c, draft_id):
    c.execute("DELETE FROM business_drafts WHERE id=%s", (draft_id,))


# ================================================================ v4 additions
# ---------- contact memory ----------
def get_contact(c, cid, chat_id):
    return c.execute(
        """INSERT INTO business_contacts (connection_id, chat_id) VALUES (%s,%s)
           ON CONFLICT (connection_id, chat_id) DO UPDATE SET connection_id=EXCLUDED.connection_id RETURNING *""",
        (cid, chat_id)).fetchone()


def bump_contact(c, cid, chat_id, name=None):
    return c.execute(
        "UPDATE business_contacts SET msg_count=msg_count+1, name=coalesce(name, %s) WHERE connection_id=%s AND chat_id=%s RETURNING msg_count",
        (name, cid, chat_id)).fetchone()["msg_count"]


def set_contact_memory(c, cid, chat_id, memory):
    c.execute("UPDATE business_contacts SET memory=%s WHERE connection_id=%s AND chat_id=%s", (memory[:1200], cid, chat_id))


# ---------- appointments ----------
def add_appointment(c, owner_id, cid, chat_id, name, when_utc, note):
    return c.execute(
        "INSERT INTO business_appointments (owner_id, connection_id, chat_id, customer_name, when_at, note) VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",
        (owner_id, cid, chat_id, name, when_utc, note)).fetchone()


def appointment_conflict(c, owner_id, when_utc):
    return bool(c.execute(
        "SELECT 1 FROM business_appointments WHERE owner_id=%s AND status='booked' AND when_at BETWEEN %s - interval '29 minutes' AND %s + interval '29 minutes'",
        (owner_id, when_utc, when_utc)).fetchone())


def list_appointments(c, owner_id, limit=15):
    return c.execute("SELECT * FROM business_appointments WHERE owner_id=%s AND status='booked' AND when_at > now() - interval '1 hour' ORDER BY when_at LIMIT %s",
                     (owner_id, limit)).fetchall()


def get_appointment(c, aid):
    return c.execute("SELECT * FROM business_appointments WHERE id=%s", (aid,)).fetchone()


def cancel_appointment(c, owner_id, aid):
    return c.execute("UPDATE business_appointments SET status='cancelled' WHERE id=%s AND owner_id=%s AND status='booked' RETURNING *",
                     (aid, owner_id)).fetchone()


def due_appointment_reminders(c):
    return c.execute(
        """UPDATE business_appointments SET reminded=TRUE WHERE id IN (
             SELECT id FROM business_appointments WHERE status='booked' AND NOT reminded
               AND when_at > now() AND when_at <= now() + interval '60 minutes' LIMIT 50 FOR UPDATE SKIP LOCKED) RETURNING *""").fetchall()


# ---------- escalations / away ----------
def add_escalation(c, team_chat_id, msg_id, cid, chat_id):
    c.execute("INSERT INTO business_escalations (team_chat_id, msg_id, connection_id, chat_id) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
              (team_chat_id, msg_id, cid, chat_id))


def get_escalation(c, team_chat_id, msg_id):
    return c.execute("SELECT * FROM business_escalations WHERE team_chat_id=%s AND msg_id=%s", (team_chat_id, msg_id)).fetchone()


def mark_away_sent(c, cid, chat_id):
    c.execute("UPDATE business_chats SET away_sent=now() WHERE connection_id=%s AND chat_id=%s", (cid, chat_id))


# ---------- staff roles ----------
def get_staff_role(c, uid):
    r = c.execute("SELECT role FROM ai_staff WHERE user_id=%s", (uid,)).fetchone()
    return r["role"] if r else None


def set_staff(c, uid, role):
    c.execute("INSERT INTO ai_staff (user_id, role) VALUES (%s,%s) ON CONFLICT (user_id) DO UPDATE SET role=EXCLUDED.role", (uid, role))


def del_staff(c, uid):
    return c.execute("DELETE FROM ai_staff WHERE user_id=%s", (uid,)).rowcount


def list_staff(c):
    return c.execute("SELECT user_id, role FROM ai_staff ORDER BY role, user_id").fetchall()


# ---------- group moderation ----------
def add_warn(c, chat_id, user_id):
    return c.execute(
        "INSERT INTO warns (chat_id, user_id, n) VALUES (%s,%s,1) ON CONFLICT (chat_id, user_id) DO UPDATE SET n=warns.n+1 RETURNING n",
        (chat_id, user_id)).fetchone()["n"]


def reset_warns(c, chat_id, user_id):
    c.execute("UPDATE warns SET n=0 WHERE chat_id=%s AND user_id=%s", (chat_id, user_id))


def get_warns(c, chat_id, user_id):
    r = c.execute("SELECT n FROM warns WHERE chat_id=%s AND user_id=%s", (chat_id, user_id)).fetchone()
    return r["n"] if r else 0


# ---------- backups ----------
def all_rows(c, table):
    assert table in {"users", "payments", "business_appointments", "feedback", "promo_codes", "settings", "business_settings"}
    return c.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
