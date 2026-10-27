# syntax=docker/dockerfile:1.7
# =============================================================================
# Crime Board — Level Management Service
# Stack asumido: Python 3.12 + FastAPI + Uvicorn
# Build context: services/level-management-service
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
# (psycopg2, uvloop, etc.). No llegan a la imagen final.
#
# DL3008 se silencia a propósito: fijar la versión exacta de paquetes Debian
# ata el build a una revisión concreta de bookworm y lo rompe en cada
# actualización de seguridad. La reproducibilidad se controla con la imagen
# base y con requirements.txt; las actualizaciones llegan al reconstruir.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Venv aislado que se copiará a la etapa de runtime
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Capa de dependencias: solo se invalida si cambia requirements.txt
COPY requirements.txt ./
# pip se fija a una versión concreta (DL3013) para que el build sea
# reproducible y no dependa del pip que traiga la imagen base.
RUN python -m pip install --no-cache-dir --upgrade "pip==26.2.2" \
    && python -m pip install --no-cache-dir -r requirements.txt

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

LABEL org.opencontainers.image.title="crime-board-level-management-service" \
      org.opencontainers.image.description="Level Management Service de Crime Board" \
      org.opencontainers.image.vendor="Crime Board"

# Solo librerías de sistema necesarias en ejecución (cliente libpq para PostgreSQL)
#
# Mismo criterio que en el builder: DL3008 se silencia para no atar el build a
# una versión concreta de paquete Debian y poder recibir parches de seguridad.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home --home-dir /home/app --shell /usr/sbin/nologin app

WORKDIR /app

# Dependencias ya compiladas en el builder
COPY --from=builder --chown=app:app /opt/venv /opt/venv

# Código de la aplicación (el .dockerignore excluye tests, docs, secretos, etc.)
COPY --chown=app:app . .

# Principio de mínimo privilegio: usuario no-root explícito
USER 10001:10001

EXPOSE 8000

# Ajusta /health si tu endpoint de salud usa otra ruta.
# Forma exec (JSON) para que la señal se entregue directamente a Python (DL3025).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]

STOPSIGNAL SIGTERM

# Un proceso por contenedor; el orquestador escala réplicas.
# Ajusta app.main:app al entrypoint real del servicio.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
