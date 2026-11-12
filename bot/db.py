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
        "SELECT count(*) AS n FROM messages WHERE user_id=%s AND role='user' "
        "AND created_at > now() - make_interval(secs => %s)",
        (user_id, seconds),
    ).fetchone()["n"]


def today_count(c, user_id):
    return c.execute(
        "SELECT count(*) AS n FROM messages WHERE user_id=%s AND role='user' "
        "AND created_at >= date_trunc('day', now())",
        (user_id,),
    ).fetchone()["n"]


def set_user_field(c, user_id, field, value):
    assert field in {"model", "persona", "is_banned"}
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
