"""Contratos HTTP del Rating Service."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RatingCreate(BaseModel):
    """Payload de ``POST /api/ratings/``.

    ``user_id`` se declara por compatibilidad con el contrato, pero el servicio
    exige que coincida con el ``sub`` del JWT: la identidad real nunca se toma
    del cuerpo de la petición.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    level_id: str = Field(min_length=1, max_length=64, examples=["faro-002"])
    user_id: str = Field(min_length=1, max_length=64, examples=["usr_abc123"])
    stars: int = Field(ge=1, le=5, examples=[4])


class RatingSummary(BaseModel):
    """Puntuación agregada de un nivel."""

    level_id: str
    average_rating: float = Field(ge=0.0, le=5.0, examples=[4.5])
    total_votes: int = Field(ge=0, examples=[12])


class RatingBatchRequest(BaseModel):
    """Payload alternativo de ``POST /api/ratings/levels/batch``."""

    model_config = ConfigDict(extra="forbid")

    level_ids: list[str] = Field(min_length=1, max_length=100)
