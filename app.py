#!/usr/bin/env python3
"""
Faiz Messenger — AI-Powered Messenger
Requires: flask, flask-socketio, g4f
Install: pip install flask flask-socketio "g4f[all]"
"""

import os, sys, json, datetime, sqlite3, threading, time, re

# ── Auto-install ──────────────────────────────────────────────────────────────
def install_deps():
    import subprocess
    pkgs = ["flask", "flask-socketio", "g4f[all]"]
    for p in pkgs:
        mod = p.split("[")[0].replace("-","_")
        try: __import__(mod)
        except ImportError:
            print(f"Installing {p}…")
            try: subprocess.check_call([sys.executable,"-m","pip","install",p,"-q","--break-system-packages"],stderr=subprocess.DEVNULL)
            except: subprocess.check_call([sys.executable,"-m","pip","install",p,"-q"])

# install_deps()  # Disabled for deployment

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
    print(f"✅ g4f ready — {len(_PROVIDER_CHAIN)} providers: {[p.__name__ for p,m in _PROVIDER_CHAIN]}")
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
                print(f"[g4f ✓] {pname}/{model}")
                return text
            else:
                print(f"[g4f ~] {pname}: {repr(text[:80])}")
        except Exception as e:
            print(f"[g4f ✗] {pname}: {str(e)[:100]}")
    return None

# ── Core AI call ──────────────────────────────────────────────────────────────
def ai_complete(system_prompt, user_message, history=None, timeout=25):
    msgs = [{"role":"system","content":system_prompt}]
    if history:
        for h in history[-8:]:
            r = h.get("role","user")
            if r not in ("user","assistant"): r = "user"
            msgs.append({"role":r,"content":str(h.get("content",""))})
    msgs.append({"role":"user","content":user_message})
    return _g4f_call(msgs, timeout=timeout)

# ── Simple fallback (only for greetings/system, NOT for assistant answers) ────
def _simple_fallback(msg: str) -> str:
    """Only used when g4f is completely unavailable — minimal responses."""
    m = msg.lower().strip()
    if any(w in m for w in ["halo","hai","hi","hello","hey","selamat"]):
        return "Halo! 👋 Saya Faiz Assistant. Koneksi AI sedang terbatas, coba lagi sebentar."
    if any(w in m for w in ["test","tes","ping","aktif"]):
        return "Saya aktif! 🤖 Namun koneksi AI saat ini terbatas."
    if any(w in m for w in ["thanks","terima kasih","makasih"]):
        return "Sama-sama! 😊"
    return "Koneksi AI sedang sibuk. Mohon coba beberapa saat lagi ya."

# ── Tone check ────────────────────────────────────────────────────────────────
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
            result = result.strip()
            if "```" in result:
                parts = result.split("```")
                result = parts[1] if len(parts)>1 else result
                if result.startswith("json"): result = result[4:]
            m = _re.search(r'\{[^{}]+\}', result, _re.DOTALL)
            if m: return json.loads(m.group())
    except: pass
    return _local_tone_check(message)

# ── AI Rewrite ────────────────────────────────────────────────────────────────
def ai_rewrite_tone(message, target_tone, personality="friendly"):
    tone_desc = {
        "professional": "sangat formal dan profesional, seperti surat bisnis atau email resmi perusahaan",
        "friendly":     "santai, hangat, dan akrab seperti ngobrol dengan teman dekat",
        "flirty":       "playful, charming, dan sedikit menggoda dengan sentuhan humor ringan",
        "business":     "ringkas, to the point, dan efisien tanpa basa-basi",
        "assertive":    "tegas, percaya diri, dan langsung tanpa terkesan kasar",
        "apologetic":   "tulus meminta maaf, rendah hati, dan penuh empati",
    }
    desc = tone_desc.get(target_tone, "santai dan ramah")

    system = (
        f"Kamu adalah pakar komunikasi. Tugasmu HANYA menulis ulang pesan berikut "
        f"dengan nada {desc}.\n\n"
        "ATURAN WAJIB:\n"
        "- Kembalikan HANYA teks pesan hasil tulisan ulang\n"
        "- JANGAN tambahkan penjelasan, label, tanda kutip, atau komentar apapun\n"
        "- Pertahankan maksud dan informasi inti dari pesan asli\n"
        "- Gunakan Bahasa Indonesia yang natural\n"
        "- Panjang hasil boleh sedikit berbeda dari asli sesuai nada yang diminta\n"
        "- MULAI LANGSUNG dengan isi pesan, bukan dengan 'Berikut', 'Hasil', dsb"
    )

    result = ai_complete(system, f'Pesan asli: "{message}"', timeout=20)

    if result and _ok(result):
        # Strip common AI preamble patterns
        result = result.strip()
        for prefix in ["Berikut", "Hasil", "Tentu", "Pesan:", "Versi", ":", '"']:
            if result.startswith(prefix):
                idx = result.find('\n')
                if idx > 0 and idx < 100:
                    result = result[idx:].strip()
                break
        result = result.strip('"').strip("'").strip()
        if len(result) > 5:
            return result

    # Local fallback for rewrite (minimal, structural only)
    msg = message.strip()
    if msg: msg = msg[0].upper() + msg[1:]
    ext = {
        "professional": ("Dengan hormat, ", " Terima kasih atas perhatiannya."),
        "apologetic":   ("Saya mohon maaf, ", " Saya sungguh menyesal."),
        "assertive":    ("", " Mohon segera ditindaklanjuti."),
        "friendly":     ("", " 😊"),
        "flirty":       ("", " ✨"),
        "business":     ("", "."),
    }
    p, s = ext.get(target_tone, ("",""))
    return f"{p}{msg}{s}"

# ── Smart Search ──────────────────────────────────────────────────────────────
def ai_smart_search(query, chat_context, user_name):
    system = (
        f"Kamu membantu {user_name} mencari pesan di histori chat mereka.\n"
        "Return HANYA JSON valid:\n"
        '{"results":[{"text":"isi pesan","context":"konteks waktu/siapa","relevance":85}],'
        '"summary":"ringkasan hasil pencarian"}\n'
        "Maksimal 5 hasil. Pilih yang paling relevan dengan query."
    )
    ctx = chat_context[-3000:]
    try:
        result = ai_complete(system, f"Histori chat:\n{ctx}\n\nQuery pencarian: {query}", timeout=20)
        if result:
            result = result.strip()
            if "```" in result:
                result = result.split("```")[1]
                if result.startswith("json"): result = result[4:]
            m = _re.search(r'\{.*\}', result, _re.DOTALL)
            if m: return json.loads(m.group())
    except: pass
    lines = [l for l in chat_context.split('\n') if query.lower() in l.lower()]
    return {
        "results":[{"text":l.split('] ')[-1] if '] ' in l else l,"context":"","relevance":80} for l in lines[:5]],
        "summary":f"Ditemukan {len(lines)} pesan mengandung '{query}'"
    }

# ── Summarize ─────────────────────────────────────────────────────────────────
def ai_summarize(chat_context, user_name, peer_name):
    system = (
        f"Ringkas percakapan antara {user_name} dan {peer_name} dalam Bahasa Indonesia.\n"
        "Return HANYA JSON valid:\n"
        '{"summary":"2-3 kalimat ringkasan","key_points":["poin penting"],'
        '"decisions":["keputusan yang dibuat"],"open_questions":["pertanyaan belum terjawab"],'
        '"sentiment":"positive/neutral/negative"}'
    )
    ctx = chat_context[-4000:]
    try:
        result = ai_complete(system, f"Percakapan:\n{ctx}", timeout=25)
        if result:
            result = result.strip()
            if "```" in result:
                result = result.split("```")[1]
                if result.startswith("json"): result = result[4:]
            m = _re.search(r'\{.*\}', result, _re.DOTALL)
            if m: return json.loads(m.group())
    except: pass
    lines = [l for l in chat_context.split('\n') if l.strip()]
    return {"summary":f"Percakapan antara {user_name} dan {peer_name} ({len(lines)} pesan).",
            "key_points":[l.split('] ')[-1][:80] for l in lines[-3:]],
            "decisions":[],"open_questions":[],"sentiment":"neutral"}

# ── AI Chat (Faiz Assistant) ──────────────────────────────────────────────────
def ai_chat(message, history, user_name, chat_context="", personality="friendly"):
    p_desc = {
        "friendly":     "Kamu ramah, hangat, dan membantu seperti sahabat baik. Gunakan bahasa santai.",
        "professional": "Kamu profesional, efisien, dan selalu to the point. Gunakan bahasa formal.",
        "flirty":       "Kamu playful, charming, dan menyenangkan. Sesekali gunakan emoji.",
        "business":     "Kamu asisten bisnis yang serius, fokus, dan berikan jawaban padat.",
    }.get(personality, "Kamu ramah, hangat, dan membantu.")

    ctx_snippet = chat_context[-1500:] if chat_context else ""
    system = (
        f"Kamu adalah Faiz Assistant, asisten AI pribadi untuk {user_name} di aplikasi Faiz Messenger.\n"
        f"{p_desc}\n\n"
        "Ketentuan:\n"
        "- Jawab dalam Bahasa Indonesia yang natural dan sesuai kepribadianmu\n"
        "- Berikan jawaban yang berguna, informatif, dan relevan\n"
        "- Jangan sebut proxy, API key, atau masalah teknis internal\n"
        "- Jika tidak tahu sesuatu, akui dengan jujur tapi tetap bantu sebaik mungkin\n"
        + (f"\nKonteks chat terkini pengguna:\n{ctx_snippet}" if ctx_snippet else "")
    )

    msgs = [{"role":"system","content":system}]
    for h in history[-8:]:
        r = h.get("role","user")
        if r not in ("user","assistant"): r = "user"
        msgs.append({"role":r,"content":str(h.get("content",""))})
    msgs.append({"role":"user","content":message})

    result = _g4f_call(msgs, timeout=30)
    if result and _ok(result):
        return result

    # Last resort: simpler prompt
    simple = [{"role":"user","content":
        f"Jawab pertanyaan ini dalam Bahasa Indonesia sebagai asisten AI yang ramah "
        f"(max 200 kata, langsung ke poin): {message}"}]
    result2 = _g4f_call(simple, timeout=20)
    if result2 and _ok(result2):
        return result2

    return _simple_fallback(message)


# ── Flask App ─────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.config['SECRET_KEY'] = 'faiz_v10_editorial'
socketio = SocketIO(app, cors_allowed_origins="*",
                    max_http_buffer_size=100*1024*1024,
                    async_mode='threading')
DB_PATH = 'faiz_v10.db'

# ── Database ──────────────────────────────────────────────────────────────────
def init_db():
    with sqlite3.connect(DB_PATH) as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            pin TEXT PRIMARY KEY, name TEXT, av TEXT, password TEXT,
            google_id TEXT, apple_id TEXT,
            hide_online INTEGER DEFAULT 0, dnd INTEGER DEFAULT 0,
            mood TEXT DEFAULT '', stealth_read INTEGER DEFAULT 0,
            ai_style TEXT DEFAULT '', lock_pin TEXT DEFAULT '',
            ai_personality TEXT DEFAULT 'friendly'
        );
        CREATE TABLE IF NOT EXISTS msgs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, s_pin TEXT, r_pin TEXT,
            cont TEXT, type TEXT, status TEXT, time TEXT,
            transcript TEXT DEFAULT '', duration REAL DEFAULT 0,
            edited INTEGER DEFAULT 0, deleted INTEGER DEFAULT 0,
            auto_delete_secs INTEGER DEFAULT 0,
            reply_to INTEGER DEFAULT NULL
        );
        CREATE TABLE IF NOT EXISTS friends (
            user_pin TEXT, friend_pin TEXT, status TEXT,
            interaction_count INTEGER DEFAULT 0,
            PRIMARY KEY (user_pin, friend_pin)
        );
        CREATE TABLE IF NOT EXISTS blocks (
            blocker TEXT, blocked TEXT, PRIMARY KEY (blocker, blocked)
        );
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pin TEXT, text TEXT, remind_at TEXT, done INTEGER DEFAULT 0
        );
        ''')
        for col, defn in [
            ('hide_online','INTEGER DEFAULT 0'), ('dnd','INTEGER DEFAULT 0'),
            ('mood','TEXT DEFAULT ""'), ('stealth_read','INTEGER DEFAULT 0'),
            ('ai_style','TEXT DEFAULT ""'), ('lock_pin','TEXT DEFAULT ""'),
            ('google_id','TEXT'), ('apple_id','TEXT'),
            ('ai_personality','TEXT DEFAULT "friendly"')
        ]:
            try: c.execute(f"ALTER TABLE users ADD COLUMN {col} {defn}")
            except: pass
        for col, defn in [
            ('transcript','TEXT DEFAULT ""'), ('duration','REAL DEFAULT 0'),
            ('edited','INTEGER DEFAULT 0'), ('deleted','INTEGER DEFAULT 0'),
            ('auto_delete_secs','INTEGER DEFAULT 0'),
            ('reply_to','INTEGER DEFAULT NULL')
        ]:
            try: c.execute(f"ALTER TABLE msgs ADD COLUMN {col} {defn}")
            except: pass
        try: c.execute("ALTER TABLE friends ADD COLUMN interaction_count INTEGER DEFAULT 0")
        except: pass

init_db()

online_users = {}
sid_to_ip = {}
ai_context_cache = {}

def get_chat_context(pin, limit=200):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT s_pin, r_pin, cont, type, time FROM msgs "
            "WHERE (s_pin=? OR r_pin=?) AND deleted=0 AND type='text' "
            "ORDER BY id DESC LIMIT ?", (pin, pin, limit)).fetchall()
        names = {}
        lines = []
        for r in reversed(rows):
            sp = r['s_pin']
            if sp not in names:
                u = conn.execute("SELECT name FROM users WHERE pin=?", (sp,)).fetchone()
                names[sp] = u['name'] if u else sp
            lines.append(f"[{r['time']}] {names[sp]}: {r['cont']}")
    return "\n".join(lines)

def refresh_ai_contexts():
    while True:
        time.sleep(300)
        for pin in list(online_users.keys()):
            try:
                ctx = get_chat_context(pin)
                ai_context_cache[pin] = {"text": ctx, "updated": time.time()}
            except: pass

threading.Thread(target=refresh_ai_contexts, daemon=True).start()

def _hist(conn, pin):
    hist = {}
    for r in conn.execute("SELECT * FROM msgs WHERE (s_pin=? OR r_pin=?) ORDER BY id", (pin,pin)):
        oth = r['r_pin'] if r['s_pin']==pin else r['s_pin']
        u = conn.execute("SELECT * FROM users WHERE pin=?", (oth,)).fetchone()
        if not u: continue
        if oth not in hist:
            hist[oth] = {'name':u['name'],'av':u['av'],'msgs':[],'unread':0,'mood':u['mood'] or ''}
        if r['deleted']: continue
        hist[oth]['msgs'].append({
            'id':r['id'],'sender':r['s_pin'],'cont':r['cont'],
            'type':r['type'],'time':r['time'],'status':r['status'],
            'transcript':r['transcript'] or '','duration':r['duration'] or 0,
            'edited':r['edited'] or 0,'deleted':0,
            'reply_to':r['reply_to'],'auto_delete_secs':r['auto_delete_secs'] or 0
        })
    return hist

def _friends(conn, pin):
    friends = {}
    for f in conn.execute("SELECT friend_pin, interaction_count FROM friends WHERE user_pin=? AND status='accepted'", (pin,)):
        fu = conn.execute("SELECT * FROM users WHERE pin=?", (f['friend_pin'],)).fetchone()
        if fu: friends[f['friend_pin']] = {
            'name':fu['name'],'av':fu['av'],'mood':fu['mood'] or '',
            'interaction_count':f['interaction_count'] or 0
        }
    return friends

def _settings(conn, pin):
    u = conn.execute("SELECT hide_online,dnd,mood,stealth_read,ai_style,lock_pin,ai_personality FROM users WHERE pin=?", (pin,)).fetchone()
    if not u: return {}
    return {
        'hide_online':bool(u['hide_online']),'dnd':bool(u['dnd']),'mood':u['mood'] or '',
        'stealth_read':bool(u['stealth_read']),'ai_style':u['ai_style'] or '',
        'lock_pin':u['lock_pin'] or '','ai_personality':u['ai_personality'] or 'friendly'
    }

def _interaction_counts(conn, pin):
    counts = {}
    for r in conn.execute("SELECT friend_pin, interaction_count FROM friends WHERE user_pin=?", (pin,)):
        counts[r['friend_pin']] = r['interaction_count'] or 0
    return counts

# ── HTML ──────────────────────────────────────────────────────────────────────
# ── Socket Handlers ───────────────────────────────────────────────────────────
@socketio.on('connect')
def on_connect():
    sid_to_ip[request.sid] = request.environ.get('HTTP_X_FORWARDED_FOR', request.remote_addr or '').split(',')[0].strip()

@socketio.on('disconnect')
def on_disconnect():
    sid = request.sid
    sid_to_ip.pop(sid, None)
    for pin, sids in list(online_users.items()):
        sids.discard(sid)
        if not sids:
            del online_users[pin]
            with sqlite3.connect(DB_PATH) as c:
                c.row_factory = sqlite3.Row
                for f in c.execute("SELECT friend_pin FROM friends WHERE user_pin=? AND status='accepted'", (pin,)):
                    emit('user_offline', {'pin': pin}, room=f['friend_pin'])

def _do_auth(pin):
    join_room(pin)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        u = conn.execute("SELECT * FROM users WHERE pin=?", (pin,)).fetchone()
        if not u: return None, None, None, None, None, None
        hist = _hist(conn, pin)
        friends = _friends(conn, pin)
        blocks = [r['blocked'] for r in conn.execute("SELECT blocked FROM blocks WHERE blocker=?", (pin,))]
        sett = _settings(conn, pin)
        icounts = _interaction_counts(conn, pin)
    if pin not in online_users: online_users[pin] = set()
    online_users[pin].add(request.sid)
    return u, hist, friends, blocks, sett, icounts

@socketio.on('auto_login')
def h_auto(d):
    pin = d.get('pin', '')
    u, hist, friends, blocks, sett, icounts = _do_auth(pin)
    if not u: return emit('auto_login_failed', {})
    emit('auth_res', {'st':'ok','pin':pin,'name':u['name'],'av':u['av'],
                      'hist':hist,'friends':friends,'blocks':blocks,'settings':sett,
                      'locked_chats':{},'interaction_counts':icounts})

@socketio.on('auth')
def h_auth(d):
    mode = d.get('mode')
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        pin = None; udata = None
        if mode == 'reg':
            pin = str(d.get('pin', ''))
            if conn.execute("SELECT 1 FROM users WHERE pin=?", (pin,)).fetchone():
                return emit('auth_res', {'st':'err','msg':'PIN sudah dipakai!'})
            conn.execute("INSERT INTO users (pin,name,av,password) VALUES (?,?,?,?)", (pin,d['name'],d['av'],d['pass']))
            udata = {'pin':pin,'name':d['name'],'av':d['av']}
        elif mode == 'login':
            u = conn.execute("SELECT * FROM users WHERE name=?", (d.get('name','').strip(),)).fetchone()
            if not u: return emit('auth_res', {'st':'err','msg':'Nama tidak ditemukan!'})
            if u['password'] != d.get('pass',''): return emit('auth_res', {'st':'err','msg':'Password salah!'})
            udata = u; pin = u['pin']
        elif mode == 'social':
            prov = d.get('provider'); sid2 = d.get('social_id','')
            col = 'google_id' if prov == 'google' else 'apple_id'
            u = conn.execute(f"SELECT * FROM users WHERE {col}=?", (sid2,)).fetchone()
            if u: udata = u; pin = u['pin']
            else: return emit('social_setup_needed', {'provider':prov,'social_id':sid2,'email':d.get('email','')})
        elif mode == 'social_reg':
            pin = str(d.get('pin',''))
            if conn.execute("SELECT 1 FROM users WHERE pin=?", (pin,)).fetchone():
                return emit('auth_res', {'st':'err','msg':'PIN sudah dipakai!'})
            prov = d.get('provider'); sid2 = d.get('social_id','')
            col = 'google_id' if prov == 'google' else 'apple_id'
            conn.execute(f"INSERT INTO users (pin,name,av,password,{col}) VALUES (?,?,?,?,?)", (pin,d['name'],d['av'],'',sid2))
            udata = {'pin':pin,'name':d['name'],'av':d['av']}
        else:
            return emit('auth_res', {'st':'err','msg':'Mode tidak dikenal'})
    u2, hist, friends, blocks, sett, icounts = _do_auth(pin)
    emit('auth_res', {'st':'ok','pin':pin,'name':udata['name'],'av':udata['av'],
                      'hist':hist,'friends':friends,'blocks':blocks,'settings':sett,
                      'locked_chats':{},'interaction_counts':icounts})

@socketio.on('mark_online')
def h_online(d):
    pin = d.get('pin',''); hide = d.get('hide', False)
    join_room(pin)
    if pin not in online_users: online_users[pin] = set()
    online_users[pin].add(request.sid)
    if not hide:
        with sqlite3.connect(DB_PATH) as c:
            c.row_factory = sqlite3.Row
            for f in c.execute("SELECT friend_pin FROM friends WHERE user_pin=? AND status='accepted'", (pin,)):
                emit('user_online', {'pin':pin}, room=f['friend_pin'])
    emit('online_status_confirmed', {'pin':pin})

@socketio.on('mark_offline')
def h_offline(d):
    pin = d.get('pin','')
    if pin in online_users:
        online_users[pin].discard(request.sid)
        if not online_users[pin]: del online_users[pin]
    with sqlite3.connect(DB_PATH) as c:
        c.row_factory = sqlite3.Row
        for f in c.execute("SELECT friend_pin FROM friends WHERE user_pin=? AND status='accepted'", (pin,)):
            emit('user_offline', {'pin':pin}, room=f['friend_pin'])

@socketio.on('update_settings')
def h_settings(d):
    pin = d.get('pin',''); key = d.get('key',''); val = d.get('val', 0)
    allowed = ['hide_online','dnd','stealth_read','mood','auto_delete','ai_style','lock_pin','ai_personality']
    if key not in allowed: return
    with sqlite3.connect(DB_PATH) as c:
        if key in ['hide_online','dnd','stealth_read']:
            c.execute(f"UPDATE users SET {key}=? WHERE pin=?", (1 if val else 0, pin))
        else:
            c.execute(f"UPDATE users SET {key}=? WHERE pin=?", (str(val), pin))
    emit('settings_updated', {'pin':pin,'key':key,'val':val})

@socketio.on('broadcast_mood')
def h_mood(d):
    with sqlite3.connect(DB_PATH) as c:
        c.row_factory = sqlite3.Row
        for f in c.execute("SELECT friend_pin FROM friends WHERE user_pin=? AND status='accepted'", (d['pin'],)):
            emit('user_mood_update', {'pin':d['pin'],'mood':d['mood']}, room=f['friend_pin'])

@socketio.on('edit_profile')
def h_edit_prof(d):
    with sqlite3.connect(DB_PATH) as c:
        c.execute("UPDATE users SET name=?,av=? WHERE pin=?", (d['name'],d['av'],d['pin']))
    emit('profile_updated', {'name':d['name'],'av':d['av']})
    with sqlite3.connect(DB_PATH) as c:
        c.row_factory = sqlite3.Row
        for f in c.execute("SELECT friend_pin FROM friends WHERE user_pin=? AND status='accepted'", (d['pin'],)):
            emit('profile_realtime', {'pin':d['pin'],'name':d['name'],'av':d['av']}, room=f['friend_pin'])

@socketio.on('increment_interaction')
def h_interaction(d):
    frm = d.get('from',''); to = d.get('to','')
    if not frm or not to: return
    with sqlite3.connect(DB_PATH) as c:
        c.execute("UPDATE friends SET interaction_count=interaction_count+1 WHERE user_pin=? AND friend_pin=?", (frm,to))
        c.execute("UPDATE friends SET interaction_count=interaction_count+1 WHERE user_pin=? AND friend_pin=?", (to,frm))
    emit('interaction_updated', {'peer':to}, room=frm)
    emit('interaction_updated', {'peer':frm}, room=to)

@socketio.on('msg')
def h_msg(d):
    t = datetime.datetime.now().strftime("%H:%M")
    force = d.get('force', False)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        if conn.execute("SELECT 1 FROM blocks WHERE blocker=? AND blocked=?", (d['to'],d['from'])).fetchone() and not force: return
        recv = conn.execute("SELECT * FROM users WHERE pin=?", (d['to'],)).fetchone()
        send = conn.execute("SELECT * FROM users WHERE pin=?", (d['from'],)).fetchone()
        if not recv or not send: return
        if recv['dnd'] and not force:
            emit('recipient_dnd', {'name':recv['name'],'peer':d['to']}, room=d['from']); return
        cur = conn.execute(
            "INSERT INTO msgs (s_pin,r_pin,cont,type,status,time,transcript,duration,auto_delete_secs,reply_to) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (d['from'],d['to'],d['cont'],d['type'],'Sent',t,
             d.get('transcript',''),d.get('duration',0),d.get('auto_delete',0),d.get('reply_to')))
        mid = cur.lastrowid
    p = {'id':mid,'sender':d['from'],'cont':d['cont'],'type':d['type'],'time':t,'status':'Sent',
         'transcript':d.get('transcript',''),'duration':d.get('duration',0),
         'edited':0,'deleted':0,'reply_to':d.get('reply_to'),'auto_delete_secs':d.get('auto_delete',0)}
    emit('new_upd', {'to':d['to'],'oN':recv['name'],'oA':recv['av'],'msg':p}, room=d['from'])
    emit('new_upd', {'to':d['from'],'oN':send['name'],'oA':send['av'],'msg':p}, room=d['to'])
    for pin in [d['from'], d['to']]:
        if pin in ai_context_cache:
            ai_context_cache[pin]['text'] += f"\n[{t}] {send['name'] if pin==d['to'] else recv['name']}: {d['cont']}"

@socketio.on('edit_msg')
def h_edit(d):
    with sqlite3.connect(DB_PATH) as c:
        c.execute("UPDATE msgs SET cont=?,edited=1 WHERE id=? AND s_pin=?", (d['cont'],d['id'],d['from']))
    emit('msg_edited', d, room=d['from'])
    emit('msg_edited', d, room=d['to'])

@socketio.on('delete_msg')
def h_del(d):
    with sqlite3.connect(DB_PATH) as c:
        c.execute("UPDATE msgs SET deleted=1 WHERE id=? AND s_pin=?", (d['id'],d['from']))
    emit('msg_deleted', d, room=d['from'])
    emit('msg_deleted', d, room=d['to'])

@socketio.on('read_all')
def h_read(d):
    with sqlite3.connect(DB_PATH) as c:
        c.execute("UPDATE msgs SET status='Read' WHERE s_pin=? AND r_pin=?", (d['to'],d['from']))
    emit('read_sync', {'from':d['from']}, room=d['to'])

@socketio.on('typing')
def h_type(d): emit('is_typing', d, room=d['to'], include_self=False)

@socketio.on('find')
def h_find(d):
    with sqlite3.connect(DB_PATH) as c:
        c.row_factory = sqlite3.Row
        u = c.execute("SELECT * FROM users WHERE pin=?", (str(d['pin']),)).fetchone()
    if u: emit('found_scan' if d.get('fromScan') else 'found', {'pin':u['pin'],'name':u['name'],'av':u['av']})
    else: emit('not_found', {})

@socketio.on('friend_request')
def h_freq(d): emit('friend_request_incoming', d, room=d['to'])

@socketio.on('friend_accept')
def h_facc(d):
    fp = d['from']; tp = d['to']
    with sqlite3.connect(DB_PATH) as c:
        c.execute("INSERT OR REPLACE INTO friends (user_pin,friend_pin,status,interaction_count) VALUES (?,?,'accepted',0)", (fp,tp))
        c.execute("INSERT OR REPLACE INTO friends (user_pin,friend_pin,status,interaction_count) VALUES (?,?,'accepted',0)", (tp,fp))
    emit('friend_added', {'pin':tp,'name':d['toName'],'av':d['toAv']}, room=fp)
    emit('friend_added', {'pin':fp,'name':d['fromName'],'av':d['fromAv']}, room=tp)

@socketio.on('delete_friend')
def h_delf(d):
    with sqlite3.connect(DB_PATH) as c:
        c.execute("DELETE FROM friends WHERE (user_pin=? AND friend_pin=?) OR (user_pin=? AND friend_pin=?)", (d['from'],d['to'],d['to'],d['from']))
    emit('friend_removed', {'by':d['from']}, room=d['to'])

@socketio.on('block_user')
def h_block(d):
    with sqlite3.connect(DB_PATH) as c:
        c.execute("INSERT OR REPLACE INTO blocks VALUES (?,?)", (d['blocker'],d['blocked']))
    emit('you_are_blocked', {'blocker':d['blocker']}, room=d['blocked'])

@socketio.on('unblock_user')
def h_unblock(d):
    with sqlite3.connect(DB_PATH) as c:
        c.execute("DELETE FROM blocks WHERE blocker=? AND blocked=?", (d['blocker'],d['blocked']))
    emit('you_are_unblocked', {'blocker':d['blocker']}, room=d['blocked'])

@socketio.on('delete_chat')
def h_dchat(d):
    with sqlite3.connect(DB_PATH) as c:
        c.execute("DELETE FROM msgs WHERE (s_pin=? AND r_pin=?) OR (s_pin=? AND r_pin=?)", (d['from'],d['to'],d['to'],d['from']))
    emit('chat_deleted', {'by':d['from']}, room=d['to'])

@socketio.on('lock_chat')
def h_lock(d): pass

@socketio.on('save_reminder')
def h_reminder(d):
    with sqlite3.connect(DB_PATH) as c:
        c.execute("INSERT INTO reminders (pin,text,remind_at) VALUES (?,?,?)", (d['pin'],d['text'],d['at']))

@socketio.on('request_ai_context')
def h_ai_ctx(d):
    pin = d.get('pin','')
    if not pin: return
    ctx = get_chat_context(pin)
    ai_context_cache[pin] = {'text': ctx, 'updated': time.time()}

# ── AI Socket Handlers ────────────────────────────────────────────────────────
@socketio.on('ai_tone_check')
def h_ai_tone(d):
    pin = d.get('pin',''); msg = d.get('message','')
    def _run():
        result = ai_tone_check(msg)
        socketio.emit('ai_tone_result', result, room=pin)
    threading.Thread(target=_run, daemon=True).start()

@socketio.on('ai_rewrite')
def h_ai_rewrite(d):
    pin = d.get('pin',''); msg = d.get('message',''); tone = d.get('tone','friendly')
    def _run():
        personality = 'friendly'
        try:
            with sqlite3.connect(DB_PATH) as _c:
                _c.row_factory = sqlite3.Row
                _u = _c.execute("SELECT ai_personality FROM users WHERE pin=?", (pin,)).fetchone()
                if _u and _u['ai_personality']: personality = _u['ai_personality']
        except: pass
        result = ai_rewrite_tone(msg, tone, personality)
        socketio.emit('ai_rewrite_result', {'result': result}, room=pin)
    threading.Thread(target=_run, daemon=True).start()

@socketio.on('ai_search')
def h_ai_search(d):
    pin = d.get('pin',''); query = d.get('query','')
    def _run():
        ctx = ai_context_cache.get(pin, {}).get('text') or get_chat_context(pin)
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            u = conn.execute("SELECT name FROM users WHERE pin=?", (pin,)).fetchone()
            uname = u['name'] if u else pin
        result = ai_smart_search(query, ctx, uname)
        socketio.emit('ai_search_result', result, room=pin)
    threading.Thread(target=_run, daemon=True).start()

@socketio.on('ai_summarize')
def h_ai_summarize(d):
    pin = d.get('pin',''); peer = d.get('peer',''); peer_name = d.get('peer_name','')
    def _run():
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            msgs = conn.execute(
                "SELECT s_pin, cont, time FROM msgs WHERE ((s_pin=? AND r_pin=?) OR (s_pin=? AND r_pin=?)) AND deleted=0 AND type='text' ORDER BY id DESC LIMIT 100",
                (pin,peer,peer,pin)).fetchall()
            u = conn.execute("SELECT name FROM users WHERE pin=?", (pin,)).fetchone()
            uname = u['name'] if u else pin
        ctx = '\n'.join([f"[{r['time']}] {r['s_pin']}: {r['cont']}" for r in reversed(msgs)])
        result = ai_summarize(ctx, uname, peer_name)
        socketio.emit('ai_summary_result', result, room=pin)
    threading.Thread(target=_run, daemon=True).start()

@socketio.on('ai_chat')
def h_ai_chat(d):
    pin = d.get('pin',''); msg = d.get('message','')
    history = d.get('history', [])
    personality = d.get('personality', 'friendly')
    def _run():
        ctx = ai_context_cache.get(pin, {}).get('text', '')
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            u = conn.execute("SELECT name FROM users WHERE pin=?", (pin,)).fetchone()
            uname = u['name'] if u else pin
        result = ai_chat(msg, history, uname, ctx, personality)
        socketio.emit('ai_chat_result', {'result': result}, room=pin)
    threading.Thread(target=_run, daemon=True).start()

@socketio.on('ai_inline_chat')
def h_ai_inline(d):
    pin = d.get('pin',''); msg = d.get('message','')
    def _run():
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            u = conn.execute("SELECT name,ai_personality FROM users WHERE pin=?", (pin,)).fetchone()
            uname = u['name'] if u else pin
            personality = u['ai_personality'] if u else 'friendly'
        ctx = ai_context_cache.get(pin, {}).get('text', '')
        result = ai_chat(msg, [], uname, ctx, personality)
        socketio.emit('ai_inline_result', {'result': result}, room=pin)
    threading.Thread(target=_run, daemon=True).start()

# ── WebRTC Relay ──────────────────────────────────────────────────────────────
@socketio.on('call_offer')
def h_co(d): emit('call_offer', d, room=d['to'])
@socketio.on('call_answer')
def h_ca(d): emit('call_answer', d, room=d['to'])
@socketio.on('call_ice')
def h_ci(d): emit('call_ice', d, room=d['to'])
@socketio.on('call_reject')
def h_cr(d): emit('call_reject', d, room=d['to'])
@socketio.on('call_end')
def h_ce(d): emit('call_ended', d, room=d['to'])

# ── Reminder Checker ──────────────────────────────────────────────────────────
def check_reminders():
    while True:
        time.sleep(30)
        now = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M")
        with sqlite3.connect(DB_PATH) as c:
            c.row_factory = sqlite3.Row
            due = c.execute("SELECT * FROM reminders WHERE done=0 AND remind_at<=?", (now,)).fetchall()
            for r in due:
                if r['pin'] in online_users:
                    socketio.emit('reminder_due', {'text':r['text']}, room=r['pin'])
                    c.execute("UPDATE reminders SET done=1 WHERE id=?", (r['id'],))

threading.Thread(target=check_reminders, daemon=True).start()

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')

if __name__ == '__main__':
    print("=" * 60)
    print("  Faiz Messenger v10.0 — Editorial Minimalism")
    print(f"  g4f AI: {'✅ Available' if G4F_AVAILABLE else '⚠️  Not available'}")
    print(f"  Providers: {[p.__name__ for p,m in _PROVIDER_CHAIN]}")
    print("  Running on: http://0.0.0.0:8080")
    print("=" * 60)
    socketio.run(app, host='0.0.0.0', port=8080, debug=False, allow_unsafe_werkzeug=True)