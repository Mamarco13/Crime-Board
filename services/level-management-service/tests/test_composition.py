"""Tests de la composición de la solución en el dosier (#6)."""

from __future__ import annotations

import asyncio

import pytest
from app.main import _to_admin, _to_detail, _to_summary
from app.models import Difficulty, LevelCreate
from app.repository import InMemoryLevelRepository
from app.seed import DEMO_LEVEL, HARD_DEMO_LEVEL
from pydantic import ValidationError


def _create(level: LevelCreate):
    return asyncio.run(InMemoryLevelRepository().create(level))


def test_visible_composition_is_derived_from_solution_types() -> None:
    public = _to_detail(_create(DEMO_LEVEL))

    assert public.solution_composition == {
        "SUSPECT": 1,
        "WEAPON": 1,
        "LOCATION": 1,
        "CLUE": 1,
    }
    assert public.solution_total == 4
    assert public.solution_composition_hidden is False


def test_hidden_composition_does_not_leak_counts_or_total() -> None:
    public = _to_detail(_create(HARD_DEMO_LEVEL))

    assert public.solution_composition is None
    assert public.solution_total is None
    assert public.solution_composition_hidden is True


def test_admin_keeps_solution_and_author_preference() -> None:
    admin = _to_admin(_create(HARD_DEMO_LEVEL))

    assert admin.reveal_solution_composition is False
    assert admin.solution_composition_hidden is True
    assert admin.solution_card_ids == [
        "suspect-mayordomo",
        "weapon-copa",
        "location-despacho",
        "clue-copa",
    ]


def test_hiding_composition_requires_hard_difficulty() -> None:
    payload = DEMO_LEVEL.model_dump()
    payload["difficulty"] = Difficulty.EASY
    payload["reveal_solution_composition"] = False

    with pytest.raises(ValidationError, match="exige dificultad difícil"):
        LevelCreate(**payload)


def test_hidden_composition_is_accepted_on_hard() -> None:
    payload = DEMO_LEVEL.model_dump()
    payload["difficulty"] = Difficulty.HARD
    payload["reveal_solution_composition"] = False

    level = LevelCreate(**payload)
    assert level.reveal_solution_composition is False
    assert level.difficulty is Difficulty.HARD


def test_summary_does_not_include_composition_fields() -> None:
    summary = _to_summary(_create(HARD_DEMO_LEVEL)).model_dump()

    assert "solution_composition" not in summary
    assert "solution_total" not in summary
    assert "solution_composition_hidden" not in summary


def test_seed_includes_a_hidden_composition_variant() -> None:
    assert HARD_DEMO_LEVEL.level_id != DEMO_LEVEL.level_id
    assert HARD_DEMO_LEVEL.difficulty is Difficulty.HARD
    assert HARD_DEMO_LEVEL.reveal_solution_composition is False


def test_catalog_filters_by_creator_username() -> None:
    repository = InMemoryLevelRepository()
    asyncio.run(repository.create(DEMO_LEVEL, creator_username="alice"))
    asyncio.run(repository.create(HARD_DEMO_LEVEL, creator_username="bob"))

    alice, alice_total = asyncio.run(
        repository.list_page(
            limit=10,
            offset=0,
            creator_username="ALICE",
        )
    )
    assert alice_total == 1
    assert alice[0].level_id == DEMO_LEVEL.level_id

    everyone, everyone_total = asyncio.run(
        repository.list_page(limit=10, offset=0)
    )
    assert everyone_total == 2
    assert len(everyone) == 2
