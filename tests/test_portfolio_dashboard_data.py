#!/usr/bin/env python3
"""
Tests de integridad de datos del Portafolio (/wallet) y el Dashboard principal.

Validan que los números que se MUESTRAN en la UI cuadran entre sí y respetan
invariantes conocidas. Cubren regresiones históricas reales:
  - wallet huérfana "Stake" (mayúscula) inflaba el equity total
  - el headline del summary no cuadraba con la suma de los cards por plataforma
  - el win_rate contaba bets void (lo inflaba)
  - realized_pnl incluía pending/void

Endpoints cubiertos:
  GET /api/wallet/total
  GET /api/wallet/summary
  GET /api/wallet/platforms
  GET /api/wallet/cross-book-metrics

Patrón: SQLite en memoria + Flask test client (igual que test_wallet_capital.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from peewee import SqliteDatabase

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.database.database import (
    Platform, Wallet, WalletBet, WalletDeposit, WalletWithdraw, WalletLedger,
)

_MODELS = [Platform, Wallet, WalletBet, WalletDeposit, WalletWithdraw, WalletLedger]


def _bet(bet_id, platform, status, staked, pnl=0.0, arb_tipo="cross_book_arb", roi=2.0):
    """Crea una WalletBet con los campos obligatorios poblados."""
    return WalletBet.create(
        bet_id=bet_id,
        platform=platform,
        placed_at="2026-06-15T10:00:00",
        fixture_name=f"Fix {bet_id}",
        arb_tipo=arb_tipo,
        total_staked=staked,
        guaranteed_return=staked * (1 + roi / 100.0),
        roi=roi,
        status=status,
        pnl=pnl,
    )


@pytest.fixture
def client():
    test_db = SqliteDatabase(":memory:")
    with test_db.bind_ctx(_MODELS):
        test_db.create_tables(_MODELS)

        # 3 plataformas válidas con saldo inicial
        Platform.create(slug="stake", name="Stake")
        Platform.create(slug="pinnacle", name="Pinnacle")
        Platform.create(slug="unibet", name="Unibet")
        Wallet.create(platform="stake", initial_balance=300.0, balance=300.0,
                      equity=300.0, deposits=300.0, withdrawals=0.0)
        Wallet.create(platform="pinnacle", initial_balance=200.0, balance=200.0,
                      equity=200.0, deposits=200.0, withdrawals=0.0)
        Wallet.create(platform="unibet", initial_balance=100.0, balance=100.0,
                      equity=100.0, deposits=100.0, withdrawals=0.0)

        from src.web.create_app import create_app
        from src.core import state
        state.active_platform = "stake"
        state.wallets.clear()
        state.rebalance_paused = False
        state.rebalance_log = []
        app = create_app()
        app.config.update(TESTING=True)
        with app.test_client() as c:
            yield c
        test_db.drop_tables(_MODELS)


# ─────────────────────────────────────────────────────────────────────────────
# /api/wallet/total — agregado del portafolio
# ─────────────────────────────────────────────────────────────────────────────

def test_total_equity_equals_balance_plus_pending(client):
    """total_equity = suma(balance) + suma(stake pendiente)."""
    # 3 wallets: 300 + 200 + 100 = 600 balance, sin bets aún
    j = client.get("/api/wallet/total").get_json()
    assert j["total_balance"] == 600.0
    assert j["total_equity"] == 600.0
    assert j["total_pending_staked"] == 0.0

    # Añadir una bet pendiente de 50 en stake → equity sube 50, balance no
    _bet("b1", "stake", "pending", 50.0)
    j2 = client.get("/api/wallet/total").get_json()
    assert j2["total_balance"] == 600.0
    assert j2["total_equity"] == 650.0
    assert j2["total_pending_staked"] == 50.0
    assert j2["total_equity"] >= j2["total_balance"]


def test_total_win_rate_excludes_void(client):
    """win_rate = won / (won + lost) * 100; las void NO cuentan."""
    _bet("w1", "stake", "won", 10.0, pnl=5.0)
    _bet("w2", "stake", "won", 10.0, pnl=5.0)
    _bet("l1", "stake", "lost", 10.0, pnl=-10.0)
    _bet("v1", "stake", "void", 10.0, pnl=0.0)  # no debe contar

    j = client.get("/api/wallet/total").get_json()
    assert j["bets_won"] == 2
    assert j["bets_lost"] == 1
    # 2 / (2+1) = 66.7
    assert j["win_rate"] == pytest.approx(66.7, abs=0.1)


def test_total_realized_pnl_only_won_lost(client):
    """realized_pnl suma solo won+lost (no pending ni void)."""
    _bet("w1", "stake", "won", 20.0, pnl=12.0)
    _bet("l1", "pinnacle", "lost", 20.0, pnl=-20.0)
    _bet("p1", "stake", "pending", 20.0, pnl=0.0)
    _bet("v1", "unibet", "void", 20.0, pnl=99.0)  # pnl basura, no debe sumar

    j = client.get("/api/wallet/total").get_json()
    assert j["realized_pnl"] == pytest.approx(12.0 - 20.0, abs=0.01)  # -8.0


def test_total_ignores_orphan_wallet(client):
    """Una wallet con platform sin Platform asociado NO infla el equity total.

    Regresión: wallet huérfana 'Stake' (mayúscula) duplicaba el seed de €50.
    """
    base = client.get("/api/wallet/total").get_json()["total_equity"]
    # Crear wallet huérfana (platform 'Stake' no existe como Platform.slug)
    Wallet.create(platform="Stake", initial_balance=50.0, balance=50.0,
                  equity=50.0, deposits=50.0, withdrawals=0.0)
    after = client.get("/api/wallet/total").get_json()["total_equity"]
    assert after == base  # la huérfana fue ignorada


def test_total_bet_counts_match_db(client):
    """bets_won/lost/pending del endpoint == conteos reales en DB."""
    _bet("w1", "stake", "won", 10.0, pnl=3.0)
    _bet("l1", "stake", "lost", 10.0, pnl=-10.0)
    _bet("p1", "pinnacle", "pending", 10.0)
    _bet("p2", "unibet", "pending", 10.0)

    j = client.get("/api/wallet/total").get_json()
    assert j["bets_won"] == 1
    assert j["bets_lost"] == 1
    assert j["bets_pending"] == 2


# ─────────────────────────────────────────────────────────────────────────────
# /api/wallet/summary — vista del portafolio
# ─────────────────────────────────────────────────────────────────────────────

def test_summary_headline_equals_sum_of_platform_cards(client):
    """total_equity del headline == suma de equity de los cards por plataforma.

    Regresión: el headline no cuadraba con la suma de los cards (dormant excluidas).
    """
    _bet("p1", "stake", "pending", 40.0)
    _bet("w1", "pinnacle", "won", 20.0, pnl=10.0)

    j = client.get("/api/wallet/summary").get_json()
    cards = [p for p in j["platforms"] if p["has_wallet"]]
    sum_equity = round(sum(p["equity"] for p in cards), 2)
    sum_balance = round(sum(p["balance"] for p in cards), 2)
    assert j["total_equity"] == sum_equity
    assert j["total_balance"] == sum_balance


def test_summary_matches_total_endpoint(client):
    """summary y total deben reportar el MISMO equity/balance del portafolio."""
    _bet("p1", "stake", "pending", 30.0)
    _bet("w1", "unibet", "won", 15.0, pnl=7.5)
    _bet("l1", "pinnacle", "lost", 15.0, pnl=-15.0)

    s = client.get("/api/wallet/summary").get_json()
    t = client.get("/api/wallet/total").get_json()
    assert s["total_equity"] == t["total_equity"]
    assert s["total_balance"] == t["total_balance"]
    assert s["bets_won"] == t["bets_won"]
    assert s["bets_lost"] == t["bets_lost"]
    assert s["bets_pending"] == t["bets_pending"]


def test_summary_platform_win_rate_excludes_void(client):
    """El win_rate por plataforma no cuenta void."""
    _bet("w1", "stake", "won", 10.0, pnl=5.0)
    _bet("l1", "stake", "lost", 10.0, pnl=-10.0)
    _bet("v1", "stake", "void", 10.0, pnl=0.0)

    j = client.get("/api/wallet/summary").get_json()
    stake_card = next(p for p in j["platforms"] if p["slug"] == "stake")
    assert stake_card["bets_won"] == 1
    assert stake_card["bets_lost"] == 1
    assert stake_card["win_rate"] == pytest.approx(50.0, abs=0.1)


def test_summary_strategies_breakdown(client):
    """El breakdown por estrategia agrupa bien por arb_tipo."""
    _bet("a1", "stake", "won", 10.0, pnl=4.0, arb_tipo="cross_book_arb")
    _bet("a2", "stake", "lost", 10.0, pnl=-10.0, arb_tipo="cross_book_arb")
    _bet("h1", "pinnacle", "won", 10.0, pnl=2.0, arb_tipo="cross_book_hedge")

    j = client.get("/api/wallet/summary").get_json()
    by_tipo = {s["tipo"]: s for s in j["strategies"]}
    assert by_tipo["cross_book_arb"]["bets"] == 2
    assert by_tipo["cross_book_arb"]["won"] == 1
    assert by_tipo["cross_book_arb"]["lost"] == 1
    assert by_tipo["cross_book_hedge"]["bets"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# /api/wallet/platforms — catálogo / cards
# ─────────────────────────────────────────────────────────────────────────────

def test_platforms_lists_all_seeded(client):
    """El catálogo lista las 3 plataformas con su balance."""
    j = client.get("/api/wallet/platforms").get_json()
    slugs = {p["slug"] for p in j["platforms"]}
    assert {"stake", "pinnacle", "unibet"}.issubset(slugs)
    stake = next(p for p in j["platforms"] if p["slug"] == "stake")
    assert stake["balance"] == 300.0


def test_platforms_equity_includes_pending(client):
    """El equity por plataforma incluye el stake pendiente de esa casa."""
    _bet("p1", "pinnacle", "pending", 60.0)
    j = client.get("/api/wallet/platforms").get_json()
    pin = next(p for p in j["platforms"] if p["slug"] == "pinnacle")
    assert pin["balance"] == 200.0
    assert pin["equity"] == 260.0


# ─────────────────────────────────────────────────────────────────────────────
# /api/wallet/cross-book-metrics — métricas de estrategia
# ─────────────────────────────────────────────────────────────────────────────

def test_cross_book_metrics_counts_consistent(client):
    """Por tipo: bets_total == won + lost + pending + void."""
    _bet("a1", "stake", "won", 10.0, pnl=3.0, arb_tipo="cross_book_arb")
    _bet("a2", "stake", "lost", 10.0, pnl=-10.0, arb_tipo="cross_book_arb")
    _bet("a3", "pinnacle", "pending", 10.0, arb_tipo="cross_book_arb")
    _bet("a4", "unibet", "void", 10.0, arb_tipo="cross_book_arb")

    j = client.get("/api/wallet/cross-book-metrics").get_json()
    arb = j["by_tipo"]["cross_book_arb"]
    total = arb["bets_won"] + arb["bets_lost"] + arb["bets_pending"] + arb["bets_void"]
    assert arb["bets_total"] == total == 4


def test_cross_book_metrics_hit_rate(client):
    """hit_rate = won / (won + lost) * 100 (pending/void no cuentan)."""
    _bet("a1", "stake", "won", 10.0, pnl=3.0, arb_tipo="cross_book_arb")
    _bet("a2", "stake", "won", 10.0, pnl=3.0, arb_tipo="cross_book_arb")
    _bet("a3", "stake", "lost", 10.0, pnl=-10.0, arb_tipo="cross_book_arb")
    _bet("a4", "stake", "pending", 10.0, arb_tipo="cross_book_arb")

    j = client.get("/api/wallet/cross-book-metrics").get_json()
    arb = j["by_tipo"]["cross_book_arb"]
    assert arb["hit_rate"] == pytest.approx(66.67, abs=0.1)


def test_cross_book_metrics_overall_aggregates(client):
    """overall agrega correctamente a través de tipos."""
    _bet("a1", "stake", "won", 10.0, pnl=3.0, arb_tipo="cross_book_arb")
    _bet("h1", "pinnacle", "lost", 10.0, pnl=-10.0, arb_tipo="cross_book_hedge")

    j = client.get("/api/wallet/cross-book-metrics").get_json()
    overall = j["overall"]
    assert overall["bets_total"] == 2
    assert overall["bets_won"] == 1
    assert overall["bets_lost"] == 1
    assert overall["pnl_total"] == pytest.approx(3.0 - 10.0, abs=0.01)


def test_cross_book_metrics_roi_realized(client):
    """roi_realized = pnl / staked * 100."""
    _bet("a1", "stake", "won", 100.0, pnl=4.0, arb_tipo="cross_book_arb")

    j = client.get("/api/wallet/cross-book-metrics").get_json()
    arb = j["by_tipo"]["cross_book_arb"]
    # pnl 4 / staked 100 = 4%
    assert arb["roi_realized"] == pytest.approx(4.0, abs=0.01)
