import os
import json
import requests
from flask import Flask, render_template, request, jsonify
from adnd_rules import check_ability, attack_roll

app = Flask(__name__)

# Cargar prompt del sistema
with open("prompts/system_prompt.txt", "r", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()

# Historial de conversación (en memoria para demo)
conversation_history = [{"role": "system", "content": SYSTEM_PROMPT}]

# Endpoint para servir la página web
@app.route("/")
def index():
    return render_template("index.html")

# Endpoint para enviar acciones del jugador
@app.route("/api/action", methods=["POST"])
def handle_action():
    data = request.json
    player_input = data.get("action", "")
    
    # Agregar acción del jugador al historial
    conversation_history.append({"role": "user", "content": player_input})
    
    # Llamada a Ollama (modelo local)
    response = call_ollama(conversation_history)
    
    # Agregar respuesta al historial
    conversation_history.append({"role": "assistant", "content": response})
    
    # Generar audio con PocketTTS
    audio_data = generate_audio(response)
    
    return jsonify({
        "text": response,
        "audio_base64": audio_data
    })

def call_ollama(messages):
    """Envía el historial al modelo local de Ollama"""
    try:
        ollama_url = "http://localhost:11434/api/generate"
        payload = {
            "model": "qwen2.5:7b",  # o llama3.1:8b, mistral
            "prompt": format_messages(messages),
            "stream": False
        }
        resp = requests.post(ollama_url, json=payload)
        return resp.json()["response"]
    except Exception as e:
        return f"Error conectando con el Dungeon Master: {str(e)}"

def format_messages(messages):
    """Convierte el historial en un solo texto para Ollama"""
    formatted = ""
    for msg in messages:
        role = "Usuario" if msg["role"] == "user" else "Dungeon Master"
        formatted += f"{role}: {msg['content']}\n"
    return formatted

def generate_audio(text):
    """Convierte texto a voz usando PocketTTS y devuelve en base64"""
    try:
        from pocket_tts import PocketTTS
        tts = PocketTTS(language="spanish")
        audio_bytes = tts.generate(text)
        import base64
        b64 = base64.b64encode(audio_bytes).decode("utf-8")
        return b64
    except Exception as e:
        print("Error TTS:", e)
        return None

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)