"""Level Management Service — punto de entrada mínimo (FastAPI).

Plantilla mínima para validar el build de la imagen y el HEALTHCHECK.
Sustituir por la implementación real del servicio.
"""

from fastapi import FastAPI

app = FastAPI(
    title="Crime Board — Level Management Service",
    version="0.1.0",
)


@app.get("/health", tags=["ops"])
async def health() -> dict[str, str]:
    """Sonda de liveness/readiness usada por Docker y Compose."""
    return {"status": "ok"}
