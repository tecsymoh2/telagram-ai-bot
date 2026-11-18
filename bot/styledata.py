"""Button colour catalogue. Colours: primary = blue, success = green, danger = red, default = no colour.
A single command button's colour (b_<id>) overrides its section colour (cat_<section>)."""
from . import menudata

# key, label, default colour, group
BUTTONS = []
for _id, s in menudata.SECTION.items():
    BUTTONS.append((f"n_{_id}", s["label"], s["nav_style"], "Main menu"))
for _id, s in menudata.SECTION.items():
    BUTTONS.append((f"cat_{_id}", f"{s['label']} – all buttons", s["sec_style"], "Section colours"))
BUTTONS += [
    ("k_menu", "📋 Menu", "primary", "Bottom keyboard"), ("k_new", "🆕 New chat", "success", "Bottom keyboard"),
    ("k_tools", "🔎 Tools", "primary", "Bottom keyboard"), ("k_premium", "⭐ Premium", "success", "Bottom keyboard"),
    ("k_settings", "⚙️ Settings", "default", "Bottom keyboard"), ("k_help", "❓ Help", "danger", "Bottom keyboard"),
    ("fb_up", "👍 Good-answer button", "success", "Answers & business"), ("fb_down", "👎 Bad-answer button", "danger", "Answers & business"),
    ("regen", "🔄 Regenerate button", "primary", "Answers & business"), ("draft_send", "Business draft: Send", "success", "Answers & business"),
    ("draft_skip", "Business draft: Skip", "danger", "Answers & business"), ("plan", "Premium plans", "success", "Answers & business"),
    ("check", "I've paid – check", "success", "Answers & business"), ("model", "Model picker", "primary", "Answers & business"),
    ("persona", "Persona picker", "primary", "Answers & business"), ("del_yes", "Erase data: confirm", "danger", "Answers & business"),
    ("del_no", "Erase data: cancel", "primary", "Answers & business"),
    ("nav_back", "⬅️ Back / Menu", "primary", "Navigation"), ("nav_cancel", "❌ Cancel", "danger", "Navigation"),
]
DEFAULTS = {k: d for k, _, d, _ in BUTTONS}
LABELS = {k: l for k, l, _, _ in BUTTONS}
for _e in menudata.ENTRY.values():
    LABELS[f"b_{_e['id']}"] = _e["label"]
GROUPS = list(dict.fromkeys(g for *_, g in BUTTONS)) + [f"Buttons: {s['title']}" for s in menudata.SECTION.values()]

STYLES = {"primary": "🔵 Blue", "success": "🟢 Green", "danger": "🔴 Red", "default": "⚪ No colour"}
DOT = {"primary": "🔵", "success": "🟢", "danger": "🔴", "default": "⚪"}
ALIASES = {"blue": "primary", "green": "success", "red": "danger", "default": "default", "none": "default",
           "primary": "primary", "success": "success", "danger": "danger", "inherit": "inherit", "section": "inherit"}


def valid_key(key):
    return key in DEFAULTS or (key.startswith("b_") and key[2:] in menudata.ENTRY)


def group_items(idx):
    """[(key, label)] shown in colour-settings group number idx."""
    names = list(dict.fromkeys(g for *_, g in BUTTONS))
    if idx < len(names):
        return [(k, l) for k, l, _, g in BUTTONS if g == names[idx]]
    sec = list(menudata.SECTION)[idx - len(names)]
    return [(f"b_{e['id']}", e["label"]) for e in menudata.ENTRY.values() if e["sec"] == sec]


def resolve(data, style_map):
    """Colour for an inline button, or None for no colour."""
    kind, _, val = data.partition(":")
    key = None
    if kind == "mr" and val in menudata.ENTRY:
        e = menudata.ENTRY[val]
        s = style_map.get(f"b_{val}") or style_map.get(f"cat_{e['sec']}", "default")
        return None if s == "default" else s
    if kind == "mn":
        key = "nav_back" if val == "home" else f"n_{val}"
    elif data.startswith("fb:1"):
        key = "fb_up"
    elif data.startswith("fb:0"):
        key = "fb_down"
    elif data in ("del:yes", "del:no"):
        key = "del_" + data.split(":")[1]
    elif data == "mcx":
        key = "nav_cancel"
    else:
        key = {"rg": "regen", "bsend": "draft_send", "bskip": "draft_skip", "buy": "plan", "chk": "check", "tchk": "check",
               "model": "model", "persona": "persona"}.get(kind)
    if not key:
        return None
    s = style_map.get(key, DEFAULTS.get(key, "default"))
    return None if s == "default" else s
