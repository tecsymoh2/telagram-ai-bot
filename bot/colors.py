"""Admin-configurable button colours."""
import json
import time

from . import db, styledata
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
    tg.STYLE_MAP = {**styledata.DEFAULTS, **{k: v for k, v in over.items() if k in styledata.DEFAULTS and v in styledata.STYLES}}
    _cache["t"] = time.time()


def current(key):
    return tg.STYLE_MAP.get(key, styledata.DEFAULTS[key])


def set_color(c, key, style):
    over = _overrides(c)
    if style == styledata.DEFAULTS[key]:
        over.pop(key, None)
    else:
        over[key] = style
    db.set_setting(c, "btn_colors", json.dumps(over) if over else None)
    refresh(c, force=True)


def reset(c):
    db.set_setting(c, "btn_colors", None)
    refresh(c, force=True)


def home_view(c):
    refresh(c, force=True)
    kb, row = [], []
    for k, label, _ in styledata.BUTTONS:
        st = current(k)
        btn = {"text": f"{styledata.DOT[st]} {label}", "callback_data": f"col:{k}"}
        if st != "default":
            btn["style"] = st
        kb.append([btn])
    kb.append([{"text": "♻️ Reset all colours", "callback_data": "colreset", "style": "danger"}])
    return ("🎨 Button colours\nTap a button to change its colour.\n🔵 blue · 🟢 green · 🔴 red · ⚪ no colour\n\n"
            "Shortcut: /setcolor <key> <blue|green|red|default>"), kb


def picker_view(c, key):
    kb = [[{"text": label, "callback_data": f"colset:{key}:{style}", **({"style": style} if style != "default" else {})}]
          for style, label in styledata.STYLES.items()]
    kb.append([{"text": "⬅️ Back", "callback_data": "colh", "style": "primary"}])
    return f"🎨 “{styledata.LABELS[key]}” (key: {key})\nNow: {styledata.STYLES[current(key)]}\nChoose a colour:", kb
