"""Button colour catalogue. Colours: primary = blue, success = green, danger = red, default = no colour."""
BUTTONS = [  # key, label, default colour
    ("fb_up", "👍 Good-answer button", "success"), ("fb_down", "👎 Bad-answer button", "danger"),
    ("regen", "🔄 Regenerate button", "primary"), ("draft_send", "Business draft: Send", "success"),
    ("draft_skip", "Business draft: Skip", "danger"), ("plan", "Premium plans", "success"),
    ("check", "I've paid – check", "success"), ("model", "Model picker", "primary"),
    ("persona", "Persona picker", "primary"), ("del_yes", "Erase data: confirm", "danger"),
    ("del_no", "Erase data: cancel", "primary"),
]
DEFAULTS = {k: d for k, _, d in BUTTONS}
LABELS = {k: l for k, l, _ in BUTTONS}
STYLES = {"primary": "🔵 Blue", "success": "🟢 Green", "danger": "🔴 Red", "default": "⚪ No colour"}
DOT = {"primary": "🔵", "success": "🟢", "danger": "🔴", "default": "⚪"}
ALIASES = {"blue": "primary", "green": "success", "red": "danger", "default": "default", "none": "default",
           "primary": "primary", "success": "success", "danger": "danger"}


def key_of(data):
    kind = data.split(":")[0]
    if data.startswith("fb:1"):
        return "fb_up"
    if data.startswith("fb:0"):
        return "fb_down"
    if data == "del:yes":
        return "del_yes"
    if data == "del:no":
        return "del_no"
    return {"rg": "regen", "bsend": "draft_send", "bskip": "draft_skip", "buy": "plan", "chk": "check", "tchk": "check",
            "model": "model", "persona": "persona"}.get(kind)
