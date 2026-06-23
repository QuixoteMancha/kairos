from importlib import import_module

msp = import_module('src.web.blueprints.multisport_bp')


def test_triplet_market_symbol_detects_btc_eth():
    assert msp._triplet_market_symbol({'question': 'Will Bitcoin be above $110k in 5 min?'}) == 'BTC'
    assert msp._triplet_market_symbol({'question': 'Will Ethereum be above $6k in 5 min?'}) == 'ETH'


def test_triplet_available_markets_deduplicates_condition_id():
    plans = [
        {'condition_id': 'c1', 'question': 'Will Bitcoin be above in 5 min?', 'market_url': 'u1'},
        {'condition_id': 'c1', 'question': 'Duplicate should be dropped', 'market_url': 'u2'},
        {'condition_id': 'c2', 'question': 'Will Ethereum be above in 5 min?', 'market_url': 'u3'},
    ]
    out = msp._triplet_available_markets(plans)
    assert [x['condition_id'] for x in out] == ['c1', 'c2']


def test_select_active_triplet_plan_prefers_requested_condition():
    plans = [
        {'condition_id': 'btc', 'plan_id': 'p1'},
        {'condition_id': 'eth', 'plan_id': 'p2'},
    ]
    out = msp._select_active_triplet_plan(plans, requested_condition_id='eth')
    assert out['plan_id'] == 'p2'


def test_select_by_symbol_rolls_to_current_live_interval():
    import time
    from datetime import datetime, timezone

    now = int(time.time())

    def _iso(ts):
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace('+00:00', 'Z')

    plans = [
        # BTC vencido (no debe elegirse)
        {'condition_id': 'btc-old', 'plan_id': 'btc1',
         'question': 'Bitcoin Up or Down', 'reference': {'end_date': _iso(now - 120)}},
        # BTC vigente más próximo a cerrar (debe elegirse)
        {'condition_id': 'btc-now', 'plan_id': 'btc2',
         'question': 'Bitcoin Up or Down', 'reference': {'end_date': _iso(now + 180)}},
        # BTC futuro lejano (no es el vigente)
        {'condition_id': 'btc-next', 'plan_id': 'btc3',
         'question': 'Bitcoin Up or Down', 'reference': {'end_date': _iso(now + 480)}},
        # ETH (otro símbolo)
        {'condition_id': 'eth-now', 'plan_id': 'eth1',
         'question': 'Ethereum Up or Down', 'reference': {'end_date': _iso(now + 200)}},
    ]
    out = msp._select_active_triplet_plan(plans, requested_symbol='BTC')
    assert out['plan_id'] == 'btc2'


def test_select_by_symbol_falls_back_to_latest_when_all_expired():
    import time
    from datetime import datetime, timezone

    now = int(time.time())

    def _iso(ts):
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace('+00:00', 'Z')

    plans = [
        {'condition_id': 'btc-a', 'plan_id': 'a', 'question': 'Bitcoin Up or Down',
         'reference': {'end_date': _iso(now - 600)}},
        {'condition_id': 'btc-b', 'plan_id': 'b', 'question': 'Bitcoin Up or Down',
         'reference': {'end_date': _iso(now - 120)}},
    ]
    out = msp._select_active_triplet_plan(plans, requested_symbol='BTC')
    assert out['plan_id'] == 'b'  # el más reciente


# ── Resumen de órdenes por tripleta (panel derecho) ──────────────────────────

def _plan_with_legs():
    return {
        'condition_id': 'btc-x', 'plan_id': 'p', 'question': 'Bitcoin Up or Down',
        'pre_event_oco': {'legs': [
            {'outcome': 'YES', 'price': 0.47, 'size_usd': 4.0},
            {'outcome': 'NO', 'price': 0.47, 'size_usd': 4.0},
        ]},
        'dual_fill_arb': {'gross_locked_pnl_usd': 0.30, 'dual_fill_total_cost_usd': 4.5},
        'live_hedge_arb': {'arb_feasible': True, 'total_cost_bps': 40.0, 'min_net_arb_bps': 20.0},
    }


def test_order_card_limits_active_hedge_idle():
    state = {
        'condition_id': 'btc-x', 'plan_id': 'p', 'plan': _plan_with_legs(),
        'events': [],
        'status': {'leg_a_filled': False, 'leg_b_filled': False,
                   'leg_a_cancelled': False, 'leg_b_cancelled': False,
                   'done': False, 'result': 'running'},
    }
    card = msp._triplet_order_card(state)
    assert card['symbol'] == 'BTC'
    assert card['legs']['A']['status'] == 'active'
    assert card['legs']['B']['status'] == 'active'
    assert card['legs']['C']['status'] == 'idle'
    assert card['legs']['C']['arb_feasible'] is True


def test_order_card_single_fill_arms_hedge():
    state = {
        'condition_id': 'btc-x', 'plan_id': 'p', 'plan': _plan_with_legs(),
        'events': [],
        'status': {'leg_a_filled': True, 'leg_b_filled': False,
                   'leg_a_cancelled': False, 'leg_b_cancelled': False,
                   'done': False, 'result': 'running'},
    }
    card = msp._triplet_order_card(state)
    assert card['legs']['A']['status'] == 'filled'
    assert card['legs']['C']['status'] == 'active'   # hedge armado


def test_order_card_dual_fill_hedge_not_needed():
    state = {
        'condition_id': 'btc-x', 'plan_id': 'p', 'plan': _plan_with_legs(),
        'events': [],
        'status': {'leg_a_filled': True, 'leg_b_filled': True,
                   'leg_a_cancelled': False, 'leg_b_cancelled': False,
                   'done': True, 'result': 'dual_fill_locked_arb'},
    }
    card = msp._triplet_order_card(state)
    assert card['legs']['C']['status'] == 'not_needed'


def test_order_card_hedge_exited_reads_net_bps():
    state = {
        'condition_id': 'btc-x', 'plan_id': 'p', 'plan': _plan_with_legs(),
        'events': [{'type': 'live_market_exit', 'leg': 'C', 'net_bps': 33.0}],
        'status': {'leg_a_filled': True, 'leg_b_filled': False,
                   'leg_a_cancelled': False, 'leg_b_cancelled': False,
                   'done': True, 'result': 'arb_exit_market'},
    }
    card = msp._triplet_order_card(state)
    assert card['legs']['C']['status'] == 'exited'
    assert card['legs']['C']['net_bps'] == 33.0


# ── Eventos de colocación + PnL en el order card ─────────────────────────────

def test_order_card_exposes_paper_pnl_when_resolved():
    state = {
        'condition_id': 'btc-x', 'plan_id': 'p', 'plan': _plan_with_legs(),
        'events': [],
        'status': {'leg_a_filled': True, 'leg_b_filled': True,
                   'leg_a_cancelled': False, 'leg_b_cancelled': False,
                   'done': True, 'result': 'dual_fill_locked_arb'},
        'paper_pnl': {'pnl_usd': 0.30, 'cost_usd': 4.5},
    }
    card = msp._triplet_order_card(state)
    assert card['paper_pnl_usd'] == 0.30
    assert card['paper_roi_pct'] == round(0.30 / 4.5 * 100, 2)


def test_record_triplet_resolution_writes_ledger():
    from src.database.database import init_db
    from src.core.orchestration import polymarket_paper_ledger as pl
    init_db()
    pl.reset_ledger()
    state = {
        'condition_id': 'cond-rec-1', 'plan_id': 'p', 'plan': _plan_with_legs(),
        'last_eval_ts': 12345,
        'status': {'done': True, 'result': 'dual_fill_locked_arb'},
    }
    msp._record_triplet_resolution(state, 'dual_fill_locked_arb', None)
    s = pl.get_summary()
    assert s['trades'] == 1
    assert 'paper_pnl' in state   # cacheó el pnl en el state
    pl.reset_ledger()

