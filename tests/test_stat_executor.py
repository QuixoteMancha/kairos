"""
test_stat_executor.py — ejecutor de STAT (value) bets + gate de portafolio.

Valida que las value bets seleccionadas se colocan como stat bets monitorables
(sport + leg_tipo estampados), con dedup, chequeo de balance, y que el gate del
portafolio descarta las estrategias probadas perdedoras.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _db():
    from src.database.database import init_db
    init_db()
    yield


# ── leg_tipo inference ───────────────────────────────────────────────────────

def test_infer_leg_tipo_winner_vs_total():
    from src.core.orchestration.settlement import _infer_stat_leg_tipo
    assert _infer_stat_leg_tipo({"descripcion": "Stat p1 football"}, "p1") == "ganador"
    assert _infer_stat_leg_tipo({"descripcion": "Stat Over 2.5 football"}, "over") == "total"
    assert _infer_stat_leg_tipo({"descripcion": "Stat total football model"}, "p1") == "total"


# ── placement ────────────────────────────────────────────────────────────────

def _make_wallet(balance=50.0):
    w = MagicMock(balance=balance, bets=[], save=MagicMock())
    return w


def test_stat_bet_placed_with_sport_and_leg_tipo():
    from src.core.orchestration import settlement as _s
    wallets = {"pinnacle": _make_wallet(50.0)}
    vbs = [{
        "tipo": "stat", "sport": "football", "fixture": "Barcelona vs Getafe",
        "roi": 4.5, "model_edge": 5.0,
        "legs": [{"side": "p1", "book": "pinnacle", "odd": 1.85, "stake_pct": 0.62}],
        "descripcion": "Stat p1 football (model p=0.62)", "hedgeable": True,
    }]
    with patch.object(_s, "get_wallet", side_effect=lambda p: wallets[p]), \
         patch.object(_s, "_active_fixture_surname_sets",
                      return_value={_s._fixture_surname_set("Barcelona vs Getafe")}):
        res = _s._execute_stat_value_bets(vbs)
    assert any(r.get("accepted") for r in res)
    bet = wallets["pinnacle"].bets[0]
    assert bet.bet_type == "stat"
    assert bet.sport == "football"
    assert bet.legs[0].tipo == "ganador"   # monitorable leg_tipo
    assert wallets["pinnacle"].balance < 50.0   # debitado


def test_stat_bet_dedup_skips_second_call():
    from src.core.orchestration import settlement as _s
    wallets = {"unibet": _make_wallet(50.0)}
    vbs = [{
        "tipo": "stat", "sport": "basketball", "fixture": "Lakers ZZ vs Celtics QQ",
        "roi": 3.0, "legs": [{"side": "p1", "book": "unibet", "odd": 1.9, "stake_pct": 0.55}],
        "descripcion": "Stat p1 basketball",
    }]
    with patch.object(_s, "get_wallet", side_effect=lambda p: wallets[p]), \
         patch.object(_s, "_active_fixture_surname_sets",
                      return_value={_s._fixture_surname_set("Lakers ZZ vs Celtics QQ")}):
        r1 = _s._execute_stat_value_bets(vbs)
        assert any(x.get("accepted") for x in r1)
        # 2do intento: el dedup en memoria (pending_keys) no persiste entre llamadas,
        # pero el bet ya está en wallets[unibet].bets → simulamos relectura DB con
        # un patch del WalletBet select. Más simple: el dedup intra-llamada evita
        # duplicar en la MISMA pasada:
        r_same = _s._execute_stat_value_bets(vbs + vbs)
    # en una sola pasada con el mismo fixture 2x, el 2do se deduplica
    accepted = [x for x in r_same if x.get("accepted")]
    assert len(accepted) == 1


def test_stat_bet_skips_unsupported_sport_leg():
    from src.core.orchestration import settlement as _s
    wallets = {"pinnacle": _make_wallet(50.0)}
    # deporte sin soporte de trigger live (golf no existe) → rechazado
    vbs = [{
        "tipo": "stat", "sport": "golf", "fixture": "A vs B",
        "legs": [{"side": "p1", "book": "pinnacle", "odd": 1.9, "stake_pct": 0.5}],
        "descripcion": "Stat golf",
    }]
    with patch.object(_s, "get_wallet", side_effect=lambda p: wallets[p]), \
         patch.object(_s, "_active_fixture_surname_sets", return_value=set()):
        res = _s._execute_stat_value_bets(vbs)
    assert all(not r.get("accepted") for r in res)
    assert wallets["pinnacle"].bets == []


def test_stat_bet_skips_when_no_balance():
    from src.core.orchestration import settlement as _s
    wallets = {"pinnacle": _make_wallet(0.5)}   # sin capital
    vbs = [{
        "tipo": "stat", "sport": "football", "fixture": "C vs D",
        "legs": [{"side": "p1", "book": "pinnacle", "odd": 1.9, "stake_pct": 0.5}],
        "descripcion": "Stat p1 football",
    }]
    with patch.object(_s, "get_wallet", side_effect=lambda p: wallets[p]), \
         patch.object(_s, "_active_fixture_surname_sets",
                      return_value={_s._fixture_surname_set("C vs D")}):
        res = _s._execute_stat_value_bets(vbs)
    assert all(not r.get("accepted") for r in res)


# ── portfolio gate ───────────────────────────────────────────────────────────

def test_allocator_drops_deactivated_strategy():
    """Una estrategia probada perdedora (active=False) se descarta del bucket."""
    import json
    from src.database.database import AppConfig
    from src.core.orchestration.portfolio import PORTFOLIO_STATE_KEY
    from src.core.orchestration.strategy_allocator import StrategyAllocator
    # portafolio donde football_elo_value está PROBADA perdedora (active=False)
    AppConfig.set_value(PORTFOLIO_STATE_KEY, json.dumps({
        "weights": {},
        "profiles": [{"name": "football_elo_value", "active": False}],
    }))
    alloc = StrategyAllocator()
    buckets = {"low_risk_arb": [], "medium_value_stat": [
        {"sport": "football", "tipo": "stat", "roi": 5.0, "legs": [{"odd": 2.0}]},
        {"sport": "basketball", "tipo": "stat", "roi": 5.0, "legs": [{"odd": 2.0}]},
    ], "risk_managed_hedge": [], "other": []}
    alloc._apply_portfolio(buckets)
    sports = {o["sport"] for o in buckets["medium_value_stat"]}
    assert "football" not in sports        # desactivada → descartada
    assert "basketball" in sports          # sin verdicto → neutro, se mantiene


def test_allocator_no_portfolio_is_neutral():
    """Sin portafolio (ni pesos ni desactivadas) no filtra nada."""
    import json
    from src.database.database import AppConfig
    from src.core.orchestration.portfolio import PORTFOLIO_STATE_KEY
    from src.core.orchestration.strategy_allocator import StrategyAllocator
    AppConfig.set_value(PORTFOLIO_STATE_KEY, json.dumps({"weights": {}, "profiles": []}))
    alloc = StrategyAllocator()
    buckets = {"low_risk_arb": [], "medium_value_stat": [
        {"sport": "football", "tipo": "stat", "roi": 5.0, "legs": [{"odd": 2.0}]},
    ], "risk_managed_hedge": [], "other": []}
    alloc._apply_portfolio(buckets)
    assert len(buckets["medium_value_stat"]) == 1


# ── surname matching (order-independent) ──────────────────────────────────────

def test_fixture_surname_set_order_independent():
    """El matching por apellidos NO depende del orden de p1/p2 entre books.

    Pinnacle suele invertir local/visitante respecto a Stake; el frozenset de
    apellidos debe ser idéntico para que no haya void de falsos positivos.
    """
    from src.core.orchestration.settlement import _fixture_surname_set
    a = _fixture_surname_set("Chicago Wolves vs Toronto Marlies")
    b = _fixture_surname_set("Toronto Marlies vs Chicago Wolves")
    assert a == b == frozenset({"wolves", "marlies"})
    # nombres no relacionados no colisionan
    assert _fixture_surname_set("Real Madrid vs Barcelona") != a


# ── bookie match URL (link en historial) ─────────────────────────────────────

def test_bookie_match_url_per_book():
    from src.core.orchestration.cross_book import bookie_match_url
    # Kambi: {client}-{eventId} → URL de evento Unibet
    assert bookie_match_url("ubse", "ubse-1026591330") == \
        "https://www.unibet.com/betting/sports/event/1026591330"
    # Pinnacle: pinnacle-{matchupId}
    assert "1631824303" in bookie_match_url("pinnacle", "pinnacle-1631824303", "soccer")
    # raw_url directo (Stake) tiene prioridad
    assert bookie_match_url("stake", "x-1", raw_url="https://stake.com/x") == "https://stake.com/x"
    # sin id fiable → vacío
    assert bookie_match_url("desconocido", "") == ""


# ── trackeability gate ────────────────────────────────────────────────────────

def test_stat_bet_skips_untrackable_fixture():
    """Si el fixture NO está en ningún feed observable → no se coloca (evita churn/void)."""
    from src.core.orchestration import settlement as _s
    wallets = {"pinnacle": _make_wallet(50.0)}
    vbs = [{
        "tipo": "stat", "sport": "ice_hockey", "fixture": "Obscure Team A vs Obscure Team B",
        "roi": 12.0, "legs": [{"side": "p1", "book": "pinnacle", "odd": 1.9}],
        "descripcion": "Stat p1 ice_hockey",
    }]
    # feed activo contiene OTROS fixtures, no el nuestro
    other = {_s._fixture_surname_set("Bruins vs Rangers")}
    with patch.object(_s, "get_wallet", side_effect=lambda p: wallets[p]), \
         patch.object(_s, "_active_fixture_surname_sets", return_value=other):
        res = _s._execute_stat_value_bets(vbs)
    assert all(not r.get("accepted") for r in res)
    assert any("no trackable" in (r.get("reason") or "") for r in res)
    assert wallets["pinnacle"].bets == []


def test_stat_bet_trackable_fixture_passes_gate():
    """Si el fixture SÍ está en el feed observable → se coloca normalmente."""
    from src.core.orchestration import settlement as _s
    wallets = {"pinnacle": _make_wallet(50.0)}
    vbs = [{
        "tipo": "stat", "sport": "ice_hockey", "fixture": "Carolina Hurricanes vs Vegas Golden Knights",
        "roi": 20.0, "legs": [{"side": "p1", "book": "pinnacle", "odd": 1.9}],
        "descripcion": "Stat p1 ice_hockey",
    }]
    # feed activo lo trae con el orden INVERTIDO (como hace Pinnacle) → igual debe matchear
    active = {_s._fixture_surname_set("Vegas Golden Knights vs Carolina Hurricanes")}
    with patch.object(_s, "get_wallet", side_effect=lambda p: wallets[p]), \
         patch.object(_s, "_active_fixture_surname_sets", return_value=active):
        res = _s._execute_stat_value_bets(vbs)
    assert any(r.get("accepted") for r in res)
    assert wallets["pinnacle"].bets and wallets["pinnacle"].bets[0].sport == "ice_hockey"


def test_stat_gate_disabled_when_feed_empty():
    """Si el feed viene vacío (outage/fetch falló) el gate NO bloquea (set vacío = off)."""
    from src.core.orchestration import settlement as _s
    wallets = {"pinnacle": _make_wallet(50.0)}
    vbs = [{
        "tipo": "stat", "sport": "football", "fixture": "Sevilla vs Valencia",
        "roi": 4.0, "legs": [{"side": "p1", "book": "pinnacle", "odd": 1.85}],
        "descripcion": "Stat p1 football",
    }]
    with patch.object(_s, "get_wallet", side_effect=lambda p: wallets[p]), \
         patch.object(_s, "_active_fixture_surname_sets", return_value=set()):
        res = _s._execute_stat_value_bets(vbs)
    assert any(r.get("accepted") for r in res)   # gate off → se coloca


# ── voider: no anular fixtures aún observables (anti falso positivo) ──────────

def _make_pending_bet(fixture, hours_old=5.0):
    from datetime import datetime, timedelta
    ts = (datetime.now() - timedelta(hours=hours_old)).isoformat()
    return MagicMock(
        bet_id="vb_" + fixture[:4], arb_tipo="stat", bet_type="stat",
        fixture_name=fixture, timestamp=ts, total_staked=2.0, status="pending", pnl=0.0,
    )


def test_void_skips_fixture_still_in_feed():
    """Bet vieja cuyo fixture SIGUE en el feed (orden invertido) NO debe anularse."""
    from src.core.orchestration import settlement as _s
    from src.core.orchestration import wallet as _wmod
    bet = _make_pending_bet("Chicago Wolves vs Toronto Marlies", hours_old=5.0)
    fake = MagicMock(balance=40.0, pending_bets=[bet])

    def _gw(slug):
        return fake if slug == "stake" else MagicMock(balance=0.0, pending_bets=[])

    active = {_s._fixture_surname_set("Toronto Marlies vs Chicago Wolves")}
    with patch.object(_s, "_active_fixture_surname_sets", return_value=active), \
         patch.object(_wmod, "get_wallet", side_effect=_gw), \
         patch.object(_wmod, "save_wallet"), patch.object(_wmod, "write_ledger"):
        voided = _s._void_cancelled_fixtures(min_hours_past=3.0)
    assert bet.bet_id not in voided
    assert bet.status == "pending"   # intacta


def test_void_proceeds_when_fixture_absent():
    """Bet vieja cuyo fixture YA NO está en ningún feed → se anula (devuelve stake)."""
    from src.core.orchestration import settlement as _s
    from src.core.orchestration import wallet as _wmod
    bet = _make_pending_bet("Ghost Town A vs Ghost Town B", hours_old=5.0)
    fake = MagicMock(balance=40.0, pending_bets=[bet])

    def _gw(slug):
        return fake if slug == "stake" else MagicMock(balance=0.0, pending_bets=[])

    # feed activo NO contiene el fixture
    active = {_s._fixture_surname_set("Real vs Barca")}
    with patch.object(_s, "_active_fixture_surname_sets", return_value=active), \
         patch.object(_s, "get_resolution_for_bet",
                      return_value=(_s.RULE_VOID, None, "walkover/abandoned")), \
         patch.object(_wmod, "get_wallet", side_effect=_gw), \
         patch.object(_wmod, "save_wallet"), patch.object(_wmod, "write_ledger"):
        voided = _s._void_cancelled_fixtures(min_hours_past=3.0)
    assert bet.bet_id in voided
    assert bet.status == "void"


def test_void_default_is_16h():
    """El default del voider es 16h (≥2 ciclos de settlement de 8h)."""
    import inspect
    from src.core.orchestration.settlement import _void_cancelled_fixtures
    sig = inspect.signature(_void_cancelled_fixtures)
    assert sig.parameters["min_hours_past"].default == 16.0


def test_void_waits_when_bet_young_under_16h():
    """Con el default (16h), una bet de 5h NO se anula aunque el fixture no esté en feed."""
    from src.core.orchestration import settlement as _s
    from src.core.orchestration import wallet as _wmod
    bet = _make_pending_bet("Ghost A vs Ghost B", hours_old=5.0)
    fake = MagicMock(balance=40.0, pending_bets=[bet])

    def _gw(slug):
        return fake if slug == "stake" else MagicMock(balance=0.0, pending_bets=[])

    with patch.object(_s, "_active_fixture_surname_sets", return_value=set()), \
         patch.object(_wmod, "get_wallet", side_effect=_gw), \
         patch.object(_wmod, "save_wallet"), patch.object(_wmod, "write_ledger"):
        voided = _s._void_cancelled_fixtures()   # default 16h
    assert bet.bet_id not in voided
    assert bet.status == "pending"


# ── Flashscore multideporte por equipo ────────────────────────────────────────

def test_flashscore_team_score_pair():
    """_team_score_pair usa AG/AH (marcador final) y cae a parciales si faltan."""
    from src.scrapers.flashscore_scraper import _team_score_pair
    # AG/AH presentes
    assert _team_score_pair({"p1_sets": 3, "p2_sets": 2}) == (3, 2)
    # fallback a parciales
    assert _team_score_pair({"p1_sets": None, "p2_sets": None,
                             "p1_s1": 1, "p2_s1": 0, "p1_s2": 2, "p2_s2": 1}) == (3, 1)
    # sin nada
    assert _team_score_pair({}) is None


def test_flashscore_sport_id_map():
    from src.scrapers.flashscore_scraper import _FS_SPORT_ID
    assert _FS_SPORT_ID["ice_hockey"] == 4
    assert _FS_SPORT_ID["football"] == 1
    assert _FS_SPORT_ID["tennis"] == 2


# ── settler pesado de stat bets (Flashscore) ─────────────────────────────────

def _make_stat_bet(fixture, side="p1", sport="ice_hockey", hours_old=5.0):
    from datetime import datetime, timedelta
    ts = (datetime.now() - timedelta(hours=hours_old)).isoformat()
    leg = MagicMock(side=side, tipo="ganador")
    return MagicMock(
        bet_id="sb_" + fixture[:4], arb_tipo="stat", bet_type="stat",
        fixture_name=fixture, sport=sport, timestamp=ts, placed_at=ts,
        legs=[leg], total_staked=2.0, guaranteed_return=4.0, status="pending",
    )


def test_settle_stat_by_score_winner_won():
    """Stat winner se liquida WON cuando Flashscore da el ganador = nuestro side."""
    from src.core.orchestration import settlement as _s
    from src.core.orchestration import wallet as _wmod
    bet = _make_stat_bet("Carolina Hurricanes vs Vegas Golden Knights", side="p1")
    resolved_flag = {"won": None}

    def _resolve(bet_id, won, actual_return=None, record_feedback=False):
        resolved_flag["won"] = won
        bet.status = "won" if won else "lost"
        return bet

    fake = MagicMock(balance=40.0, pending_bets=[bet], resolve_bet=_resolve)

    def _gw(slug):
        return fake if slug == "stake" else MagicMock(balance=0.0, pending_bets=[], resolve_bet=lambda *a, **k: None)

    fs_result = {"resultado": "4-2", "home_score": 4, "away_score": 2, "winner": "p1", "sport": "ice_hockey"}
    with patch("src.scrapers.flashscore_scraper.find_team_match_result", return_value=fs_result), \
         patch.object(_wmod, "get_wallet", side_effect=_gw), \
         patch.object(_wmod, "save_wallet"):
        settled = _s._settle_stat_bets_by_score(min_hours_past=1.0)
    assert bet.bet_id in settled
    assert resolved_flag["won"] is True


def test_settle_stat_by_score_winner_lost():
    """Stat winner se liquida LOST cuando el ganador ≠ nuestro side."""
    from src.core.orchestration import settlement as _s
    from src.core.orchestration import wallet as _wmod
    bet = _make_stat_bet("Carolina Hurricanes vs Vegas Golden Knights", side="p2")

    def _resolve(bet_id, won, actual_return=None, record_feedback=False):
        bet.status = "won" if won else "lost"
        return bet

    fake = MagicMock(balance=40.0, pending_bets=[bet], resolve_bet=_resolve)

    def _gw(slug):
        return fake if slug == "stake" else MagicMock(balance=0.0, pending_bets=[], resolve_bet=lambda *a, **k: None)

    fs_result = {"resultado": "4-2", "winner": "p1", "sport": "ice_hockey"}
    with patch("src.scrapers.flashscore_scraper.find_team_match_result", return_value=fs_result), \
         patch.object(_wmod, "get_wallet", side_effect=_gw), \
         patch.object(_wmod, "save_wallet"):
        settled = _s._settle_stat_bets_by_score(min_hours_past=1.0)
    assert bet.bet_id in settled
    assert bet.status == "lost"


def test_settle_stat_total_leg_retries_when_score_not_yet_available():
    """Bug corregido: las patas \'total\' (over/under) llegaban a quedarse
    pending para siempre (la funcion las saltaba por completo, sin tocar
    Flashscore). Ahora SI se intentan via find_team_match_result -- si la
    fuente todavia no tiene el resultado (None), se deja pending para
    reintentar en el siguiente ciclo (no se inventa un resultado)."""
    from src.core.orchestration import settlement as _s
    from src.core.orchestration import wallet as _wmod
    tennis_bet = _make_stat_bet("Alcaraz vs Sinner", sport="tennis")
    total_leg = MagicMock(side="over", tipo="total", linea=2.5)
    total_bet = MagicMock(bet_id="tb_1", arb_tipo="stat", bet_type="stat",
                          fixture_name="A vs B", sport="football",
                          timestamp=tennis_bet.timestamp, placed_at=tennis_bet.placed_at,
                          legs=[total_leg], total_staked=2.0, guaranteed_return=4.0, status="pending")
    fake = MagicMock(balance=40.0, pending_bets=[tennis_bet, total_bet],
                     resolve_bet=lambda *a, **k: None)

    def _gw(slug):
        return fake if slug == "stake" else MagicMock(balance=0.0, pending_bets=[], resolve_bet=lambda *a, **k: None)

    called = {"n": 0}

    def _fs(*a, **k):
        called["n"] += 1
        return None

    with patch("src.scrapers.flashscore_scraper.find_team_match_result", side_effect=_fs), \
         patch.object(_wmod, "get_wallet", side_effect=_gw), \
         patch.object(_wmod, "save_wallet"):
        settled = _s._settle_stat_bets_by_score(min_hours_past=1.0)
    assert settled == []
    assert called["n"] == 1   # la pata total SI llega a Flashscore (la de tenis usa find_match_result, no contado aqui)


def test_settle_stat_total_leg_resolves_won_via_team_score():
    """Pata \'over\' total_points=210.5 -- Flashscore da home=110/away=105
    (total=215>210.5) -> WON."""
    from src.core.orchestration import settlement as _s
    from src.core.orchestration import wallet as _wmod
    total_leg = MagicMock(side="over", tipo="total", linea=210.5)
    bet = MagicMock(bet_id="tb_won", arb_tipo="stat", bet_type="stat",
                     fixture_name="Lakers vs Celtics", sport="basketball",
                     timestamp=(__import__("datetime").datetime.now() - __import__("datetime").timedelta(hours=5)).isoformat(),
                     placed_at=None, legs=[total_leg], total_staked=2.0,
                     guaranteed_return=4.0, status="pending")
    resolved_flag = {"won": None}

    def _resolve(bet_id, won, actual_return=None, record_feedback=False):
        resolved_flag["won"] = won
        bet.status = "won" if won else "lost"
        return bet

    fake = MagicMock(balance=40.0, pending_bets=[bet], resolve_bet=_resolve)

    def _gw(slug):
        return fake if slug == "stake" else MagicMock(balance=0.0, pending_bets=[], resolve_bet=lambda *a, **k: None)

    fs_result = {"resultado": "110-105", "home_score": 110, "away_score": 105}
    with patch("src.scrapers.flashscore_scraper.find_team_match_result", return_value=fs_result), \
         patch.object(_wmod, "get_wallet", side_effect=_gw), \
         patch.object(_wmod, "save_wallet"):
        settled = _s._settle_stat_bets_by_score(min_hours_past=1.0)
    assert settled == [bet.bet_id]
    assert resolved_flag["won"] is True


def test_settle_stat_total_leg_resolves_lost_via_team_score():
    """Pata \'over\' total_points=210.5 -- Flashscore da total=205<210.5 -> LOST."""
    from src.core.orchestration import settlement as _s
    from src.core.orchestration import wallet as _wmod
    total_leg = MagicMock(side="over", tipo="total", linea=210.5)
    bet = MagicMock(bet_id="tb_lost", arb_tipo="stat", bet_type="stat",
                     fixture_name="Lakers vs Celtics", sport="basketball",
                     timestamp=(__import__("datetime").datetime.now() - __import__("datetime").timedelta(hours=5)).isoformat(),
                     placed_at=None, legs=[total_leg], total_staked=2.0,
                     guaranteed_return=4.0, status="pending")
    resolved_flag = {"won": None}

    def _resolve(bet_id, won, actual_return=None, record_feedback=False):
        resolved_flag["won"] = won
        bet.status = "won" if won else "lost"
        return bet

    fake = MagicMock(balance=40.0, pending_bets=[bet], resolve_bet=_resolve)

    def _gw(slug):
        return fake if slug == "stake" else MagicMock(balance=0.0, pending_bets=[], resolve_bet=lambda *a, **k: None)

    fs_result = {"resultado": "100-105", "home_score": 100, "away_score": 105}
    with patch("src.scrapers.flashscore_scraper.find_team_match_result", return_value=fs_result), \
         patch.object(_wmod, "get_wallet", side_effect=_gw), \
         patch.object(_wmod, "save_wallet"):
        settled = _s._settle_stat_bets_by_score(min_hours_past=1.0)
    assert settled == [bet.bet_id]
    assert resolved_flag["won"] is False


def test_settle_stat_total_leg_pushes_to_void_on_exact_line():
    """Pata \'over\' total_points=210 exacto -- Flashscore da total=210 ->
    PUSH (void, stake devuelto, PNL=0), no won ni lost."""
    from src.core.orchestration import settlement as _s
    from src.core.orchestration import wallet as _wmod
    total_leg = MagicMock(side="over", tipo="total", linea=210.0)
    bet = MagicMock(bet_id="tb_push", arb_tipo="stat", bet_type="stat",
                     fixture_name="Lakers vs Celtics", sport="basketball",
                     timestamp=(__import__("datetime").datetime.now() - __import__("datetime").timedelta(hours=5)).isoformat(),
                     placed_at=None, legs=[total_leg], total_staked=2.0,
                     guaranteed_return=4.0, status="pending")
    fake = MagicMock(balance=40.0, pending_bets=[bet], resolve_bet=lambda *a, **k: None)

    def _gw(slug):
        return fake if slug == "stake" else MagicMock(balance=0.0, pending_bets=[], resolve_bet=lambda *a, **k: None)

    fs_result = {"resultado": "110-100", "home_score": 110, "away_score": 100}
    with patch("src.scrapers.flashscore_scraper.find_team_match_result", return_value=fs_result), \
         patch.object(_wmod, "get_wallet", side_effect=_gw), \
         patch.object(_wmod, "save_wallet"), \
         patch.object(_wmod, "write_ledger") as mock_ledger:
        settled = _s._settle_stat_bets_by_score(min_hours_past=1.0)
    assert settled == [bet.bet_id]
    assert bet.status == "void"
    assert bet.pnl == 0.0
    assert fake.balance == 42.0   # 40 + stake (2.0) devuelto
    assert mock_ledger.called


