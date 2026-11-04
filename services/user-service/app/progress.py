"""Purga best-effort del progreso al eliminar una cuenta.

User Service es la fuente de verdad de las cuentas. Cuando un usuario borra su
cuenta, avisa a Progress Service con la clave interna para eliminar libreta,
sesiones, intentos y estadísticas. La llamada es best-effort: si el servicio no
responde, la cuenta se elimina igual y se registra un warning para reintentar
la purga por operación (GDPR).
"""

from __future__ import annotations

import logging
from urllib.parse import quote

import httpx

from .config import Settings

logger = logging.getLogger(__name__)


async def purge_progress_data(settings: Settings, user_id: str) -> bool:
    """Elimina el progreso del usuario; nunca propaga errores de red."""
    if settings.progress_service_url is None:
        logger.debug(
            "PROGRESS_SERVICE_URL no definida: se omite la purga de progreso"
        )
        return False

    headers = {
        "Accept": "application/json",
        "User-Agent": "open-crime-user-service/1.1",
    }
    if settings.internal_api_key:
        headers["X-Internal-Api-Key"] = settings.internal_api_key

    url = (
        f"{settings.progress_service_url}/api/internal/progress/users/"
        f"{quote(user_id, safe='')}"
    )
    try:
        async with httpx.AsyncClient(
            timeout=settings.progress_timeout_seconds
        ) as client:
            response = await client.delete(url, headers=headers)
            response.raise_for_status()
        logger.info("Progreso purgado para el usuario %s", user_id)
        return True
    except httpx.HTTPError as exc:
        logger.warning(
            "No se pudo purgar el progreso del usuario %s: %s",
            user_id,
            exc,
        )
        return False
