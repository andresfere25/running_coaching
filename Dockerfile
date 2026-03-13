# Dockerfile — Running Coaching API
# Sirve la API FastAPI + el frontend estático Alpine.js en /app
#
# Build:  docker build -t running-coaching .
# Run:    docker run -p 8000:8000 --env-file .env running-coaching
# Prod:   agregar las vars de entorno en Railway/Fly.io/Render en lugar de --env-file

FROM python:3.11-slim

# Evitar archivos .pyc y habilitar logs sin buffer
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Instalar dependencias del sistema mínimas
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copiar e instalar dependencias Python primero (cache layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir "supabase>=2.0.0"

# Copiar el código del proyecto
COPY api/        api/
COPY src/        src/
COPY frontend/   frontend/
COPY migrations/ migrations/
COPY run_pipeline.py .

# Puerto expuesto
EXPOSE 8000

# Arrancar la API (sin --reload en producción)
# DATA_DIR y el resto de vars deben venir de la plataforma de deploy
CMD ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
