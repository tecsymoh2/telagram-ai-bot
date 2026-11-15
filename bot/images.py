"""Image tools: sticker maker, enhancer, optional background remover."""
import io

import httpx
from PIL import Image, ImageEnhance, ImageOps

from . import config


def to_sticker(data: bytes) -> bytes:
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    scale = 512 / max(img.size)
    img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "WEBP", quality=90)
    return out.getvalue()


def enhance(data: bytes) -> bytes:
    img = Image.open(io.BytesIO(data)).convert("RGB")
    img = ImageOps.autocontrast(img, cutoff=1)
    img = ImageEnhance.Sharpness(img).enhance(1.5)
    img = ImageEnhance.Color(img).enhance(1.12)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=93)
    return out.getvalue()


def remove_background(data: bytes, key: str = "") -> bytes:
    key = key or config.REMOVEBG_API_KEY
    if not key:
        raise RuntimeError("no key")
    r = httpx.post("https://api.remove.bg/v1.0/removebg", headers={"X-Api-Key": key},
                   files={"image_file": ("image.jpg", data)}, data={"size": "auto"}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"remove.bg error {r.status_code}")
    return r.content
