# Plan: Time-Oscillation Limit Arb ("Strategy 4")

Status: DRAFT — not started. Read-only proposal design only. No real order
placement is designed or implied anywhere in this document.

## 1. What this is, and how it differs from dwell-time (Strategy 1)

Dwell-time (Strategy 1, `src/core/strategies/event_volatility/` +
`limit_order_formation.py`) proposes two resting limit levels derived from a
**category-specific probabilistic model** (tennis Markov, football
Dixon-Coles/Poisson, basketball/baseball parametric win-prob, weather
climatology). It answers "where does the *true* probability tend to sit,
model-side" and only supports categories with a registered model (sports +
weather). It is explicitly a same-market, same-platform strategy.

Time-oscillation limit arb is a **different signal source**: instead of a
probabilistic win model, it uses **observed historical price volatility**
(via `/prices-history`-style endpoints) to estimate how far and how often a
market's own price actually swings around its current level, independent of
having any category model at all. This means it can cover categories
dwell-time cannot reach today (crypto 5/15-min markets, arbitrary
non-sports finite events, anything without a Markov/Poisson/parametric
model) as long as the market has price history.

**Recommendation: build it as a separate Strategy 4, not an extension of
dwell-time.** Reasons:
- Different inputs (price-history volatility vs. probabilistic model) and
  different applicability (any liquid market vs. 4 sports categories) — a
  shared abstraction would end up as a thin "either mode" wrapper with no
  real code reuse in the estimation step itself.
- `propose_limit_levels()`'s core logic (rank bands, try combined-price
  pairs, subtract fees) already *does* generalize as a shared **downstream**
  step — see Phase 2 below, which reuses it by feeding it synthetic
  "dwell bands" derived from volatility instead of from a category model.
  That reuse point is the right level of sharing: same *level-selection and
  fee-netting* code, different *band-estimation* code.
- Keeping it separate avoids overloading `_SUPPORTED_CATEGORIES` semantics
  (dwell-time's category dispatch means "has a probabilistic model"; time-
  oscillation's applicability test is "has enough price-history depth/
  liquidity", an orthogonal condition).

## 2. Reusable infrastructure (confirmed by reading the code)

| Piece | File | Reuse plan |
|---|---|---|
| `DwellBand` dataclass + `DwellTimeModel` protocol | `event_volatility/base.py` | Reuse the `DwellBand` shape (low, high, side, expected_time_fraction) as the output contract for the new volatility estimator, so it can be fed into a copy/adaptation of `propose_limit_levels`'s band-pairing logic without inventing a new shape. |
| `propose_limit_levels()` pairing/fee-netting logic | `limit_order_formation.py` | Reuse the "rank bands by expected_time_fraction, try combined pairs by rank-sum, gate on price_a+price_b<1.0, net fees via `platform_fees.leg_fee_pct`" algorithm — extract or duplicate as `propose_oscillation_levels()`. See Phase 2 — decide extract vs. duplicate. |
| `simulate_limit_fill()` / `propose_and_check_fillable()` | `fill_simulator.py`, `limit_order_formation.py` | Reuse AS-IS. Fillability checking against real CLOB depth per capital tier is identical for this strategy — no new simulator code needed. |
| `platform_fees.leg_fee_pct()` | `src/core/orchestration/platform_fees.py` | Reuse AS-IS for net-edge calculation. |
| `DwellTimeProposal` model + `find_dwell_time_liquidity_overlaps()` | `src/database/models/domain.py`, `scanning.py` | Reuse pattern, NOT the same table (see Phase 4) — same shape of "read-only proposal row" + "cross-strategy overlap detector", needs its own table and its own overlap detector generalized to N strategies. |
| `run_dwell_time_strategy_worker()` loop shape | `workers.py:456` | Reuse the loop skeleton (poll events, build context, call proposer, persist, sleep) for the new worker. |
| Polymarket `/prices-history` endpoint | confirmed public, no client wiring yet | NEW: needs a client method (Phase 1). Kalshi/legacy archived-platform equivalents unconfirmed — treat as "Polymarket only" until Priya confirms others. |

## 3. Phased plan

### Phase 1 — Volatility estimator from price history (NEW code)
**Files:** new `src/core/strategies/oscillation/` package (`volatility_model.py`,
`__init__.py`), extend `src/scrapers/polymarket_client.py` with a
`get_price_history(token_id, interval)` method wrapping `/prices-history`.
**What it does:** fetch historical price series for a token, compute a
realized-volatility / oscillation-band estimate — e.g. bucket historical
price into probability bands the same way dwell-time bands work, but derived
from **time-in-band over the lookback window** rather than a forward
probabilistic model. Output: `list[DwellBand]`-shaped bands per side (yes/no
are just `1-p`, so one series suffices for a binary market).
**Decision needed (flag for Sara):** how much lookback window and what
volatility regime (recent 1h vs. full event history) is appropriate — this
is a genuine calibration question, not an engineering one. Recommend
starting with a fixed lookback (e.g. last N hours or last N% of event
duration) and letting Vesper audit the choice before trusting output.
**Parallelizable:** yes, independent of Phase 2 once the `DwellBand` output
contract is agreed (can literally stub Phase 2 against a hand-built bands
list while Phase 1 is in progress).

### Phase 2 — Level formation + fee netting (mostly REUSE)
**Files:** new `src/core/strategies/oscillation/level_formation.py` (or,
if Sara prefers real code sharing, refactor `limit_order_formation.py`'s
inner pairing loop into a shared private helper both modules call — decide
during implementation, not up front, since it's a small function).
**What it does:** takes Phase 1's `DwellBand` list for a market, runs the
same rank-by-time-fraction / try-pairs-under-1.0 / net-fees logic dwell-time
uses, returns the same `{"opportunity", "sides", "implied_edge",
"implied_edge_gross", "reason"}` shape dwell-time already returns, so
downstream code (fillability check, persistence, overlap detection) needs
minimal new branching.
**Depends on:** Phase 1's output contract (can start once that's frozen,
even before Phase 1 is fully implemented).

### Phase 3 — Fillability check + worker loop (REUSE `fill_simulator`, NEW worker)
**Files:** `src/core/orchestration/workers.py` (new
`run_oscillation_strategy_worker()`, modeled on
`run_dwell_time_strategy_worker()` at line 456), calling
`fill_simulator.simulate_limit_fill()` unchanged.
**What it does:** for each candidate market with sufficient price history,
call Phase 1+2, then check real CLOB depth per capital tier exactly like
`propose_and_check_fillable()` does, and log a proposal row (see Phase 4).
**Depends on:** Phases 1 and 2 complete. Not parallelizable with them, but
the worker skeleton itself (event iteration, sleep loop, logging) can be
scaffolded in parallel and left with a stub call.

### Phase 4 — Persistence + cross-strategy overlap detection (NEW table, adapted detector)
**Files:** `src/database/models/domain.py` (new `OscillationProposal` model,
same shape as `DwellTimeProposal` — timestamp, fixture, category/market id,
side, proposed_price, expected_time_fraction, implied_edge_pct/gross,
book, external_id, fillable_at_tiers), `src/core/orchestration/scanning.py`
(generalize `find_dwell_time_liquidity_overlaps()` into a function that
takes a list of proposal-row sources rather than hardcoding
`DwellTimeProposal`, so it detects overlaps across Strategy 1, this new
Strategy 4, AND Strategies 2/3 in one pass).
**Critical design requirement (see Risks below):** this generalized
detector must be wired in BEFORE Strategy 4 goes live, not after — the
dwell-time/cross-platform overlap bug was found and fixed reactively once
already; do not repeat that pattern with a 4th strategy sharing the same
liquidity pool.
**Depends on:** Phase 3's proposal shape existing. Independent of Phase 1/2's
internals (only needs the output contract), so schema work + detector
generalization can start in parallel with Phase 1/2 once the contract is
frozen.

### Phase 5 — Tests + Vesper calibration audit (parallelizable with nothing — comes last)
**Files:** `tests/test_oscillation_*.py` mirroring
`tests/test_dwell_time_worker.py` / `tests/test_limit_order_formation.py`
structure; dispatch Vesper specifically to audit (a) the volatility/lookback
choice from Phase 1, (b) that `implied_edge` is genuinely fee-netted and
never overstates realizable profit, (c) the directional-risk framing in
Risks section below is actually surfaced in the proposal output (e.g. a
`one_sided_fill_risk` field), not just in this document.

## 4. Design risks (must be resolved, not just noted)

**A. Shared-liquidity double-counting across 4 strategies now, not 2.**
The dwell-time/cross-platform bug (fixed, see
`find_dwell_time_liquidity_overlaps`) was a 2-strategy problem. Adding
Strategy 4 makes it a genuine N-strategy problem: the same
(book, external_id) token could simultaneously appear in a dwell-time
proposal, an oscillation proposal, AND a cross-platform/same-market
opportunity. Phase 4's generalized detector is the mitigation, but it is
still (like today's) a **detector, not a dedup/claim system** — none of the
4 strategies currently reserves capital, so nothing "wins" a contested leg
yet. **This is fine only as long as all 4 stay proposal-only.** The moment
any strategy is authorized to size real capital, a real cross-strategy
claiming/priority system (extending `_dedupe_arbs_by_shared_leg`'s
ROI-ordered claim pattern to be strategy-aware) becomes mandatory BEFORE
that authorization — flagged as a hard blocking decision point below.

**B. Directional risk if only one leg fills (the core risk unique to this
strategy).** Dwell-time already carries this risk in principle, but
time-oscillation is *explicitly* designed around one leg filling now and
the other later when price swings back — meaning there's real, sometimes
long, exposure between fills where the position is one-sided and directional
(not the flat/hedged risk profile the word "arb" implies while unfilled).
Mitigation to design in Phase 2/3, not deferred:
  - Every proposal must carry an explicit `one_sided_fill_risk` estimate:
    given the observed volatility, how likely / how long until the opposite
    leg's band is revisited (derivable straight from Phase 1's
    `expected_time_fraction` and the historical revisit frequency, not a new
    model).
  - A proposal must be flagged (or excluded) if the event's remaining
    duration is too short for the opposite band to plausibly be revisited
    (e.g. near event close) — a real fillability trap: filling one leg with
    the market at its terminal, no-oscillation-left value.
  - Since nothing is actually executed, this is a **modeling honesty**
    requirement on the output — the proposal must not imply "both legs will
    fill" without qualifying that a single-fill outcome is a live,
    quantified directional exposure, not a rare edge case.

## 5. Explicit decision points for Sara / the user (do not build past these without sign-off)

1. **Scope confirmation:** this entire strategy, like the other 3, is
   read-only detection/proposal only. No real order placement is proposed
   anywhere in this plan, and none should be added without a separate,
   explicit authorization completely outside this plan's scope. Confirm
   before Phase 1 starts.
2. **Separate strategy vs. dwell-time extension** — this doc recommends
   separate (Section 1). Sara should confirm or override before Phase 1/2
   file layout is finalized, since it determines whether Phase 2 extracts
   shared code or duplicates it.
3. **Volatility lookback window / regime choice (Phase 1)** — a genuine
   calibration decision, not something to hardcode unilaterally; route
   through Vesper before trusting any live output.
4. **Kalshi/legacy archived-platform price-history support** — unconfirmed whether
   equivalent endpoints exist publicly; until Priya confirms, this strategy
   should be scoped Polymarket-only. Confirm before Phase 1 assumes
   multi-platform.
5. **Cross-strategy capital-claiming system** — Risk A above: must be built
   BEFORE any of the 4 strategies is authorized to size/reserve real
   capital. This plan does not build it (out of scope, proposal-only), but
   flags it as a hard prerequisite for any future execution authorization,
   not an optional nice-to-have.
