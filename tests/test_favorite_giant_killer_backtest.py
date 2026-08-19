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


def _tournament(level="ATP 250"):
    """2026-08-17, pedido explícito del usuario ('sólo se están tomando
    torneos 250+ en nivel?' -- no lo estaba, se agregó el filtro
    fail-closed). Nivel default = 250 (el mínimo que debe PASAR el
    filtro); los tests que necesitan verificar la exclusión pasan un
    nivel por debajo (Challenger/ITF) o `None` explícitamente."""
    return Tournament.create(
        tournament_name=f"Test Open {next(_seq)}", year=2024, level=level,
        city="Testville", fetched_at=datetime.now(timezone.utc),
    )


def _match(a, b, *, match_date, winner, surface="Hard", tour="ATP",
           a_rank=1, b_rank=50, a_elo=2000.0, b_elo=1500.0, match_id=None,
           tournament_level="ATP 250"):
    row = MatchHistory.create(
        match_id=match_id or f"m-{next(_seq)}", match_date=match_date, tour=tour,
        surface=surface, player_a=a, player_b=b,
        player_a_elo_rank=a_rank, player_b_elo_rank=b_rank,
        player_a_elo=a_elo, player_b_elo=b_elo,
        tournament=_tournament(tournament_level) if tournament_level else None,
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


def test_tournament_level_filter_excludes_below_250_and_missing_tournament():
    """2026-08-17, pedido explícito del usuario: 'sólo se están tomando
    torneos 250+ en nivel?' -- no lo estaba. Ahora un caso en
    Challenger/ITF, o sin torneo vinculado, debe excluirse fail-closed
    (nunca se asume main-tour por default)."""
    fav, dog = _player(), _player()
    _seed_qualifying_history(fav, dog)
    case_date = "2024-01-10"

    row_challenger = _match(fav, dog, match_date=case_date, winner="a",
                             match_id="case-challenger", tournament_level="Challenger")
    _odds(row_challenger, fav, dog, 1.3, 3.2, case_date)

    fav2, dog2 = _player(), _player()
    _seed_qualifying_history(fav2, dog2)
    row_no_tournament = _match(fav2, dog2, match_date=case_date, winner="a",
                                match_id="case-no-tournament", tournament_level=None)
    _odds(row_no_tournament, fav2, dog2, 1.3, 3.2, case_date)

    cases, skipped = collect_cases()
    assert not any(c["match_id"] in ("case-challenger", "case-no-tournament") for c in cases)
    assert skipped.get("below_250_level", 0) >= 2


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


# --- filtro de resiliencia del favorito (comeback + tiebreaks) ---

def _match_with_score(a, b, *, match_date, score, winner, surface="Hard",
                       a_rank=1, b_rank=50, a_elo=2000.0, b_elo=1500.0, match_id=None,
                       tournament_level="ATP 250"):
    return MatchHistory.create(
        match_id=match_id or f"m-{next(_seq)}", match_date=match_date, tour="ATP",
        surface=surface, player_a=a, player_b=b, score=score,
        player_a_elo_rank=a_rank, player_b_elo_rank=b_rank,
        player_a_elo=a_elo, player_b_elo=b_elo,
        tournament=_tournament(tournament_level) if tournament_level else None,
        winner=(a if winner == "a" else b), fetched_at=datetime.now(timezone.utc),
    )


def test_comeback_filter_rejects_favorite_who_never_comes_back():
    """El favorito de este caso perdió el set1 en 5 partidos previos de
    temporada, frente a un rival de ranking similar al underdog del
    caso (50, banda [25,75]), y NUNCA ganó el set 3/decisivo (0% de WR
    al 3er set tras perder el 1ro) -- con el filtro activo, debe
    rechazarse."""
    fav, dog = _player(), _player()
    _seed_qualifying_history(fav, dog)

    # 5 partidos previos (3 sets, rival de ranking similar al underdog
    # del caso -- rank 50, banda [25,75]) donde el favorito pierde el
    # set1 y también el set 3/decisivo (0% de WR al 3er set tras L1).
    victim = _player()
    for i in range(5):
        d = f"2024-01-{11+i:02d}"
        row = _match_with_score(
            fav, victim, match_date=d, score="6-7, 6-3, 4-6", winner="b",
            a_rank=10, b_rank=50, match_id=f"loss-after-l1-{i}",
        )
        _odds(row, fav, victim, 1.3, 3.2, d)

    case_date = "2024-01-20"
    row = _match(fav, dog, match_date=case_date, winner="a", match_id="case-comeback-veto")
    _odds(row, fav, dog, 1.3, 3.2, case_date)

    cases_no_filter, _ = collect_cases(min_favorite_comeback_rate=None)
    cases_with_filter, skipped = collect_cases(min_favorite_comeback_rate=0.3)

    assert any(c["match_id"] == "case-comeback-veto" for c in cases_no_filter)
    assert not any(c["match_id"] == "case-comeback-veto" for c in cases_with_filter)
    assert skipped.get("favorite_weak_comeback_after_l1", 0) >= 1


def test_comeback_filter_allows_favorite_with_strong_comeback_record():
    """El favorito ganó el set 3/decisivo 5/5 veces tras perder el set1,
    de la temporada y contra rivales de ranking similar al underdog del
    caso -- con el filtro activo (umbral 30%), el caso debe seguir
    pasando."""
    fav, dog = _player(), _player()
    _seed_qualifying_history(fav, dog)

    victim = _player()
    for i in range(5):
        d = f"2024-01-{11+i:02d}"
        row = _match_with_score(
            fav, victim, match_date=d, score="6-7, 6-3, 6-2", winner="a",
            a_rank=10, b_rank=50, match_id=f"comeback-win-{i}",
        )
        _odds(row, fav, victim, 1.3, 3.2, d)

    case_date = "2024-01-20"
    row = _match(fav, dog, match_date=case_date, winner="a", match_id="case-comeback-ok")
    _odds(row, fav, dog, 1.3, 3.2, case_date)

    cases, skipped = collect_cases(min_favorite_comeback_rate=0.3)
    matching = [c for c in cases if c["match_id"] == "case-comeback-ok"]
    assert len(matching) == 1
    assert matching[0]["favorite_comeback_after_l1_rate"] == 1.0
    assert matching[0]["favorite_comeback_after_l1_n"] == 5


def test_tiebreak_filter_rejects_favorite_who_loses_tiebreaks():
    """El favorito perdió 5 tiebreaks seguidos (0% tiebreak WR) --
    con el filtro activo debe rechazarse."""
    fav, dog = _player(), _player()
    _seed_qualifying_history(fav, dog)

    victim = _player()
    for i in range(5):
        d = f"2024-01-{11+i:02d}"
        # Favorito (player_a) pierde el set en tiebreak (6-7) y el partido.
        row = _match_with_score(
            fav, victim, match_date=d, score="6-7, 4-6", winner="b",
            a_rank=10, b_rank=50, match_id=f"tb-loss-{i}",
        )
        _odds(row, fav, victim, 1.3, 3.2, d)

    case_date = "2024-01-20"
    row = _match(fav, dog, match_date=case_date, winner="a", match_id="case-tb-veto")
    _odds(row, fav, dog, 1.3, 3.2, case_date)

    cases_no_filter, _ = collect_cases(min_favorite_tiebreak_win_rate=None)
    cases_with_filter, skipped = collect_cases(min_favorite_tiebreak_win_rate=0.3)

    assert any(c["match_id"] == "case-tb-veto" for c in cases_no_filter)
    assert not any(c["match_id"] == "case-tb-veto" for c in cases_with_filter)
    assert skipped.get("favorite_weak_tiebreak", 0) >= 1


def test_resilience_filters_fail_closed_without_enough_sample():
    """Con solo 1 partido previo (por debajo de MIN_RESILIENCE_SAMPLE),
    el filtro NO debe aplicarse -- muestra insuficiente, fail-closed."""
    fav, dog = _player(), _player()
    _seed_qualifying_history(fav, dog)

    victim = _player()
    row = _match_with_score(
        fav, victim, match_date="2024-01-11", score="6-7, 3-6", winner="b",
        a_rank=10, b_rank=15, match_id="only-one-loss",
    )
    _odds(row, fav, victim, 1.3, 3.2, "2024-01-11")

    case_date = "2024-01-20"
    row = _match(fav, dog, match_date=case_date, winner="a", match_id="case-insufficient-sample")
    _odds(row, fav, dog, 1.3, 3.2, case_date)

    cases, skipped = collect_cases(min_favorite_comeback_rate=0.9)
    assert any(c["match_id"] == "case-insufficient-sample" for c in cases)
    assert skipped.get("favorite_weak_comeback_after_l1", 0) == 0


def test_comeback_filter_ignores_rivals_of_dissimilar_rank():
    """Pedido explícito del usuario: la tasa de comeback debe calcularse
    'frente a jugadores de ranking similar' al underdog de ESTE caso
    (rank 50). 5 partidos de comeback perfecto (100%) contra un rival de
    ranking MUY distinto (rank 400, fuera de la banda [25,75]) no deben
    contar -- la muestra queda vacía y el filtro no debe activarse
    (fail-closed por sub-muestra, no un 100% espurio)."""
    fav, dog = _player(), _player()
    _seed_qualifying_history(fav, dog)

    distant_victim = _player()
    for i in range(5):
        d = f"2024-01-{11+i:02d}"
        row = _match_with_score(
            fav, distant_victim, match_date=d, score="6-7, 6-3, 6-2", winner="a",
            a_rank=10, b_rank=400, match_id=f"comeback-distant-rank-{i}",
        )
        _odds(row, fav, distant_victim, 1.3, 3.2, d)

    case_date = "2024-01-20"
    row = _match(fav, dog, match_date=case_date, winner="a", match_id="case-distant-rank")
    _odds(row, fav, dog, 1.3, 3.2, case_date)

    # umbral exigente (90%): si el rival de rank distinto contara, esta
    # muestra 100% pasaría igual -- lo que queremos verificar es que la
    # muestra quede en 0 (banda de ranking la excluye), no si aprueba.
    cases, _ = collect_cases(min_favorite_comeback_rate=0.9)
    matching = [c for c in cases if c["match_id"] == "case-distant-rank"]
    assert len(matching) == 1
    assert matching[0]["favorite_comeback_after_l1_n"] == 0
    assert matching[0]["favorite_comeback_after_l1_rate"] is None


def test_comeback_filter_ignores_matches_outside_season_window():
    """Pedido explícito del usuario: solo cuenta la tasa 'de la
    temporada' -- un comeback real (rival de ranking similar) de hace
    más de `SEASON_WINDOW_DAYS` (365) no debe entrar en la muestra."""
    fav, dog = _player(), _player()
    _seed_qualifying_history(fav, dog)

    victim = _player()
    for i in range(5):
        old_date = f"2022-01-{5+i:02d}"  # >365 días antes del caso (2024-01-20)
        row = _match_with_score(
            fav, victim, match_date=old_date, score="6-7, 6-3, 6-2", winner="a",
            a_rank=10, b_rank=50, match_id=f"comeback-too-old-{i}",
        )
        _odds(row, fav, victim, 1.3, 3.2, old_date)

    case_date = "2024-01-20"
    row = _match(fav, dog, match_date=case_date, winner="a", match_id="case-old-comeback")
    _odds(row, fav, dog, 1.3, 3.2, case_date)

    cases, _ = collect_cases(min_favorite_comeback_rate=0.9)
    matching = [c for c in cases if c["match_id"] == "case-old-comeback"]
    assert len(matching) == 1
    assert matching[0]["favorite_comeback_after_l1_n"] == 0


def test_implied_prob_filter_rejects_weak_favorites():
    """2026-08-17, umbral fijado por el usuario tras la sesion de
    testing: solo tomar casos con probabilidad implicita del favorito
    >=X. Cuota 1.3 -> implied_prob=0.769, debe rechazarse con umbral
    0.85; cuota 1.1 -> implied_prob=0.909, debe pasar."""
    fav, dog = _player(), _player()
    _seed_qualifying_history(fav, dog)

    case_date = "2024-01-20"
    weak_row = _match(fav, dog, match_date=case_date, winner="a", match_id="case-weak-fav")
    _odds(weak_row, fav, dog, 1.3, 3.2, case_date)

    fav2, dog2 = _player(), _player()
    _seed_qualifying_history(fav2, dog2)
    strong_row = _match(fav2, dog2, match_date=case_date, winner="a", match_id="case-strong-fav")
    _odds(strong_row, fav2, dog2, 1.1, 6.0, case_date)

    cases, skipped = collect_cases(min_favorite_implied_prob=0.85)
    match_ids = {c["match_id"] for c in cases}
    assert "case-weak-fav" not in match_ids
    assert "case-strong-fav" in match_ids
    assert skipped.get("favorite_implied_prob_too_low", 0) >= 1


def test_rest_since_last_tournament_filter_rejects_no_rest_first_match():
    """2026-08-17, pedido explicito del usuario: descartar casos donde
    el favorito practicamente no descanso desde el ultimo TORNEO
    (distinto al actual) -- no confundir con descanso entre rondas del
    MISMO torneo, que es normal y no debe activar el filtro."""
    fav, dog = _player(), _player()
    _seed_qualifying_history(fav, dog)

    # El favorito juega OTRO torneo el dia anterior al caso (torneo
    # distinto -- se crea con un Tournament nuevo via _match), luego
    # entra al torneo del caso sin descanso real.
    other_victim = _player()
    prior_row = _match(fav, other_victim, match_date="2024-01-19", winner="a",
                        match_id="other-tournament-match", tournament_level="ATP 250")
    _odds(prior_row, fav, other_victim, 1.3, 3.2, "2024-01-19")

    case_date = "2024-01-20"
    case_row = _match(fav, dog, match_date=case_date, winner="a", match_id="case-no-rest")
    _odds(case_row, fav, dog, 1.3, 3.2, case_date)

    cases, skipped = collect_cases(min_days_rest_since_last_tournament=2)
    assert not any(c["match_id"] == "case-no-rest" for c in cases)
    assert skipped.get("favorite_no_rest_since_last_tournament", 0) >= 1


def test_rest_since_last_tournament_filter_ignores_same_tournament_rounds():
    """El descanso corto entre RONDAS del MISMO torneo no debe activar
    el filtro -- solo importa la transicion entre torneos distintos."""
    fav, dog = _player(), _player()
    _seed_qualifying_history(fav, dog)
    victim = _player()

    # Mismo torneo (misma referencia de Tournament) el dia anterior --
    # se logra pasando tournament_level=None y seteando el mismo
    # tournament manualmente despues de crear las filas.
    from database.historical.tennis import Tournament
    shared_tournament = Tournament.create(
        tournament_name="Shared Open", year=2024, level="ATP 250",
        city="Testville", fetched_at=datetime.now(timezone.utc),
    )
    prior_row = MatchHistory.create(
        match_id="same-tournament-r1", match_date="2024-01-19", tour="ATP",
        surface="Hard", player_a=fav, player_b=victim,
        player_a_elo_rank=1, player_b_elo_rank=50,
        player_a_elo=2000.0, player_b_elo=1500.0, tournament=shared_tournament,
        winner=fav, fetched_at=datetime.now(timezone.utc),
    )
    _odds(prior_row, fav, victim, 1.3, 3.2, "2024-01-19")

    case_row = MatchHistory.create(
        match_id="case-same-tournament-r2", match_date="2024-01-20", tour="ATP",
        surface="Hard", player_a=fav, player_b=dog,
        player_a_elo_rank=1, player_b_elo_rank=50,
        player_a_elo=2000.0, player_b_elo=1500.0, tournament=shared_tournament,
        winner=fav, fetched_at=datetime.now(timezone.utc),
    )
    _odds(case_row, fav, dog, 1.3, 3.2, "2024-01-20")

    cases, skipped = collect_cases(min_days_rest_since_last_tournament=2)
    assert any(c["match_id"] == "case-same-tournament-r2" for c in cases)
    assert skipped.get("favorite_no_rest_since_last_tournament", 0) == 0


def test_favourite_now_defined_by_elo_not_by_market_odds():
    """2026-08-17, pedido explicito del usuario: el favorito se define
    por Elo real (player_a_elo > player_b_elo), no por cuota de mercado.
    Un caso donde el Elo dice A pero la cuota diria B (mercado
    equivocado/desactualizado) debe tomar a A como favorito."""
    fav, dog = _player(), _player()
    _seed_qualifying_history(fav, dog)
    case_date = "2024-01-20"
    # fav tiene MEJOR Elo (2000 default) pero le ponemos una cuota PEOR
    # que la del underdog -- el mercado "cree" que dog es favorito.
    row = _match(fav, dog, match_date=case_date, winner="a", match_id="case-elo-fav",
                 a_elo=2000.0, b_elo=1500.0)
    _odds(row, fav, dog, 3.2, 1.3, case_date)  # cuota invertida a proposito

    cases, skipped = collect_cases(max_underdog_elo_slope=None)
    matching = [c for c in cases if c["match_id"] == "case-elo-fav"]
    assert len(matching) == 1
    assert matching[0]["favorite"] == fav.canonical_name


def test_favourite_by_elo_fails_closed_without_elo_data():
    """Sin Elo real de ambos jugadores, no se puede definir favorito por
    este criterio -- se excluye, nunca se asume."""
    fav, dog = _player(), _player()
    _seed_qualifying_history(fav, dog)
    case_date = "2024-01-20"
    row = _match(fav, dog, match_date=case_date, winner="a", match_id="case-no-elo",
                 a_elo=None, b_elo=None)
    _odds(row, fav, dog, 1.3, 3.2, case_date)

    cases, skipped = collect_cases(max_underdog_elo_slope=None)
    assert not any(c["match_id"] == "case-no-elo" for c in cases)
    assert skipped.get("no_elo_for_favourite_definition", 0) >= 1


def test_underdog_elo_momentum_veto_rejects_rising_underdog():
    """El underdog viene subiendo su Elo de forma sostenida (pendiente
    positiva fuerte) en sus ultimos partidos -- debe vetarse el pick,
    aunque su Elo estatico siga siendo menor al del favorito."""
    fav, dog = _player(), _player()
    _seed_qualifying_history(fav, dog)

    # el underdog sube su Elo de forma sostenida en 10 partidos previos
    # (point-in-time, ANTES del caso) -- 10 valores exactos porque
    # elo_history tiene maxlen=10: garantiza que la ventana quede
    # compuesta SOLO por esta racha ascendente (sin arrastrar el Elo
    # plano de `_seed_qualifying_history`), pendiente limpia y positiva.
    # 2 de las 10 son derrotas (indices 0,1) a proposito -- si el
    # underdog ganara las 10, su WR reciente (100%) empataria con la del
    # favorito (tambien 100% en el seed) y el caso se rechazaria antes
    # por el filtro heredado de "forma reciente" (empate no cuenta como
    # "mejor"), nunca llegaria a evaluar el momentum de Elo.
    victim = _player()
    rising_elo = [1300.0 + i * 25 for i in range(10)]
    for i, elo in enumerate(rising_elo):
        d = f"2024-01-{10+i:02d}"
        row = _match(dog, victim, match_date=d, winner=("b" if i < 2 else "a"), surface="Hard",
                     a_rank=10, b_rank=15, a_elo=elo, b_elo=1000.0,
                     match_id=f"dog-rising-{i}")
        _odds(row, dog, victim, 1.3, 3.2, d)

    case_date = "2024-01-20"
    row = _match(fav, dog, match_date=case_date, winner="a", match_id="case-momentum-veto",
                 a_elo=2000.0, b_elo=1500.0)
    _odds(row, fav, dog, 1.3, 3.2, case_date)

    cases, skipped = collect_cases(max_underdog_elo_slope=15.0)
    assert not any(c["match_id"] == "case-momentum-veto" for c in cases)
    assert skipped.get("underdog_elo_momentum", 0) >= 1


def test_underdog_elo_momentum_veto_disabled_reproduces_baseline():
    """max_underdog_elo_slope=None desactiva el filtro -- mismo caso de
    arriba debe pasar si se desactiva."""
    fav, dog = _player(), _player()
    _seed_qualifying_history(fav, dog)
    victim = _player()
    rising_elo = [1300.0 + i * 25 for i in range(10)]
    for i, elo in enumerate(rising_elo):
        d = f"2024-01-{10+i:02d}"
        row = _match(dog, victim, match_date=d, winner=("b" if i < 2 else "a"), surface="Hard",
                     a_rank=10, b_rank=15, a_elo=elo, b_elo=1000.0,
                     match_id=f"dog-rising2-{i}")
        _odds(row, dog, victim, 1.3, 3.2, d)

    case_date = "2024-01-20"
    row = _match(fav, dog, match_date=case_date, winner="a", match_id="case-momentum-disabled",
                 a_elo=2000.0, b_elo=1500.0)
    _odds(row, fav, dog, 1.3, 3.2, case_date)

    cases, skipped = collect_cases(max_underdog_elo_slope=None)
    assert any(c["match_id"] == "case-momentum-disabled" for c in cases)


def test_resilience_never_uses_future_matches():
    """El comeback-rate del favorito no debe verse afectado por
    partidos posteriores a la fecha del caso."""
    fav, dog = _player(), _player()
    _seed_qualifying_history(fav, dog)

    case_date = "2024-01-20"
    row = _match(fav, dog, match_date=case_date, winner="a", match_id="case-before-future")
    _odds(row, fav, dog, 1.3, 3.2, case_date)

    victim = _player()
    for i in range(5):
        d = f"2024-02-{1+i:02d}"  # DESPUÉS del caso
        future_row = _match_with_score(
            fav, victim, match_date=d, score="6-7, 3-6", winner="b",
            a_rank=10, b_rank=15, match_id=f"future-loss-{i}",
        )
        _odds(future_row, fav, victim, 1.3, 3.2, d)

    cases, _ = collect_cases(min_favorite_comeback_rate=0.9)
    matching = [c for c in cases if c["match_id"] == "case-before-future"]
    assert len(matching) == 1
    assert matching[0]["favorite_comeback_after_l1_n"] == 0
