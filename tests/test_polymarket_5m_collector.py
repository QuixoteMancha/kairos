import time

import pytest

from src.database.database import init_db, Crypto5mInterval
from src.core.orchestration import polymarket_5m_collector as coll


@pytest.fixture(autouse=True)
def _clean_intervals():
    init_db()
    Crypto5mInterval.delete().where(Crypto5mInterval.slug.contains("TEST")).execute()
    yield
    Crypto5mInterval.delete().where(Crypto5mInterval.slug.contains("TEST")).execute()


def _history(start_ts, yes_seq):
    return [
        {"ts": start_ts + i * 30, "yes_price": v}
        for i, v in enumerate(yes_seq)
    ]


def test_record_interval_inserts_with_features():
    start = int(time.time())
    market = {
        "slug": "btc-updown-5m-TEST1",
        "condition_id": "condTEST1",
        "start_date": start,
        "end_date": start + 300,
        "liquidity": 5000,
        "volume_24h": 12000,
    }
    coll.record_interval(market, _history(start, [0.50, 0.52, 0.55, 0.58]))

    row = Crypto5mInterval.get(Crypto5mInterval.slug == "btc-updown-5m-TEST1")
    assert row.symbol == "BTC"
    assert row.open_yes == 0.50
    assert row.close_yes == 0.58
    assert row.high_yes == 0.58
    assert row.n_points == 4
    assert row.resolved is False


def test_finalize_labels_resolved_interval_up():
    start = int(time.time()) - 600  # ya vencido
    market = {
        "slug": "btc-updown-5m-TEST2",
        "condition_id": "condTEST2",
        "start_date": start,
        "end_date": start + 300,
        "liquidity": 4000,
        "volume_24h": 9000,
    }
    coll.record_interval(market, _history(start, [0.50, 0.60, 0.66]))
    n = coll.finalize_resolved_intervals()
    assert n >= 1

    row = Crypto5mInterval.get(Crypto5mInterval.slug == "btc-updown-5m-TEST2")
    assert row.resolved is True
    assert row.result == "up"
    assert row.label == 1


def test_finalize_labels_resolved_interval_down():
    start = int(time.time()) - 600
    market = {
        "slug": "eth-updown-5m-TEST3",
        "condition_id": "condTEST3",
        "start_date": start,
        "end_date": start + 300,
    }
    coll.record_interval(market, _history(start, [0.50, 0.40, 0.32]))
    coll.finalize_resolved_intervals()

    row = Crypto5mInterval.get(Crypto5mInterval.slug == "eth-updown-5m-TEST3")
    assert row.symbol == "ETH"
    assert row.result == "down"
    assert row.label == 0


def test_recent_results_returns_resolved_only():
    start = int(time.time()) - 900
    for i in range(3):
        s = start + i * 300
        coll.record_interval(
            {
                "slug": f"sol-updown-5m-TEST{i}9",
                "condition_id": f"condTESTsol{i}",
                "start_date": s,
                "end_date": s + 300,
            },
            _history(s, [0.50, 0.55 if i % 2 == 0 else 0.45]),
        )
    coll.finalize_resolved_intervals()

    out = coll.recent_results(symbol="SOL", limit=10)
    assert all(r["symbol"] == "SOL" for r in out)
    assert all(r["result"] in ("up", "down") for r in out)


# ── Volatilidad realizada (señal para offsets adaptativos de la tripleta) ─────

def test_realized_vol_bps_higher_for_volatile_symbol():
    base = int(time.time()) - 5000
    # Símbolo VOLÁTIL: la prob YES oscila fuerte dentro de cada ventana.
    for i in range(6):
        s = base + i * 300
        coll.record_interval(
            {"slug": f"btc-updown-5m-TESTV{i}", "condition_id": f"cv{i}",
             "start_date": s, "end_date": s + 300},
            _history(s, [0.50, 0.62, 0.40, 0.58, 0.44]),
        )
    # Símbolo PLANO: apenas se mueve.
    for i in range(6):
        s = base + i * 300
        coll.record_interval(
            {"slug": f"eth-updown-5m-TESTF{i}", "condition_id": f"cf{i}",
             "start_date": s, "end_date": s + 300},
            _history(s, [0.50, 0.505, 0.498, 0.502, 0.50]),
        )

    vol_btc = coll.realized_vol_bps("BTC", lookback=6)
    vol_eth = coll.realized_vol_bps("ETH", lookback=6)
    assert vol_btc is not None and vol_eth is not None
    assert vol_btc > vol_eth          # volátil > plano
    assert vol_btc > 200              # swing fuerte → sigma alto en bps


def test_realized_vol_bps_none_without_data():
    assert coll.realized_vol_bps("NOEXISTE_SYM", lookback=6) is None


def test_realized_vol_bps_by_symbol_map():
    base = int(time.time()) - 4000
    for i in range(4):
        s = base + i * 300
        coll.record_interval(
            {"slug": f"sol-updown-5m-TESTM{i}", "condition_id": f"cm{i}",
             "start_date": s, "end_date": s + 300},
            _history(s, [0.50, 0.56, 0.47, 0.53]),
        )
    m = coll.realized_vol_bps_by_symbol(["SOL", "NOEXISTE"], lookback=6)
    assert "SOL" in m
    assert "NOEXISTE" not in m
    assert m["SOL"] > 0

