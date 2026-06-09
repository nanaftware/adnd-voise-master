import os
import json
import base64
import requests
import threading
import io
from flask import Flask, render_template, request, jsonify, session
from flask_socketio import SocketIO, emit, join_room, leave_room

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, template_folder="template")
app.secret_key = os.environ.get("SECRET_KEY", "crónicas-del-abismo-2024")
socketio = SocketIO(app, cors_allowed_origins="*")

# ================= CONFIGURACIÓN =================
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise ValueError("❌ Falta la variable de entorno GROQ_API_KEY")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL_NAME = "llama-3.1-8b-instant"

with open(os.path.join(BASE_DIR, "prompts", "system_prompt.txt"), "r", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()

# ================= ESTADO GLOBAL DE LA PARTIDA =================
# Sala única por ahora (se puede extender a múltiples salas)
game_state = {
    "players": {},        # { player_id: { name, avatar, alignment, class, color } }
    "turn_order": [],     # lista de player_ids en orden de turno
    "current_turn": 0,    # índice en turn_order
    "conversation": [     # historial para Groq
        {"role": "system", "content": SYSTEM_PROMPT}
    ],
    "chat_log": []        # log visual para todos
}
state_lock = threading.Lock()

AVATARS = ["🧙", "⚔️", "🏹", "🛡️", "🗡️", "🔮", "⚗️", "🌿"]
COLORS  = ["#C8A040", "#A04040", "#4080A0", "#60A060", "#9060A0", "#C07030", "#40A0A0", "#A06080"]

# ================= RUTAS WEB =================
@app.route("/")
def index():
    return render_template("index.html")

# ──── API: unirse a la partida ────
@app.route("/api/join", methods=["POST"])
def join_game():
    data = request.json
    name      = data.get("name", "").strip()[:20]
    avatar    = data.get("avatar", "🧙")
    alignment = data.get("alignment", "Neutral")
    cls       = data.get("cls", "Guerrero")

    if not name:
        return jsonify({"error": "Nombre requerido"}), 400

    with state_lock:
        if len(game_state["players"]) >= 4:
            return jsonify({"error": "La sala está llena (máx. 4 jugadores)"}), 400

        # Verificar nombre duplicado
        for p in game_state["players"].values():
            if p["name"].lower() == name.lower():
                return jsonify({"error": "Ese nombre ya está en uso"}), 400

        player_id = f"p{len(game_state['players']) + 1}_{name}"
        color_idx = len(game_state["players"]) % len(COLORS)

        game_state["players"][player_id] = {
            "name":      name,
            "avatar":    avatar,
            "alignment": alignment,
            "cls":       cls,
            "color":     COLORS[color_idx]
        }
        game_state["turn_order"].append(player_id)

        # Primer jugador en unirse narra la llegada de todos
        player_count = len(game_state["players"])

    return jsonify({
        "player_id":   player_id,
        "turn_order":  game_state["turn_order"],
        "players":     game_state["players"],
        "current_turn": game_state["current_turn"]
    })

# ──── API: estado actual ────
@app.route("/api/state")
def get_state():
    with state_lock:
        return jsonify({
            "players":      game_state["players"],
            "turn_order":   game_state["turn_order"],
            "current_turn": game_state["current_turn"],
            "chat_log":     game_state["chat_log"][-40:]  # últimos 40 mensajes
        })

# ──── API: acción del jugador ────
@app.route("/api/action", methods=["POST"])
def handle_action():
    data      = request.json
    player_id = data.get("player_id", "")
    action    = data.get("action", "").strip()

    if not action:
        return jsonify({"error": "Acción vacía"}), 400

    with state_lock:
        # Verificar que es el turno de este jugador
        if not game_state["turn_order"]:
            return jsonify({"error": "No hay jugadores en la partida"}), 400

        current_pid = game_state["turn_order"][game_state["current_turn"]]
        if player_id != current_pid:
            return jsonify({"error": "No es tu turno"}), 403

        player = game_state["players"][player_id]
        player_name = player["name"]
        player_cls  = player["cls"]

        # Construir mensaje con contexto del jugador
        user_msg = f"[{player_name} el {player_cls}]: {action}"
        game_state["conversation"].append({"role": "user", "content": user_msg})

        # Log visual — acción del jugador
        game_state["chat_log"].append({
            "type":   "player",
            "sender": player_name,
            "avatar": player["avatar"],
            "color":  player["color"],
            "text":   action
        })

    # Llamar a Groq (fuera del lock para no bloquearlo)
    gm_response = call_groq(game_state["conversation"])

    with state_lock:
        game_state["conversation"].append({"role": "assistant", "content": gm_response})

        # Log visual — respuesta del GM
        game_state["chat_log"].append({
            "type":   "gm",
            "sender": "Dungeon Master",
            "avatar": "🎭",
            "color":  "#C8A040",
            "text":   gm_response
        })

        # Avanzar turno
        game_state["current_turn"] = (
            game_state["current_turn"] + 1
        ) % len(game_state["turn_order"])

        next_pid    = game_state["turn_order"][game_state["current_turn"]]
        next_player = game_state["players"][next_pid]["name"]
        new_turn    = game_state["current_turn"]

    # Generar audio
    audio_b64 = generate_audio(gm_response)

    # Notificar a todos por WebSocket
    socketio.emit("game_update", {
        "chat_log":     game_state["chat_log"][-40:],
        "current_turn": new_turn,
        "turn_order":   game_state["turn_order"],
        "next_player":  next_player,
        "players":      game_state["players"]
    }, room="main")

    return jsonify({
        "text":         gm_response,
        "audio_base64": audio_b64,
        "current_turn": new_turn,
        "next_player":  next_player
    })

# ──── API: salir de la partida ────
@app.route("/api/leave", methods=["POST"])
def leave_game():
    data      = request.json
    player_id = data.get("player_id", "")

    with state_lock:
        if player_id not in game_state["players"]:
            return jsonify({"ok": True})

        name = game_state["players"][player_id]["name"]
        del game_state["players"][player_id]

        idx = game_state["turn_order"].index(player_id)
        game_state["turn_order"].remove(player_id)

        if game_state["turn_order"]:
            game_state["current_turn"] = game_state["current_turn"] % len(game_state["turn_order"])
        else:
            game_state["current_turn"] = 0

        game_state["chat_log"].append({
            "type":   "system",
            "sender": "Sistema",
            "avatar": "⚙️",
            "color":  "#666",
            "text":   f"{name} abandonó la partida."
        })

    socketio.emit("game_update", {
        "chat_log":     game_state["chat_log"][-40:],
        "current_turn": game_state["current_turn"],
        "turn_order":   game_state["turn_order"],
        "players":      game_state["players"]
    }, room="main")

    return jsonify({"ok": True})

# ================= WEBSOCKET =================
@socketio.on("join_room")
def on_join(data):
    join_room("main")
    emit("game_update", {
        "chat_log":     game_state["chat_log"][-40:],
        "current_turn": game_state["current_turn"],
        "turn_order":   game_state["turn_order"],
        "players":      game_state["players"]
    })

# ================= LLM (GROQ) =================
def call_groq(messages):
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type":  "application/json"
    }
    payload = {
        "model":       MODEL_NAME,
        "messages":    messages,
        "temperature": 0.75,
        "max_tokens":  600,
        "top_p":       0.9,
        "stream":      False
    }
    try:
        response = requests.post(GROQ_URL, json=payload, headers=headers, timeout=45)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"❌ Error Groq: {e}")
        return "El Dungeon Master guarda silencio por un momento... (error de conexión, intentá de nuevo)"

# ================= TEXTO A VOZ (gTTS) =================
def generate_audio(text):
    try:
        from gtts import gTTS
        # Limitar texto a 500 chars para no generar audios muy largos
        texto_corto = text[:500]
        tts = gTTS(text=texto_corto, lang="es", slow=False)
        buf = io.BytesIO()
        tts.write_to_fp(buf)
        buf.seek(0)
        return base64.b64encode(buf.read()).decode("utf-8")
    except Exception as e:
        print(f"❌ Error TTS: {e}")
        return None

# ================= INICIO =================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port)
