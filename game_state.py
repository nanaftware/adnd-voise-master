import json
import os
from datetime import datetime

SAVES_DIRECTORY = "saves"

# Asegurar que el directorio de guardados existe
os.makedirs(SAVES_DIRECTORY, exist_ok=True)

def save_game(conversation_history, player_name, campaign_name="default"):
    """
    Guarda el estado actual de la partida en un archivo JSON.
    
    Args:
        conversation_history: Lista de mensajes del chat
        player_name: Nombre del jugador (para identificar la partida)
        campaign_name: Nombre de la campaña (opcional)
    
    Returns:
        str: Nombre del archivo creado o None si hay error
    """
    try:
        # Crear nombre de archivo con timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{SAVES_DIRECTORY}/{player_name}_{campaign_name}_{timestamp}.json"
        
        # Estructura de datos a guardar
        save_data = {
            "player_name": player_name,
            "campaign_name": campaign_name,
            "timestamp": timestamp,
            "conversation_history": conversation_history,
            "metadata": {
                "total_messages": len(conversation_history),
                "saved_at": datetime.now().isoformat()
            }
        }
        
        # Guardar archivo JSON
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)
        
        print(f"✅ Partida guardada: {filename}")
        return filename
    
    except Exception as e:
        print(f"❌ Error guardando partida: {e}")
        return None

def load_game(filename):
    """
    Carga una partida guardada desde un archivo JSON.
    
    Args:
        filename: Ruta del archivo a cargar (relativa o absoluta)
    
    Returns:
        dict or None: Datos de la partida o None si hay error
    """
    try:
        with open(filename, "r", encoding="utf-8") as f:
            save_data = json.load(f)
        
        print(f"✅ Partida cargada: {filename}")
        return save_data
    
    except FileNotFoundError:
        print(f"❌ Archivo no encontrado: {filename}")
        return None
    except json.JSONDecodeError as e:
        print(f"❌ Error leyendo JSON: {e}")
        return None
    except Exception as e:
        print(f"❌ Error cargando partida: {e}")
        return None

def list_saved_games(player_name=None):
    """
    Lista todas las partidas guardadas (opcionalmente filtradas por jugador).
    
    Args:
        player_name: Nombre del jugador para filtrar (opcional)
    
    Returns:
        list: Lista de diccionarios con información de cada partida
    """
    saves = []
    try:
        for filename in os.listdir(SAVES_DIRECTORY):
            if filename.endswith(".json"):
                filepath = os.path.join(SAVES_DIRECTORY, filename)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    
                    # Filtrar por jugador si se especifica
                    if player_name and data.get("player_name") != player_name:
                        continue
                    
                    saves.append({
                        "filename": filepath,
                        "player_name": data.get("player_name", "Unknown"),
                        "campaign_name": data.get("campaign_name", "Unknown"),
                        "timestamp": data.get("timestamp", "Unknown"),
                        "total_messages": data.get("metadata", {}).get("total_messages", 0),
                        "saved_at": data.get("metadata", {}).get("saved_at", "Unknown")
                    })
                except:
                    # Saltar archivos corruptos
                    continue
        
        # Ordenar por fecha (más reciente primero)
        saves.sort(key=lambda x: x["timestamp"], reverse=True)
        return saves
    
    except Exception as e:
        print(f"❌ Error listando partidas: {e}")
        return []

def delete_save(filename):
    """
    Elimina un archivo de guardado.
    
    Args:
        filename: Ruta del archivo a eliminar
    
    Returns:
        bool: True si se eliminó correctamente, False en caso contrario
    """
    try:
        if os.path.exists(filename):
            os.remove(filename)
            print(f"✅ Partida eliminada: {filename}")
            return True
        else:
            print(f"⚠️ Archivo no existe: {filename}")
            return False
    except Exception as e:
        print(f"❌ Error eliminando partida: {e}")
        return False
