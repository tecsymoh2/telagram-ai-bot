import base64

import httpx

from . import config

BASE = "https://api.groq.com/openai/v1"
_client = httpx.Client(timeout=55, headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"})


class AIError(Exception):
    pass


def _post(path, **kwargs):
    r = _client.post(f"{BASE}{path}", **kwargs)
    if r.status_code == 429:
        raise AIError("The AI is busy right now (rate limited). Please try again in a moment.")
    if r.status_code >= 400:
        raise AIError(f"AI error {r.status_code}: {r.text[:200]}")
    return r.json()


def build_system(user, notes):
    parts = [config.SYSTEM_PROMPT]
    persona = config.PERSONAS.get(user["persona"], "")
    if persona:
        parts.append(persona)
    if notes:
        parts.append("Things the user asked you to remember:\n" + "\n".join(f"- {n['content']}" for n in notes))
    if user.get("first_name"):
        parts.append(f"The user's name is {user['first_name']}.")
    return "\n\n".join(parts)


def chat(model, system, messages, max_tokens=1024):
    data = _post(
        "/chat/completions",
        json={
            "model": model,
            "messages": [{"role": "system", "content": system}, *messages],
            "max_tokens": max_tokens,
            "temperature": 0.7,
        },
    )
    return data["choices"][0]["message"]["content"].strip()


def vision(system, history, prompt, image_bytes):
    b64 = base64.b64encode(image_bytes).decode()
    content = [
        {"type": "text", "text": prompt or "Describe this image."},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
    ]
    return chat(config.VISION_MODEL, system, [*history, {"role": "user", "content": content}])


def transcribe(audio_bytes, filename="voice.ogg"):
    data = _post(
        "/audio/transcriptions",
        data={"model": config.WHISPER_MODEL},
        files={"file": (filename, audio_bytes)},
    )
    return data.get("text", "").strip()
