"""Tests de `scripts/tennis/buy_the_dip_backtest.py` -- 2026-08-30,
especificación cerrada de Buy the Dip (contraparte simétrica de
Closing Scalp)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from database.db import init_db_historical
from database.historical.tennis.models import (
    LivePointSnapshot, LiveStatsSnapshot, Match, MatchHistory, Player, ProbabilityTick,
)
from scripts.tennis.buy_the_dip_backtest import (
    MAX_ELO_GAP_RELATIVE, MIN_RANK_GAP_FAVORING_TRAILING, _elo_parity, _rank_favors_trailing,
    collect_buy_the_dip_events,
)


@pytest.fixture(autouse=True)
def clean_tables():
    init_db_historical()
    models = (LiveStatsSnapshot, LivePointSnapshot, ProbabilityTick, Match, MatchHistory, Player)
    for model in models:
        model.delete().execute()
    yield
    for model in models:
        model.delete().execute()


def _player(name):
    return Player.create(canonical_name=name, created_at=datetime.now(timezone.utc))


def _match(market_id, *, history=None, best_of=3):
    return Match.create(
        market_id=market_id, ticker_a="A", ticker_b="B", tour="WTA",
        surface="Hard", player_a_name="Player A", player_b_name="Player B",
        best_of=best_of, close_time="2026-08-17T18:00:00Z",
        detected_at=datetime.now(timezone.utc), flashscore_match_id="fs-x",
        player_a_is_flashscore_p1=False, match_history=history,
    )


def _snapshot(match, minute, price, *, price_b=None, points_a=0, points_b=0, breaks_a=0, breaks_b=0,
              first_pct_a=60.0, first_pct_b=60.0,
              first_won_a=55.0, first_won_b=75.0,
              second_won_a=50.0, second_won_b=55.0,
              current_set=1, raw_point_json="{}"):
    tick = ProbabilityTick.create(
        match=match, observed_at=datetime.now(timezone.utc) + timedelta(minutes=minute),
        market_price_a=price, market_price_b=price_b if price_b is not None else round(1 - price, 4),
    )
    LivePointSnapshot.create(
        match=match, probability_tick=tick, flashscore_match_id="fs-x",
        player_a_is_flashscore_p1=False,
        observed_at=datetime.now(timezone.utc) + timedelta(minutes=minute),
        raw_point_json=raw_point_json, current_set=current_set,
    )
    return LiveStatsSnapshot.create(
        match=match, probability_tick=tick, flashscore_match_id="fs-x",
        player_a_is_flashscore_p1=False,
        observed_at=datetime.now(timezone.utc) + timedelta(minutes=minute),
        raw_stats_json="{}",
        total_points_won_a=points_a, total_points_won_b=points_b,
        break_points_won_a=breaks_a, break_points_won_b=breaks_b,
        first_serve_pct_a=first_pct_a, first_serve_pct_b=first_pct_b,
        first_serve_points_won_pct_a=first_won_a, first_serve_points_won_pct_b=first_won_b,
        second_serve_points_won_pct_a=second_won_a, second_serve_points_won_pct_b=second_won_b,
    )


def test_elo_parity_true_when_gap_small():
    a = _player("Par A")
    b = _player("Par B")
    history = MatchHistory.create(
        match_id="te:1", source="tennisexplorer", tour="WTA", match_date="2026-08-17",
        player_a=a, player_b=b, fetched_at=datetime.now(timezone.utc),
        player_a_surface_elo=1700, player_b_surface_elo=1650,  # gap relativo ~3%, <10%
    )
    assert _elo_parity(history) is True


def test_rank_favors_trailing_true_when_trailing_much_better_ranked():
    """2026-08-30, Lectura B confirmada: el que va perdiendo (b) tiene
    ranking MEJOR (número más bajo) que el líder (a), por más de 20
    puestos -- la señal correcta."""
    a = _player("Leader A")
    b = _player("Trailing B")
    history = MatchHistory.create(
        match_id="te:2", source="tennisexplorer", tour="WTA", match_date="2026-08-17",
        player_a=a, player_b=b, fetched_at=datetime.now(timezone.utc),
        player_a_ranking=90, player_b_ranking=20,  # b (perdiendo) 70 puestos mejor que a
    )
    assert _rank_favors_trailing(history, "b") is True


def test_rank_favors_trailing_false_when_gap_too_small():
    a = _player("Leader A2")
    b = _player("Trailing B2")
    history = MatchHistory.create(
        match_id="te:3", source="tennisexplorer", tour="WTA", match_date="2026-08-17",
        player_a=a, player_b=b, fetched_at=datetime.now(timezone.utc),
        player_a_ranking=40, player_b_ranking=35,  # gap=5, no supera 20
    )
    assert _rank_favors_trailing(history, "b") is False


def test_rank_favors_trailing_false_when_direction_is_wrong():
    """El que va perdiendo (b) tiene ranking PEOR que el líder -- lo
    inverso de lo que pide la especificación, debe excluirse."""
    a = _player("Leader A3")
    b = _player("Trailing B3")
    history = MatchHistory.create(
        match_id="te:4", source="tennisexplorer", tour="WTA", match_date="2026-08-17",
        player_a=a, player_b=b, fetched_at=datetime.now(timezone.utc),
        player_a_ranking=5, player_b_ranking=90,  # a (lider) es el mejor rankeado
    )
    assert _rank_favors_trailing(history, "b") is False


def test_buy_the_dip_detects_break_trigger_with_weak_leader_stats():
    """A rompe a B (B va perdiendo), pero A (el que gana) tiene 2do
    servicio débil (<40%) y SPW no mayor al de B -- confirma que la
    ventaja de A NO es real, se compra el dip de B."""
    a = _player("Weak Leader A")
    b = _player("Dip B")
    history = MatchHistory.create(
        match_id="te:3", source="tennisexplorer", tour="WTA", match_date="2026-08-17",
        player_a=a, player_b=b, fetched_at=datetime.now(timezone.utc),
        player_a_ranking=90, player_b_ranking=20,
        player_a_surface_elo=1700, player_b_surface_elo=1650,
        score="6-4, 4-6, 6-3", winner=b,
    )
    match = _match("KX-DIP1", history=history)
    match.winner = "b"
    match.save()

    # A rompe a B (breaks_a sube). A tiene 2do servicio flojo (30%) y
    # SPW menor al de B (first_won_a=50 vs first_won_b=80).
    _snapshot(match, 0, 0.50, points_a=10, points_b=10, breaks_a=0, breaks_b=0, current_set=1,
              first_pct_a=60, first_won_a=50, second_won_a=30,
              first_pct_b=60, first_won_b=80, second_won_b=55)
    _snapshot(match, 5, 0.65, points_a=14, points_b=11, breaks_a=1, breaks_b=0, current_set=1,
              first_pct_a=60, first_won_a=50, second_won_a=30,
              first_pct_b=60, first_won_b=80, second_won_b=55)
    _snapshot(match, 10, 0.68, points_a=15, points_b=13, breaks_a=1, breaks_b=0, current_set=1,
              first_pct_a=60, first_won_a=50, second_won_a=30,
              first_pct_b=60, first_won_b=80, second_won_b=55)

    events = collect_buy_the_dip_events()
    assert len(events) == 1
    ev = events[0]
    assert ev.side == "b"
    assert ev.trigger == "break"
    assert ev.entry_price == 0.35  # market_price_b = 1-0.65


def test_excludes_break_trigger_when_leader_has_real_stat_dominance():
    a = _player("Strong Leader A")
    b = _player("NoDip B")
    history = MatchHistory.create(
        match_id="te:4", source="tennisexplorer", tour="WTA", match_date="2026-08-17",
        player_a=a, player_b=b, fetched_at=datetime.now(timezone.utc),
        player_a_ranking=90, player_b_ranking=20,
        player_a_surface_elo=1700, player_b_surface_elo=1650,
    )
    match = _match("KX-DIP2", history=history)
    # A rompe a B pero A domina de verdad en stats (2do servicio alto, SPW alto).
    _snapshot(match, 0, 0.50, points_a=10, points_b=10, breaks_a=0, breaks_b=0, current_set=1,
              first_pct_a=65, first_won_a=80, second_won_a=60,
              first_pct_b=55, first_won_b=55, second_won_b=45)
    _snapshot(match, 5, 0.70, points_a=14, points_b=11, breaks_a=1, breaks_b=0, current_set=1,
              first_pct_a=65, first_won_a=80, second_won_a=60,
              first_pct_b=55, first_won_b=55, second_won_b=45)
    _snapshot(match, 10, 0.72, points_a=15, points_b=13, breaks_a=1, breaks_b=0, current_set=1,
              first_pct_a=65, first_won_a=80, second_won_a=60,
              first_pct_b=55, first_won_b=55, second_won_b=45)

    assert collect_buy_the_dip_events() == []


def test_excludes_when_leader_has_better_ranking_than_trailing():
    """2026-08-30: dirección INVERSA a la que pide la especificación --
    el líder (a) está mejor rankeado que el que va perdiendo (b). Debe
    excluirse aunque el líder tenga stats débiles."""
    a = _player("Mismatch A")
    b = _player("Mismatch B")
    history = MatchHistory.create(
        match_id="te:5", source="tennisexplorer", tour="WTA", match_date="2026-08-17",
        player_a=a, player_b=b, fetched_at=datetime.now(timezone.utc),
        player_a_ranking=5, player_b_ranking=90,  # a (lider) mejor rankeado -- direccion incorrecta
        player_a_surface_elo=1700, player_b_surface_elo=1650,  # elo parejo -- se aisla el motivo real
    )
    match = _match("KX-DIP3", history=history)
    _snapshot(match, 0, 0.50, points_a=10, points_b=10, breaks_a=0, breaks_b=0, current_set=1,
              first_pct_a=60, first_won_a=50, second_won_a=30,
              first_pct_b=60, first_won_b=80, second_won_b=55)
    _snapshot(match, 5, 0.65, points_a=14, points_b=11, breaks_a=1, breaks_b=0, current_set=1,
              first_pct_a=60, first_won_a=50, second_won_a=30,
              first_pct_b=60, first_won_b=80, second_won_b=55)
    _snapshot(match, 10, 0.68, points_a=15, points_b=13, breaks_a=1, breaks_b=0, current_set=1,
              first_pct_a=60, first_won_a=50, second_won_a=30,
              first_pct_b=60, first_won_b=80, second_won_b=55)

    assert collect_buy_the_dip_events() == []


def test_excludes_final_set():
    a = _player("Decider A")
    b = _player("Decider B")
    history = MatchHistory.create(
        match_id="te:6", source="tennisexplorer", tour="WTA", match_date="2026-08-17",
        player_a=a, player_b=b, fetched_at=datetime.now(timezone.utc),
        player_a_ranking=90, player_b_ranking=20,
        player_a_surface_elo=1700, player_b_surface_elo=1650,
    )
    match = _match("KX-DIP4", history=history)  # best_of=3
    _snapshot(match, 0, 0.50, points_a=10, points_b=10, breaks_a=0, breaks_b=0, current_set=3,
              first_pct_a=60, first_won_a=50, second_won_a=30,
              first_pct_b=60, first_won_b=80, second_won_b=55)
    _snapshot(match, 5, 0.65, points_a=14, points_b=11, breaks_a=1, breaks_b=0, current_set=3,
              first_pct_a=60, first_won_a=50, second_won_a=30,
              first_pct_b=60, first_won_b=80, second_won_b=55)
    _snapshot(match, 10, 0.68, points_a=15, points_b=13, breaks_a=1, breaks_b=0, current_set=3,
              first_pct_a=60, first_won_a=50, second_won_a=30,
              first_pct_b=60, first_won_b=80, second_won_b=55)

    assert collect_buy_the_dip_events() == []


def test_excludes_price_below_floor():
    a = _player("Cheap A")
    b = _player("Cheap B")
    history = MatchHistory.create(
        match_id="te:7", source="tennisexplorer", tour="WTA", match_date="2026-08-17",
        player_a=a, player_b=b, fetched_at=datetime.now(timezone.utc),
        player_a_ranking=90, player_b_ranking=20,
        player_a_surface_elo=1700, player_b_surface_elo=1650,
    )
    match = _match("KX-DIP5", history=history)
    # B (perdiendo) queda a 0.10, por debajo del piso de 0.20
    _snapshot(match, 0, 0.50, price_b=0.50, points_a=10, points_b=10, breaks_a=0, breaks_b=0, current_set=1,
              first_pct_a=60, first_won_a=50, second_won_a=30,
              first_pct_b=60, first_won_b=80, second_won_b=55)
    _snapshot(match, 5, 0.90, price_b=0.10, points_a=14, points_b=11, breaks_a=1, breaks_b=0, current_set=1,
              first_pct_a=60, first_won_a=50, second_won_a=30,
              first_pct_b=60, first_won_b=80, second_won_b=55)
    _snapshot(match, 10, 0.90, price_b=0.10, points_a=15, points_b=13, breaks_a=1, breaks_b=0, current_set=1,
              first_pct_a=60, first_won_a=50, second_won_a=30,
              first_pct_b=60, first_won_b=80, second_won_b=55)

    assert collect_buy_the_dip_events() == []


def test_buy_the_dip_detects_lost_previous_set_trigger():
    """B perdió el set 1 (6-3 para A) -- al entrar al set 2, se evalúa
    el dip de B sin necesidad de un quiebre nuevo en el set 2."""
    a = _player("SetWinner A")
    b = _player("SetLoser B")
    history = MatchHistory.create(
        match_id="te:8", source="tennisexplorer", tour="WTA", match_date="2026-08-17",
        player_a=a, player_b=b, fetched_at=datetime.now(timezone.utc),
        player_a_ranking=90, player_b_ranking=20,
        player_a_surface_elo=1700, player_b_surface_elo=1650,
        score="6-3, 4-6, 6-2", winner=b,
    )
    match = _match("KX-DIP6", history=history)
    match.winner = "b"
    match.save()

    import json
    set1_done = {"set_1": {"games": (
        [{"winner_side": 2}] * 6 + [{"winner_side": 1}] * 3  # A (side 2, pa_is_p1=False) gana 6-3
    )}}
    raw = json.dumps(set1_done)

    _snapshot(match, 0, 0.20, points_a=6, points_b=3, breaks_a=1, breaks_b=0, current_set=1,
              first_pct_a=60, first_won_a=50, second_won_a=30,
              first_pct_b=60, first_won_b=80, second_won_b=55)
    _snapshot(match, 5, 0.25, points_a=8, points_b=5, breaks_a=1, breaks_b=0, current_set=1,
              first_pct_a=60, first_won_a=50, second_won_a=30,
              first_pct_b=60, first_won_b=80, second_won_b=55)
    _snapshot(match, 10, 0.30, points_a=10, points_b=10, breaks_a=1, breaks_b=0, current_set=2,
              first_pct_a=60, first_won_a=50, second_won_a=30,
              first_pct_b=60, first_won_b=80, second_won_b=55, raw_point_json=raw)

    events = collect_buy_the_dip_events()
    dip_events = [e for e in events if e.trigger == "lost_previous_set"]
    assert len(dip_events) == 1
    assert dip_events[0].side == "b"


def test_take_profit_triggers_on_retrace_confirmed_by_leader_stats():
    """2026-08-30, pedido explícito del usuario: TP con confirmación
    (Lectura B) -- el precio del dip (B) sube a un pico (0.50), luego
    retrocede a +10pp desde la entrada (0.30 -> 0.40), Y las stats del
    líder (A) confirman el retroceso (su 2do servicio dejó de ser débil,
    subió de 30% a 45%, por encima del umbral de 40%) -- se asegura la
    ganancia ahí, sin esperar el resultado real del partido."""
    a = _player("TP Leader A")
    b = _player("TP Dip B")
    history = MatchHistory.create(
        match_id="te:9", source="tennisexplorer", tour="WTA", match_date="2026-08-17",
        player_a=a, player_b=b, fetched_at=datetime.now(timezone.utc),
        player_a_ranking=90, player_b_ranking=20,
        player_a_surface_elo=1700, player_b_surface_elo=1650,
        score="6-3, 4-6, 6-2", winner=a,  # el resultado real NO importa, el TP resuelve antes
    )
    match = _match("KX-TP1", history=history)
    match.winner = "a"
    match.save()

    # t0: baseline. t1: A rompe a B -> entrada (price_b=0.30). t2: price_b
    # sube a 0.50 (pico). t3: retrocede a 0.40 (+10pp desde 0.30) Y el 2do
    # servicio de A ya no es débil (45% >= 40%) -- confirma el TP.
    _snapshot(match, 0, 0.50, price_b=0.50, points_a=10, points_b=10, breaks_a=0, breaks_b=0, current_set=1,
              first_pct_a=60, first_won_a=50, second_won_a=30,
              first_pct_b=60, first_won_b=80, second_won_b=55)
    _snapshot(match, 5, 0.70, price_b=0.30, points_a=14, points_b=11, breaks_a=1, breaks_b=0, current_set=1,
              first_pct_a=60, first_won_a=50, second_won_a=30,
              first_pct_b=60, first_won_b=80, second_won_b=55)
    _snapshot(match, 10, 0.50, price_b=0.50, points_a=15, points_b=13, breaks_a=1, breaks_b=0, current_set=1,
              first_pct_a=60, first_won_a=50, second_won_a=30,
              first_pct_b=60, first_won_b=80, second_won_b=55)
    _snapshot(match, 15, 0.60, price_b=0.40, points_a=16, points_b=15, breaks_a=1, breaks_b=0, current_set=1,
              first_pct_a=60, first_won_a=45, second_won_a=45,  # 2do servicio de A ya no es debil
              first_pct_b=60, first_won_b=80, second_won_b=55)

    events = collect_buy_the_dip_events()
    break_events = [e for e in events if e.trigger == "break"]
    assert len(break_events) == 1
    ev = break_events[0]
    assert ev.entry_price == 0.30
    assert ev.take_profit_triggered is True
    assert ev.exit_price == pytest.approx(0.40)
    assert ev.match_return_pct == pytest.approx(100.0 * 0.10 / 0.30, abs=0.01)
    assert ev.closer_won_match is True


def test_take_profit_not_triggered_without_stats_confirmation():
    """Mismo retroceso de precio (pico 0.50 -> 0.40), pero el 2do
    servicio del líder SIGUE siendo débil -- no hay confirmación de
    stats, no se toma profit, la posición sigue abierta hasta el
    resultado real."""
    a = _player("NoTP Leader A")
    b = _player("NoTP Dip B")
    history = MatchHistory.create(
        match_id="te:10", source="tennisexplorer", tour="WTA", match_date="2026-08-17",
        player_a=a, player_b=b, fetched_at=datetime.now(timezone.utc),
        player_a_ranking=90, player_b_ranking=20,
        player_a_surface_elo=1700, player_b_surface_elo=1650,
        score="4-6, 6-3, 2-6", winner=b,
    )
    match = _match("KX-TP2", history=history)
    match.winner = "b"
    match.save()

    _snapshot(match, 0, 0.50, price_b=0.50, points_a=10, points_b=10, breaks_a=0, breaks_b=0, current_set=1,
              first_pct_a=60, first_won_a=50, second_won_a=30,
              first_pct_b=60, first_won_b=80, second_won_b=55)
    _snapshot(match, 5, 0.70, price_b=0.30, points_a=14, points_b=11, breaks_a=1, breaks_b=0, current_set=1,
              first_pct_a=60, first_won_a=50, second_won_a=30,
              first_pct_b=60, first_won_b=80, second_won_b=55)
    _snapshot(match, 10, 0.50, price_b=0.50, points_a=15, points_b=13, breaks_a=1, breaks_b=0, current_set=1,
              first_pct_a=60, first_won_a=50, second_won_a=30,
              first_pct_b=60, first_won_b=80, second_won_b=55)
    _snapshot(match, 15, 0.60, price_b=0.40, points_a=16, points_b=15, breaks_a=1, breaks_b=0, current_set=1,
              first_pct_a=60, first_won_a=50, second_won_a=30,  # sigue debil, sin confirmacion
              first_pct_b=60, first_won_b=80, second_won_b=55)

    events = collect_buy_the_dip_events()
    break_events = [e for e in events if e.trigger == "break"]
    assert len(break_events) == 1
    ev = break_events[0]
    assert ev.take_profit_triggered is False
    assert ev.closer_won_match is True  # se resolvio por el resultado real (b gano el partido)
