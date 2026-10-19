# Station probability research

The weather research path reads immutable captured evidence. It is separate from
the live fair-price fallback and does not submit orders. Capture now supports GFS
seamless, ECMWF IFS 0.25 and HRRR, NWS surface observations and Kalshi orderbooks.
These are multiple deterministic models, with correlated errors, not independent
members of a physical ensemble.

An explicit uncalibrated scenario:

```sh
python -m scripts.weather_probability KXHIGHNY-26JUL10-T90 \
  --as-of 2026-07-10T03:00:00+00:00 --sigma-f 2.2 --rounding nearest_integer
```

Use a ticker and timestamp actually present in your archive. The example is not
a claim of available data. No migrations, capture, orders or database writes are
performed. The command needs access to the existing historical database.

The report returns `scenario`, `calibrated_research` or `abstain`, with reasons, evidence IDs, UTC window,
model centers and YES/NO probabilities. It uses the contract revision available
at `--as-of`, never its eventual result or mutable latest rules. Only unambiguous
NWS CLI station rules are supported; TWC contracts are rejected. Station metadata
is the stored registry, not a historical station-configuration reconstruction.

CLI days are 24 hours in local standard time, including during DST. All 24 hourly
Fahrenheit samples must exist at the requested station coordinates. The maximum
or minimum is a grid forecast proxy, not an observed station settlement value.
The latest available revision per model is selected, so repeat GFS downloads
cannot masquerade as independent ensemble members. A Gaussian mixture supports
multiple model identifiers. Incomplete/stale models are excluded with reasons;
an old revision never replaces an incomplete latest one. Short HRRR coverage
therefore does not block complete GFS/ECMWF forecasts. At least one usable model
is required. The default maximum model range is 5 F and forecast age is 6 h;
these are research guardrails, not empirically optimized thresholds.

`--sigma-f` declares an **uncalibrated** residual sigma. Alternatively,
`--training-data` learns from earlier labelled station-days. Rounding remains an
explicit scenario assumption that must be checked against each contract's rules:
`nearest_integer` maps an assumed continuous temperature into integer Fahrenheit
bins; `continuous` applies bounds directly. Greater/less comparisons, inclusive
comparisons and inclusive between bounds are supported; custom strikes abstain.
No rules-text inference of rounding is attempted. These probabilities do not
establish an edge or profitability.

Missing or future publication/capture times, stale forecasts, incomplete days,
wrong units, unsupported sources, closed contracts and excessive model
disagreement cause abstention. Historical runs with NULL `available_at` remain
excluded. Intraday evaluation requires an NWS surface observation at the exact
station with verified quality (`V`, also accepting the prefixed `qc:V`), a capture
time before the decision, and age at most 90 minutes. It adjusts remaining hourly
forecasts by an observation innovation capped at 6 F and decaying over 3 hours.
It discards past forecast peaks and interpolates the decision-time temperature.
The observed high-minus-1-F or low-plus-1-F constrains the distribution, including
the probability mass at that bound. These are provisional-sample research
assumptions; the tolerance and decay are not learned or a guarantee against
measurement/revision errors. Sparse observations are not complete daily extrema.

## Dataset and chronological experiments

```sh
python -m scripts.weather_research dataset --as-of 2026-09-13T23:00:00Z \
  --rounding nearest_integer --out /tmp/weather-dataset.json
python -m scripts.weather_research evaluate /tmp/weather-dataset.json \
  --minimum-days 20 --fee-coefficient 0.07 --slippage 0.01 --evolve \
  --out /tmp/weather-experiment.json
python -m scripts.weather_probability KXHIGHNY-26JUL10-T90 \
  --as-of 2026-07-10T03:00:00Z --training-data /tmp/weather-dataset.json \
  --rounding nearest_integer
```

These example dates/tickers must be replaced with actual captured coverage.
The dataset command performs SELECTs only, requires the existing historical DB,
and writes JSON to the requested output path. `--limit` provides a diagnostic
subset; evaluation rejects truncated datasets. Status uses a bounded subset
(`--readiness-limit`, default 2000) and explicitly reports truncation, sources,
rejection reasons, usable contracts and independent station-days.

Samples join decision-time contract revisions and forecasts to a final numeric
Kalshi settlement captured by the build cutoff. Revised rules, non-final results,
contradictory settlement predicates and conflicting values for the same station,
date and high/low target are excluded. Training label availability is the captured
final revision time; old archive observations are not used as known historical
features. Source/rounding assumptions remain visible in every dataset.

Calibration uses one example per station/day/kind/horizon, requiring 20 independent
days per model by default. Horizon buckets are 0–12 h, 12–36 h, over 36 h, and
intraday. It prefers the same season (adjacent months) when sufficiently populated,
otherwise falls back to station/horizon over at most 730 days. It fits bias and
residual distributions, with inverse residual-variance model weights, a 0.5 F
Gaussian sigma floor and 0.35 F empirical smoothing. Intraday calibration uses
intraday examples, never substitutes pre-day errors. All constants are research
choices requiring prospective validation.

The report compares empirical, corrected Gaussian and uncorrected Gaussian
probabilities, with station-day-weighted Brier score, log loss and reliability.
The regime-conditioned ablation is described in [weather_navier_stokes.md](weather_navier_stokes.md).
Each prediction trains exclusively on earlier dates with labels already captured
before its decision. Repeated ticks/brackets cannot inflate calibration N.

The simulator requires displayed size, uses ask plus explicit slippage, rounds
fees per order, locks cash through captured settlement availability, enters a
ticker once and caps combined high/low/bracket risk per station/day. The supplied
fee coefficient is a scenario assumption, not validated historical series fees;
the simulator does not infer it from today's metadata. Depth snapshots establish
displayed liquidity only, not successful execution. Reported ROI is PnL over
order costs; drawdown is on realized PnL, not marked-to-market equity.

Evolution varies entry edge, timing, disagreement/ramp ceilings and station-day
exposure. It optimizes two chronological date blocks and reports a final third
that never enters fitness. Validation labels not available at the first holdout
decision are embargoed. It requires chronological calibration metadata and enough
station-days per fold. Costs are fixed outside the genome. Rules are frozen for
holdout; the predeclared calibration process may learn from earlier holdout days
as their labels become available. No automatic promotion or execution exists.

Low coverage yields abstention or `insufficient_data`, never a trained model or
profitability claim. Existing deployed capture history must be inspected before
interpreting any real result. This change is local until the weather image is
explicitly rebuilt/deployed; older deployed images still capture only GFS.

Validation: `python -m pytest tests/test_weather_probability.py tests/test_weather_evidence_capture.py tests/test_weather_research_pipeline.py -q`.

References: [Kalshi market fields](https://docs.kalshi.com/api-reference/events/get-event),
[NOAA daily climate summary](https://repository.library.noaa.gov/view/noaa/50328/noaa_50328_DS1.pdf).
