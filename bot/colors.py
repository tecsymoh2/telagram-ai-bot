"""Admin-configurable button colours (per section, or per individual button)."""
import json
import time

from . import db, menudata, styledata
from . import telegram as tg

_cache = {"t": 0.0}


def _overrides(c):
    try:
        return json.loads(db.get_setting(c, "btn_colors") or "{}")
    except Exception:
        return {}


def refresh(c, force=False):
    if not force and time.time() - _cache["t"] < 20:
        return
    over = _overrides(c)
    tg.STYLE_MAP = {**styledata.DEFAULTS, **{k: v for k, v in over.items() if styledata.valid_key(k) and v in styledata.STYLES}}
    _cache["t"] = time.time()


def effective(key):
    """Colour a button really shows (individual buttons fall back to their section colour)."""
    if key.startswith("b_"):
        return tg.STYLE_MAP.get(key) or tg.STYLE_MAP.get(f"cat_{menudata.ENTRY[key[2:]]['sec']}", "default")
    return tg.STYLE_MAP.get(key, styledata.DEFAULTS[key])


def set_color(c, key, style):
    over = _overrides(c)
    if style == "inherit" or (not key.startswith("b_") and style == styledata.DEFAULTS.get(key)):
        over.pop(key, None)
    else:
        over[key] = style
    db.set_setting(c, "btn_colors", json.dumps(over) if over else None)
    refresh(c, force=True)


def reset(c):
    db.set_setting(c, "btn_colors", None)
    refresh(c, force=True)


def _group_index(key):
    names = list(dict.fromkeys(g for *_, g in styledata.BUTTONS))
    if key.startswith("b_"):
        return len(names) + list(menudata.SECTION).index(menudata.ENTRY[key[2:]]["sec"])
    return names.index(next(g for k, _, _, g in styledata.BUTTONS if k == key))


def home_view(c):
    refresh(c, force=True)
    kb = [[{"text": f"🎨 {g}", "callback_data": f"colg:{i}"}] for i, g in enumerate(styledata.GROUPS)]
    kb.append([{"text": "♻️ Reset all colours", "callback_data": "colreset", "style": "danger"}])
    return ("🎨 Button colours\nPick a group, then a button, then a colour.\n"
            "• “Section colours” recolour every button in a menu section at once.\n"
            "• “Buttons: …” groups let you colour single buttons.\n"
            "🔵 blue · 🟢 green · 🔴 red · ⚪ no colour"), kb


def group_view(c, idx):
    items = styledata.group_items(idx)
    kb, row = [], []
    for key, label in items:
        st = effective(key)
        btn = {"text": f"{styledata.DOT[st]} {label}", "callback_data": f"col:{key}"}
        if st != "default":
            btn["style"] = st
        row.append(btn)
        if len(row) == 2:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    kb.append([{"text": "⬅️ Groups", "callback_data": "colh"}])
    note = "\nBottom keyboard colours update the next time a user opens the bot (/start)." if styledata.GROUPS[idx] == "Bottom keyboard" else ""
    return f"🎨 {styledata.GROUPS[idx]}: tap a button to change its colour.{note}", kb


def picker_view(c, key):
    opts = [(s, label) for s, label in styledata.STYLES.items()]
    kb = [[{"text": label, "callback_data": f"colset:{key}:{style}", **({"style": style} if style != "default" else {})}] for style, label in opts]
    if key.startswith("b_"):
        kb.append([{"text": "↩️ Same as its section", "callback_data": f"colset:{key}:inherit"}])
    kb.append([{"text": "⬅️ Back", "callback_data": f"colg:{_group_index(key)}", "style": "primary"}])
    return f"🎨 “{styledata.LABELS[key]}”\nNow: {styledata.STYLES[effective(key)]}\nChoose a colour:", kb
