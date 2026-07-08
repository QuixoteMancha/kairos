"""
test_rebalance_capital.py — invariantes del rebalanceo automático de wallets.

Foco: CONSERVACIÓN DE CAPITAL (es dinero ficticio que se MUEVE entre cuentas;
la suma total debe ser invariante ante transferencias) + no dejar donantes
negativos + durabilidad vía ledger (la transferencia sobrevive a una recarga).
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
from src.core import state
from src.core.orchestration import rebalance as reb
from src.core.orchestration import cross_book as cb
from src.core.orchestration import wallet as wmod

_MODELS = [Platform, Wallet, WalletBet, WalletDeposit, WalletWithdraw, WalletLedger]


@pytest.fixture
def rebalance_env():
    test_db = SqliteDatabase(":memory:")
    with test_db.bind_ctx(_MODELS):
        test_db.create_tables(_MODELS)

        # Dos casas cross-book activas, una rica y una deficitaria.
        Platform.create(slug="polymarket", name="Polymarket", is_active=True)
        Platform.create(slug="kalshi", name="Kalshi", is_active=True)
        Wallet.create(platform="polymarket", initial_balance=100.0, balance=100.0,
                      equity=100.0, deposits=0.0, withdrawals=0.0)
        Wallet.create(platform="kalshi", initial_balance=2.0, balance=2.0,
                      equity=2.0, deposits=0.0, withdrawals=0.0)

        # Estado: limpiar caches + habilitar métricas (last_count>0 = casa elegible).
        state.wallets.clear()
        state.rebalance_paused = False
        state._last_rebalance_ts = 0.0
        _prev_metrics = dict(cb._cb_book_metrics)
        cb._cb_book_metrics["polymarket"] = {"last_count": 5}
        cb._cb_book_metrics["kalshi"] = {"last_count": 5}

        try:
            yield
        finally:
            cb._cb_book_metrics.clear()
            cb._cb_book_metrics.update(_prev_metrics)
            state.wallets.clear()
            test_db.drop_tables(_MODELS)


def _sum_wallets_in_memory() -> float:
    return round(sum(w.balance for w in state.wallets.values()), 2)


def _sum_wallets_from_db_reload() -> float:
    """Recarga las wallets desde DB (reconciliación vía ledger) y suma balances."""
    total = 0.0
    for slug in ("polymarket", "kalshi"):
        wmod.forget_wallet(slug)
        total += wmod.get_wallet(slug).balance
    return round(total, 2)


def test_rebalance_runs_and_moves_capital(rebalance_env):
    total_before = round(100.0 + 2.0, 2)
    did = reb.run_rebalance()
    assert did is True, "debería ejecutar al menos una transferencia (pinnacle deficitario)"
    # pinnacle recibió capital → ya no está en ~2
    assert state.wallets["kalshi"].balance > 2.0


def test_capital_conserved_in_memory(rebalance_env):
    total_before = round(100.0 + 2.0, 2)
    reb.run_rebalance()
    total_after = _sum_wallets_in_memory()
    assert total_after == total_before, (
        f"capital no conservado: {total_before} → {total_after}"
    )


def test_capital_conserved_after_reload(rebalance_env):
    """La transferencia vive en el ledger → al recargar, la suma se mantiene."""
    total_before = round(100.0 + 2.0, 2)
    reb.run_rebalance()
    total_reloaded = _sum_wallets_from_db_reload()
    assert total_reloaded == total_before, (
        f"capital no conservado tras recarga (ledger): {total_before} → {total_reloaded}"
    )


def test_donor_never_goes_negative(rebalance_env):
    reb.run_rebalance()
    for slug, w in state.wallets.items():
        assert w.balance >= 0, f"{slug} quedó negativo: {w.balance}"


def test_transfer_ledger_sums_to_zero(rebalance_env):
    reb.run_rebalance()
    net = sum(float(r.amount or 0) for r in
              WalletLedger.select().where(WalletLedger.op_type == "rebalance_transfer"))
    assert round(net, 2) == 0.0, f"los asientos de transferencia no suman cero: {net}"


def test_manifold_excluded_from_real_money_rebalance(rebalance_env):
    """Manifold es 'mana' (dinero virtual no retirable) — nunca debe
    participar como donor/recipient en el rebalanceo de capital REAL, aunque
    esté activo, tenga wallet y métricas > 0 (regresión: antes _CBPLAT
    incluía Manifold sin filtrar por REAL_MONEY_PLATFORMS)."""
    Platform.create(slug="manifold", name="Manifold", is_active=True)
    Wallet.create(platform="manifold", initial_balance=500.0, balance=500.0,
                  equity=500.0, deposits=0.0, withdrawals=0.0)
    cb._cb_book_metrics["manifold"] = {"last_count": 5}
    state.wallets.clear()

    reb.run_rebalance()

    ledger_platforms = {
        r.platform
        for r in WalletLedger.select().where(WalletLedger.op_type == "rebalance_transfer")
    }
    assert "manifold" not in ledger_platforms, (
        "Manifold no debe recibir/donar capital real en el rebalanceo"
    )
    manifold_wallet = wmod.get_wallet("manifold")
    assert manifold_wallet.balance == 500.0, (
        "el balance de Manifold no debe cambiar por el rebalanceo de capital real"
    )


def test_manifold_excluded_from_idle_donor_pool(rebalance_env):
    """Regresión específica (2026-07-08, hallazgo de auditoría verificado por
    Sara): Manifold SIN scraper activo (no en cb._cb_book_metrics, por lo que
    nunca entra a `active_slugs`) con un balance alto caía en el bucket de
    'wallets ociosas' (`idle_balances`) del rebalanceo, porque ese loop solo
    excluía slugs ya presentes en `balances` -- sin filtrar por _CBPLAT ni por
    REAL_MONEY_PLATFORMS. Eso permitía que Manifold actuara como donante de
    'capital real' en la pasada de déficit. Distinto del test de arriba
    (`test_manifold_excluded_from_real_money_rebalance`), que cubre el path
    con métricas activas (`active_slugs`) -- este cubre el path SIN métricas
    (`idle_balances`), que era el que realmente tenía el agujero."""
    Platform.create(slug="manifold", name="Manifold", is_active=True)
    Wallet.create(platform="manifold", initial_balance=500.0, balance=500.0,
                  equity=500.0, deposits=0.0, withdrawals=0.0)
    # Deliberadamente SIN entrada en cb._cb_book_metrics -> nunca entra a
    # active_slugs, cae directo al loop de "wallets ociosas".
    state.wallets.clear()

    reb.run_rebalance()

    ledger_platforms = {
        r.platform
        for r in WalletLedger.select().where(WalletLedger.op_type == "rebalance_transfer")
    }
    assert "manifold" not in ledger_platforms, (
        "Manifold (sin scraper activo) no debe entrar al pool de donantes ociosos "
        "ni recibir/donar capital real en el rebalanceo"
    )
    manifold_wallet = wmod.get_wallet("manifold")
    assert manifold_wallet.balance == 500.0, (
        "el balance de Manifold no debe cambiar por el rebalanceo de capital real"
    )


def test_no_rebalance_when_paused_does_not_move_capital(rebalance_env):
    # Si ya está pausado y balanceado, no fuerza nada raro; pero el cooldown y
    # la pausa no deben crear/destruir capital. Forzamos cooldown activo.
    state._last_rebalance_ts = 9e18  # futuro lejano → cooldown bloquea
    total_before = round(100.0 + 2.0, 2)
    did = reb.run_rebalance()
    assert did is False
    # nada se movió
    assert WalletLedger.select().where(
        WalletLedger.op_type == "rebalance_transfer").count() == 0
