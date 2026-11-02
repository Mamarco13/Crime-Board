"""Modelos de dominio y contratos del Level Management Service.

El módulo reúne dos capas relacionadas:

1. **Persistencia** (SQLAlchemy): ``LevelModel`` y ``CardModel``. La solución
   del caso se guarda en ``levels.solution_card_ids`` y nunca se expone en los
   endpoints públicos.
2. **API** (Pydantic): esquemas de entrada/salida. ``LevelDetail`` omite la
   solución y las tarjetas ocultas; ``LevelAdmin`` lo incluye todo y solo se usa
   en el endpoint interno protegido por clave.

**Archivador**: una tarjeta puede nacer oculta (``hidden=True``) y declarar
``aliases`` (códigos, direcciones o palabras). El jugador la recupera desde el
archivador del motor escribiendo uno de esos términos. Las tarjetas ocultas no
viajan al cliente hasta que se recuperan.

Las tarjetas usan clave primaria compuesta ``(level_id, id)`` para permitir que
un mismo identificador de tarjeta (por ejemplo ``weapon-copa``) se reutilice en
distintos niveles sin colisiones.
"""

from __future__ import annotations

import enum
import re
import unicodedata
from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

# ---------------------------------------------------------------------------
# Enumeraciones compartidas por persistencia y API
# ---------------------------------------------------------------------------

# Identificadores "slug": minúsculas, dígitos, guion y guion bajo. Evita
# problemas al construir URLs y mantiene consistentes los ids de las tarjetas.
SLUG_PATTERN = r"^[a-z0-9][a-z0-9_-]{1,63}$"


class CardType(str, enum.Enum):
    """Tipos de tarjeta que puede contener el tablero."""

    SUSPECT = "SUSPECT"
    WEAPON = "WEAPON"
    LOCATION = "LOCATION"
    CLUE = "CLUE"


class Difficulty(str, enum.Enum):
    """Curva de dificultad declarada por el autor del nivel."""

    TUTORIAL = "tutorial"
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


def _enum_column(enum_cls: type[enum.Enum], name: str) -> Enum:
    """``Enum`` de SQLAlchemy que persiste el *valor* y no el nombre.

    ``native_enum=False`` genera un VARCHAR portable (útil si en el futuro se
    añade SQLite para tests) y ``values_callable`` evita guardar
    ``TUTORIAL`` cuando la API habla de ``tutorial``.
    """
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        length=32,
        values_callable=lambda members: [member.value for member in members],
    )


# ---------------------------------------------------------------------------
# Persistencia: tablas SQLAlchemy
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """Base declarativa de SQLAlchemy 2.x."""


class LevelModel(Base):
    """Nivel publicado en el catálogo."""

    __tablename__ = "levels"

    level_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    difficulty: Mapped[Difficulty] = mapped_column(
        _enum_column(Difficulty, "difficulty"),
        nullable=False,
    )
    # Identidad del detective que publicó el caso. Los casos históricos y el
    # seed usan el valor por defecto; las publicaciones nuevas lo reciben del
    # JWT validado en el endpoint, nunca del cuerpo de la petición.
    creator_username: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="Anónimo",
        server_default=text("'Anónimo'"),
    )
    # Lista de ids que forman la combinación ganadora. JSON es suficiente para
    # el MVP; si la solución necesitara consultas relacionales, se normalizaría
    # en una tabla intermedia.
    solution_card_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    cards: Mapped[list[CardModel]] = relationship(
        back_populates="level",
        cascade="all, delete-orphan",
        order_by="CardModel.position",
        lazy="selectin",
    )


class CardModel(Base):
    """Tarjeta (sospechoso, arma, lugar o pista) de un nivel."""

    __tablename__ = "cards"
    __table_args__ = (
        PrimaryKeyConstraint("level_id", "id", name="pk_cards"),
    )

    id: Mapped[str] = mapped_column(String(64), nullable=False)
    level_id: Mapped[str] = mapped_column(
        ForeignKey("levels.level_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    type: Mapped[CardType] = mapped_column(
        _enum_column(CardType, "card_type"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    image_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # Tarjetas que empiezan fuera del tablón y se recuperan desde el archivador.
    hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Términos aceptados por el archivador. Se guardan normalizados para que la
    # comparación no dependa de acentos, mayúsculas ni puntuación.
    aliases: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # Orden estable con el que el cliente pinta las tarjetas del tablero.
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    level: Mapped[LevelModel] = relationship(back_populates="cards")


# ---------------------------------------------------------------------------
# Normalización de alias (compartida por validación y comparación)
# ---------------------------------------------------------------------------
_ALIAS_PUNCTUATION = re.compile(r"[^a-z0-9ñ\s]")


def normalize_alias(value: str) -> str:
    """Minúsculas, sin acentos, sin puntuación y con espacios colapsados."""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(
        character for character in value if not unicodedata.combining(character)
    )
    value = value.lower()
    value = _ALIAS_PUNCTUATION.sub(" ", value)
    return " ".join(value.split())


# ---------------------------------------------------------------------------
# API: esquemas Pydantic
# ---------------------------------------------------------------------------


class CardBase(BaseModel):
    """Campos comunes de una tarjeta."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(
        min_length=2,
        max_length=64,
        pattern=SLUG_PATTERN,
        description="Identificador único de la tarjeta dentro del nivel.",
        examples=["suspect-mayordomo"],
    )
    type: CardType = Field(description="Categoría de la tarjeta.")
    title: str = Field(min_length=1, max_length=120, examples=["Alfredo Vázquez"])
    description: str = Field(
        default="",
        max_length=4000,
        description="Texto visible en el reverso o al interrogar.",
    )
    image_url: str | None = Field(
        default=None,
        max_length=2048,
        description="URL absoluta http(s) de la ilustración (opcional).",
    )

    @field_validator("image_url")
    @classmethod
    def _validate_image_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.startswith(("http://", "https://")):
            raise ValueError("image_url debe ser una URL absoluta http(s)")
        return value


class CardCreate(CardBase):
    """Tarjeta enviada al publicar un nivel."""

    hidden: bool = Field(
        default=False,
        description=(
            "Si es ``true``, la tarjeta no aparece en el tablón hasta que el "
            "jugador la recupera desde el archivador."
        ),
    )
    aliases: list[str] = Field(
        default_factory=list,
        max_length=20,
        description=(
            "Códigos, direcciones o palabras que recuperan la tarjeta desde el "
            "archivador. Solo se usan en el motor; no se publican al cliente."
        ),
        examples=[["copa", "copa de champán", "champán"]],
    )

    @field_validator("aliases")
    @classmethod
    def _validate_aliases(cls, value: list[str]) -> list[str]:
        cleaned = [" ".join(alias.split()) for alias in value if alias.strip()]
        if any(len(alias) < 2 for alias in cleaned):
            raise ValueError("Cada alias debe tener al menos 2 caracteres")
        normalized = [normalize_alias(alias) for alias in cleaned]
        if len(set(normalized)) != len(normalized):
            raise ValueError("aliases no puede contener duplicados")
        return cleaned


class Card(CardBase):
    """Tarjeta visible devuelta al cliente."""

    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


class CardAdmin(CardBase):
    """Tarjeta completa, con su estado de archivo, solo para uso interno."""

    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
        str_strip_whitespace=True,
    )

    hidden: bool = False
    aliases: list[str] = Field(default_factory=list)


class LevelCreate(BaseModel):
    """Payload de ``POST /api/levels/``."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # Opcional: el servidor genera un id único si no se envía. Se permite
    # fijarlo para seeds locales o importaciones idempotentes.
    level_id: str | None = Field(
        default=None,
        min_length=2,
        max_length=64,
        pattern=SLUG_PATTERN,
        examples=["tutorial-001"],
    )
    title: str = Field(
        min_length=1,
        max_length=120,
        examples=["El último brindis en Villa Aurora"],
    )
    description: str = Field(default="", max_length=4000)
    difficulty: Difficulty = Difficulty.EASY
    cards: list[CardCreate] = Field(min_length=1, max_length=200)
    solution_card_ids: list[str] = Field(
        min_length=1,
        max_length=50,
        description="Combinación ganadora. No se publica en los GET públicos.",
    )

    @field_validator("solution_card_ids")
    @classmethod
    def _validate_solution_ids(cls, value: list[str]) -> list[str]:
        cleaned = [card_id.strip() for card_id in value]
        if any(not card_id for card_id in cleaned):
            raise ValueError("solution_card_ids no puede contener ids vacíos")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("solution_card_ids no puede contener duplicados")
        return cleaned

    @model_validator(mode="after")
    def _validate_card_references(self) -> LevelCreate:
        card_ids = [card.id for card in self.cards]
        if len(set(card_ids)) != len(card_ids):
            raise ValueError("Los ids de las tarjetas deben ser únicos en el nivel")

        if not any(not card.hidden for card in self.cards):
            raise ValueError(
                "El nivel necesita al menos una tarjeta visible para abrir el tablón"
            )

        alias_owner: dict[str, str] = {}
        for card in self.cards:
            if card.hidden and not card.aliases:
                raise ValueError(
                    f"La tarjeta oculta '{card.id}' necesita al menos un alias "
                    "para poder recuperarla del archivador"
                )
            for alias in card.aliases:
                normalized = normalize_alias(alias)
                if not normalized:
                    raise ValueError(
                        f"El alias '{alias}' de la tarjeta '{card.id}' no es válido"
                    )
                if normalized in alias_owner:
                    raise ValueError(
                        f"El alias '{alias}' está repetido entre las tarjetas "
                        f"'{alias_owner[normalized]}' y '{card.id}'"
                    )
                alias_owner[normalized] = card.id

        unknown = [
            card_id
            for card_id in self.solution_card_ids
            if card_id not in card_ids
        ]
        if unknown:
            raise ValueError(
                "La solución referencia tarjetas inexistentes: "
                + ", ".join(sorted(unknown))
            )
        return self


class LevelSummary(BaseModel):
    """Elemento del catálogo paginado (sin tarjetas ni solución)."""

    level_id: str
    title: str
    description: str
    difficulty: Difficulty
    creator_username: str = Field(
        default="Anónimo",
        max_length=64,
        description="Detective que publicó el caso.",
    )
    card_count: int = Field(ge=0)
    created_at: datetime


class LevelDetail(BaseModel):
    """Nivel listo para renderizar el tablero.

    Deliberadamente **no** incluye ``solution_card_ids`` ni las tarjetas
    ocultas. ``card_count`` sí publica el total de fichas del expediente para
    que el cliente pueda mostrar el progreso sin revelar su contenido.
    """

    level_id: str
    title: str
    description: str
    difficulty: Difficulty
    creator_username: str = Field(
        default="Anónimo",
        max_length=64,
        description="Detective que publicó el caso.",
    )
    cards: list[Card]
    card_count: int = Field(ge=0)
    created_at: datetime


class LevelAdmin(LevelDetail):
    """Vista administrativa/interna que sí contiene la solución y el archivo."""

    cards: list[CardAdmin]
    solution_card_ids: list[str] = Field(
        description="Combinación ganadora. Uso exclusivo interno.",
    )


class LevelPage(BaseModel):
    """Respuesta paginada del catálogo."""

    items: list[LevelSummary]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
