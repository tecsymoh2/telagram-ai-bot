import os, logging
import psycopg
import requests
from psycopg.rows import dict_row
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO)
log=logging.getLogger("telegram-ai-bot")
app=FastAPI(title="Telegram AI Bot")
DB=os.environ.get("DATABASE_URL")
TG_TOKEN=os.environ.get("TELEGRAM_BOT_TOKEN")
TG_SECRET=os.environ.get("TELEGRAM_WEBHOOK_SECRET")
GROQ_KEY=os.environ.get("GROQ_API_KEY")
MODEL=os.getenv("GROQ_MODEL","openai/gpt-oss-20b")
ADMINS={int(x) for x in os.getenv("ADMIN_IDS","").split(",") if x.strip()}
SYSTEM=os.getenv("SYSTEM_PROMPT","You are a helpful, accurate AI assistant.")

def db():
    if not DB: raise RuntimeError("DATABASE_URL is missing")
    return psycopg.connect(DB,row_factory=dict_row)

def tg(method,payload):
    if not TG_TOKEN: raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")
    r=requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/{method}",json=payload,timeout=25)
    r.raise_for_status(); data=r.json()
    if not data.get("ok"): raise RuntimeError(str(data))
    return data.get("result")

def send(chat_id,text):
    text=text or "I couldn't generate a response."
    for i in range(0,len(text),4000): tg("sendMessage",{"chat_id":chat_id,"text":text[i:i+4000]})

def ensure_user(user):
    with db() as c:
        c.execute("""INSERT INTO telegram_users(telegram_id,username,first_name,last_name,language_code,is_bot,last_seen_at,updated_at)
        VALUES(%s,%s,%s,%s,%s,%s,NOW(),NOW()) ON CONFLICT(telegram_id) DO UPDATE SET
        username=EXCLUDED.username,first_name=EXCLUDED.first_name,last_name=EXCLUDED.last_name,
        language_code=EXCLUDED.language_code,last_seen_at=NOW(),updated_at=NOW()""",
        (user["id"],user.get("username"),user.get("first_name"),user.get("last_name"),user.get("language_code"),user.get("is_bot",False)))
        row=c.execute("SELECT id FROM conversations WHERE telegram_id=%s AND is_active ORDER BY updated_at DESC LIMIT 1",(user["id"],)).fetchone()
        if row: return row["id"]
        return c.execute("INSERT INTO conversations(telegram_id,title) VALUES(%s,'New conversation') RETURNING id",(user["id"],)).fetchone()["id"]

def save(cid,uid,role,text):
    with db() as c:
        c.execute("INSERT INTO messages(conversation_id,telegram_id,role,content,model) VALUES(%s,%s,%s,%s,%s)",(cid,uid,role,text,MODEL if role=="assistant" else None))
        c.execute("UPDATE conversations SET updated_at=NOW() WHERE id=%s",(cid,))

def get_history(cid):
    with db() as c: rows=c.execute("SELECT role,content FROM messages WHERE conversation_id=%s AND role IN ('user','assistant') ORDER BY created_at DESC LIMIT 16",(cid,)).fetchall()
    rows.reverse(); return [{"role":r["role"],"content":r["content"]} for r in rows]

def answer(messages):
    if not GROQ_KEY: raise RuntimeError("GROQ_API_KEY is missing")
    r=requests.post("https://api.groq.com/openai/v1/chat/completions",headers={"Authorization":f"Bearer {GROQ_KEY}"},
      json={"model":MODEL,"messages":[{"role":"system","content":SYSTEM}]+messages,"temperature":0.7,"max_tokens":1200},timeout=50)
    r.raise_for_status(); return r.json()["choices"][0]["message"]["content"]

def command(uid,chat_id,cmd):
    if cmd=="/start": send(chat_id,"Hello! I'm your AI assistant. Send a message to chat.\n\nUse /help to see commands.")
    elif cmd=="/help": send(chat_id,"Send a message to chat with AI.\n/reset — new conversation\n/about — bot details\n/status — status")
    elif cmd=="/about": send(chat_id,"AI assistant powered by Groq and Neon PostgreSQL.")
    elif cmd=="/status": send(chat_id,"I'm online and ready.")
    elif cmd=="/reset":
        with db() as c:
            c.execute("UPDATE conversations SET is_active=FALSE,updated_at=NOW() WHERE telegram_id=%s AND is_active",(uid,))
            c.execute("INSERT INTO conversations(telegram_id,title) VALUES(%s,'New conversation')",(uid,))
        send(chat_id,"Conversation reset. Let's start fresh.")
    elif cmd=="/admin":
        if uid not in ADMINS: send(chat_id,"You don't have permission to use this command."); return
        with db() as c:
            users=c.execute("SELECT COUNT(*) n FROM telegram_users").fetchone()["n"]
            msgs=c.execute("SELECT COUNT(*) n FROM messages").fetchone()["n"]
        send(chat_id,f"Admin statistics\nUsers: {users}\nMessages: {msgs}")
    else: send(chat_id,"Unknown command. Use /help.")

@app.get("/")
def health(): return {"ok":True,"service":"telegram-ai-bot"}

@app.post("/api")
async def webhook(req:Request):
    if TG_SECRET and req.headers.get("x-telegram-bot-api-secret-token")!=TG_SECRET: raise HTTPException(403,"Invalid webhook secret")
    update=await req.json(); msg=update.get("message") or update.get("edited_message")
    if not msg or not msg.get("from") or not msg.get("chat"): return JSONResponse({"ok":True})
    text=(msg.get("text") or "").strip()
    if not text: return JSONResponse({"ok":True})
    uid=msg["from"]["id"]; chat_id=msg["chat"]["id"]
    try:
        cid=ensure_user(msg["from"])
        if text.startswith("/"): command(uid,chat_id,text.split()[0].split("@")[0].lower())
        else:
            save(cid,uid,"user",text); reply=answer(get_history(cid)); save(cid,uid,"assistant",reply); send(chat_id,reply)
    except Exception:
        log.exception("Webhook failed")
        try: send(chat_id,"Sorry, something went wrong. Please try again shortly.")
        except Exception: pass
    return JSONResponse({"ok":True})
