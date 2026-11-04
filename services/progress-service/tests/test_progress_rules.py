"""Reglas de negocio del progreso: notas, sesiones, intentos y purga."""

from __future__ import annotations

import asyncio

from app.repository import (
    InMemoryProgressRepository,
    NoteConflictError,
    WorkspaceConflictError,
)

USER = "usr_test"
LEVEL = "faro-002"
OTHER_LEVEL = "tutorial-001"


def run(coroutine):
    return asyncio.run(coroutine)


def test_note_versioning_and_conflict() -> None:
    repo = InMemoryProgressRepository()

    created = run(
        repo.upsert_note(
            user_id=USER,
            level_key=LEVEL,
            content="Primera nota",
            base_version=None,
        )
    )
    assert created.version == 1

    updated = run(
        repo.upsert_note(
            user_id=USER,
            level_key=LEVEL,
            content="Segunda nota",
            base_version=1,
        )
    )
    assert updated.version == 2
    assert updated.content == "Segunda nota"

    try:
        run(
            repo.upsert_note(
                user_id=USER,
                level_key=LEVEL,
                content="Conflicto",
                base_version=1,
            )
        )
    except NoteConflictError as error:
        assert error.current is not None
        assert error.current.version == 2
        assert error.current.content == "Segunda nota"
    else:  # pragma: no cover - el test debe fallar si no hay conflicto
        raise AssertionError("Se esperaba NoteConflictError")


def test_note_scope_is_isolated_per_user() -> None:
    repo = InMemoryProgressRepository()
    run(
        repo.upsert_note(
            user_id=USER,
            level_key=LEVEL,
            content="Privada",
            base_version=None,
        )
    )

    assert run(repo.get_note(user_id="otro", level_key=LEVEL)) is None
    assert run(repo.get_note(user_id=USER, level_key=LEVEL)).content == "Privada"


def test_workspace_deduplicates_and_checks_version() -> None:
    repo = InMemoryProgressRepository()

    empty = run(repo.get_workspace(user_id=USER, level_id=LEVEL))
    assert empty.version == 0
    assert empty.crossed_out_ids == []

    saved = run(
        repo.upsert_workspace(
            user_id=USER,
            level_id=LEVEL,
            crossed_out_ids=["clue-botas", "clue-botas", "suspect-faro"],
            accusation_ids=["suspect-faro"],
            base_version=0,
        )
    )
    assert saved.version == 1
    assert saved.crossed_out_ids == ["clue-botas", "suspect-faro"]

    try:
        run(
            repo.upsert_workspace(
                user_id=USER,
                level_id=LEVEL,
                crossed_out_ids=[],
                accusation_ids=[],
                base_version=0,
            )
        )
    except WorkspaceConflictError as error:
        assert error.current.version == 1
    else:  # pragma: no cover
        raise AssertionError("Se esperaba WorkspaceConflictError")


def test_attempts_are_idempotent_by_client_id() -> None:
    repo = InMemoryProgressRepository()

    first = run(
        repo.record_attempt(
            user_id=USER,
            level_id=LEVEL,
            client_attempt_id="attempt-1",
            session_key="browser-1",
            solved=False,
            selected_card_ids=["suspect-faro"],
            elapsed_ms=120,
        )
    )
    duplicate = run(
        repo.record_attempt(
            user_id=USER,
            level_id=LEVEL,
            client_attempt_id="attempt-1",
            session_key="browser-1",
            solved=False,
            selected_card_ids=["suspect-faro"],
            elapsed_ms=120,
        )
    )

    assert first.recorded is True
    assert first.attempt_number == 1
    assert duplicate.recorded is False
    assert duplicate.idempotent is True
    assert duplicate.attempt_number == 1

    stats = run(repo.get_state(user_id=USER, level_id=LEVEL))[2]
    assert stats is not None
    assert stats.total_attempts == 1


def test_first_solve_is_immutable_after_replays() -> None:
    repo = InMemoryProgressRepository()

    run(
        repo.record_attempt(
            user_id=USER,
            level_id=LEVEL,
            client_attempt_id="attempt-1",
            session_key=None,
            solved=False,
            selected_card_ids=["suspect-faro"],
            elapsed_ms=100,
        )
    )
    solved = run(
        repo.record_attempt(
            user_id=USER,
            level_id=LEVEL,
            client_attempt_id="attempt-2",
            session_key=None,
            solved=True,
            selected_card_ids=["suspect-faro", "weapon-cuerda"],
            elapsed_ms=200,
        )
    )
    replay = run(
        repo.record_attempt(
            user_id=USER,
            level_id=LEVEL,
            client_attempt_id="attempt-3",
            session_key=None,
            solved=True,
            selected_card_ids=["suspect-faro", "weapon-cuerda"],
            elapsed_ms=300,
        )
    )

    assert solved.first_solve is True
    assert solved.first_solve_attempts == 2
    assert replay.first_solve is False
    assert replay.is_replay is True

    stats = run(repo.get_state(user_id=USER, level_id=LEVEL))[2]
    assert stats is not None
    assert stats.first_solve_attempts == 2
    assert stats.total_attempts == 3
    assert stats.total_solves == 2
    assert stats.replay_count == 1


def test_restart_closes_session_and_keeps_first_solve_count() -> None:
    repo = InMemoryProgressRepository()

    original = run(
        repo.get_or_create_session(user_id=USER, level_id=LEVEL)
    )
    run(
        repo.record_attempt(
            user_id=USER,
            level_id=LEVEL,
            client_attempt_id="attempt-1",
            session_key=None,
            solved=False,
            selected_card_ids=["suspect-faro"],
            elapsed_ms=100,
        )
    )

    restarted = run(
        repo.restart_session(user_id=USER, level_id=LEVEL)
    )
    assert restarted.session_id != original.session_id
    assert restarted.is_replay is True
    assert restarted.replay_of == original.session_id

    detail = run(
        repo.get_case_detail(user_id=USER, level_id=LEVEL)
    )
    statuses = {session.session_id: session.status for session in detail.sessions}
    assert statuses[original.session_id] == "abandoned"
    assert statuses[restarted.session_id] == "active"
    assert detail.stats is not None
    assert detail.stats.replay_count == 0

    solved = run(
        repo.record_attempt(
            user_id=USER,
            level_id=LEVEL,
            client_attempt_id="attempt-2",
            session_key=None,
            solved=True,
            selected_card_ids=["suspect-faro"],
            elapsed_ms=100,
        )
    )
    assert solved.first_solve is True
    assert solved.first_solve_attempts == 2

    after_solve = run(
        repo.restart_session(user_id=USER, level_id=LEVEL)
    )
    assert after_solve.is_replay is True
    stats = run(repo.get_state(user_id=USER, level_id=LEVEL))[2]
    assert stats is not None
    assert stats.first_solve_attempts == 2
    assert stats.replay_count == 1


def test_summary_history_and_detail() -> None:
    repo = InMemoryProgressRepository()

    run(
        repo.record_attempt(
            user_id=USER,
            level_id=LEVEL,
            client_attempt_id="attempt-1",
            session_key=None,
            solved=True,
            selected_card_ids=["suspect-faro"],
            elapsed_ms=100,
        )
    )
    run(
        repo.record_attempt(
            user_id=USER,
            level_id=OTHER_LEVEL,
            client_attempt_id="attempt-2",
            session_key=None,
            solved=False,
            selected_card_ids=["suspect-mayordomo"],
            elapsed_ms=100,
        )
    )

    summary = run(repo.summary(user_id=USER))
    assert summary.cases_played == 2
    assert summary.cases_solved == 1
    assert summary.solve_rate == 0.5
    assert summary.first_solve_attempts_total == 1
    assert summary.total_attempts == 2

    solved_items, solved_total = run(
        repo.list_cases(
            user_id=USER,
            status_filter="solved",
            limit=10,
            offset=0,
        )
    )
    assert solved_total == 1
    assert solved_items[0].level_id == LEVEL

    unsolved_items, unsolved_total = run(
        repo.list_cases(
            user_id=USER,
            status_filter="unsolved",
            limit=10,
            offset=0,
        )
    )
    assert unsolved_total == 1
    assert unsolved_items[0].level_id == OTHER_LEVEL

    detail = run(
        repo.get_case_detail(user_id=USER, level_id=LEVEL)
    )
    assert detail.stats is not None
    assert detail.stats.total_attempts == 1
    assert len(detail.sessions) == 1
    assert len(detail.attempts) == 1
    assert detail.attempts[0].solved is True


def test_purge_user_is_idempotent() -> None:
    repo = InMemoryProgressRepository()
    run(
        repo.upsert_note(
            user_id=USER,
            level_key=LEVEL,
            content="Nota",
            base_version=None,
        )
    )
    run(
        repo.upsert_workspace(
            user_id=USER,
            level_id=LEVEL,
            crossed_out_ids=["clue-botas"],
            accusation_ids=[],
            base_version=None,
        )
    )
    run(
        repo.record_attempt(
            user_id=USER,
            level_id=LEVEL,
            client_attempt_id="attempt-1",
            session_key=None,
            solved=False,
            selected_card_ids=["clue-botas"],
            elapsed_ms=100,
        )
    )

    deleted = run(repo.purge_user(user_id=USER))
    assert deleted["notes"] == 1
    assert deleted["workspaces"] == 1
    assert deleted["attempts"] == 1
    assert deleted["sessions"] == 1
    assert deleted["stats"] == 1

    again = run(repo.purge_user(user_id=USER))
    assert all(count == 0 for count in again.values())
    assert run(repo.get_note(user_id=USER, level_key=LEVEL)) is None
