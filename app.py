import os, json, base64, requests, threading, io, time
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, template_folder="template")
app.secret_key = os.environ.get("SECRET_KEY", "cronicas-del-abismo-2024")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ── Configuración ──────────────────────────────────────────────
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise ValueError("Falta GROQ_API_KEY")

GROQ_URL   = "https://api.groq.com/openai/v1/chat/completions"
MODEL_NAME = "llama-3.1-8b-instant"

with open(os.path.join(BASE_DIR, "prompts", "system_prompt.txt"), "r", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()

COLORS  = ["#C8A040", "#A04040", "#4080A0", "#60A060",
           "#9060A0", "#C07030", "#40A0A0", "#A06080"]
AVATARS = ["🧙","⚔️","🏹","🛡️","🗡️","🔮","⚗️","🌿"]

# ── Estado global ───────────────────────────────────────────────
game_state = {
    "configured":   False,   # True cuando el primer jugador configuró la sala
    "max_humans":   1,
    "max_players":  4,
    "players":      {},      # pid → {name,avatar,alignment,cls,color,is_npc,personality}
    "turn_order":   [],
    "current_turn": 0,
    "conversation": [{"role": "system", "content": SYSTEM_PROMPT}],
    "chat_log":     [],
    "npc_processing": False  # evita que dos turnos NPC corran a la vez
}
state_lock = threading.Lock()

# ── Helpers ─────────────────────────────────────────────────────
def call_groq(messages, temperature=0.75, max_tokens=600):
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type":  "application/json"
    }
    payload = {
        "model": MODEL_NAME, "messages": messages,
        "temperature": temperature, "max_tokens": max_tokens,
        "top_p": 0.9, "stream": False
    }
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
        tts = gTTS(text=text[:500], lang="es", slow=False)
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
        "type": type_, "sender": sender,
        "avatar": avatar, "color": color,
        "text": text, "badge": badge
    })

# ── Lógica NPC ──────────────────────────────────────────────────
def npc_take_turn(pid):
    """Ejecuta el turno de un NPC. Llamado en hilo separado."""
    with state_lock:
        if game_state["npc_processing"]:
            return
        game_state["npc_processing"] = True
        p = game_state["players"].get(pid)
        if not p:
            game_state["npc_processing"] = False
            return

    # Pequeña pausa para que se sienta natural
    time.sleep(2)

    # Construir contexto del NPC
    recent = "\n".join(
        f"{m['sender']}: {m['text']}"
        for m in game_state["chat_log"][-8:]
        if m["type"] in ("player", "gm", "npc")
    )

    npc_prompt = [
        {
            "role": "system",
            "content": (
                f"Eres {p['name']}, un {p['cls']} {p['alignment']} en una partida de AD&D. "
                f"Personalidad: {p.get('personality','aventurero pragmático')}. "
                "Eres un JUGADOR, no el Dungeon Master. Reaccionás a lo que narra el DM "
                "y a las acciones de tus compañeros. "
                "Decidís UNA acción concreta y breve (1-3 oraciones) para tu turno. "
                "Si la situación no requiere que actúes, podés pasar con una frase corta. "
                "Hablás en primera persona. No interpretás al DM ni describís el entorno. "
                "No uses asteriscos ni acotaciones de narrador."
            )
        },
        {
            "role": "user",
            "content": (
                f"Contexto reciente de la partida:\n{recent}\n\n"
                f"Es tu turno, {p['name']}. ¿Qué hacés?"
            )
        }
    ]

    npc_action = call_groq(npc_prompt, temperature=0.85, max_tokens=150)
    if not npc_action:
        npc_action = f"{p['name']} observa en silencio, esperando el momento oportuno."

    # Determinar si el NPC pasa (respuesta muy corta o contiene "paso"/"espero")
    passing_keywords = ["paso", "espero", "observo en silencio", "aguardo", "nada por ahora"]
    is_passing = len(npc_action) < 40 or any(kw in npc_action.lower() for kw in passing_keywords)

    with state_lock:
        user_msg = f"[{p['name']} el {p['cls']} — NPC]: {npc_action}"
        game_state["conversation"].append({"role": "user", "content": user_msg})
        append_chat(
            "npc" if not is_passing else "npc-pass",
            p["name"], p["avatar"], p["color"],
            npc_action, badge="NPC"
        )

    # Llamar al GM solo si el NPC hizo algo (no si pasó)
    gm_response = None
    if not is_passing:
        gm_response = call_groq(game_state["conversation"])
        if not gm_response:
            gm_response = "El Dungeon Master asiente en silencio."
        with state_lock:
            game_state["conversation"].append({"role": "assistant", "content": gm_response})
            append_chat("gm", "Dungeon Master", "🎭", "#C8A040", gm_response)

    # Avanzar turno
    with state_lock:
        game_state["npc_processing"] = False
        _advance_turn()

    broadcast_state()

    # Si el siguiente también es NPC, lanzar otro hilo
    with state_lock:
        next_pid = _current_pid()
        next_is_npc = next_pid and game_state["players"].get(next_pid, {}).get("is_npc", False)

    if next_is_npc:
        threading.Thread(target=npc_take_turn, args=(next_pid,), daemon=True).start()

def _advance_turn():
    if game_state["turn_order"]:
        game_state["current_turn"] = (
            game_state["current_turn"] + 1
        ) % len(game_state["turn_order"])

def _current_pid():
    if not game_state["turn_order"]:
        return None
    return game_state["turn_order"][game_state["current_turn"]]

# ── Rutas ───────────────────────────────────────────────────────
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

# ── Generar NPCs con LLM ────────────────────────────────────────
@app.route("/api/generate_npcs", methods=["POST"])
def generate_npcs():
    data       = request.json
    count      = int(data.get("count", 0))
    max_humans = int(data.get("max_humans", 1))

    if count <= 0:
        return jsonify({"npcs": []})

    classes    = ["Guerrero","Mago","Clérigo","Ladrón","Paladín","Druida","Bardo","Explorador"]
    alignments = ["Legal Bueno","Neutral Bueno","Caótico Bueno",
                  "Legal Neutral","Neutral","Caótico Neutral",
                  "Legal Malvado","Neutral Malvado","Caótico Malvado"]

    prompt = [
        {
            "role": "system",
            "content": (
                "Eres un generador de personajes para AD&D. "
                "Respondés SOLO con un array JSON válido, sin texto adicional, "
                "sin bloques de código, sin explicaciones."
            )
        },
        {
            "role": "user",
            "content": (
                f"Genera {count} personajes únicos y contrastantes para una partida de AD&D. "
                f"Clases disponibles: {', '.join(classes)}. "
                f"Alineamientos disponibles: {', '.join(alignments)}. "
                "Cada personaje debe tener: name (nombre fantástico en español, máx 15 chars), "
                "cls (una de las clases), alignment (uno de los alineamientos), "
                "personality (rasgo de personalidad breve, máx 60 chars, en español). "
                f"Que sean variados y complementarios entre sí. "
                f"Formato: [{{'name':'...','cls':'...','alignment':'...','personality':'...'}}]"
            )
        }
    ]

    raw = call_groq(prompt, temperature=0.9, max_tokens=400)
    try:
        # Limpiar posibles bloques de código
        clean = raw.replace("```json","").replace("```","").strip()
        npcs  = json.loads(clean)
        if not isinstance(npcs, list):
            raise ValueError("No es lista")
        # Sanitizar
        npcs = npcs[:count]
        for i, npc in enumerate(npcs):
            npc["name"]        = str(npc.get("name","NPC"))[:20]
            npc["cls"]         = npc.get("cls","Guerrero")
            npc["alignment"]   = npc.get("alignment","Neutral")
            npc["personality"] = str(npc.get("personality","misterioso"))[:80]
            npc["avatar"]      = AVATARS[(max_humans + i) % len(AVATARS)]
        return jsonify({"npcs": npcs})
    except Exception as e:
        print(f"❌ Error parseando NPCs: {e}\nRaw: {raw}")
        # Fallback manual
        fallback = [
            {"name": f"Aventurero {i+1}", "cls": classes[i % len(classes)],
             "alignment": "Neutral", "personality": "callado pero leal",
             "avatar": AVATARS[(max_humans + i) % len(AVATARS)]}
            for i in range(count)
        ]
        return jsonify({"npcs": fallback})

# ── Configurar sala (primer jugador) ───────────────────────────
@app.route("/api/configure", methods=["POST"])
def configure_room():
    data       = request.json
    max_humans = int(data.get("max_humans", 1))
    npcs       = data.get("npcs", [])   # lista de {name,cls,alignment,personality,avatar}

    with state_lock:
        if game_state["configured"]:
            return jsonify({"error": "La sala ya fue configurada"}), 400
        if max_humans < 1 or max_humans > 4:
            return jsonify({"error": "Entre 1 y 4 jugadores humanos"}), 400

        game_state["max_humans"]  = max_humans
        game_state["max_players"] = max_humans + len(npcs)
        game_state["configured"]  = True

        # Registrar NPCs
        for i, npc in enumerate(npcs):
            color_idx = (max_humans + i) % len(COLORS)
            pid = f"npc_{i+1}_{npc['name']}"
            game_state["players"][pid] = {
                "name":        npc["name"],
                "avatar":      npc.get("avatar", AVATARS[(max_humans+i) % len(AVATARS)]),
                "alignment":   npc["alignment"],
                "cls":         npc["cls"],
                "color":       COLORS[color_idx],
                "is_npc":      True,
                "personality": npc.get("personality", "aventurero")
            }
            game_state["turn_order"].append(pid)

    broadcast_state()
    return jsonify({"ok": True})

# ── Unirse a partida (jugadores humanos) ───────────────────────
@app.route("/api/join", methods=["POST"])
def join_game():
    data      = request.json
    name      = data.get("name","").strip()[:20]
    avatar    = data.get("avatar","🧙")
    alignment = data.get("alignment","Neutral")
    cls       = data.get("cls","Guerrero")

    if not name:
        return jsonify({"error": "Nombre requerido"}), 400

    with state_lock:
        if not game_state["configured"]:
            return jsonify({"error": "La sala aún no fue configurada"}), 400

        human_count = sum(1 for p in game_state["players"].values() if not p.get("is_npc"))
        if human_count >= game_state["max_humans"]:
            return jsonify({"error": f"Ya hay {game_state['max_humans']} jugadores humanos"}), 400

        for p in game_state["players"].values():
            if p["name"].lower() == name.lower():
                return jsonify({"error": "Ese nombre ya está en uso"}), 400

        color_idx = human_count % len(COLORS)
        pid = f"h{human_count+1}_{name}"

        game_state["players"][pid] = {
            "name":      name,
            "avatar":    avatar,
            "alignment": alignment,
            "cls":       cls,
            "color":     COLORS[color_idx],
            "is_npc":    False,
            "personality": ""
        }

        # Insertar al humano en la posición correcta (antes de los NPCs o al final)
        # Los humanos van intercalados con los NPCs para turnos variados
        npc_pids   = [p for p in game_state["turn_order"] if p.startswith("npc_")]
        human_pids = [p for p in game_state["turn_order"] if not p.startswith("npc_")]
        human_pids.append(pid)

        # Intercalar: h1, npc1, h2, npc2...
        new_order = []
        for i in range(max(len(human_pids), len(npc_pids))):
            if i < len(human_pids): new_order.append(human_pids[i])
            if i < len(npc_pids):   new_order.append(npc_pids[i])
        game_state["turn_order"] = new_order
        game_state["current_turn"] = 0

        human_count_new = human_count + 1
        all_humans_joined = human_count_new >= game_state["max_humans"]

    broadcast_state()

    # Si ya entraron todos los humanos, arrancar la partida con mensaje del GM
    if all_humans_joined:
        threading.Thread(target=_start_game, daemon=True).start()

    return jsonify({
        "player_id":   pid,
        "players":     game_state["players"],
        "turn_order":  game_state["turn_order"],
        "current_turn": game_state["current_turn"]
    })

def _start_game():
    """Pide al GM que presente a todos los aventureros e inicie la historia."""
    time.sleep(1)
    party = ", ".join(
        f"{p['name']} el {p['cls']}"
        for p in game_state["players"].values()
    )
    intro_msg = (
        f"El grupo de aventureros que se reunió esta noche es: {party}. "
        "Presenta brevemente la escena de apertura de la aventura, "
        "nombrando a cada personaje y estableciendo el ambiente. "
        "Luego dirige la primera pregunta o situación al primer jugador en el orden de turno."
    )
    game_state["conversation"].append({"role": "user", "content": intro_msg})
    gm_intro = call_groq(game_state["conversation"])
    if not gm_intro:
        gm_intro = "La aventura comienza..."
    game_state["conversation"].append({"role": "assistant", "content": gm_intro})

    with state_lock:
        append_chat("gm", "Dungeon Master", "🎭", "#C8A040", gm_intro)

    audio = generate_audio(gm_intro)
    broadcast_state({"intro_audio": audio})

    # Si el primer turno es NPC, arrancarlo
    with state_lock:
        first_pid = _current_pid()
        first_is_npc = first_pid and game_state["players"].get(first_pid,{}).get("is_npc", False)
    if first_is_npc:
        threading.Thread(target=npc_take_turn, args=(first_pid,), daemon=True).start()

# ── Acción humana ──────────────────────────────────────────────
@app.route("/api/action", methods=["POST"])
def handle_action():
    data      = request.json
    player_id = data.get("player_id","")
    action    = data.get("action","").strip()

    if not action:
        return jsonify({"error": "Acción vacía"}), 400

    with state_lock:
        if not game_state["turn_order"]:
            return jsonify({"error": "No hay jugadores"}), 400
        current_pid = _current_pid()
        if player_id != current_pid:
            return jsonify({"error": "No es tu turno"}), 403

        p = game_state["players"][player_id]
        user_msg = f"[{p['name']} el {p['cls']}]: {action}"
        game_state["conversation"].append({"role": "user", "content": user_msg})
        append_chat("player", p["name"], p["avatar"], p["color"], action)

    gm_response = call_groq(game_state["conversation"])
    if not gm_response:
        gm_response = "El Dungeon Master guarda silencio... intentá de nuevo."

    with state_lock:
        game_state["conversation"].append({"role": "assistant", "content": gm_response})
        append_chat("gm", "Dungeon Master", "🎭", "#C8A040", gm_response)
        _advance_turn()
        next_pid     = _current_pid()
        next_is_npc  = next_pid and game_state["players"].get(next_pid,{}).get("is_npc", False)

    audio = generate_audio(gm_response)
    broadcast_state({"gm_audio": audio})

    if next_is_npc:
        threading.Thread(target=npc_take_turn, args=(next_pid,), daemon=True).start()

    return jsonify({
        "text":         gm_response,
        "audio_base64": audio,
        "current_turn": game_state["current_turn"]
    })

# ── Salir ──────────────────────────────────────────────────────
@app.route("/api/leave", methods=["POST"])
def leave_game():
    data = request.json
    pid  = data.get("player_id","")
    with state_lock:
        if pid not in game_state["players"]:
            return jsonify({"ok": True})
        name = game_state["players"][pid]["name"]
        del game_state["players"][pid]
        if pid in game_state["turn_order"]:
            game_state["turn_order"].remove(pid)
        if game_state["turn_order"]:
            game_state["current_turn"] %= len(game_state["turn_order"])
        else:
            game_state["current_turn"] = 0
        append_chat("system","Sistema","⚙️","#666",f"{name} abandonó la partida.")

    broadcast_state()
    return jsonify({"ok": True})

# ── WebSocket ──────────────────────────────────────────────────
@socketio.on("join_room")
def on_join(data):
    join_room("main")
    emit("game_update", {
        "chat_log":     game_state["chat_log"][-50:],
        "current_turn": game_state["current_turn"],
        "turn_order":   game_state["turn_order"],
        "players":      game_state["players"],
        "configured":   game_state["configured"]
    })

# ── Inicio ─────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port)