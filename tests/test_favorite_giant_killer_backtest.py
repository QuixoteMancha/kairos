"""Tests de `favorite_giant_killer_backtest` -- 2026-08-17, pedido
explícito del usuario ("crea una estrategia... manteniendo los filtros
que teníamos de diferencia de ranking + wr por superficie más alta").

Verifica: (1) los 3 filtros heredados de `bet_on_favourite_backtest`
siguen intactos, (2) el veto nuevo de "asesino de gigantes" es
point-in-time real (nunca mira el resultado del propio caso ni del
futuro), (3) respeta el mínimo de intentos antes de aplicar el veto,
(4) `max_underdog_upset_rate=None` reproduce el comportamiento sin veto."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis import MatchHistory, Player, Tournament
from database.historical.tennis.models import TennisHistoricalOdds
from scripts.tennis.favorite_giant_killer_backtest import collect_cases


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    models = (MatchHistory, TennisHistoricalOdds, Player, Tournament)
    with db_historical.connection_context():
        for m in models:
            m.delete().execute()
    yield
    with db_historical.connection_context():
        for m in models:
            m.delete().execute()


_seq = iter(range(10_000))


def _player(**overrides) -> Player:
    now = datetime.now(timezone.utc)
    n = next(_seq)
    base = {"canonical_name": f"Player{n}", "tour": "ATP", "created_at": now}
    base.update(overrides)
    return Player.create(**base)


def _odds(match_history, player, opponent, self_odds, opp_odds, match_date):
    TennisHistoricalOdds.create(
        match_history=match_history, player_name=player.canonical_name,
        opponent_name=opponent.canonical_name, match_date=match_date,
        odds_self=self_odds, odds_opponent=opp_odds, source="tennisexplorer",
        fetched_at=datetime.now(timezone.utc),
    )


def _match(a, b, *, match_date, winner, surface="Hard", tour="ATP",
           a_rank=1, b_rank=50, a_elo=2000.0, b_elo=1500.0, match_id=None):
    row = MatchHistory.create(
        match_id=match_id or f"m-{next(_seq)}", match_date=match_date, tour=tour,
        surface=surface, player_a=a, player_b=b,
        player_a_elo_rank=a_rank, player_b_elo_rank=b_rank,
        player_a_elo=a_elo, player_b_elo=b_elo,
        winner=(a if winner == "a" else b), fetched_at=datetime.now(timezone.utc),
    )
    return row


def _seed_qualifying_history(fav, dog, *, tour="ATP", surface="Hard", start="2024-01-01"):
    """Genera el historial mínimo real que exigen los 3 filtros heredados
    (surface >=3, forma reciente >=5, ambos con el favorito ganando más)
    ANTES de la fecha del caso que se quiere testear.

    Rank gap DELIBERADAMENTE chico (5 < 20, umbral ATP) para que estas
    filas de seed NUNCA califiquen como casos por sí mismas -- solo
    alimentan el estado point-in-time (superficie/forma), igual que
    haría cualquier partido real que no pasa el filtro de ranking. Sin
    esto, una vez acumulada suficiente historia las propias filas de
    seed empiezan a calificar como casos adicionales y ensucian el
    conteo esperado en los tests.

    Elo del seed IGUALADO (gap < UPSET_ELO_GAP=50) a propósito -- si no,
    cada fila de seed (favorito gana con el Elo default 2000 vs 1500)
    se registraría también como "underdog perdió el upset", ensuciando
    el historial de upsets que los tests quieren aislar."""
    base = datetime.fromisoformat(start).date()
    for i in range(6):
        d = (base + timedelta(days=i)).isoformat()
        row = _match(fav, dog, match_date=d, winner="a", surface=surface, tour=tour,
                     a_rank=10, b_rank=15, a_elo=1750.0, b_elo=1740.0,
                     match_id=f"seed-{fav.id}-{dog.id}-{i}")
        _odds(row, fav, dog, 1.3, 3.2, d)


def test_inherits_rank_gap_surface_and_form_filters():
    """Los 3 filtros originales siguen aplicando -- un caso que los pasa
    sin ningún historial de upset debe aparecer igual (veto desactivado
    por falta de muestra, no por diseño roto)."""
    fav, dog = _player(), _player()
    _seed_qualifying_history(fav, dog)
    case_date = "2024-01-10"
    row = _match(fav, dog, match_date=case_date, winner="a", match_id="case-1")
    _odds(row, fav, dog, 1.3, 3.2, case_date)

    cases, skipped = collect_cases()
    assert len(cases) == 1
    assert cases[0]["favorite"] == fav.canonical_name
    assert cases[0]["underdog_upset_attempts"] == 0


def test_rank_gap_filter_still_rejects_close_ranks():
    fav, dog = _player(), _player()
    _seed_qualifying_history(fav, dog)
    case_date = "2024-01-10"
    # gap de 5 posiciones, muy por debajo de RANK_GAP_BY_TOUR["ATP"]=20.
    row = _match(fav, dog, match_date=case_date, winner="a",
                 a_rank=10, b_rank=15, match_id="case-close-rank")
    _odds(row, fav, dog, 1.3, 3.2, case_date)

    cases, skipped = collect_cases()
    assert len(cases) == 0
    assert skipped.get("rank_gap", 0) >= 1


def test_giant_killer_veto_rejects_proven_upset_underdog():
    """El underdog de este caso ya venció 4 veces a rivales con Elo
    >=50 puntos por encima del suyo -- debe vetarse el pick del
    favorito."""
    fav, dog = _player(), _player()
    _seed_qualifying_history(fav, dog)

    # El underdog acumula un historial REAL de upsets contra terceros,
    # ANTES de la fecha del caso -- point-in-time correcto.
    victim = _player()
    for i in range(4):
        d = f"2024-01-{11+i:02d}"
        row = _match(
            dog, victim, match_date=d, winner="a",  # dog (player_a) gana
            a_elo=1500.0, b_elo=1600.0,  # dog era underdog por Elo (gap=100>=50)
            a_rank=10, b_rank=15,  # gap chico -> nunca califica como caso
            match_id=f"upset-{i}",
        )
        _odds(row, dog, victim, 3.0, 1.4, d)

    case_date = "2024-01-20"
    row = _match(fav, dog, match_date=case_date, winner="a", match_id="case-veto")
    _odds(row, fav, dog, 1.3, 3.2, case_date)

    cases, skipped = collect_cases()
    assert len(cases) == 0
    assert skipped.get("underdog_giant_killer", 0) == 1


def test_giant_killer_veto_respects_minimum_attempts():
    """Con solo 2 upsets previos (por debajo de MIN_UPSET_ATTEMPTS=3),
    el veto NO debe aplicarse -- muestra insuficiente para confiar en la
    tasa."""
    fav, dog = _player(), _player()
    _seed_qualifying_history(fav, dog)

    victim = _player()
    for i in range(2):
        d = f"2024-01-{11+i:02d}"
        row = _match(dog, victim, match_date=d, winner="a",
                     a_elo=1500.0, b_elo=1600.0, a_rank=10, b_rank=15,
                     match_id=f"upset2-{i}")
        _odds(row, dog, victim, 3.0, 1.4, d)

    case_date = "2024-01-20"
    row = _match(fav, dog, match_date=case_date, winner="a", match_id="case-no-veto")
    _odds(row, fav, dog, 1.3, 3.2, case_date)

    cases, skipped = collect_cases()
    assert len(cases) == 1
    assert cases[0]["underdog_upset_attempts"] == 2


def test_giant_killer_veto_never_uses_the_case_itself_or_future_matches():
    """El veto no debe contar el propio caso en evaluación, ni partidos
    posteriores del underdog -- solo lo estrictamente anterior."""
    fav, dog = _player(), _player()
    _seed_qualifying_history(fav, dog)

    case_date = "2024-01-20"
    row = _match(fav, dog, match_date=case_date, winner="b", match_id="case-itself")
    _odds(row, fav, dog, 1.3, 3.2, case_date)

    # Partido FUTURO del underdog contra alguien de Elo mucho mayor,
    # ganado -- no debe contar para el caso ya evaluado arriba.
    victim = _player()
    future_date = "2024-02-01"
    future_row = _match(dog, victim, match_date=future_date, winner="a",
                         a_elo=1500.0, b_elo=1700.0, a_rank=10, b_rank=15,
                         match_id="future-upset")
    _odds(future_row, dog, victim, 4.0, 1.2, future_date)

    cases, skipped = collect_cases()
    # El caso original debe seguir presente (0 intentos de upset previos:
    # ni el resultado propio ni el partido futuro cuentan).
    matching = [c for c in cases if c["match_id"] == "case-itself"]
    assert len(matching) == 1
    assert matching[0]["underdog_upset_attempts"] == 0


def test_disabling_veto_reproduces_baseline_filters_only():
    fav, dog = _player(), _player()
    _seed_qualifying_history(fav, dog)
    victim = _player()
    for i in range(4):
        d = f"2024-01-{11+i:02d}"
        row = _match(dog, victim, match_date=d, winner="a",
                     a_elo=1500.0, b_elo=1600.0, a_rank=10, b_rank=15,
                     match_id=f"upset3-{i}")
        _odds(row, dog, victim, 3.0, 1.4, d)

    case_date = "2024-01-20"
    row = _match(fav, dog, match_date=case_date, winner="a", match_id="case-disabled-veto")
    _odds(row, fav, dog, 1.3, 3.2, case_date)

    cases_with_veto, _ = collect_cases()
    cases_without_veto, _ = collect_cases(max_underdog_upset_rate=None)

    assert len(cases_with_veto) == 0
    assert len(cases_without_veto) == 1
