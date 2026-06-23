from src.core.orchestration.polymarket_triplet_model import (
    build_triplet_plan_for_market,
    build_live_hedge_arb_profile,
    evaluate_live_for_filled_leg,
    next_triplet_state,
    recommend_pre_event_offset_bps,
    should_force_hedge_near_close,
    TripletState,
    TripletEvent,
)


def _sample_market():
    return {
        "market_type": "crypto_5m_binary",
        "venue": "polymarket",
        "id": "m1",
        "condition_id": "c1",
        "question": "BTC above 105k in 5 minutes?",
        "yes_price": 0.50,
        "spread": 0.01,
        "minimum_tick_size": 0.01,
        "liquidity": 10000,
        "market_url": "https://polymarket.com/event/x",
    }


def test_build_triplet_plan_shapes_and_offsets():
    plan = build_triplet_plan_for_market(
        _sample_market(),
        offset_bps=40,
        order_usd=4,
        min_net_arb_bps=20,
        stop_loss_bps=35,
        maker_fee_bps=0,
        taker_fee_bps=25,
        expected_market_slippage_bps=15,
    )
    assert plan["model"].startswith("triplet_")
    legs = plan["pre_event_oco"]["legs"]
    assert len(legs) == 2
    assert legs[0]["outcome"] == "YES"
    assert legs[1]["outcome"] == "NO"
    assert legs[0]["post_only"] is True
    assert legs[1]["post_only"] is True
    assert plan["pre_event_oco"]["cancel_peer_on_first_fill"] is False
    assert plan["pre_event_oco"]["mode"] == "dual_fill_lock"
    assert plan["dual_fill_arb"]["arb_guaranteed_on_dual_fill"] is True


def test_dual_fill_lock_uses_share_matched_sizing():
    plan = build_triplet_plan_for_market(
        _sample_market(),
        offset_bps=40,
        order_usd=4,
        min_net_arb_bps=20,
        stop_loss_bps=35,
        maker_fee_bps=0,
        taker_fee_bps=25,
        expected_market_slippage_bps=15,
    )
    leg_a, leg_b = plan["pre_event_oco"]["legs"]
    shares_a = leg_a["size_usd"] / leg_a["price"]
    shares_b = leg_b["size_usd"] / leg_b["price"]
    assert abs(shares_a - shares_b) <= 1e-6


def test_can_disable_dual_fill_lock_and_use_first_fill_mode():
    plan = build_triplet_plan_for_market(
        _sample_market(),
        offset_bps=40,
        order_usd=4,
        min_net_arb_bps=20,
        stop_loss_bps=35,
        maker_fee_bps=0,
        taker_fee_bps=25,
        expected_market_slippage_bps=15,
        guarantee_dual_fill_arb=False,
    )
    assert plan["pre_event_oco"]["cancel_peer_on_first_fill"] is True
    assert plan["pre_event_oco"]["mode"] == "first_fill_then_live_hedge"


def test_evaluate_live_for_yes_filled_arb_exit():
    out = evaluate_live_for_filled_leg(
        filled_outcome="YES",
        entry_price=0.45,
        live_yes_price=0.48,
        min_net_arb_bps=20,
        stop_loss_bps=35,
        maker_fee_bps=0,
        taker_fee_bps=20,
        expected_market_slippage_bps=10,
    )
    assert out["decision"]["action"] == "arb_exit_market"


def test_evaluate_live_for_no_filled_stop_exit():
    # NO filled at 0.55 implies YES reference 0.45. If YES rises to 0.60 then NO falls to 0.40.
    out = evaluate_live_for_filled_leg(
        filled_outcome="NO",
        entry_price=0.55,
        live_yes_price=0.60,
        min_net_arb_bps=20,
        stop_loss_bps=30,
        maker_fee_bps=0,
        taker_fee_bps=20,
        expected_market_slippage_bps=10,
    )
    assert out["decision"]["action"] == "hedge_exit_market"


def test_triplet_state_machine_core_path_a_then_arb_exit():
    s = next_triplet_state(TripletState.PENDING_PRE_EVENT, TripletEvent.FILL_A)
    assert s == TripletState.ACTIVE_FILLED_A
    s = next_triplet_state(s, TripletEvent.LIVE_ARB_EXIT)
    assert s == TripletState.EXITED_ARB


def test_triplet_state_machine_timeout_cancel():
    s = next_triplet_state(TripletState.PENDING_PRE_EVENT, TripletEvent.TIMEOUT_NO_FILL)
    assert s == TripletState.CANCELLED


def test_recommend_offset_prefers_wider_in_high_activity_regime():
    market = {
        **_sample_market(),
        "spread": 0.03,
        "liquidity": 18000,
        "volume_24h": 300000,
        "day_change": 0.05,
        "value_score": 2.0,
    }
    rec = recommend_pre_event_offset_bps(
        market,
        target_leg_fill_prob=0.35,
        min_offset_bps=10,
        max_offset_bps=120,
        offset_step_bps=10,
    )
    assert rec["mode"] == "adaptive_probabilistic"
    assert rec["estimated_sigma_bps"] >= 120
    assert rec["selected_offset_bps"] >= 80


def test_recommend_offset_prefers_tighter_in_low_activity_regime():
    market = {
        **_sample_market(),
        "spread": 0.005,
        "liquidity": 700,
        "volume_24h": 500,
        "day_change": 0.001,
        "value_score": 0.1,
    }
    rec = recommend_pre_event_offset_bps(
        market,
        target_leg_fill_prob=0.35,
        min_offset_bps=10,
        max_offset_bps=120,
        offset_step_bps=10,
    )
    assert 12 <= rec["estimated_sigma_bps"] <= 120
    assert 10 <= rec["selected_offset_bps"] <= 60


def test_build_triplet_plan_can_use_adaptive_offsets():
    plan = build_triplet_plan_for_market(
        {
            **_sample_market(),
            "spread": 0.02,
            "liquidity": 12000,
            "volume_24h": 250000,
            "day_change": 0.03,
            "value_score": 1.8,
        },
        offset_bps=40,
        order_usd=4,
        min_net_arb_bps=20,
        stop_loss_bps=35,
        maker_fee_bps=0,
        taker_fee_bps=25,
        expected_market_slippage_bps=15,
        adaptive_offsets=True,
        target_leg_fill_prob=0.4,
        min_offset_bps=10,
        max_offset_bps=80,
        offset_step_bps=10,
    )
    assert plan["entry_model"]["mode"] == "adaptive_probabilistic"
    assert 10 <= plan["entry_model"]["selected_offset_bps"] <= 80


# ── Volatilidad REALIZADA como señal primaria del offset ─────────────────────

def _adaptive_kwargs(**over):
    base = dict(
        offset_bps=40, order_usd=4, min_net_arb_bps=20, stop_loss_bps=35,
        maker_fee_bps=0, taker_fee_bps=25, expected_market_slippage_bps=15,
        adaptive_offsets=True, target_leg_fill_prob=0.35,
        min_offset_bps=20, max_offset_bps=600, offset_step_bps=20,
    )
    base.update(over)
    return base


def test_realized_vol_widens_offset_vs_low_vol():
    """Mayor volatilidad realizada → órdenes MÁS amplias; menor vol → más estrechas."""
    high_vol = build_triplet_plan_for_market(
        {**_sample_market(), "realized_vol_bps": 500.0}, **_adaptive_kwargs()
    )
    low_vol = build_triplet_plan_for_market(
        {**_sample_market(), "realized_vol_bps": 60.0}, **_adaptive_kwargs()
    )
    hi = high_vol["entry_model"]
    lo = low_vol["entry_model"]
    # la señal viene de la volatilidad realizada, no de microestructura
    assert hi["sigma_source"] == "realized_vol"
    assert lo["sigma_source"] == "realized_vol"
    # más vol → mayor sigma → mayor offset → mayor distancia en precio
    assert hi["estimated_sigma_bps"] > lo["estimated_sigma_bps"]
    assert hi["selected_offset_bps"] > lo["selected_offset_bps"]
    assert hi["offset_cents"] > lo["offset_cents"]


def test_realized_vol_falls_back_to_heuristic_when_absent():
    """Sin volatilidad realizada → usa el heurístico de microestructura (compat)."""
    plan = build_triplet_plan_for_market(_sample_market(), **_adaptive_kwargs())
    assert plan["entry_model"]["sigma_source"] == "microstructure_heuristic"
    assert plan["entry_model"]["realized_vol_bps"] is None


def test_offset_cents_exposed_for_ui():
    plan = build_triplet_plan_for_market(
        {**_sample_market(), "realized_vol_bps": 300.0}, **_adaptive_kwargs()
    )
    em = plan["entry_model"]
    # delta de precio coherente con offset_bps sobre ref_yes=0.5
    expected_delta = 0.5 * (em["selected_offset_bps"] / 10000.0)
    assert abs(em["offset_price_delta"] - expected_delta) < 1e-6
    assert em["offset_cents"] == round(expected_delta * 100.0, 4)


def test_high_realized_vol_produces_wider_than_old_cap():
    """Con vol alta y el nuevo tope, la distancia supera el viejo límite (~0.6¢)."""
    plan = build_triplet_plan_for_market(
        {**_sample_market(), "realized_vol_bps": 600.0}, **_adaptive_kwargs()
    )
    # antes el max_offset_bps=120 topaba en ~0.6¢; ahora debe poder ensancharse más
    assert plan["entry_model"]["offset_cents"] > 0.6


# ── Market-hedge: arb NETO tras comisiones (taker) ───────────────────────────

def test_live_hedge_arb_profile_accounts_for_fees():
    prof = build_live_hedge_arb_profile(
        0.45, 0.45,
        taker_fee_bps=25, expected_market_slippage_bps=15, min_net_arb_bps=20,
    )
    # coste total = taker + slippage = 40 bps
    assert prof["total_cost_bps"] == 40.0
    # breakeven exit debe estar por ENCIMA del entry para cubrir fees + arb objetivo
    yes = prof["if_yes_filled"]
    assert yes["breakeven_exit_price"] > yes["entry_price"]
    # required gross = min_net(20) + cost(40) = 60 bps → exit = entry*(1+0.006)
    assert yes["breakeven_exit_price"] == round(0.45 * (1 + 60 / 10000.0), 6)
    assert prof["arb_feasible"] is True


def test_live_hedge_arb_infeasible_when_entry_too_high():
    # entry muy alto → breakeven se va por encima de 1.0 → arb no alcanzable
    prof = build_live_hedge_arb_profile(
        0.999, 0.999,
        taker_fee_bps=200, expected_market_slippage_bps=200, min_net_arb_bps=100,
    )
    assert prof["if_yes_filled"]["arb_feasible"] is False
    assert prof["arb_feasible"] is False


def test_plan_exposes_live_hedge_arb():
    plan = build_triplet_plan_for_market(
        _sample_market(),
        offset_bps=40, order_usd=4, min_net_arb_bps=20, stop_loss_bps=35,
        maker_fee_bps=0, taker_fee_bps=25, expected_market_slippage_bps=15,
    )
    lha = plan["live_hedge_arb"]
    assert lha["taker_fee_bps"] == 25
    assert lha["total_cost_bps"] == 40.0
    assert "if_yes_filled" in lha and "if_no_filled" in lha
    assert "arb_feasible" in lha


# ── Trigger TEMPORAL del market-hedge (cubrir cerca del cierre) ──────────────

def test_force_hedge_when_single_fill_near_close():
    # una sola pata llena + queda poco tiempo → forzar hedge
    assert should_force_hedge_near_close(
        seconds_to_end=30, leg_a_filled=True, leg_b_filled=False,
        done=False, force_seconds=60) is True


def test_no_force_when_dual_fill():
    # ambas patas llenas = arb bloqueado, NO hace falta hedge aunque quede poco
    assert should_force_hedge_near_close(
        seconds_to_end=10, leg_a_filled=True, leg_b_filled=True,
        done=False, force_seconds=60) is False


def test_no_force_when_no_fill():
    # ninguna pata llena → nada que cubrir todavía
    assert should_force_hedge_near_close(
        seconds_to_end=10, leg_a_filled=False, leg_b_filled=False,
        done=False, force_seconds=60) is False


def test_no_force_when_far_from_close():
    # single-fill pero aún hay tiempo → dejar que el peer limit intente llenarse
    assert should_force_hedge_near_close(
        seconds_to_end=180, leg_a_filled=True, leg_b_filled=False,
        done=False, force_seconds=60) is False


def test_no_force_when_done_or_unknown_time():
    assert should_force_hedge_near_close(
        seconds_to_end=10, leg_a_filled=True, leg_b_filled=False,
        done=True, force_seconds=60) is False
    assert should_force_hedge_near_close(
        seconds_to_end=None, leg_a_filled=True, leg_b_filled=False,
        done=False, force_seconds=60) is False


