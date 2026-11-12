import os


def _int(name, default):
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
import hashlib

WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET") or (
    hashlib.sha256(("tgbot:" + os.getenv("TELEGRAM_BOT_TOKEN", "")).encode()).hexdigest()[:48]
)
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")

MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
WHISPER_MODEL = os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo")
ALLOWED_MODELS = [
    m.strip()
    for m in os.getenv("ALLOWED_MODELS", f"{MODEL},llama-3.1-8b-instant,openai/gpt-oss-120b").split(",")
    if m.strip()
]

ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x.isdigit()}
SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "You are a helpful, friendly assistant inside Telegram. Keep answers clear and concise.",
)

DAILY_LIMIT = _int("DAILY_LIMIT", 50)
RATE_LIMIT_PER_MIN = _int("RATE_LIMIT_PER_MIN", 8)
HISTORY_LIMIT = _int("HISTORY_LIMIT", 20)

PERSONAS = {
    "default": "",
    "coder": "Act as an expert senior software engineer. Give working code in fenced blocks and brief explanations.",
    "teacher": "Act as a patient teacher. Explain step by step with simple examples, then check understanding.",
    "translator": "Act as a translator. Translate the user's text to English; if it's already English, ask for the target language.",
    "writer": "Act as a creative writing assistant with a vivid, engaging style.",
    "concise": "Answer in as few words as possible. No filler.",
    "coach": "Act as a supportive productivity and life coach. Ask one focused question at a time.",
}
