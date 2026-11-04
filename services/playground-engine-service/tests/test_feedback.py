"""Tests del feedback estricto cuando la composición está oculta (#6)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from app.engine import evaluate_accusation
from app.main import app
from app.schemas import LevelSnapshot
from fastapi.testclient import TestClient


def _level(*, hidden_composition: bool) -> LevelSnapshot:
    return LevelSnapshot(
        level_id="feedback-test",
        title="Prueba de feedback",
        cards=[
            {
                "id": "suspect-a",
                "type": "SUSPECT",
                "title": "Sospechoso",
                "description": "Visible",
            },
            {
                "id": "weapon-a",
                "type": "WEAPON",
                "title": "Arma",
                "description": "Visible",
            },
            {
                "id": "location-a",
                "type": "LOCATION",
                "title": "Lugar",
                "description": "Visible",
            },
            {
                "id": "clue-a",
                "type": "CLUE",
                "title": "Pista",
                "description": "Visible",
            },
        ],
        solution_card_ids=["suspect-a", "weapon-a", "location-a", "clue-a"],
        solution_composition_hidden=hidden_composition,
    )


class FakeCatalog:
    def __init__(self, level: LevelSnapshot) -> None:
        self.level = level

    async def get_level(self, level_id: str) -> LevelSnapshot:
        if level_id != self.level.level_id:
            from app.catalog import LevelNotFoundError

            raise LevelNotFoundError(level_id)
        return self.level


@pytest.fixture()
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        app.state.catalog = FakeCatalog(_level(hidden_composition=True))
        yield test_client


def test_hidden_composition_derives_strict_feedback() -> None:
    level = _level(hidden_composition=True)

    assert level.strict_feedback is True


def test_visible_composition_keeps_partial_counts() -> None:
    outcome = evaluate_accusation(
        _level(hidden_composition=False),
        ["suspect-a", "weapon-a"],
    )

    assert outcome.solved is False
    assert "Solo 2 de tus 2" in outcome.feedback_message
    assert "número exacto" in outcome.feedback_message


def test_hidden_composition_returns_binary_feedback() -> None:
    outcome = evaluate_accusation(
        _level(hidden_composition=True),
        ["suspect-a", "weapon-a"],
    )

    assert outcome.solved is False
    assert outcome.feedback_message == "La acusación no encaja con las pruebas del caso."


def test_hidden_composition_still_confirms_a_correct_solution() -> None:
    level = _level(hidden_composition=True)
    outcome = evaluate_accusation(level, list(level.solution_card_ids))

    assert outcome.solved is True
    assert "Caso resuelto" in outcome.feedback_message


def test_validate_endpoint_does_not_leak_partial_matches(client: TestClient) -> None:
    response = client.post(
        "/api/engine/validate",
        json={
            "level_id": "feedback-test",
            "selected_card_ids": ["suspect-a", "weapon-a"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["solved"] is False
    assert body["feedback_message"] == "La acusación no encaja con las pruebas del caso."
