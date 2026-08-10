"""Tests de `fix_player_canonical_name_te_order.py` (2026-08-10, pedido
explícito del usuario tras encontrar "Vanta Valentin"/"Mike Lorenzo" con
pinta de corrupción en `Player.canonical_name`). Casos reales
investigados que motivan este script:
- "Vanta Valentin" (source=tennisexplorer, canonical_name = texto
  crudo sin normalizar) DEBE reordenarse a "Valentin Vanta".
- "Mike Lorenzo" (source=sackmann, jugador ATP real ya en orden
  correcto) NO debe tocarse -- falso positivo de la auditoría visual."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis.models import MatchHistory, Player
from scripts.tennis.fix_player_canonical_name_te_order import (
    find_reorder_candidates, run,
)


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        MatchHistory.delete().execute()
        Player.delete().execute()
    yield
    with db_historical.connection_context():
        MatchHistory.delete().execute()
        Player.delete().execute()


def _now():
    return datetime.now(timezone.utc)


def _row(match_id, player_a, player_b, player_a_id=None, player_b_id=None, **overrides):
    fields = dict(
        match_id=match_id, player_a_name=player_a, player_b_name=player_b,
        player_a_id=player_a_id, player_b_id=player_b_id,
        match_date="2026-08-11", source="tennisexplorer", fetched_at=_now(),
    )
    fields.update(overrides)
    return MatchHistory.create(**fields)


def test_reorders_unnormalized_te_native_player():
    p = Player.create(canonical_name="Vanta Valentin", created_at=_now())
    _row("1", "Rodesch Chris", "Vanta Valentin", player_b_id=p.id, source="tennisexplorer")

    candidates = find_reorder_candidates()

    assert len(candidates) == 1
    assert candidates[0][0].id == p.id
    assert candidates[0][1] == "Valentin Vanta"


def test_leaves_sackmann_sourced_player_untouched_even_if_reorder_would_change_it():
    # "Mike Lorenzo" real, ya en orden correcto, source=sackmann --
    # NUNCA debe tocarse (sackmann ya entrega Nombre Apellido nativo).
    p = Player.create(canonical_name="Mike Lorenzo", created_at=_now())
    _row("1", "Mike Lorenzo", "Noel Holland", player_a_id=p.id, source="sackmann")

    candidates = find_reorder_candidates()

    assert candidates == []


def test_leaves_already_normalized_player_untouched():
    # canonical_name YA no coincide con el texto crudo TE (alguien ya
    # lo normalizó) -- no debe reordenarse de nuevo.
    p = Player.create(canonical_name="Elina Svitolina", created_at=_now())
    _row("1", "Svitolina Elina", "Opponent X", player_a_id=p.id, source="tennisexplorer")

    candidates = find_reorder_candidates()

    assert candidates == []


def test_skips_player_with_mixed_sources_fail_closed():
    p = Player.create(canonical_name="Vanta Valentin", created_at=_now())
    _row("1", "Rodesch Chris", "Vanta Valentin", player_b_id=p.id, source="tennisexplorer")
    _row("2", "Someone", "Vanta Valentin", player_b_id=p.id, source="sackmann")

    candidates = find_reorder_candidates()

    assert candidates == []


def test_skips_single_token_name_fail_closed():
    p = Player.create(canonical_name="Vanta", created_at=_now())
    _row("1", "Opponent X", "Vanta", player_b_id=p.id, source="tennisexplorer")

    candidates = find_reorder_candidates()

    assert candidates == []


def test_skips_abbreviated_surname_initial_form_fail_closed():
    # Bug real encontrado en dry-run contra producción: 12,539/16,871
    # candidatos crudos eran de este patrón ("Apellido Inicial.") --
    # reordenar ciegamente produce basura ("A. Del Federico").
    p = Player.create(canonical_name="Del Federico A.", created_at=_now())
    _row("1", "Opponent X", "Del Federico A.", player_b_id=p.id, source="tennisexplorer")

    candidates = find_reorder_candidates()

    assert candidates == []


def test_skips_parenthetical_birth_year_suffix_fail_closed():
    # Bug real encontrado en dry-run: sufijo de desambiguación por año
    # de nacimiento se rompe al reordenar ("(1990) Moreno Alejandro").
    p = Player.create(canonical_name="Moreno Alejandro (1990)", created_at=_now())
    _row("1", "Opponent X", "Moreno Alejandro (1990)", player_b_id=p.id, source="tennisexplorer")

    candidates = find_reorder_candidates()

    assert candidates == []


def test_skips_player_with_both_name_order_variants_in_raw_text_fail_closed():
    # Regresión real encontrada en producción (2026-08-10): tras
    # fusionar 2 Player duplicados, el sobreviviente hereda partidos
    # de AMBAS variantes de orden. Reordenar a ciegas revierte un
    # nombre YA correcto de vuelta a TE-nativo (32 casos reales,
    # ej. "Naomi Osaka" -> "Osaka Naomi"). Debe saltarse.
    p = Player.create(canonical_name="Naomi Osaka", created_at=_now())
    _row("1", "Opponent X", "Naomi Osaka", player_b_id=p.id, source="tennisexplorer")
    _row("2", "Opponent Y", "Osaka Naomi", player_b_id=p.id, source="tennisexplorer")

    candidates = find_reorder_candidates()

    assert candidates == []


def test_run_dry_run_does_not_mutate():
    p = Player.create(canonical_name="Vanta Valentin", created_at=_now())
    _row("1", "Rodesch Chris", "Vanta Valentin", player_b_id=p.id, source="tennisexplorer")

    result = run(apply=False)

    assert result["candidates_found"] == 1
    reloaded = Player.get_by_id(p.id)
    assert reloaded.canonical_name == "Vanta Valentin"


def test_run_apply_mutates_canonical_name():
    p = Player.create(canonical_name="Vanta Valentin", created_at=_now())
    _row("1", "Rodesch Chris", "Vanta Valentin", player_b_id=p.id, source="tennisexplorer")

    result = run(apply=True)

    assert len(result["applied"]) == 1
    reloaded = Player.get_by_id(p.id)
    assert reloaded.canonical_name == "Valentin Vanta"


def test_run_skips_collision_and_reports_it_fail_closed():
    p1 = Player.create(canonical_name="Vanta Valentin", created_at=_now())
    p2 = Player.create(canonical_name="Valentin Vanta", created_at=_now())
    _row("1", "Rodesch Chris", "Vanta Valentin", player_b_id=p1.id, source="tennisexplorer")

    result = run(apply=True)

    assert result["applied"] == []
    assert len(result["collisions_skipped"]) == 1
    assert result["collisions_skipped"][0]["player_id"] == p1.id
    assert result["collisions_skipped"][0]["collides_with_player_id"] == p2.id
    reloaded = Player.get_by_id(p1.id)
    assert reloaded.canonical_name == "Vanta Valentin"  # sin cambios
