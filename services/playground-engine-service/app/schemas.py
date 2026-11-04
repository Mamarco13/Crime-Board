"""Contratos HTTP y modelo interno del Playground Engine Service.

Los esquemas públicos replican el contrato acordado con el cliente:

* ``InterrogationRequest/Response`` para interrogar sospechosos.
* ``ArchiveRequest/Response`` para buscar fichas en el archivador.
* ``AccusationRequest/Response`` para validar la acusación final.

``LevelSnapshot`` es la representación interna que el motor recibe del
Level Management Service (incluye la solución, las tarjetas ocultas y sus
alias, y nunca se devuelve al cliente).
"""

from __future__ import annotations

import enum

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

# Mismo patrón que Level Management: ids seguros para URLs y claves de caché.
SLUG_PATTERN = r"^[a-z0-9][a-z0-9_-]{1,63}$"


class CardType(str, enum.Enum):
    """Categorías de tarjeta del tablero."""

    SUSPECT = "SUSPECT"
    WEAPON = "WEAPON"
    LOCATION = "LOCATION"
    CLUE = "CLUE"


class Card(BaseModel):
    """Tarjeta tal y como la consume el cliente."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    id: str = Field(min_length=2, max_length=64)
    type: CardType
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    image_url: str | None = Field(default=None, max_length=2048)


class InternalCard(Card):
    """Tarjeta del canal interno, con su estado de archivo y alias."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    hidden: bool = False
    aliases: list[str] = Field(default_factory=list, max_length=20)


class ArchiveRequest(BaseModel):
    """Consulta libre al archivador del expediente."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    level_id: str = Field(min_length=2, max_length=64, pattern=SLUG_PATTERN)
    query: str = Field(
        min_length=1,
        max_length=200,
        description="Código, dirección o palabra que el jugador busca.",
        examples=["calle falsa 123"],
    )
    session_id: str = Field(
        default="default",
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]{1,64}$",
        description="Identificador de partida para el estado efímero.",
    )
    guest: bool = Field(
        default=False,
        description=(
            "Consulta de invitado. Se acepta sin sesión ni token: el estado se "
            "aísla mediante session_id."
        ),
    )


class ArchiveResponse(BaseModel):
    """Resultado de una búsqueda en el archivador."""

    found: bool
    card: Card | None = Field(
        default=None,
        description="Ficha recuperada; solo se envía cuando la búsqueda acierta.",
    )
    already_unlocked: bool = Field(
        default=False,
        description="La ficha ya estaba sobre el tablero en esta partida.",
    )
    message: str
    score: float | None = Field(default=None, ge=0, le=1)


class InterrogationRequest(BaseModel):
    """Acción de interrogar a un sospechoso del tablero."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    level_id: str = Field(min_length=2, max_length=64, pattern=SLUG_PATTERN)
    suspect_card_id: str = Field(
        min_length=2,
        max_length=64,
        pattern=SLUG_PATTERN,
        examples=["suspect-mayordomo"],
    )
    # Extensión opcional y compatible: identifica la partida para no volver a
    # entregar una pista ya desbloqueada. Sin valor, se asume una partida local
    # única ("default"). En cuanto exista login, este id vendrá del token.
    session_id: str = Field(
        default="default",
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]{1,64}$",
        description="Identificador de partida para el estado efímero.",
    )
    guest: bool = Field(
        default=False,
        description=(
            "Partida de invitado. No requiere sesión ni token: el estado se "
            "aísla mediante session_id."
        ),
    )


class InterrogationResponse(BaseModel):
    """Resultado de un interrogatorio."""

    suspect_card_id: str
    unlocked_clue_text: str = Field(
        description="Declaración del sospechoso lista para mostrar en el tablón."
    )
    new_card_unlocked: Card | None = Field(
        default=None,
        description=(
            "Tarjeta de pista desbloqueada. Es ``None`` si esa pista ya se "
            "había obtenido en la misma partida."
        ),
    )


class AccusationRequest(BaseModel):
    """Combinación de tarjetas con la que el jugador acusa."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    level_id: str = Field(min_length=2, max_length=64, pattern=SLUG_PATTERN)
    selected_card_ids: list[str] = Field(min_length=1, max_length=50)
    guest: bool = Field(
        default=False,
        description=(
            "Acusación de invitado. Se acepta sin sesión ni token; la "
            "validación es idéntica a la de un usuario registrado."
        ),
    )

    @field_validator("selected_card_ids")
    @classmethod
    def _validate_selected_ids(cls, value: list[str]) -> list[str]:
        cleaned = [card_id.strip() for card_id in value]
        if any(not card_id for card_id in cleaned):
            raise ValueError("selected_card_ids no puede contener ids vacíos")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("selected_card_ids no puede contener duplicados")
        return cleaned


class AccusationResponse(BaseModel):
    """Veredicto del motor."""

    solved: bool
    feedback_message: str = Field(
        description="Felicitación o pista para continuar la investigación."
    )
    elapsed_ms: int = Field(ge=0, description="Tiempo de validación en milisegundos.")


class LevelSnapshot(BaseModel):
    """Nivel completo recibido por el canal interno (incluye la solución)."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    level_id: str = Field(min_length=2, max_length=64)
    title: str = Field(min_length=1, max_length=120)
    cards: list[InternalCard] = Field(min_length=1, max_length=200)
    solution_card_ids: list[str] = Field(min_length=1, max_length=50)
    solution_composition_hidden: bool = Field(
        default=False,
        description=(
            "El autor oculta la composición en el dosier. El motor debe evitar "
            "revelar coincidencias parciales."
        ),
    )
    strict_feedback: bool = Field(
        default=False,
        description=(
            "Feedback binario sin recuentos. Se deriva de "
            "``solution_composition_hidden`` para no filtrar la solución."
        ),
    )

    @model_validator(mode="after")
    def _validate_integrity(self) -> LevelSnapshot:
        """Falla cerrado si el catálogo entrega un nivel incoherente."""
        card_ids = [card.id for card in self.cards]
        if len(set(card_ids)) != len(card_ids):
            raise ValueError("El nivel contiene ids de tarjeta duplicados")

        if not any(not card.hidden for card in self.cards):
            raise ValueError(
                "El nivel necesita al menos una tarjeta visible para abrir "
                "el tablón"
            )

        for card in self.cards:
            if card.hidden and not card.aliases:
                raise ValueError(
                    f"La tarjeta oculta '{card.id}' no tiene alias de búsqueda"
                )

        unknown = [
            card_id
            for card_id in self.solution_card_ids
            if card_id not in set(card_ids)
        ]
        if unknown:
            raise ValueError(
                "La solución referencia tarjetas ausentes: "
                + ", ".join(sorted(unknown))
            )
        if len(set(self.solution_card_ids)) != len(self.solution_card_ids):
            raise ValueError("La solución contiene ids duplicados")
        if self.solution_composition_hidden:
            self.strict_feedback = True
        return self
