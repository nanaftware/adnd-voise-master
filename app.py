import os
import base64
import requests
from flask import Flask, render_template, request, jsonify
from adnd_rules import check_ability, attack_roll

app = Flask(__name__)

# ================= CONFIGURACIÓN =================
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise ValueError("❌ Falta la variable de entorno GROQ_API_KEY")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL_NAME = "llama3-8b-8192"

# Cargar el prompt del sistema
with open("prompts/system_prompt.txt", "r", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()

# --- NUEVO: Límites para controlar el tamaño del historial ---
MAX_HISTORY_MESSAGES = 15  # Máximo de mensajes en el historial (incluyendo el system)
MAX_MESSAGE_LENGTH = 1000  # Máxima longitud de cada mensaje (en caracteres)

def trim_history(history):
    """
    Recorta el historial para que no exceda los límites.
    - Asegura que el primer mensaje sea siempre el 'system'.
    - Mantiene solo los últimos N mensajes después del system.
    - Recorta el texto de cada mensaje si es demasiado largo.
    """
    if not history:
        return [{"role": "system", "content": SYSTEM_PROMPT}]
    
    # 1. Asegurar que el system prompt es el primer elemento
    if history[0]["role"] != "system":
        trimmed = [{"role": "system", "content": SYSTEM_PROMPT}]
    else:
        trimmed = [history[0].copy()] # Copia para no modificar el original

    # 2. Tomar los últimos (MAX_HISTORY_MESSAGES - 1) mensajes después del system
    rest_messages = history[1:]
    if len(rest_messages) > MAX_HISTORY_MESSAGES - 1:
        rest_messages = rest_messages[-(MAX_HISTORY_MESSAGES - 1):]

    # 3. Recortar el contenido de cada mensaje si es necesario
    for msg in rest_messages:
        msg_copy = msg.copy()
        if len(msg_copy["content"]) > MAX_MESSAGE_LENGTH:
            msg_copy["content"] = msg_copy["content"][:MAX_MESSAGE_LENGTH] + "..."
        trimmed.append(msg_copy)
        
    return trimmed

# Inicializar el historial GLOBAL (para una sola sesión de juego)
# ¡ATENCIÓN! Esto sigue siendo en RAM. Para múltiples jugadores,
# necesitarías usar sesiones (flask.session) o una base de datos.
conversation_history = [{"role": "system", "content": SYSTEM_PROMPT}]
# =================================================

@app.route("/")
def index():
    return render_template("index.html")

# --- NUEVO: Endpoint para reiniciar la partida ---
@app.route("/api/reset", methods=["POST"])
def reset_game():
    global conversation_history
    conversation_history = [{"role": "system", "content": SYSTEM_PROMPT}]
    return jsonify({"status": "ok", "message": "La memoria del Dungeon Master ha sido reiniciada."})

@app.route("/api/action", methods=["POST"])
def handle_action():
    global conversation_history
    
    data = request.json
    player_input = data.get("action", "").strip()
    if not player_input:
        return jsonify({"error": "Acción vacía"}), 400

    # Añadir la acción del jugador
    conversation_history.append({"role": "user", "content": player_input})
    
    # --- NUEVO: Recortar el historial ANTES de llamar a Groq ---
    trimmed_history = trim_history(conversation_history)

    # Obtener respuesta del LLM usando el historial recortado
    gm_response, error = call_groq(trimmed_history)
    
    if error:
        # Si el error es por payload muy grande, reiniciamos el historial y lo intentamos de nuevo
        if "413" in error:
            print("⚠️ Error 413 detectado. Reiniciando el historial y reintentando...")
            conversation_history = [{"role": "system", "content": SYSTEM_PROMPT}]
            # Reintentar una sola vez con el historial limpio
            trimmed_history = trim_history(conversation_history)
            gm_response, error = call_groq(trimmed_history)
            if error:
                return jsonify({"error": f"El Dungeon Master no pudo procesar la solicitud incluso después de reiniciar: {error}"}), 500
        else:
            return jsonify({"error": f"Error del Dungeon Master: {error}"}), 500

    # Añadir la respuesta del GM al historial GLOBAL
    conversation_history.append({"role": "assistant", "content": gm_response})
    
    # --- NUEVO: Recortar el historial DESPUÉS de añadir la respuesta ---
    conversation_history = trim_history(conversation_history)

    # Generar audio (opcional, puede fallar silenciosamente)
    audio_b64 = generate_audio(gm_response)

    return jsonify({
        "text": gm_response,
        "audio_base64": audio_b64
    })

def call_groq(messages):
    """Envía el historial YA RECORTADO a Groq."""
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 500,
        "top_p": 0.9,
        "stream": False
    }
    try:
        response = requests.post(GROQ_URL, json=payload, headers=headers, timeout=30)
        response.raise_for_status()  # Lanza una excepción para códigos de error HTTP (4xx, 5xx)
        result = response.json()
        return result["choices"][0]["message"]["content"], None
    except requests.exceptions.RequestException as e:
        error_msg = f"Error de conexión con Groq: {e}"
        print(error_msg)
        # Intenta obtener más detalle del error si existe
        if e.response is not None:
            try:
                detail = e.response.json()
                error_msg = f"Groq API Error (Código {e.response.status_code}): {detail}"
                print(detail)
            except:
                error_msg = f"Groq API Error (Código {e.response.status_code}): {e.response.text}"
                print(e.response.text)
        return "", error_msg
    except Exception as e:
        error_msg = f"Error inesperado en call_groq: {e}"
        print(error_msg)
        return "", error_msg

def generate_audio(text):
    """Genera audio con PocketTTS. Devuelve base64 o None si falla."""
    try:
        from pocket_tts import PocketTTS
        tts = PocketTTS(language="spanish")
        audio_bytes = tts.generate(text)
        return base64.b64encode(audio_bytes).decode("utf-8")
    except ImportError:
        print("ℹ️ PocketTTS no está instalado. El audio estará deshabilitado.")
        return None
    except Exception as e:
        print(f"❌ Error generando audio: {e}")
        return None

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
