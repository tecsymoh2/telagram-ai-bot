import html
import time
from urllib.parse import quote

from . import db, security, techland


def _e(x):
    return html.escape(str(x if x is not None else "—"))


def bars(values, labels, unit=""):
    """Tiny responsive SVG bar chart that follows the page's light/dark colors."""
    n = len(values)
    top = max(values) if values and max(values) > 0 else 1
    w, h, pad = 560, 150, 22
    bw = (w - 2 * 8) / max(n, 1)
    out = [f'<svg viewBox="0 0 {w} {h + pad}" width="100%" role="img">']
    for i, v in enumerate(values):
        bh = (v / top) * (h - 24)
        x = 8 + i * bw
        out.append(f'<rect x="{x + 2:.1f}" y="{h - bh:.1f}" width="{bw - 4:.1f}" height="{bh:.1f}" rx="3" fill="var(--ac)" opacity=".85"/>')
        if v:
            out.append(f'<text x="{x + bw / 2:.1f}" y="{h - bh - 3:.1f}" font-size="9" text-anchor="middle" fill="var(--mu)">{v}</text>')
        if i % 2 == 0 or n <= 7:
            out.append(f'<text x="{x + bw / 2:.1f}" y="{h + 14}" font-size="9" text-anchor="middle" fill="var(--mu)">{_e(labels[i])}</text>')
    out.append("</svg>")
    return "".join(out)


def render(c, uid, exp, sig, notice=""):
    rating = db.rating_stats(c)
    series = db.daily_series(c, 14)
    labels = [f"{r['day']:%d/%m}" for r in series]
    chart_msgs = bars([int(r["msgs"]) for r in series], labels)
    chart_users = bars([int(r["new_users"]) for r in series], labels)
    chart_rev = bars([int(r["rev"]) for r in series], labels)
    s = db.global_stats(c)
    rev = db.revenue(c)
    users = db.list_users(c, 60, 0)
    pays = db.recent_payments(c, 15)
    plans = db.get_plans(c)
    maint = db.get_setting(c, "maintenance") == "1"
    auth = f'<input type="hidden" name="uid" value="{_e(uid)}"><input type="hidden" name="exp" value="{_e(exp)}"><input type="hidden" name="sig" value="{_e(sig)}">'

    def form(op, target="", extra="", label="", cls=""):
        return (f'<form method="post" action="/api?action=dash" class="inline">{auth}'
                f'<input type="hidden" name="op" value="{op}"><input type="hidden" name="target" value="{_e(target)}">'
                f'{extra}<button class="{cls}">{label}</button></form>')

    rows = ""
    for u in users:
        name = f"@{u['username']}" if u.get("username") else (u.get("first_name") or "—")
        prem = u.get("premium_until")
        badge = "🚫" if u["is_banned"] else ("⭐" if prem and prem.timestamp() > time.time() else "")
        act = form("unban" if u["is_banned"] else "ban", u["id"], label="Unban" if u["is_banned"] else "Ban", cls="warn")
        act += form("grant", u["id"], '<input name="days" value="30" size="3">', "+days")
        if prem:
            act += form("revoke", u["id"], label="Revoke")
        rows += f"<tr><td>{badge} {_e(name)}<br><small>{_e(u['id'])}</small></td><td>{u['last_seen']:%d %b %H:%M}</td><td class='act'>{act}</td></tr>"

    prow = "".join(
        f"<tr><td>{p['created_at']:%d %b %H:%M}</td><td>{_e(p['user_id'])}</td><td>{_e(p['plan'])}</td>"
        f"<td>{_e(p['amount'])}</td><td>{_e(p['status'])}</td></tr>" for p in pays) or "<tr><td colspan=5>No payments yet</td></tr>"
    plan_txt = ", ".join(f"{_e(n)}: KES {_e(p['price'])}/{_e(p['days'])}d" for n, p in plans.items()) or "none"

    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Bot Admin</title>
<style>
:root{{--bg:#f6f7f9;--card:#fff;--tx:#14181f;--mu:#667;--bd:#e3e6ea;--ac:#2563eb;--wa:#dc2626}}
@media(prefers-color-scheme:dark){{:root{{--bg:#0f1217;--card:#181c23;--tx:#e8eaee;--mu:#9aa3b2;--bd:#262c36;--ac:#5b8cff;--wa:#f87171}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--tx);font:15px system-ui,sans-serif;padding:14px;max-width:900px;margin:auto}}
h1{{font-size:20px}}h2{{font-size:16px;margin:22px 0 8px}}
.grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}}@media(min-width:600px){{.grid{{grid-template-columns:repeat(4,1fr)}}}}
.card{{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:12px}}
.card b{{display:block;font-size:22px}}.card span{{color:var(--mu);font-size:12px}}
.wrap{{overflow-x:auto;background:var(--card);border:1px solid var(--bd);border-radius:12px}}
table{{width:100%;border-collapse:collapse}}td,th{{padding:8px 10px;border-bottom:1px solid var(--bd);text-align:left;font-size:13px;vertical-align:top}}
small{{color:var(--mu)}}form.inline{{display:inline-flex;gap:4px;margin:2px}}
button{{background:var(--ac);color:#fff;border:0;border-radius:8px;padding:6px 10px;font-size:12px}}button.warn{{background:var(--wa)}}
input{{background:var(--bg);color:var(--tx);border:1px solid var(--bd);border-radius:6px;padding:4px}}
.notice{{background:var(--card);border:1px solid var(--ac);border-radius:10px;padding:10px;margin:10px 0}}td.act{{min-width:170px}}
</style></head><body>
<h1>🤖 Bot admin</h1>
{f'<div class="notice">{_e(notice)}</div>' if notice else ''}
<div class="grid">
<div class="card"><b>{s['users']}</b><span>Users ({s['active_24h']} active 24h)</span></div>
<div class="card"><b>{s['messages_24h']}</b><span>Messages 24h ({s['messages']} total)</span></div>
<div class="card"><b>{db.premium_count(c)}</b><span>Premium users</span></div>
<div class="card"><b>KES {rev['total']}</b><span>Revenue (30d: {rev['last30']})</span></div>
</div>
<h2>Messages per day</h2><div class="card">{chart_msgs}</div>
<h2>New users per day</h2><div class="card">{chart_users}</div>
<h2>Revenue per day (KES)</h2><div class="card">{chart_rev}</div>
<h2>Status</h2>
<div class="card">Maintenance: <b style="display:inline">{'ON' if maint else 'off'}</b>
{form('maint_off' if maint else 'maint_on', label='Turn off' if maint else 'Turn on', cls='warn' if not maint else '')}
<br><small>Payments API key: {'set ✅' if techland.configured(c) else 'not set ❌ (use /setkey in Telegram)'} · Plans: {plan_txt} · Pending reminders: {db.count_pending_reminders(c)} · Answer ratings: 👍 {rating['up']} / 👎 {rating['down']}</small></div>
<h2>Recent payments</h2>
<div class="wrap"><table><tr><th>When</th><th>User</th><th>Plan</th><th>KES</th><th>Status</th></tr>{prow}</table></div>
<h2>Users (latest activity)</h2>
<div class="wrap"><table><tr><th>User</th><th>Last seen</th><th>Actions</th></tr>{rows}</table></div>
<p><small>Link expires in 1 hour. Request a new one with /dashboard.</small></p>
</body></html>"""


def apply_action(c, form):
    """form: dict of strings. Returns notice text."""
    op = form.get("op", "")
    target = form.get("target", "")
    if op in ("maint_on", "maint_off"):
        db.set_setting(c, "maintenance", "1" if op == "maint_on" else "0")
        return "Maintenance " + ("enabled" if op == "maint_on" else "disabled")
    if not target.isdigit():
        return "Invalid user"
    t = int(target)
    if op in ("ban", "unban"):
        db.set_user_field(c, t, "is_banned", op == "ban")
        return f"User {t} {'banned' if op == 'ban' else 'unbanned'}"
    if op == "grant":
        days = form.get("days", "30")
        if not days.isdigit() or not 1 <= int(days) <= 3650:
            return "Days must be 1–3650"
        row = db.grant_premium(c, t, int(days))
        return f"Premium for {t} until {row['premium_until']:%Y-%m-%d}" if row else "User not found"
    if op == "revoke":
        db.revoke_premium(c, t)
        return f"Premium removed from {t}"
    return "Unknown action"
