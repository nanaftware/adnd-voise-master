import os
import json
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
MODEL_NAME = "llama3-8b-8192"   # Puedes cambiar a "mixtral-8x7b-32768" o "gemma2-9b-it"

# Cargar el prompt del sistema para el Dungeon Master
with open("prompts/system_prompt.txt", "r", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()

# Historial de la conversación (se mantiene en RAM para esta demo)
conversation_history = [
    {"role": "system", "content": SYSTEM_PROMPT}
]

# ================= RUTAS WEB =================
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/action", methods=["POST"])
def handle_action():
    data = request.json
    player_input = data.get("action", "").strip()
    if not player_input:
        return jsonify({"error": "Acción vacía"}), 400

    # Añadir la acción del jugador al historial
    conversation_history.append({"role": "user", "content": player_input})

    # Obtener respuesta del LLM (Groq)
    gm_response = call_groq(conversation_history)

    # Guardar respuesta en el historial
    conversation_history.append({"role": "assistant", "content": gm_response})

    # Generar audio con PocketTTS
    audio_b64 = generate_audio(gm_response)

    return jsonify({
        "text": gm_response,
        "audio_base64": audio_b64
    })

# ================= LLM (GROQ) =================
'''
   def call_groq(messages):
    """Envía el historial a Groq y devuelve la respuesta del Dungeon Master."""
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
        response.raise_for_status()
        result = response.json()
        return result["choices"][0]["message"]["content"]
    except requests.exceptions.RequestException as e:
        print(f"❌ Error conectando con Groq: {e}")
        return "El Dungeon Master está teniendo problemas técnicos. Por favor, intenta de nuevo más tarde."
    except (KeyError, IndexError) as e:
        print(f"❌ Error en la respuesta de Groq: {e}")
        return "El Dungeon Master respondió de forma inesperada. Intenta otra acción."
        '''
# =================================================
def call_groq(messages):
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
        print(f"STATUS: {response.status_code}")
        print(f"BODY: {response.text}")
        response.raise_for_status()
        result = response.json()
        return result["choices"][0]["message"]["content"]
    except requests.exceptions.RequestException as e:
        print(f"❌ Error Groq: {e}")
        print(f"❌ Response: {response.text if 'response' in dir() else 'sin respuesta'}")
        return f"Error: {str(e)}"
        
# ================= TEXTO A VOZ (POCKET TTS) =================
def generate_audio(text):
    """Genera un archivo WAV en base64 a partir del texto usando PocketTTS."""
    try:
        from pocket_tts import PocketTTS
        tts = PocketTTS(language="spanish")
        audio_bytes = tts.generate(text)
        return base64.b64encode(audio_bytes).decode("utf-8")
    except Exception as e:
        print(f"❌ Error generando audio con PocketTTS: {e}")
        return None   # El frontend no mostrará audio, pero el texto sí

# ================= INICIO =================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
