"""Emparejamiento determinista del archivador.

El jugador escribe un código, una dirección o una palabra y el motor la compara
con los alias declarados por el diseñador para cada ficha del expediente. No se
usa IA generativa: la decisión depende solo de reglas reproducibles.

Reglas (de mayor a menor confianza):

1. Igualdad tras normalizar (acentos, mayúsculas, puntuación).
2. La consulta contiene el alias como subfrase (``0.95``).
3. El alias contiene la consulta como subfrase (``0.85``).
4. Similitud de secuencia (``difflib``) al menos ``0.82`` para tolerar erratas.

Los alias o consultas de menos de 4 caracteres nunca se aceptan por similitud:
códigos cortos como ``123`` no deben abrir fichas por parecido accidental.

Desempate: mayor puntuación, después alias más largo y, por último, el orden en
que las tarjetas se declararon en el nivel.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass

from .schemas import InternalCard, LevelSnapshot

_PUNCTUATION = re.compile(r"[^a-z0-9ñ\s]")
_WHITESPACE = re.compile(r"\s+")

_EXACT_SCORE = 1.0
_QUERY_CONTAINS_ALIAS_SCORE = 0.95
_ALIAS_CONTAINS_QUERY_SCORE = 0.85
_FUZZY_THRESHOLD = 0.82
_MIN_FUZZY_LENGTH = 4


def normalize_text(value: str) -> str:
    """Minúsculas, sin acentos y sin puntuación, para comparar de forma estable."""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(
        character
        for character in value
        if not unicodedata.combining(character)
    )
    value = value.lower()
    value = _PUNCTUATION.sub(" ", value)
    return _WHITESPACE.sub(" ", value).strip()


def _contains_phrase(haystack: str, needle: str) -> bool:
    """Contención por tokens completos, no por subcadena arbitraria."""
    if not needle:
        return False
    return f" {needle} " in f" {haystack} "


def score_alias(query_norm: str, alias_norm: str) -> float:
    """Puntuación de una consulta contra un alias (``0.0`` si no encaja)."""
    if not query_norm or not alias_norm:
        return 0.0
    if query_norm == alias_norm:
        return _EXACT_SCORE
    if _contains_phrase(query_norm, alias_norm):
        return _QUERY_CONTAINS_ALIAS_SCORE
    if _contains_phrase(alias_norm, query_norm):
        return _ALIAS_CONTAINS_QUERY_SCORE

    if min(len(query_norm), len(alias_norm)) < _MIN_FUZZY_LENGTH:
        return 0.0

    ratio = difflib.SequenceMatcher(None, query_norm, alias_norm).ratio()
    return ratio if ratio >= _FUZZY_THRESHOLD else 0.0


@dataclass(frozen=True, slots=True)
class ArchiveMatch:
    """Mejor ficha encontrada para una consulta."""

    card: InternalCard | None
    score: float
    matched: bool


def match_archive(level: LevelSnapshot, query: str) -> ArchiveMatch:
    """Busca la ficha del expediente cuyo alias encaja mejor con ``query``."""
    query_norm = normalize_text(query)
    if len(query_norm) < 2:
        return ArchiveMatch(card=None, score=0.0, matched=False)

    best_card: InternalCard | None = None
    best_score = 0.0
    best_alias_length = 0
    best_order = len(level.cards)

    for order, card in enumerate(level.cards):
        for alias in card.aliases:
            alias_norm = normalize_text(alias)
            score = score_alias(query_norm, alias_norm)
            if score <= 0:
                continue
            candidate = (score, len(alias_norm), -order)
            best = (best_score, best_alias_length, -best_order)
            if candidate > best:
                best_card = card
                best_score = score
                best_alias_length = len(alias_norm)
                best_order = order

    if best_card is None:
        return ArchiveMatch(card=None, score=0.0, matched=False)

    return ArchiveMatch(
        card=best_card,
        score=round(best_score, 3),
        matched=True,
    )
