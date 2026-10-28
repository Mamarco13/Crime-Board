"""Tests del matcher del archivador.

El corpus de ``tests/fixtures/archive_corpus.json`` es la especificación
compartida con el matcher local del frontend: si una consulta debe recuperar
una ficha en Python, debe recuperarla también en el modo simulación.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.archive import match_archive, normalize_text, score_alias
from app.schemas import CardType, InternalCard, LevelSnapshot

FIXTURES = Path(__file__).parent / "fixtures"


def _load_corpus() -> dict:
    return json.loads((FIXTURES / "archive_corpus.json").read_text("utf-8"))


def _build_level() -> LevelSnapshot:
    corpus = _load_corpus()
    cards = [
        InternalCard(
            id=item["id"],
            type=CardType.CLUE,
            title=item["id"],
            hidden=True,
            aliases=item["aliases"],
        )
        for item in corpus["cards"]
    ]
    cards.append(
        InternalCard(
            id="suspect-a",
            type=CardType.SUSPECT,
            title="Sospechoso",
            description="Visible",
        )
    )
    return LevelSnapshot(
        level_id="archive-test",
        title="Prueba del archivador",
        cards=cards,
        solution_card_ids=["suspect-a"],
    )


def test_normalize_ignores_accents_case_and_punctuation() -> None:
    assert normalize_text("  CASA del DENTISTA!  ") == "casa del dentista"
    assert normalize_text("Calle Falsa, 123.") == "calle falsa 123"
    assert normalize_text("clínica dental") == "clinica dental"


def test_corpus_cases() -> None:
    corpus = _load_corpus()
    level = _build_level()

    for case in corpus["cases"]:
        result = match_archive(level, case["input"])
        if case["expected"] is None:
            assert not result.matched, case["input"]
            continue
        assert result.matched, case["input"]
        assert result.card is not None
        assert result.card.id == case["expected"], case["input"]
        if "score" in case:
            assert result.score == case["score"], case["input"]


def test_short_terms_do_not_use_fuzzy_matching() -> None:
    assert score_alias("1234", "123") == 0.0
    assert score_alias("123", "123") == 1.0


def test_fuzzy_requires_both_sides_to_be_long_enough() -> None:
    assert score_alias("copa", "casa") == 0.0
    assert score_alias("invernadro", "invernadero") >= 0.82


def test_contains_uses_complete_tokens() -> None:
    # Un alias de menos de 4 caracteres no entra por similitud ni por
    # subcadena: "cop" no debe activarse dentro de "copas".
    assert score_alias("copas", "cop") == 0.0


def test_tie_break_prefers_longer_alias_then_configuration_order() -> None:
    first = InternalCard(
        id="first",
        type=CardType.CLUE,
        title="Primera",
        hidden=True,
        aliases=["pista"],
    )
    longer = InternalCard(
        id="longer",
        type=CardType.CLUE,
        title="Más larga",
        hidden=True,
        aliases=["pista larga"],
    )
    level = LevelSnapshot(
        level_id="tie-test",
        title="Desempate",
        cards=[
            first,
            longer,
            InternalCard(
                id="visible",
                type=CardType.SUSPECT,
                title="Visible",
            ),
        ],
        solution_card_ids=["visible"],
    )

    # "pista larga" contiene "pista": gana el alias exacto más largo.
    assert match_archive(level, "pista larga").card.id == "longer"

    duplicate = InternalCard(
        id="duplicate",
        type=CardType.LOCATION,
        title="Duplicada",
        hidden=True,
        aliases=["pista"],
    )
    level.cards.insert(1, duplicate)

    # Misma puntuación y longitud: gana la primera tarjeta configurada.
    assert match_archive(level, "pista").card.id == "first"
