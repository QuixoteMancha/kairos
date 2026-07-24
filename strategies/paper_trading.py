"""
Paper (simulated) trading with Kelly-fractional position sizing, wired
against LimitArb signals (see strategies/limit_arb.py and
database/accounting/{papertrade,paperaccount}.py).

HARD RED LINE -- READ FIRST: Kairos has NO real order execution anywhere.
Everything in this module is simulated money (PaperAccount.bankroll_usd,
starts at $200 fake USDC) -- it never touches Kalshi or any real account.
This is purely a data-collection mechanism to calibrate Kelly sizing
against real market outcomes over time.

DESIGN NOTE -- where these helpers live: this module talks to
PaperTrade/PaperAccount directly (init_db() + db.connection_context()),
the same low-level pattern database/accounting/writer.py uses for
LimitArbSignal, rather than adding more functions to writer.py. Kept
separate because this is a distinct concern (position sizing + P&L
bookkeeping, not just signal logging) with its own module-level constants
and formulas that the user specified explicitly -- bundling it into
writer.py would mix two different responsibilities in one file.

All formulas below (Kelly sizing, calibration recalibration, proxy
p_success) are as explicitly specified/confirmed by the user -- do not
change without checking back first.
"""

from datetime import datetime

from database.db import db, init_db
from database.accounting.papertrade import PaperTrade
from database.accounting.paperaccount import PaperAccount
from outer_apis.kalshi_fees import order_fee

STARTING_BANKROLL_USD = 200.0
KELLY_FRACTION_MULTIPLIER = 0.5   # half-Kelly
MAX_POSITION_FRACTION = 0.10      # never more than 10% of available equity per signal
MIN_POSITION_USD = 1.0            # below this, don't open a paper trade
P_SUCCESS_FLOOR = 0.50
P_SUCCESS_CEILING = 0.90
CALIBRATION_PRIOR_PSEUDOCOUNT = 30
MIN_SAMPLES_FOR_RECALIBRATION = 20
CALIBRATION_FACTOR_MIN = 0.5
CALIBRATION_FACTOR_MAX = 1.5


def compute_p_success_proxy(raw_edge: float, avg_volatility: float) -> float:
    """confidence_ratio = raw_edge / (raw_edge + avg_volatility), mapped to
    p = 0.5 + 0.4*confidence_ratio, clipped to [P_SUCCESS_FLOOR, P_SUCCESS_CEILING].

    Fail-closed: if raw_edge + avg_volatility == 0 (shouldn't happen given
    MIN_VOLATILITY/MIN_RETURN_OPPORTUNITY gating in limit_arb.py, but
    guarded here anyway), return P_SUCCESS_FLOOR -- never overconfident
    from an absence of data."""
    denom = raw_edge + avg_volatility
    if denom == 0:
        return P_SUCCESS_FLOOR
    confidence_ratio = raw_edge / denom
    p = 0.5 + 0.4 * confidence_ratio
    return max(P_SUCCESS_FLOOR, min(P_SUCCESS_CEILING, p))


def get_available_equity() -> float:
    """bankroll_usd from PaperAccount MINUS the sum of position_size_usd of
    all PaperTrade rows with status='open' (capital already committed)."""
    init_db()
    with db.connection_context():
        account, _ = PaperAccount.get_or_create(id=1, defaults={'bankroll_usd': STARTING_BANKROLL_USD})
        committed = (PaperTrade
                     .select()
                     .where(PaperTrade.status == 'open'))
        committed_sum = sum(t.position_size_usd for t in committed)
        return account.bankroll_usd - committed_sum


def compute_kelly_position_size(p_success_proxy: float, return_opportunity: float,
                                  price_adjustment: float, available_equity: float,
                                  calibration_factor: float) -> tuple[float, float, float]:
    """Returns (position_size_usd, kelly_fraction_used, p_success_final).

    Fail-closed: if available_equity <= 0, returns (0.0, 0.0, p_success_final)
    -- never bet with zero/negative equity."""
    p_success_final = p_success_proxy * calibration_factor
    p_success_final = max(P_SUCCESS_FLOOR, min(P_SUCCESS_CEILING, p_success_final))

    if available_equity <= 0:
        return 0.0, 0.0, p_success_final

    b = return_opportunity / max(price_adjustment, 1e-6)
    f_kelly = max(p_success_final - (1 - p_success_final) / b, 0.0)
    kelly_fraction_used = min(f_kelly * KELLY_FRACTION_MULTIPLIER, MAX_POSITION_FRACTION)
    position_size_usd = round(available_equity * kelly_fraction_used, 2)
    return position_size_usd, kelly_fraction_used, p_success_final


def open_paper_trade(*, signal_id: int, market_id: str, case: str, avg_volatility: float,
                      raw_edge: float, return_opportunity: float, price_adjustment: float,
                      market_leg_price: float, category: str | None) -> None:
    """Opens a simulated paper position for a LimitArb signal, if the
    computed Kelly position size clears MIN_POSITION_USD. Fail-closed: if
    the fee can't be computed (order_fee returns None) or the size is too
    small, no trade is opened -- nothing fabricated. Exceptions propagate
    (the call site in strategies/limit_arb.py wraps this in its own
    try/except, so we don't duplicate the fail-closed swallow here)."""
    init_db()
    with db.connection_context():
        account, _ = PaperAccount.get_or_create(id=1, defaults={'bankroll_usd': STARTING_BANKROLL_USD})
        calibration_factor = account.kelly_calibration_factor

        committed = (PaperTrade
                     .select()
                     .where(PaperTrade.status == 'open'))
        committed_sum = sum(t.position_size_usd for t in committed)
        available_equity = account.bankroll_usd - committed_sum

        p_success_proxy = compute_p_success_proxy(raw_edge, avg_volatility)
        position_size_usd, kelly_fraction_used, _p_success_final = compute_kelly_position_size(
            p_success_proxy=p_success_proxy,
            return_opportunity=return_opportunity,
            price_adjustment=price_adjustment,
            available_equity=available_equity,
            calibration_factor=calibration_factor,
        )

        if position_size_usd < MIN_POSITION_USD:
            return

        market_leg_fee = order_fee(market_leg_price, category=category)
        if market_leg_fee is None:
            return

        PaperTrade.create(
            signal_id=signal_id,
            market_id=market_id,
            case=case,
            position_size_usd=position_size_usd,
            p_success_proxy_at_open=p_success_proxy,
            kelly_fraction_used=kelly_fraction_used,
            market_leg_price=market_leg_price,
            market_leg_fee=market_leg_fee,
            status='open',
        )


def close_paper_trade(*, signal_id: int, exit_reason: str,
                       exit_locked_profit_pct: float | None,
                       return_opportunity: float,
                       limit_leg_price: float | None = None,
                       category: str | None = None,
                       close_market_leg_price: float | None = None) -> None:
    """Closes the open PaperTrade for this signal_id, if one exists (if
    not -- e.g. the signal never cleared MIN_POSITION_USD at open time --
    this is a silent no-op, not an error). Computes realized_pnl_usd
    according to exit_reason -- see module docstring / task spec for the
    per-reason formulas. Updates PaperAccount.bankroll_usd (+= realized
    pnl, treating None as 0.0 for the bankroll update only -- the trade
    row itself keeps realized_pnl_usd=None so it's clear no real number
    was measured)."""
    init_db()
    with db.connection_context():
        trade = (PaperTrade
                 .select()
                 .where((PaperTrade.signal_id == signal_id) & (PaperTrade.status == 'open'))
                 .first())
        if trade is None:
            return

        close_reason = exit_reason
        realized_pnl_usd = None

        if exit_reason == 'limit_filled':
            # Both legs filled -- profit = position_size_usd * return_opportunity,
            # minus the limit leg's fee if computable. If not computable,
            # subtract 0 -- a known conservative-optimistic approximation
            # (documented, not fatal).
            limit_fee = 0.0
            if limit_leg_price is not None and category is not None:
                computed_fee = order_fee(limit_leg_price, category=category)
                if computed_fee is not None:
                    limit_fee = computed_fee
            realized_pnl_usd = trade.position_size_usd * return_opportunity - limit_fee
        elif exit_reason in ('timeout', 'trailing_stop', 'time_decay'):
            # Only the market leg filled -- profit = position_size_usd *
            # exit_locked_profit_pct. Can be negative or None (never
            # fabricated if None).
            if exit_locked_profit_pct is not None:
                realized_pnl_usd = trade.position_size_usd * exit_locked_profit_pct
            else:
                realized_pnl_usd = None
        elif exit_reason == 'market_gone':
            # Added 2026-07-23: if the caller could reconstruct a real
            # terminal price for the market leg (via
            # KalshiAPI.get_market_any_status's 'result' field, see
            # workers/limitarb_monitor_worker.py), exit_locked_profit_pct
            # is a REAL measured return, not a guess -- use it instead of
            # the old breakeven assumption. Only fall back to the
            # unresolved breakeven when truly nothing is known (neither a
            # live quote nor a settlement result was recoverable for
            # either leg).
            if exit_locked_profit_pct is not None:
                close_reason = 'market_gone'
                realized_pnl_usd = trade.position_size_usd * exit_locked_profit_pct
            else:
                close_reason = 'market_gone_unresolved'
                realized_pnl_usd = 0.0

        trade.status = 'closed'
        trade.closed_at = datetime.utcnow()
        trade.close_reason = close_reason
        trade.realized_pnl_usd = realized_pnl_usd
        if close_market_leg_price is not None:
            trade.close_market_leg_price = close_market_leg_price
        trade.save()

        account, _ = PaperAccount.get_or_create(id=1, defaults={'bankroll_usd': STARTING_BANKROLL_USD})
        # None treated as 0.0 for bankroll bookkeeping purposes only -- the
        # trade row's realized_pnl_usd stays None, so it's clear this was
        # never a real measured gain, just a neutral accounting choice.
        account.bankroll_usd += (realized_pnl_usd if realized_pnl_usd is not None else 0.0)
        account.updated_at = datetime.utcnow()
        account.save()


def recalibrate_kelly_factor() -> dict:
    """Recalibrates PaperAccount.kelly_calibration_factor against closed
    PaperTrade outcomes (excluding 'market_gone_unresolved' -- an
    uncertain result, not a real one). See module docstring for the
    formula, confirmed by the user."""
    init_db()
    with db.connection_context():
        closed_trades = list(PaperTrade
                              .select()
                              .where((PaperTrade.status == 'closed') &
                                     (PaperTrade.close_reason != 'market_gone_unresolved')))

        n = len(closed_trades)
        if n < MIN_SAMPLES_FOR_RECALIBRATION:
            return {'recalibrated': False, 'n': n}

        # Classifiable trades: realized_pnl_usd must be known (not None)
        # to count as win/loss -- ignored from both numerator and
        # denominator otherwise, can't classify without the real number.
        classifiable = [t for t in closed_trades if t.realized_pnl_usd is not None]
        n_classifiable = len(classifiable)
        if n_classifiable == 0:
            # Nothing classifiable -- fail-closed, don't recalibrate off zero signal.
            return {'recalibrated': False, 'n': n}

        wins = sum(1 for t in classifiable if t.realized_pnl_usd > 0)
        empirical_p_win = wins / n_classifiable
        avg_proxy_p = sum(t.p_success_proxy_at_open for t in classifiable) / n_classifiable

        raw_ratio = empirical_p_win / avg_proxy_p if avg_proxy_p != 0 else 1.0

        calibration_factor = (CALIBRATION_PRIOR_PSEUDOCOUNT * 1.0 + n_classifiable * raw_ratio) / \
                              (CALIBRATION_PRIOR_PSEUDOCOUNT + n_classifiable)
        calibration_factor = max(CALIBRATION_FACTOR_MIN, min(CALIBRATION_FACTOR_MAX, calibration_factor))

        account, _ = PaperAccount.get_or_create(id=1, defaults={'bankroll_usd': STARTING_BANKROLL_USD})
        account.kelly_calibration_factor = calibration_factor
        account.last_recalibrated_at = datetime.utcnow()
        account.updated_at = datetime.utcnow()
        account.save()

        return {
            'recalibrated': True,
            'n': n_classifiable,
            'empirical_p_win': empirical_p_win,
            'avg_proxy_p': avg_proxy_p,
            'calibration_factor': calibration_factor,
        }
