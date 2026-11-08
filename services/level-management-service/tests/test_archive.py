"""Pruebas del contrato del archivador en el catálogo."""

from __future__ import annotations

import asyncio

import pytest
from app.main import _to_admin, _to_detail
from app.models import LevelCreate, normalize_alias
from app.repository import InMemoryLevelRepository
from app.seed import DEMO_LEVEL
from pydantic import ValidationError


def _payload() -> dict:
    return {
        "level_id": "archive-test",
        "title": "Prueba del archivador",
        "description": "Prueba",
        "difficulty": "easy",
        "cards": [
            {
                "id": "suspect-a",
                "type": "SUSPECT",
                "title": "Sospechoso",
                "description": "Dice que estaba en casa.",
            },
            {
                "id": "location-casa",
                "type": "LOCATION",
                "title": "Casa del dentista",
                "description": "Chalet en el 123.",
                "hidden": True,
                "aliases": ["calle falsa 123", "casa del dentista"],
            },
        ],
        "solution_card_ids": ["suspect-a", "location-casa"],
    }


def test_normalize_alias_ignores_accents_case_and_punctuation() -> None:
    assert normalize_alias("  CASA del DENTISTA!  ") == "casa del dentista"
    assert normalize_alias("Calle Falsa, 123.") == "calle falsa 123"
    assert normalize_alias("clínica dental") == "clinica dental"


def test_seed_uses_three_visible_and_thirteen_hidden_cards() -> None:
    visible = [card for card in DEMO_LEVEL.cards if not card.hidden]
    hidden = [card for card in DEMO_LEVEL.cards if card.hidden]

    assert len(DEMO_LEVEL.cards) == 16
    assert len(visible) == 3
    assert len(hidden) == 13
    assert all(card.aliases for card in hidden)


def test_public_detail_hides_secret_cards_but_publishes_total() -> None:
    level = asyncio.run(InMemoryLevelRepository().create(DEMO_LEVEL))
    public = _to_detail(level)

    assert len(public.cards) == 3
    assert public.card_count == 16
    assert all(card.type.value == "SUSPECT" for card in public.cards)
    assert "aliases" not in public.cards[0].model_dump()


def test_admin_detail_exposes_hidden_cards_and_aliases() -> None:
    level = asyncio.run(InMemoryLevelRepository().create(DEMO_LEVEL))
    admin = _to_admin(level)

    hidden = [card for card in admin.cards if card.hidden]
    assert len(admin.cards) == 16
    assert len(hidden) == 13
    assert admin.solution_card_ids == [
        "suspect-mayordomo",
        "weapon-copa",
        "location-despacho",
        "clue-copa",
    ]
    assert "calle falsa 123" in next(
        card.aliases for card in hidden if card.id == "location-casa-dentista"
    )


def test_repository_stores_normalized_aliases() -> None:
    payload = _payload()
    payload["cards"][1]["aliases"] = ["Calle Falsa, 123", "Casa del Dentista"]

    level = asyncio.run(InMemoryLevelRepository().create(LevelCreate(**payload)))
    hidden = next(card for card in level.cards if card.id == "location-casa")

    assert hidden.hidden is True
    assert hidden.aliases == ["calle falsa 123", "casa del dentista"]


def test_hidden_card_requires_at_least_one_alias() -> None:
    payload = _payload()
    payload["cards"][1]["aliases"] = []

    with pytest.raises(ValidationError, match="necesita al menos un alias"):
        LevelCreate(**payload)


def test_aliases_must_be_unique_across_cards() -> None:
    payload = _payload()
    payload["cards"][0]["aliases"] = ["Casa del Dentista"]

    with pytest.raises(ValidationError, match="está repetido"):
        LevelCreate(**payload)


def test_duplicate_aliases_inside_a_card_are_rejected() -> None:
    payload = _payload()
    payload["cards"][1]["aliases"] = ["Calle Falsa 123", "calle falsa, 123"]

    with pytest.raises(ValidationError, match="no puede contener duplicados"):
        LevelCreate(**payload)


def test_solution_may_reference_hidden_cards() -> None:
    level = LevelCreate(**_payload())
    assert "location-casa" in level.solution_card_ids


def test_solution_rejects_unknown_hidden_card() -> None:
    payload = _payload()
    payload["solution_card_ids"] = ["suspect-a", "location-inexistente"]

    with pytest.raises(ValidationError, match="tarjetas inexistentes"):
        LevelCreate(**payload)


def test_level_without_visible_cards_is_rejected() -> None:
    payload = _payload()
    payload["cards"][0]["hidden"] = True
    payload["cards"][0]["aliases"] = ["sospechoso"]

    with pytest.raises(ValidationError, match="al menos una tarjeta visible"):
        LevelCreate(**payload)
