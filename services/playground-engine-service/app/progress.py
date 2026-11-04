"""Cliente HTTP del motor hacia Progress Service.

La escritura del intento es **fail-open**: si el servicio de progreso está
caído, tarda más de ``PROGRESS_TIMEOUT_SECONDS`` o rechaza la petición, el
veredicto se devuelve igual y el bloque ``progress`` queda con
``recorded=false``. Así una avería del perfil nunca bloquea una partida.
"""

from __future__ import annotations

import logging
from uuid import uuid4

import httpx

from .schemas import AttemptProgress

logger = logging.getLogger(__name__)


class ProgressClient:
    """Registro de intentos con timeout corto y cliente reutilizado."""

    def __init__(
        self,
        *,
        base_url: str,
        internal_api_key: str | None,
        timeout_seconds: float,
    ) -> None:
        headers = {
            "Accept": "application/json",
            "User-Agent": "crime-board-playground-engine/0.3",
        }
        if internal_api_key:
            headers["X-Internal-Api-Key"] = internal_api_key

        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_seconds,
            headers=headers,
        )

    async def record_attempt(
        self,
        *,
        user_id: str,
        level_id: str,
        session_key: str | None,
        client_attempt_id: str | None,
        solved: bool,
        selected_card_ids: list[str],
        elapsed_ms: int,
    ) -> AttemptProgress:
        """Registra el intento; nunca propaga errores de red o HTTP."""
        attempt_id = client_attempt_id or str(uuid4())
        payload = {
            "user_id": user_id,
            "level_id": level_id,
            "session_key": session_key,
            "client_attempt_id": attempt_id,
            "solved": solved,
            "selected_card_ids": selected_card_ids,
            "elapsed_ms": max(elapsed_ms, 0),
        }
        try:
            response = await self._client.post(
                "/api/internal/progress/attempts",
                json=payload,
            )
            response.raise_for_status()
            return AttemptProgress.model_validate(response.json())
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "No se pudo registrar el intento en progreso: nivel=%s "
                "usuario=%s error=%s",
                level_id,
                user_id,
                exc,
            )
            return AttemptProgress(recorded=False)

    async def close(self) -> None:
        await self._client.aclose()
