import hashlib
import hmac
import os
import re
import time

import httpx

from . import db

DEFAULT_BASE = "https://techland-stk.vercel.app"


class PayError(Exception):
    pass


def creds(c):
    key = db.get_setting(c, "techland_key") or os.getenv("TECHLAND_API_KEY", "")
    base = (db.get_setting(c, "techland_base") or os.getenv("TECHLAND_BASE_URL") or DEFAULT_BASE).rstrip("/")
    return key, base


def configured(c):
    return bool(creds(c)[0])


def normalize_phone(text):
    t = re.sub(r"[\s\-]", "", text or "")
    return t if re.fullmatch(r"(\+?254|0)\d{9}", t) else None


def _request(c, method, path, **kw):
    key, base = creds(c)
    if not key:
        raise PayError("Payments are not configured yet. Please contact the admin.")
    headers = {"Authorization": f"Bearer {key}", **kw.pop("headers", {})}
    try:
        r = httpx.request(method, base + path, headers=headers, timeout=30, **kw)
        data = r.json()
    except Exception:
        raise PayError("Payment service is unreachable. Please try again shortly.")
    if r.status_code >= 400 or not data.get("ok", False):
        raise PayError(str(data.get("error") or f"Payment error {r.status_code}"))
    return data


def create_payment(c, phone, amount, reference, external_ref, idem_key):
    return _request(
        c, "POST", "/api/v1/payments",
        json={"phone": phone, "amount": int(amount), "reference": reference[:12], "external_ref": external_ref[:64]},
        headers={"Idempotency-Key": idem_key[:80]},
    )


def get_payment(c, pid):
    return _request(c, "GET", f"/api/v1/payments/{pid}")["payment"]


def verify_signature(raw: bytes, ts: str, sig: str, secret: str) -> bool:
    try:
        mac = "sha256=" + hmac.new(secret.encode(), ts.encode() + b"." + raw, hashlib.sha256).hexdigest()
        return abs(time.time() - int(ts)) < 300 and hmac.compare_digest(mac, sig)
    except Exception:
        return False
