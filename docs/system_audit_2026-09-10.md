# System and ML audit — 2026-09-10

This is a risk-and-evidence audit, not evidence of profitable trading. All
current strategy paths remain proposal or paper/shadow paths unless a separate,
explicit live-order action is authorized.

## Corrections applied

- Cross-ticker `MarketArb` plans now buy **YES** on both complementary player
  tickers. A same-ticker binary plan still uses YES/NO. Before this correction,
  the displayed second leg did not match the guaranteed-payout calculation.
- `TennisFModel` now actually applies its configured seasonal yElo blend. The
  helper and weight existed, but the blend was absent from the pipeline.
- Favourite shadow settlement now deducts a conservative entry fee for the
  fractional contract quantity implied by the stake. Shadow PnL is therefore
  less optimistic; it is still a proxy until fill-level fees are captured.
- Tennis ML validation uses the two most recent available calendar years and
  writes its research artifact retrained through the latest row. The prior
  fixed 2025/2026 folds would age out while leaving artifacts a year behind.
- The ML dataset hash includes the supervised `hit` label. Historical winner
  corrections therefore produce a new experiment identity and artifact path
  instead of silently reusing one built from a different target vector.
- `MarketArb` now fetches and caches Kalshi fee metadata for markets carrying
  a series ticker. Unknown or non-quadratic series fees veto the proposal;
  legacy market shapes without a series retain the documented conservative
  fallback until their builders expose the metadata.
- Fee arithmetic uses decimal rounding, including fractional contract counts,
  so an exact cent is not accidentally rounded to two cents by binary float
  noise in paper/shadow PnL or an arb threshold. Series metadata verifies the
  multiplier, not a pre-trade fill-level fee: Kalshi's rounding fees/rebates
  still require actual fills.
- The statistics-orientation completion path invokes the ML optimizer through
  the same disposable child process as the daily worker. It no longer leaves
  a full sklearn allocation resident in that long-running repair daemon.
- The data-quality API selects the newest fitted classifier rather than the
  trailing `evolutionary_rules` aggregate written in the same optimizer run,
  so its ML status is operationally meaningful.
- Empty historical coverage is an explicitly tested ML state: the optimizer
  returns an insufficient-data research report instead of crashing before the
  collection pipeline has accumulated a trainable sample.
- A challenger cannot be review-eligible until every required rolling
  validation fold is present. A single late-year fold no longer masquerades as
  two-period evidence when earlier history is below the training threshold.
- Tennis probability ticks now retain each leg's executable ask size, with
  historical migration 26 and additive archive compatibility. This starts the
  forward collection needed to veto proposals that exceed observable liquidity;
  it does not fabricate unavailable historical depth.

## ML findings and priority

1. **Do not promote the tennis optimizer from its current results.** Its label
   and feature construction are point-in-time oriented, but its strategy
   return uses TennisExplorer bookmaker odds as a proxy. It explicitly has no
   historical executable Kalshi price, fill, spread, liquidity or fee data.
   `eligible_for_review` must continue to mean manual research only, not
   deployable alpha. Persisted runs are labelled `research_proxy_pricing` so
   the dashboard and orientation worker do not mistake them for promotion.
2. **Temporal granularity needs improvement before model selection.** Historical
   tennis rows are ordered by date and ID. Features that update after a match
   can include a different match on the same calendar date, although no
   intraday ordering is proven. Capture or backfill reliable start timestamps,
   then build folds and features strictly before the decision timestamp.
3. **Fee metadata should come from Kalshi per series.** The generic fee helper
   is a conservative fallback, but it cannot know series-specific or maker
   schedules. Kalshi exposes `fee_type` and `fee_multiplier` on series and a
   `series/fee_changes` endpoint; cache that metadata with the market snapshot
   and reject an opportunity when its fee semantics are unknown.
4. **The fastest credible route to profit is data quality, not looser filters.**
   Retain all pre-decision Kalshi bid/ask sizes, order-book depth, fee metadata,
   exact timestamps and settlement/fill data. Then evaluate an execution-aware
   candidate chronologically, including partial fills and latency. Only compare
   a challenger with the baseline on the same eligible timestamps.

## Gates for any future promotion

- Out-of-sample chronological folds with no same-day ambiguity.
- Actual Kalshi executable entry price and per-fill fee for every evaluated
  trade; no bookmaker-odds substitution.
- Positive net PnL after fees, spread, slippage and missed/partial-fill model,
  with adequate trade count and drawdown/concentration limits.
- Shadow/live-readback reconciliation showing the stored entry and result agree
  with the exchange response.

## Primary references

- Kalshi series API exposes `fee_type` and `fee_multiplier`:
  https://docs.kalshi.com/api-reference/market/get-series
- Kalshi fee-change endpoint:
  https://docs.kalshi.com/api-reference/exchange/get-series-fee-changes
- Kalshi fixed-point quantities and rounding fees:
  https://docs.kalshi.com/getting_started/fixed_point_migration
  and https://docs.kalshi.com/getting_started/fee_rounding
