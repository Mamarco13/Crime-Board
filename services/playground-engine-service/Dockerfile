# syntax=docker/dockerfile:1.7
# =============================================================================
# Crime Board — Playground Engine Service
# Stack asumido: Python 3.12 + FastAPI + Uvicorn
# Build context: services/playground-engine-service
# =============================================================================

# -----------------------------------------------------------------------------
# Etapa 1: builder
# -----------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Dependencias de compilación para wheels con extensiones nativas
# (uvloop, hiredis, etc.). No llegan a la imagen final.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# Venv aislado que se copiará a la etapa de runtime
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Capa de dependencias: solo se invalida si cambia requirements.txt
COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

# -----------------------------------------------------------------------------
# Etapa 2: runtime
# -----------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/app" \
    HOME=/home/app \
    APP_HOME=/app

LABEL org.opencontainers.image.title="crime-board-playground-engine-service" \
      org.opencontainers.image.description="Playground Engine Service de Crime Board" \
      org.opencontainers.image.vendor="Crime Board"

# Usuario no-root explícito. No se instalan paquetes de sistema porque el
# servicio solo necesita Redis/Kafka vía red.
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home --home-dir /home/app --shell /usr/sbin/nologin app

WORKDIR /app

# Dependencias ya compiladas en el builder
COPY --from=builder --chown=app:app /opt/venv /opt/venv

# Código de la aplicación (el .dockerignore excluye tests, docs, secretos, etc.)
COPY --chown=app:app . .

# Principio de mínimo privilegio
USER 10001:10001

EXPOSE 8000

# Ajusta /health si tu endpoint de salud usa otra ruta
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

STOPSIGNAL SIGTERM

# Un proceso por contenedor; el orquestador escala réplicas.
# Ajusta app.main:app al entrypoint real del servicio.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
