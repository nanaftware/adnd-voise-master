FROM python:3.11-slim

# Instalar dependencias del sistema (para PocketTTS y posibles codecs)
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    ffmpeg \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Exponer el puerto que usará Render
EXPOSE 10000

# Comando de inicio
CMD ["gunicorn", "--bind", "0.0.0.0:10000", "app:app"]