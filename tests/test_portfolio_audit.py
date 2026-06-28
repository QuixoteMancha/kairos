#!/usr/bin/env python3
"""
AUDITORÍA de los datos del Portafolio y el Dashboard.

A diferencia de un test "smoke", estos tests actúan como un AUDITOR independiente:
sobre un dataset semilla totalmente conocido, recalculan por su cuenta — con una
segunda implementación, no la del endpoint — cada métrica que la UI muestra:

  - P&L realizado            (Σ pnl de won+lost; excluye pending/void)
  - Capital en vuelo         (Σ stake de bets pendientes)
  - Equity total             (balance + capital en vuelo)
  - Apostado total           (Σ stake de TODAS las bets)
  - Win rate                 (won / (won+lost); void NO cuenta)
  - Retorno realizado        (pnl realizado / capital base)
  - ROI por estrategia       (escala fracción consistente)

Y verifican INVARIANTES CRUZADAS entre endpoints (lo que en la UI "no cuadra"):
  - total_equity  == Σ equity por plataforma
  - total_balance == Σ balance por plataforma
  - apostado total (/total) == Σ wagered de las estrategias (/summary)
  - pnl realizado (/total) == Σ pnl de las estrategias (/summary)
  - summary y total reportan el MISMO equity/balance/conteos
  - capital en vuelo == equity - balance (en cada plataforma y en el total)

Dataset golden (hand-computed en los comentarios) para no depender de datos vivos.
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


# ─────────────────────────────────────────────────────────────────────────────
# Dataset golden — números calculados a mano
# ─────────────────────────────────────────────────────────────────────────────
#
# Wallets (balance == deposits, sin retiros):
#   stake    balance 100   deposits 100
#   pinnacle balance  80   deposits  80
#   unibet   balance  60   deposits  60
#   ── total_balance = 240   total_deposits = 240
#
# Bets (roi guardado como FRACCIÓN, p.ej. 0.05 = 5%):
#   id  plat      status   stake  pnl    roi    tipo
#   w1  stake     won       20    +8     0.05   cross_book_arb_2way
#   l1  stake     lost      20    -20    0.05   cross_book_arb_2way
#   p1  stake     pending   15     0     0.03   cross_book_arb_2way
#   w2  pinnacle  won       30    +3     0.02   cross_book_arb_3way
#   p2  pinnacle  pending   25     0     0.02   cross_book_arb_3way
#   v1  unibet    void      10     0     0.04   cross_book_hedge
#   w3  unibet    won       40    +1     0.01   cross_book_hedge
#
# Balances REALISTAS (como el sistema real: stake se debita al colocar la bet):
#   balance = seed + Σpnl(won+lost) - Σstake(pending)   [void = neto 0]
#   stake:    100 + 8 - 20 - 15 = 73   (pending 15 → equity 88)
#   pinnacle:  80 + 3 - 25      = 58   (pending 25 → equity 83)
#   unibet:    60 + 1           = 61   (pending  0 → equity 61)
#   deposits = 0 (seed vía initial_balance, igual que el Docker real)
#
# Recálculo independiente:
#   realized_pnl        = 8 - 20 + 3 + 1            = -8.0
#   capital_en_vuelo    = 15 + 25                   = 40.0   (solo pending)
#   total_balance       = 73 + 58 + 61              = 192.0
#   total_equity        = 192 + 40                  = 232.0  (= seed 240 + pnl -8)
#   total_wagered       = 20+20+15+30+25+10+40      = 160.0  (TODAS las bets)
#   bets_won/lost/pend/void = 3 / 1 / 2 / 1
#   win_rate            = 3/(3+1)*100               = 75.0
#   equity por plat: stake 88, pinnacle 83, unibet 61   (Σ = 232)
#   pnl por plat:    stake -12, pinnacle +3, unibet +1  (Σ = -8)
#   estrategias:
#     2way:  bets 3, won 1, lost 1, pnl -12, wagered 55, win 50%, avg_roi 5%
#     3way:  bets 2, won 1,        pnl  +3, wagered 55, win 100%, avg_roi 2%
#     hedge: bets 2, won 1, void 1, pnl +1, wagered 50, win 100%, avg_roi 1%
#     Σ wagered = 160  Σ pnl = -8
# ─────────────────────────────────────────────────────────────────────────────

_SEED_WALLETS = [
    ("stake", 100.0),
    ("pinnacle", 80.0),
    ("unibet", 60.0),
]

_SEED_BETS = [
    ("w1", "stake", "won", 20.0, 8.0, 0.05, "cross_book_arb_2way"),
    ("l1", "stake", "lost", 20.0, -20.0, 0.05, "cross_book_arb_2way"),
    ("p1", "stake", "pending", 15.0, 0.0, 0.03, "cross_book_arb_2way"),
    ("w2", "pinnacle", "won", 30.0, 3.0, 0.02, "cross_book_arb_3way"),
    ("p2", "pinnacle", "pending", 25.0, 0.0, 0.02, "cross_book_arb_3way"),
    ("v1", "unibet", "void", 10.0, 0.0, 0.04, "cross_book_hedge"),
    ("w3", "unibet", "won", 40.0, 1.0, 0.01, "cross_book_hedge"),
]


def _realistic_balances() -> dict:
    """Balance final por wallet replicando el flujo real de colocación/resolución."""
    bal = {slug: seed for slug, seed in _SEED_WALLETS}
    for _id, plat, status, stake, pnl, _roi, _tipo in _SEED_BETS:
        if status in ("won", "lost"):
            bal[plat] += pnl              # won: +pnl ; lost: pnl == -stake
        elif status == "pending":
            bal[plat] -= stake           # stake en vuelo, fuera del balance libre
        # void: neto 0 (refund == stake)
    return {k: round(v, 2) for k, v in bal.items()}


@pytest.fixture
def client():
    test_db = SqliteDatabase(":memory:")
    with test_db.bind_ctx(_MODELS):
        test_db.create_tables(_MODELS)
        balances = _realistic_balances()
        for slug, seed in _SEED_WALLETS:
            Platform.create(slug=slug, name=slug.title())
            # deposits=0: el seed entra por initial_balance (igual que el Docker real)
            Wallet.create(platform=slug, initial_balance=seed, balance=balances[slug],
                          equity=balances[slug], deposits=0.0, withdrawals=0.0)
        for bet_id, plat, status, stake, pnl, roi, tipo in _SEED_BETS:
            WalletBet.create(
                bet_id=bet_id, platform=plat, placed_at="2026-06-15T10:00:00",
                fixture_name=f"Fix {bet_id}", arb_tipo=tipo,
                total_staked=stake, guaranteed_return=stake * (1 + roi),
                roi=roi, status=status, pnl=pnl,
            )

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
# AUDITOR independiente — recalcula desde las filas crudas (NO usa el endpoint)
# ─────────────────────────────────────────────────────────────────────────────

def _audit_from_db():
    bets = list(WalletBet.select())
    wallets = list(Wallet.select())
    valid_slugs = {p.slug for p in Platform.select()}

    won = [b for b in bets if b.status == "won"]
    lost = [b for b in bets if b.status == "lost"]
    pend = [b for b in bets if b.status == "pending"]
    void = [b for b in bets if b.status == "void"]

    realized_pnl = round(sum(b.pnl or 0.0 for b in won + lost), 2)
    capital_en_vuelo = round(sum(b.total_staked for b in pend), 2)
    total_balance = round(sum(w.balance for w in wallets if w.platform in valid_slugs), 2)
    total_equity = round(total_balance + capital_en_vuelo, 2)
    total_wagered = round(sum(b.total_staked for b in bets), 2)
    settled = len(won) + len(lost)
    win_rate = round(len(won) / settled * 100, 1) if settled else 0.0

    per_platform = {}
    for w in wallets:
        if w.platform not in valid_slugs:
            continue
        pbets = [b for b in bets if b.platform == w.platform]
        ppend = [b for b in pbets if b.status == "pending"]
        per_platform[w.platform] = {
            "balance": round(w.balance, 2),
            "equity": round(w.balance + sum(b.total_staked for b in ppend), 2),
            "pnl": round(sum(b.pnl or 0.0 for b in pbets if b.status in ("won", "lost")), 2),
        }

    strategies = {}
    for b in bets:
        s = strategies.setdefault(b.arb_tipo, {"wagered": 0.0, "pnl": 0.0})
        s["wagered"] += b.total_staked
        if b.status in ("won", "lost"):
            s["pnl"] += b.pnl or 0.0
    for s in strategies.values():
        s["wagered"] = round(s["wagered"], 2)
        s["pnl"] = round(s["pnl"], 2)

    return {
        "realized_pnl": realized_pnl,
        "capital_en_vuelo": capital_en_vuelo,
        "total_balance": total_balance,
        "total_equity": total_equity,
        "total_wagered": total_wagered,
        "bets_won": len(won), "bets_lost": len(lost),
        "bets_pending": len(pend), "bets_void": len(void),
        "win_rate": win_rate,
        "per_platform": per_platform,
        "strategies": strategies,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. /api/wallet/total contra el AUDITOR
# ─────────────────────────────────────────────────────────────────────────────

def test_audit_total_realized_pnl(client):
    a = _audit_from_db()
    j = client.get("/api/wallet/total").get_json()
    assert j["realized_pnl"] == a["realized_pnl"] == -8.0


def test_audit_total_capital_en_vuelo(client):
    a = _audit_from_db()
    j = client.get("/api/wallet/total").get_json()
    assert j["total_pending_staked"] == a["capital_en_vuelo"] == 40.0


def test_audit_total_equity_is_balance_plus_in_flight(client):
    a = _audit_from_db()
    j = client.get("/api/wallet/total").get_json()
    assert j["total_balance"] == a["total_balance"] == 192.0
    assert j["total_equity"] == a["total_equity"] == 232.0
    # invariante: equity == balance + capital en vuelo
    assert round(j["total_equity"] - j["total_balance"], 2) == j["total_pending_staked"]


def test_audit_total_wagered_includes_all_bets(client):
    a = _audit_from_db()
    j = client.get("/api/wallet/total").get_json()
    assert j["total_wagered"] == a["total_wagered"] == 160.0


def test_audit_total_win_rate_excludes_void(client):
    a = _audit_from_db()
    j = client.get("/api/wallet/total").get_json()
    assert j["bets_won"] == a["bets_won"] == 3
    assert j["bets_lost"] == a["bets_lost"] == 1
    assert j["bets_pending"] == a["bets_pending"] == 2
    assert j["win_rate"] == a["win_rate"] == 75.0


def test_audit_total_equity_equals_sum_of_platforms(client):
    """total_equity == Σ equity de cada plataforma (recalculado a mano)."""
    a = _audit_from_db()
    j = client.get("/api/wallet/total").get_json()
    sum_eq = round(sum(p["equity"] for p in a["per_platform"].values()), 2)
    assert j["total_equity"] == sum_eq == 232.0


# ─────────────────────────────────────────────────────────────────────────────
# 2. /api/wallet/summary contra el AUDITOR + invariantes cruzadas
# ─────────────────────────────────────────────────────────────────────────────

def test_audit_summary_headline_equals_sum_of_cards(client):
    a = _audit_from_db()
    j = client.get("/api/wallet/summary").get_json()
    cards = [p for p in j["platforms"] if p["has_wallet"]]
    assert round(sum(p["equity"] for p in cards), 2) == j["total_equity"] == a["total_equity"]
    assert round(sum(p["balance"] for p in cards), 2) == j["total_balance"] == a["total_balance"]


def test_audit_summary_apostado_equals_strategies_sum(client):
    """INVARIANTE clave: apostado total == Σ wagered del breakdown por estrategia.

    (Es justo el descuadre observado en la UI: 'apostado total' ≠ suma del breakdown.)
    """
    j = client.get("/api/wallet/summary").get_json()
    t = client.get("/api/wallet/total").get_json()
    sum_strat_wagered = round(sum(s["wagered"] for s in j["strategies"]), 2)
    assert sum_strat_wagered == t["total_wagered"] == 160.0


def test_audit_summary_pnl_equals_strategies_sum(client):
    j = client.get("/api/wallet/summary").get_json()
    t = client.get("/api/wallet/total").get_json()
    sum_strat_pnl = round(sum(s["pnl"] for s in j["strategies"]), 2)
    assert sum_strat_pnl == t["realized_pnl"] == -8.0


def test_audit_summary_per_platform_pnl_sums_to_realized(client):
    a = _audit_from_db()
    j = client.get("/api/wallet/summary").get_json()
    cards = [p for p in j["platforms"] if p["has_wallet"]]
    assert round(sum(p["pnl"] for p in cards), 2) == a["realized_pnl"] == -8.0


def test_audit_summary_matches_total_endpoint(client):
    s = client.get("/api/wallet/summary").get_json()
    t = client.get("/api/wallet/total").get_json()
    assert s["total_equity"] == t["total_equity"]
    assert s["total_balance"] == t["total_balance"]
    assert s["bets_won"] == t["bets_won"]
    assert s["bets_lost"] == t["bets_lost"]
    assert s["bets_pending"] == t["bets_pending"]


def test_audit_summary_strategy_roi_is_fraction_scaled(client):
    """avg_roi debe estar en escala razonable (fracción→%), nunca cientos de %."""
    j = client.get("/api/wallet/summary").get_json()
    by_tipo = {s["tipo"]: s for s in j["strategies"]}
    # 2way roi fracción 0.05 → 5.0%
    assert by_tipo["cross_book_arb_2way"]["avg_roi"] == pytest.approx(5.0, abs=0.01)
    assert by_tipo["cross_book_arb_3way"]["avg_roi"] == pytest.approx(2.0, abs=0.01)
    # ningún avg_roi absurdo (>100%) para datos de arbitraje
    for s in j["strategies"]:
        assert -100.0 <= s["avg_roi"] <= 100.0


def test_audit_summary_roi_normalizes_mixed_scale(client):
    """Si una bet guardó roi en escala porcentaje (0.05→5.0 por error de ingest),
    el display debe normalizar y NO mostrar 500%."""
    # Mutar una won bet 3way a roi=2.0 (porcentaje mal escalado en vez de 0.02)
    WalletBet.update(roi=2.0).where(WalletBet.bet_id == "w2").execute()
    j = client.get("/api/wallet/summary").get_json()
    by_tipo = {s["tipo"]: s for s in j["strategies"]}
    # 2.0 normalizado como fracción → 2.0% (no 200%)
    assert by_tipo["cross_book_arb_3way"]["avg_roi"] == pytest.approx(2.0, abs=0.01)


# ─────────────────────────────────────────────────────────────────────────────
# 3. /api/wallet/platforms — cards (incluye win_rate auditado)
# ─────────────────────────────────────────────────────────────────────────────

def test_audit_platforms_equity_and_balance(client):
    a = _audit_from_db()
    j = client.get("/api/wallet/platforms").get_json()
    cards = {p["slug"]: p for p in j["platforms"]}
    for slug, exp in a["per_platform"].items():
        assert cards[slug]["balance"] == exp["balance"]
        assert cards[slug]["equity"] == exp["equity"]


def test_audit_platforms_win_rate_present_and_correct(client):
    """Cada card debe exponer win_rate = won/(won+lost); 0 si no hay resueltas."""
    j = client.get("/api/wallet/platforms").get_json()
    cards = {p["slug"]: p for p in j["platforms"]}
    # stake: 1 won / 1 lost = 50%
    assert "win_rate" in cards["stake"]
    assert cards["stake"]["win_rate"] == pytest.approx(50.0, abs=0.1)
    # unibet: 1 won, 0 lost (void no cuenta) = 100%
    assert cards["unibet"]["win_rate"] == pytest.approx(100.0, abs=0.1)


def test_audit_platforms_zero_resolved_is_zero_winrate(client):
    """Una plataforma sin bets resueltas debe mostrar win_rate 0 (no 50%)."""
    Platform.create(slug="comeon", name="ComeOn")
    Wallet.create(platform="comeon", initial_balance=50.0, balance=50.0,
                  equity=50.0, deposits=50.0, withdrawals=0.0)
    j = client.get("/api/wallet/platforms").get_json()
    comeon = next(p for p in j["platforms"] if p["slug"] == "comeon")
    assert comeon["bets_won"] == 0
    assert comeon["bets_lost"] == 0
    assert comeon["win_rate"] == 0.0


def test_audit_platforms_pnl_present_and_sums_to_total(client):
    """Cada card de /platforms expone pnl, y la suma == realized_pnl global.

    (Bug del dashboard: P&L por casa mostraba +€0.00 porque el endpoint no lo
    devolvía → la suma no cuadraba con el P&L total.)
    """
    a = _audit_from_db()
    j = client.get("/api/wallet/platforms").get_json()
    cards = {p["slug"]: p for p in j["platforms"]}
    # pnl presente en cada card
    for p in j["platforms"]:
        assert "pnl" in p
    # por plataforma == auditor (stake -12, pinnacle +3, unibet +1)
    assert cards["stake"]["pnl"] == a["per_platform"]["stake"]["pnl"] == -12.0
    assert cards["pinnacle"]["pnl"] == a["per_platform"]["pinnacle"]["pnl"] == 3.0
    assert cards["unibet"]["pnl"] == a["per_platform"]["unibet"]["pnl"] == 1.0
    # la suma de P&L por casa == realized_pnl global (NO 0.00)
    assert round(sum(p["pnl"] for p in j["platforms"]), 2) == a["realized_pnl"] == -8.0


def test_audit_platforms_lists_all_registered(client):
    """Todas las plataformas con Platform row aparecen, con has_platform_row=True."""
    j = client.get("/api/wallet/platforms").get_json()
    slugs = {p["slug"] for p in j["platforms"]}
    assert {"stake", "pinnacle", "unibet"}.issubset(slugs)
    for p in j["platforms"]:
        # Synthetic entries (e.g. polymarket paper ledger) may not have a platform DB row
        if p.get("has_platform_row") is False:
            continue
        assert p.get("has_platform_row") is True


def test_audit_platforms_registered_without_wallet_shows_zero(client):
    """Una plataforma registrada SIN wallet aparece con balance/pnl 0 (no se oculta)."""
    Platform.create(slug="betano", name="Betano")  # registrada, sin Wallet
    j = client.get("/api/wallet/platforms").get_json()
    betano = next((p for p in j["platforms"] if p["slug"] == "betano"), None)
    assert betano is not None
    assert betano["has_platform_row"] is True
    assert betano["has_wallet"] is False
    assert betano["balance"] == 0.0
    assert betano["pnl"] == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 4. /api/wallet/cross-book-metrics contra el AUDITOR
# ─────────────────────────────────────────────────────────────────────────────

def test_audit_cross_book_roi_realized_from_pnl(client):
    """roi_realized == pnl/staked*100 (independiente de la escala de roi guardado)."""
    j = client.get("/api/wallet/cross-book-metrics").get_json()
    hedge = j["by_tipo"]["cross_book_hedge"]
    # hedge: pnl realizado = +1 (w3), staked total = 10(void)+40(won) = 50 → 2.0%
    assert hedge["roi_realized"] == pytest.approx(1.0 / 50.0 * 100, abs=0.01)


def test_audit_cross_book_counts_partition(client):
    """bets_total == won + lost + pending + void para cada tipo."""
    j = client.get("/api/wallet/cross-book-metrics").get_json()
    for tipo, m in j["by_tipo"].items():
        assert m["bets_total"] == (
            m["bets_won"] + m["bets_lost"] + m["bets_pending"] + m["bets_void"]
        )


def test_audit_cross_book_roi_expected_sane(client):
    """roi_expected normalizado: nunca cientos de % aunque roi venga mal escalado."""
    WalletBet.update(roi=4.0).where(WalletBet.bet_id == "v1").execute()  # 0.04 mal escalado
    j = client.get("/api/wallet/cross-book-metrics").get_json()
    for tipo, m in j["by_tipo"].items():
        assert -100.0 <= m["roi_expected"] <= 100.0


# ─────────────────────────────────────────────────────────────────────────────
# 5. SALUD del portafolio — conservación de capital, balances, rebalance
#    (responde: "algunas wallets tenían más capital que otras, ¿estaba bien?")
# ─────────────────────────────────────────────────────────────────────────────

def test_audit_capital_conservation(client):
    """INVARIANTE maestra: equity == Σ seed + pnl realizado + dep - retiros.

    Detecta dinero creado/destruido (bugs de commit parcial, seeds fantasma, etc).
    Con balances realistas: Σ seed = 240, realized_pnl = -8, deposits = 0
    → equity esperado = 240 + (-8) + 0 - 0 = 232. El capital en vuelo (40) ya
    está dentro del equity (es stake que salió del balance libre hacia 'en juego').
    """
    seed = round(sum(w.initial_balance for w in Wallet.select()), 2)  # 240
    j = client.get("/api/wallet/total").get_json()
    expected_equity = round(seed + j["realized_pnl"]
                            + j["total_deposits"] - j["total_withdrawals"], 2)
    assert j["total_equity"] == expected_equity == 232.0


def test_audit_no_negative_balance(client):
    """Ninguna wallet debe quedar con balance negativo (bug de commit parcial)."""
    j = client.get("/api/wallet/platforms").get_json()
    for p in j["platforms"]:
        assert p["balance"] >= 0.0, f"{p['slug']} balance negativo: {p['balance']}"


def test_audit_seed_implicit_non_negative(client):
    """El seed implícito (equity - pnl - dep + wit) debe ser >= 0.

    Si fuese negativo, significaría que el equity no alcanza a cubrir el P&L
    declarado → señal de dinero inventado o pnl inflado.
    """
    j = client.get("/api/wallet/total").get_json()
    seed_implicit = round(j["total_equity"] - j["realized_pnl"]
                          - j["total_deposits"] + j["total_withdrawals"], 2)
    assert seed_implicit >= -0.01


def test_audit_rebalance_alert_fires_on_critical_wallet(client):
    """Una wallet activa por debajo del mínimo crítico (5€) debe generar alerta.

    Detecta el caso 'una wallet quedó vacía y nadie avisó'.
    """
    # Drenar pinnacle a 2.0 (crítico < 5.0)
    Wallet.update(balance=2.0).where(Wallet.platform == "pinnacle").execute()
    j = client.get("/api/wallet/summary").get_json()
    alert_slugs = {a["slug"] for a in j["rebalance_alerts"]}
    assert "pinnacle" in alert_slugs
    pin_alert = next(a for a in j["rebalance_alerts"] if a["slug"] == "pinnacle")
    assert pin_alert["severity"] == "critical"


def test_audit_balanced_wallets_no_false_alert(client):
    """Wallets razonablemente balanceadas NO deben disparar alertas falsas.

    (El caso real: stake 44 vs unibet 102 — distinto capital, pero sano.)
    """
    # stake 100, pinnacle 80, unibet 60 → promedio 80; ninguno < 30% ni < 5€
    j = client.get("/api/wallet/summary").get_json()
    criticals = [a for a in j["rebalance_alerts"] if a["severity"] == "critical"]
    assert criticals == []


def test_audit_total_equity_conserved_after_deposit(client):
    """Tras un depósito, el equity sube EXACTAMENTE el monto depositado."""
    before = client.get("/api/wallet/total").get_json()["total_equity"]
    r = client.post("/api/wallet/deposit", json={"amount": 50, "platform": "stake"})
    assert r.status_code == 200
    after = client.get("/api/wallet/total").get_json()["total_equity"]
    assert round(after - before, 2) == 50.0


# ─────────────────────────────────────────────────────────────────────────────
# 6. RETORNO REALIZADO — base = capital sembrado + depósitos (no 0% fantasma)
#    (responde el bug del dashboard: 'Retorno realizado +0.00%' con P&L > 0)
# ─────────────────────────────────────────────────────────────────────────────

def test_audit_return_uses_seed_capital_not_zero(client):
    """El retorno NO debe ser 0% cuando hay P&L y el capital vino del seed.

    seed = 240, realized_pnl = -8, deposits = 0 → retorno = -8/240*100 = -3.33%.
    (Antes salía 0% porque dividía por depósitos == 0.)
    """
    j = client.get("/api/wallet/total").get_json()
    assert j["total_seed"] == 240.0
    assert j["cost_basis"] == 240.0  # seed + net deposits (0)
    assert j["total_return_pct"] == pytest.approx(-8.0 / 240.0 * 100, abs=0.01)
    assert j["total_return_pct"] != 0.0  # ya no es el 0% fantasma


def test_audit_summary_return_matches_total(client):
    """El retorno de /summary usa la misma base (seed) que /total."""
    s = client.get("/api/wallet/summary").get_json()
    t = client.get("/api/wallet/total").get_json()
    assert s["cost_basis"] == t["cost_basis"] == 240.0
    assert s["total_return_pct"] == pytest.approx(t["total_return_pct"], abs=0.01)


def test_audit_return_positive_with_profit(client):
    """Con P&L positivo, el retorno es positivo y coherente con la base."""
    # Convertir la lost bet en won grande → pnl realizado positivo
    WalletBet.update(status="won", pnl=30.0).where(WalletBet.bet_id == "l1").execute()
    j = client.get("/api/wallet/total").get_json()
    # nuevo realized_pnl = 8 + 30 + 3 + 1 = 42 ; base seed 240 → 17.5%
    assert j["realized_pnl"] == 42.0
    assert j["total_return_pct"] == pytest.approx(42.0 / 240.0 * 100, abs=0.01)


def test_audit_return_includes_deposits_in_base(client):
    """Un depósito aumenta la base de coste (cost_basis), reduciendo el retorno %."""
    base_before = client.get("/api/wallet/total").get_json()["cost_basis"]
    client.post("/api/wallet/deposit", json={"amount": 60, "platform": "stake"})
    j = client.get("/api/wallet/total").get_json()
    assert j["cost_basis"] == round(base_before + 60, 2) == 300.0
    # retorno = pnl / nueva base
    assert j["total_return_pct"] == pytest.approx(j["realized_pnl"] / 300.0 * 100, abs=0.01)


