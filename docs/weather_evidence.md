# Weather evidence capture

The worker only captures public data. It does not train an optimizer or submit
orders. Offline calibration/evolution is now available through
[the research CLI](weather_probability.md). Legacy `weather_market_snapshot` rows are preserved, but their city-center
coordinates and UTC daily aggregates are not validated station observations.

## Three separate streams

- Observations: IEM's archive of NWS daily CLI reports, initially KNYC, KMDW,
  KAUS, KMIA, KDEN, KPHL, from `WEATHER_HISTORY_START_YEAR` (2024). Coordinates,
  timezone and station metadata come from the IEM NWSCLI network, not geocoding.
  Raw daily values preserve missing `M`, trace `T`, report identifier and link.
  CLI temperatures are Fahrenheit; precipitation/snow are inches.
- Forecasts: hourly GFS seamless, ECMWF IFS 0.25 and HRRR forecasts at those station coordinates, with
  response grid coordinates and units retained. Current forecasts have retrieval
  availability timestamps. Historical single runs use the previous day's 00 UTC
  initialization, three forecast days, for linked settled temperature dates from
  April 3, 2026. They are archived once per station/date, not once per contract.
- Markets: all discovered Climate and Weather series, immutable raw rules,
  observed quotes, settlement/result revisions, and hourly historical candles.
  Recent and historical market endpoints have independent persistent cursors.
  Temperature series for the six initial cities receive a separate queue lane.
  Other cities and non-temperature markets are archived but NOT certified ready
  for modeling; missing station/date semantics stay unknown.
- Intraday: NWS surface collections retain temperature, timestamps and quality
  flags; retrieval establishes availability. They are not final daily CLI extrema.
  Orderbook capture preserves top-level prices and sizes for mapped temperature
  contracts, including TWC, even when evaluation rejects the settlement source.

## Leakage and settlement guardrails

`initialized_at` is NOT publication time. Historical forecast `available_at`
remains NULL: a strategy evaluator must exclude these records unless independent
availability evidence is supplied, or explicitly label a publication-delay
assumption in a separate research scenario. Archive observations also have unknown
availability; current downloads do not prove what revisions existed historically.

CLI days span midnight-to-midnight **local standard time**, including during DST.
Hourly forecasts are kept in UTC and NOT reduced to a civil-time or UTC daily
maximum. A future evaluator must implement the contract's precise measuring window.

Recent contracts can use The Weather Company whereas older ones used NWS CLI.
Preserve the source per contract. TWC contracts are NOT marked station-verified
against NWS, even when the rules mention the same CLI code. Kalshi's actual
settlement value is stored separately; it is not reconstructed from forecasts.
Unrecognized locations are never assigned a guessed coordinate.

Candles are aggregates, NOT executable historical quotes. A backtest also needs
as-of rules, fees, spread, volume, chronological holdout and missing-data vetoes.
Neither historical candles nor a higher forecast hit rate establishes profitability.

## Operations

Migration 25 adds four tables without deleting legacy data. Old deployed images
reject migration versions they do not know on restart: coordinate image upgrades
before restarting older services. Do not rebuild/restart unrelated workers blindly.

`WEATHER_CAPTURE_REQUEST_BUDGET` bounds job slots per cycle (default 28, max 140).
Each slot performs at most one HTTP request (5s connect/25s read timeout), with
250ms spacing. Separate lanes prevent market archives starving weather capture.
Lane rotation persists across cycles, including budgets below the lane count.
`WEATHER_FORECAST_MODELS` controls the three supported model identifiers. Adding
models and surface/books shares the same budget; it does not guarantee hourly
per-station refresh. Inspect actual ages and queue backlog. `weather_schedule`
is a queue cursor, not an HTTP job. No additional schema migration is needed.
Failures retain page cursors and retry after an hour. Completion/cursor updates
follow evidence writes; content hashes make replays idempotent. Run only one
capture process at a time; the queue has no distributed lease.

Commands:

```sh
python -m pytest tests/test_weather_evidence_capture.py -q
python -m scripts.weather_capture_status
# Only when the normal worker is stopped, to avoid overlapping job claims:
python -m scripts.weather_capture_status --capture
```

The scoped Compose override pins the weather image and caps its runtime at
0.5 CPU / 512 MiB. Build the image, stop only the old weather service, run a
single capture to verify migration/data, then start that service with no dependencies:

```sh
docker compose -f docker-compose.yml -f docker-compose.weather.yml build weather_market_capture_worker
docker compose -f docker-compose.yml -f docker-compose.weather.yml stop weather_market_capture_worker
docker compose -f docker-compose.yml -f docker-compose.weather.yml run --rm --no-deps weather_market_capture_worker python -m scripts.weather_capture_status --capture
docker compose -f docker-compose.yml -f docker-compose.weather.yml up -d --no-deps weather_market_capture_worker
```

The 900-second interval is the **queue cycle**, not guaranteed per-market coverage.
Inspect evidence timestamps and failed jobs before building a training sample.
Never treat uncollected intervals as zero prices or zero precipitation.

## Sources

- https://mesonet.agron.iastate.edu/geojson/network.php?network=NWSCLI
- https://mesonet.agron.iastate.edu/json/cli.py?station=KNYC&year=2026
- https://mesonet.agron.iastate.edu/nws/clitable.php?station=KNYC&year=2026
- https://open-meteo.com/en/docs/single-runs-api
- https://docs.kalshi.com/getting_started/historical_data
- https://docs.kalshi.com/api-reference/historical/get-historical-market-candlesticks

## Deployment verification — 2026-09-09

Deployed only `kairos_weather_market_capture_worker` on the mini-PC using
`kairos:weather-evidence-20260909`. Migration 25 and readback succeeded. After
the initial bounded capture and first worker cycle: 6 stations, 380 series,
615 contracts (100 station-verified), 2,330 CLI daily records, 6 live forecasts,
8 historical runs, 48 observed quotes and 7 candle response windows. These are
partial coverage counts, not independent training examples or complete history.
The KMDW 2025 observation request had a remote connection closure and is queued
for retry; the other jobs persisted normally. Runtime observed at 29.68 MiB,
0% CPU between cycles, restart count zero.

Validation: focused weather/catalog suite passed. The full suite is also green:
1,063 passed, 1 skipped. The timing-worker fixture uses relative dates so its
capture-window assertions remain valid as the calendar advances.

At the initial deployment, the status command labelled contracts with a verified settlement location/date as
`settlement_labelled_temperature_contracts`, not model-ready. As of this design,
historical forecast runs have no independently verified publication timestamp,
so the point-in-time training-ready count intentionally stays zero. Live data
must accumulate through future settlements before it can support a leakage-safe
weather strategy evaluation. The current local research extension replaces the
fixed zero with an evidence join and labelled coverage/abstention report; this
does not retroactively establish archive availability.

## Public endpoint validation — 2026-09-13

Bounded public GETs returned HTTP 200 for all three forecast identifiers, including
the requested temperature, dew point, cloud and wind variables. Their returned
hourly axes contained 96 timestamps (coverage of finite values is still validated
per model/day). NWS KNYC returned 58 samples for a 30-hour collection and quality
flag `V`. A current Kalshi temperature orderbook returned the `orderbook_fp` /
`no_dollars` format with fractional displayed size.

The inspected KXHIGHNY-26SEP14-T81 contract explicitly named The Weather Company,
despite the general help page describing NWS for daily contracts. Contract rules
therefore take precedence. TWC measuring-window semantics remain unverified in
this evaluator, so it abstains for that source while preserving its market data.
These public checks did not migrate databases, deploy the image or submit orders.
