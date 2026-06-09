import os, json, base64, requests, threading, io, time
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, template_folder="template")
app.secret_key = os.environ.get("SECRET_KEY", "cronicas-del-abismo-2024")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ── Configuración ──────────────────────────────────────────────
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL_NPC = "meta-llama/llama-3.1-8b-instruct:free"

def call_npc_model(messages, max_tokens=120):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_KEY}",
        "Content-Type": "application/json"
    }
    payload = {"model": MODEL_NPC, "messages": messages,
               "temperature": 0.85, "max_tokens": max_tokens}
    try:
        r = requests.post(OPENROUTER_URL, json=payload, headers=headers, timeout=30)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"❌ OpenRouter error: {e}")
        return None
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise ValueError("Falta GROQ_API_KEY")

GROQ_URL   = "https://api.groq.com/openai/v1/chat/completions"
MODEL_NAME = "llama-3.1-8b-instant"
MAX_HISTORY = 6  # mensajes recientes enviados a Groq (excluye system)

# Prompt corto para no superar el límite de tokens de Groq gratuito
SYSTEM_PROMPT = """Eres el Dungeon Master de Crónicas del Abismo, AD&D 3.5 fantasía oscura. Narrás en español con voz épica y atmosférica.

REGLAS CORE:
- Atributos: FUE, DES, CON, INT, SAB, CAR (3-20). Mod=(valor-10)//2
- Combate: 1d20+AB+mod vs Defensa(10+DES+armadura). Crítico en 20 natural (daño doble). 1 natural = fallo automático
- Acciones por turno: Mayor + Movimiento + Libre. Flanqueo: +2 ataque
- Maniobras: derribo, desarme, empuje, presa (FUE vs FUE/DES)
- Magia: d20+mod INT/SAB vs dificultad (nivel×3+5). Fallo grave = disrupción caótica
- Clases: Guerrero(d10), Mago(d4), Clérigo(d8), Ladrón(d6), Paladín(d10), Druida(d8), Bardo(d6), Explorador(d8)
- Salvaciones: Fortaleza(CON), Reflejos(DES), Voluntad(SAB)
- 0 PG = inconsciente. -10 PG = muerto
- Enemigos huyen al 50% PG (Vol CD12)

ESTILO: Narrás con dramatismo. Nunca exponés estadísticas crudas. El mundo reacciona a las decisiones. Describís con detalle sensorial. Los NPCs tienen motivos propios."""

COLORS  = ["#C8A040","#A04040","#4080A0","#60A060","#9060A0","#C07030","#40A0A0","#A06080"]
AVATARS = ["🧙","⚔️","🏹","🛡️","🗡️","🔮","⚗️","🌿"]

# ── Estado global ──────────────────────────────────────────────
game_state = {
    "configured":     False,
    "max_humans":     1,
    "max_players":    4,
    "players":        {},
    "turn_order":     [],
    "current_turn":   0,
    "conversation":   [{"role":"system","content":SYSTEM_PROMPT}],
    "chat_log":       [],
    "npc_processing": False
}
state_lock = threading.Lock()

# ── Helpers ────────────────────────────────────────────────────
def call_groq(messages, temperature=0.75, max_tokens=500):
    # Enviar solo system + últimos MAX_HISTORY mensajes
    if len(messages) > MAX_HISTORY + 1:
        messages = [messages[0]] + messages[-(MAX_HISTORY):]
    headers = {"Authorization":f"Bearer {GROQ_API_KEY}","Content-Type":"application/json"}
    payload = {"model":MODEL_NAME,"messages":messages,
               "temperature":temperature,"max_tokens":max_tokens,"top_p":0.9,"stream":False}
    try:
        r = requests.post(GROQ_URL, json=payload, headers=headers, timeout=45)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"❌ Groq error: {e}")
        return None

def generate_audio(text):
    try:
        from gtts import gTTS
        tts = gTTS(text=text[:400], lang="es", slow=False)
        buf = io.BytesIO()
        tts.write_to_fp(buf)
        buf.seek(0)
        return base64.b64encode(buf.read()).decode("utf-8")
    except Exception as e:
        print(f"❌ TTS error: {e}")
        return None

def broadcast_state(extra=None):
    payload = {
        "chat_log":     game_state["chat_log"][-50:],
        "current_turn": game_state["current_turn"],
        "turn_order":   game_state["turn_order"],
        "players":      game_state["players"],
        "configured":   game_state["configured"],
    }
    if extra:
        payload.update(extra)
    socketio.emit("game_update", payload, room="main")

def append_chat(type_, sender, avatar, color, text, badge=""):
    game_state["chat_log"].append({
        "type":type_,"sender":sender,"avatar":avatar,
        "color":color,"text":text,"badge":badge
    })

def _advance_turn():
    if game_state["turn_order"]:
        game_state["current_turn"] = (game_state["current_turn"]+1) % len(game_state["turn_order"])

def _current_pid():
    if not game_state["turn_order"]: return None
    return game_state["turn_order"][game_state["current_turn"]]

# ── Lógica NPC ─────────────────────────────────────────────────
def npc_take_turn(pid):
    with state_lock:
        if game_state["npc_processing"]: return
        game_state["npc_processing"] = True
        p = game_state["players"].get(pid)
        if not p:
            game_state["npc_processing"] = False
            return

    time.sleep(2)

    recent = "\n".join(
        f"{m['sender']}: {m['text']}"
        for m in game_state["chat_log"][-6:]
        if m["type"] in ("player","gm","npc")
    )

    npc_prompt = [
        {"role":"system","content":(
            f"Eres {p['name']}, un {p['cls']} {p['alignment']}. "
            f"Personalidad: {p.get('personality','aventurero pragmático')}. "
            "Eres JUGADOR, no DM. Decidís UNA acción breve (1-3 oraciones) para tu turno. "
            "Si no hay nada relevante, pasás con una frase corta. Primera persona. Sin asteriscos."
        )},
        {"role":"user","content":(
            f"Contexto reciente:\n{recent}\n\n¿Qué hacés, {p['name']}?"
        )}
    ]

    npc_action = call_groq(npc_prompt, temperature=0.85, max_tokens=120)
    if not npc_action:
        npc_action = f"{p['name']} observa en silencio."

    passing_kw = ["paso","espero","observo en silencio","aguardo","nada por ahora"]
    is_passing = len(npc_action) < 40 or any(kw in npc_action.lower() for kw in passing_kw)

    with state_lock:
        game_state["conversation"].append({"role":"user","content":f"[{p['name']} NPC]: {npc_action}"})
        append_chat("npc" if not is_passing else "npc-pass",
                    p["name"],p["avatar"],p["color"],npc_action,badge="NPC")

    gm_response = None
    if not is_passing:
        gm_response = call_groq(game_state["conversation"])
        if not gm_response:
            gm_response = "El Dungeon Master asiente en silencio."
        with state_lock:
            game_state["conversation"].append({"role":"assistant","content":gm_response})
            append_chat("gm","Dungeon Master","🎭","#C8A040",gm_response)

    with state_lock:
        game_state["npc_processing"] = False
        _advance_turn()

    broadcast_state()

    with state_lock:
        next_pid     = _current_pid()
        next_is_npc  = next_pid and game_state["players"].get(next_pid,{}).get("is_npc",False)
    if next_is_npc:
        threading.Thread(target=npc_take_turn,args=(next_pid,),daemon=True).start()

# ── Rutas ──────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/state")
def get_state():
    return jsonify({
        "configured":   game_state["configured"],
        "max_humans":   game_state["max_humans"],
        "max_players":  game_state["max_players"],
        "players":      game_state["players"],
        "turn_order":   game_state["turn_order"],
        "current_turn": game_state["current_turn"],
        "chat_log":     game_state["chat_log"][-50:]
    })

@app.route("/api/generate_npcs", methods=["POST"])
def generate_npcs():
    data  = request.json
    count = int(data.get("count",0))
    max_h = int(data.get("max_humans",1))
    if count <= 0: return jsonify({"npcs":[]})

    classes = ["Guerrero","Mago","Clérigo","Ladrón","Paladín","Druida","Bardo","Explorador"]
    aligns  = ["Legal Bueno","Neutral Bueno","Caótico Bueno","Legal Neutral","Neutral",
               "Caótico Neutral","Legal Malvado","Neutral Malvado","Caótico Malvado"]

    prompt = [
        {"role":"system","content":"Generás personajes AD&D. Respondés SOLO con JSON array válido, sin texto extra ni bloques de código."},
        {"role":"user","content":(
            f"Generá {count} personajes únicos para AD&D. "
            f"Clases: {', '.join(classes)}. Alineamientos: {', '.join(aligns)}. "
            "Cada uno: name(máx 15 chars), cls, alignment, personality(máx 50 chars). "
            "Variados y complementarios. "
            f'Formato: [{{"name":"...","cls":"...","alignment":"...","personality":"..."}}]'
        )}
    ]

    raw = call_groq(prompt, temperature=0.9, max_tokens=350)
    try:
        clean = raw.replace("```json","").replace("```","").strip()
        npcs  = json.loads(clean)
        if not isinstance(npcs, list): raise ValueError
        npcs = npcs[:count]
        for i, npc in enumerate(npcs):
            npc["name"]        = str(npc.get("name","NPC"))[:20]
            npc["cls"]         = npc.get("cls","Guerrero")
            npc["alignment"]   = npc.get("alignment","Neutral")
            npc["personality"] = str(npc.get("personality","misterioso"))[:80]
            npc["avatar"]      = AVATARS[(max_h+i) % len(AVATARS)]
        return jsonify({"npcs":npcs})
    except Exception as e:
        print(f"❌ Error NPCs: {e} | Raw: {raw}")
        fallback = [
            {"name":f"Aventurero {i+1}","cls":classes[i%len(classes)],
             "alignment":"Neutral","personality":"callado pero leal",
             "avatar":AVATARS[(max_h+i)%len(AVATARS)]}
            for i in range(count)
        ]
        return jsonify({"npcs":fallback})

@app.route("/api/configure", methods=["POST"])
def configure_room():
    data  = request.json
    max_h = int(data.get("max_humans",1))
    npcs  = data.get("npcs",[])

    with state_lock:
        if game_state["configured"]:
            return jsonify({"error":"Ya configurada"}), 400
        game_state["max_humans"]  = max_h
        game_state["max_players"] = max_h + len(npcs)
        game_state["configured"]  = True

        for i, npc in enumerate(npcs):
            pid = f"npc_{i+1}_{npc['name']}"
            game_state["players"][pid] = {
                "name":npc["name"],"avatar":npc.get("avatar",AVATARS[(max_h+i)%len(AVATARS)]),
                "alignment":npc["alignment"],"cls":npc["cls"],
                "color":COLORS[(max_h+i)%len(COLORS)],
                "is_npc":True,"personality":npc.get("personality","aventurero")
            }
            game_state["turn_order"].append(pid)

    broadcast_state()
    return jsonify({"ok":True})

@app.route("/api/join", methods=["POST"])
def join_game():
    data      = request.json
    name      = data.get("name","").strip()[:20]
    avatar    = data.get("avatar","🧙")
    alignment = data.get("alignment","Neutral")
    cls       = data.get("cls","Guerrero")

    if not name: return jsonify({"error":"Nombre requerido"}), 400

    with state_lock:
        if not game_state["configured"]:
            return jsonify({"error":"Sala no configurada"}), 400
        human_count = sum(1 for p in game_state["players"].values() if not p.get("is_npc"))
        if human_count >= game_state["max_humans"]:
            return jsonify({"error":f"Ya hay {game_state['max_humans']} humanos"}), 400
        for p in game_state["players"].values():
            if p["name"].lower() == name.lower():
                return jsonify({"error":"Nombre en uso"}), 400

        pid = f"h{human_count+1}_{name}"
        game_state["players"][pid] = {
            "name":name,"avatar":avatar,"alignment":alignment,"cls":cls,
            "color":COLORS[human_count%len(COLORS)],"is_npc":False,"personality":""
        }

        npc_pids   = [p for p in game_state["turn_order"] if p.startswith("npc_")]
        human_pids = [p for p in game_state["turn_order"] if not p.startswith("npc_")]
        human_pids.append(pid)
        new_order = []
        for i in range(max(len(human_pids), len(npc_pids))):
            if i < len(human_pids): new_order.append(human_pids[i])
            if i < len(npc_pids):   new_order.append(npc_pids[i])
        game_state["turn_order"]   = new_order
        game_state["current_turn"] = 0

        all_joined = (human_count+1) >= game_state["max_humans"]

    broadcast_state()
    if all_joined:
        threading.Thread(target=_start_game, daemon=True).start()

    return jsonify({
        "player_id":    pid,
        "players":      game_state["players"],
        "turn_order":   game_state["turn_order"],
        "current_turn": game_state["current_turn"]
    })

def _start_game():
    time.sleep(1)
    party = ", ".join(f"{p['name']} el {p['cls']}" for p in game_state["players"].values())
    intro = (f"El grupo: {party}. Presentá la escena de apertura nombrando a cada personaje "
             "y estableciendo el ambiente oscuro. Luego dirigí la primera situación al grupo.")
    game_state["conversation"].append({"role":"user","content":intro})
    gm_intro = call_groq(game_state["conversation"])
    if not gm_intro: gm_intro = "La aventura comienza en la oscuridad..."
    game_state["conversation"].append({"role":"assistant","content":gm_intro})
    with state_lock:
        append_chat("gm","Dungeon Master","🎭","#C8A040",gm_intro)
    audio = generate_audio(gm_intro)
    broadcast_state({"intro_audio":audio})

    with state_lock:
        first_pid    = _current_pid()
        first_is_npc = first_pid and game_state["players"].get(first_pid,{}).get("is_npc",False)
    if first_is_npc:
        threading.Thread(target=npc_take_turn,args=(first_pid,),daemon=True).start()

@app.route("/api/action", methods=["POST"])
def handle_action():
    data      = request.json
    player_id = data.get("player_id","")
    action    = data.get("action","").strip()
    if not action: return jsonify({"error":"Acción vacía"}), 400

    with state_lock:
        if not game_state["turn_order"]: return jsonify({"error":"Sin jugadores"}), 400
        if player_id != _current_pid():  return jsonify({"error":"No es tu turno"}), 403
        p = game_state["players"][player_id]
        game_state["conversation"].append({"role":"user","content":f"[{p['name']} el {p['cls']}]: {action}"})
        append_chat("player",p["name"],p["avatar"],p["color"],action)

    gm_response = call_groq(game_state["conversation"])
    if not gm_response: gm_response = "El Dungeon Master guarda silencio... intentá de nuevo."

    with state_lock:
        game_state["conversation"].append({"role":"assistant","content":gm_response})
        append_chat("gm","Dungeon Master","🎭","#C8A040",gm_response)
        _advance_turn()
        next_pid    = _current_pid()
        next_is_npc = next_pid and game_state["players"].get(next_pid,{}).get("is_npc",False)

    audio = generate_audio(gm_response)
    broadcast_state({"gm_audio":audio})

    if next_is_npc:
        threading.Thread(target=npc_take_turn,args=(next_pid,),daemon=True).start()

    return jsonify({"text":gm_response,"audio_base64":audio,"current_turn":game_state["current_turn"]})

@app.route("/api/leave", methods=["POST"])
def leave_game():
    data = request.json
    pid  = data.get("player_id","")
    with state_lock:
        if pid not in game_state["players"]: return jsonify({"ok":True})
        name = game_state["players"][pid]["name"]
        del game_state["players"][pid]
        if pid in game_state["turn_order"]: game_state["turn_order"].remove(pid)
        game_state["current_turn"] = (game_state["current_turn"] % len(game_state["turn_order"])) if game_state["turn_order"] else 0
        append_chat("system","Sistema","⚙️","#666",f"{name} abandonó la partida.")
    broadcast_state()
    return jsonify({"ok":True})

@socketio.on("join_room")
def on_join(data):
    join_room("main")
    emit("game_update",{
        "chat_log":     game_state["chat_log"][-50:],
        "current_turn": game_state["current_turn"],
        "turn_order":   game_state["turn_order"],
        "players":      game_state["players"],
        "configured":   game_state["configured"]
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT",5000))
    socketio.run(app, host="0.0.0.0", port=port)
