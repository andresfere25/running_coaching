# Dockerfile — Running Coaching API
# Sirve la API FastAPI + el frontend estático Alpine.js en /app
#
# Build local:
#   docker build -t running-coaching .
#   docker run -p 8000:8000 --env-file .env running-coaching
#
# Deploy Railway:
#   Conectar repo → Railway detecta Dockerfile automáticamente.
#   Variables de entorno en Railway dashboard (ver DEPLOY.md).

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
RUN pip install --no-cache-dir -r requirements.txt

# Crear directorio para secrets (se puebla en runtime desde env vars)
RUN mkdir -p secrets

# Copiar el código del proyecto
COPY api/        api/
COPY src/        src/
COPY frontend/   frontend/
COPY migrations/ migrations/
COPY run_pipeline.py .

# Puerto documentado — Railway inyecta $PORT en runtime
EXPOSE 8000

# Shell form para expandir $PORT en runtime.
# GOOGLE_SA_JSON_B64: si está presente, se decodifica y escribe a disco antes de arrancar.
CMD sh -c '\
  if [ -n "$GOOGLE_SA_JSON_B64" ]; then \
    echo "$GOOGLE_SA_JSON_B64" | base64 -d > secrets/google_service_account.json && \
    echo "[startup] Google SA JSON escrito desde GOOGLE_SA_JSON_B64"; \
  fi && \
  exec python -m uvicorn api.main:app --host 0.0.0.0 --port "${PORT:-8000}" \
'
