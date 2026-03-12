#!/usr/bin/env python3
"""
Faiz Messenger — AI-Powered Messenger
Requires: flask, flask-socketio, g4f
Install: pip install flask flask-socketio "g4f[all]"
"""

import os, sys, json, datetime, sqlite3, threading, time, re

# ── Auto-install (Disabled for deployment) ──────────────────────────────────
def install_deps():
    pass

from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room

# ── Silence noisy loggers ─────────────────────────────────────────────────────
import logging as _log
for _n in ["g4f","httpx","asyncio","aiohttp","curl_cffi","urllib3","h2","hpack"]:
    _log.getLogger(_n).setLevel(_log.CRITICAL)

# ── g4f Setup ─────────────────────────────────────────────────────────────────
G4F_AVAILABLE = False
g4f = None
_PROVIDER_CHAIN = []

try:
    import g4f as _g4f
    g4f = _g4f
    G4F_AVAILABLE = True
    from g4f import Provider as _P

    # Provider chain — ordered by reliability, free, no key needed
    _PROV_MODELS = [
        ("Blackbox",       "blackboxai"),
        ("DDG",            "gpt-4o-mini"),
        ("PollinationsAI", "openai"),
        ("Free2GPT",       "gemini-pro"),
        ("Nexra",          "gpt-4o"),
        ("ChatGptEs",      "gpt-4o-mini"),
        ("Pizzagpt",       "gpt-4o-mini"),
    ]
    for _nm, _model in _PROV_MODELS:
        try:
            _prov = getattr(_P, _nm)
            _PROVIDER_CHAIN.append((_prov, _model))
        except AttributeError:
            pass
    print(f"✅ g4f ready — {len(_PROVIDER_CHAIN)} providers")
except Exception as _e:
    print(f"⚠️  g4f unavailable: {_e}")

import re as _re

_JUNK_SIGNALS = [
    "api_key", "api key required", "puter.js", ".har file", "har file",
    "no .har file found", "proxies cheaper", "invalid service",
    "must be a valid", "bad_request", "model not found", "upstream connect",
    "cloudflare", "captcha", "rate limit", "403 forbidden", "op.wtf",
    "buy cheap", "need proxies",
]
_SPAM_RE = _re.compile(r'(need proxies.*|https?://op\.wtf\S*|buy cheap.*)', _re.I|_re.S)

def _clean(t):
    if not t: return ""
    t = _SPAM_RE.sub("", t)
    return "\n".join(l for l in t.splitlines()
        if not (len(l.strip()) < 100 and "op.wtf" in l.lower())).strip()

def _ok(t):
    if not t or len(t.strip()) < 10: return False
    tl = t.lower()
    return not any(s in tl for s in _JUNK_SIGNALS)

def _g4f_call(messages, timeout=25):
    if not G4F_AVAILABLE or g4f is None:
        return None
    for prov, model in _PROVIDER_CHAIN:
        pname = getattr(prov, "__name__", str(prov))
        try:
            resp = g4f.ChatCompletion.create(
                model=model, messages=messages, provider=prov, timeout=timeout)
            text = _clean(str(resp).strip()) if resp else ""
            if _ok(text):
                return text
        except:
            pass
    return None

def ai_complete(system_prompt, user_message, history=None, timeout=25):
    msgs = [{"role":"system","content":system_prompt}]
    if history:
        for h in history[-8:]:
            r = h.get("role","user")
            if r not in ("user","assistant"): r = "user"
            msgs.append({"role":r,"content":str(h.get("content",""))})
    msgs.append({"role":"user","content":user_message})
    return _g4f_call(msgs, timeout=timeout)

def _local_tone_check(msg):
    lower = msg.lower()
    bad = ["bodoh","brengsek","anjing","bangsat","tolol","goblok","sialan",
           "bajingan","celaka","kampret","babi","idiot","dungu","tai"]
    score = sum(35 for w in bad if w in lower)
    if _re.search(r'[A-Z]{5,}', msg): score += 15
    if _re.search(r'!{3,}', msg): score += 10
    score = min(score, 100)
    if score >= 35:
        return {"is_harmful":True,"tone":"aggressive","severity":score,
                "warning":"Pesan mengandung kata-kata yang mungkin menyinggung.",
                "suggestion":"Coba sampaikan dengan nada lebih tenang dan konstruktif."}
    return {"is_harmful":False,"tone":"normal","severity":0,"warning":None,"suggestion":None}

def ai_tone_check(message):
    if not G4F_AVAILABLE or g4f is None:
        return _local_tone_check(message)
    system = ('Analisis tone pesan Bahasa Indonesia ini. Return HANYA JSON valid:\n'
              '{"is_harmful":false,"tone":"normal","severity":0,"warning":null,"suggestion":null}\n'
              'Nilai tone: normal/angry/sarcastic/sad/defensive/aggressive. severity: 0-100 integer.')
    try:
        result = ai_complete(system, f'Pesan: "{message}"', timeout=15)
        if result:
            m = _re.search(r'\{[^{}]+\}', result, _re.DOTALL)
            if m: return json.loads(m.group())
    except: pass
    return _local_tone_check(message)

def ai_rewrite_tone(message, target_tone, personality="friendly"):
    tone_desc = {
        "professional": "formal dan profesional",
        "friendly":     "santai dan akrab",
        "flirty":       "playful dan sedikit menggoda",
        "business":     "ringkas dan efisien",
        "assertive":    "tegas dan percaya diri",
        "apologetic":   "meminta maaf dan penuh empati",
    }
    desc = tone_desc.get(target_tone, "santai dan ramah")
    system = f"Tulis ulang pesan berikut dengan nada {desc}. Return HANYA teks hasil tulis ulang."
    result = ai_complete(system, f'Pesan asli: "{message}"', timeout=20)
    if result and _ok(result):
        return result.strip('"').strip("'").strip()
    return message

# ── Database ──────────────────────────────────────────────────────────────────
DB_PATH = "messenger.db"
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS messages 
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                      sender TEXT, receiver TEXT, content TEXT, 
                      timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)""")

init_db()

# ── Flask App ─────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.config['SECRET_KEY'] = 'faiz-secret-123'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('send_message')
def handle_message(data):
    sender = data.get('sender', 'User')
    receiver = data.get('receiver', 'Faiz AI')
    content = data.get('content', '')
    
    # Save to DB
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO messages (sender, receiver, content) VALUES (?, ?, ?)",
                     (sender, receiver, content))
    
    emit('receive_message', {'sender': sender, 'content': content}, broadcast=True)
    
    # AI response if addressed to AI
    if receiver == "Faiz AI" or "@faiz" in content.lower():
        threading.Thread(target=ai_reply, args=(content,)).start()

def ai_reply(user_msg):
    reply = ai_complete("Kamu adalah Faiz AI, asisten messenger yang ramah.", user_msg)
    if not reply:
        reply = "Maaf, saya sedang sibuk. Coba lagi nanti ya!"
    
    with app.app_context():
        socketio.emit('receive_message', {'sender': 'Faiz AI', 'content': reply})

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False)
