# Repository Guidelines

## Project Structure & Module Organization

Kairos is a Python 3.13 market-scanning service. `kairos.py` and `engine.py` coordinate scans. Domain models live in `models/`, strategies in `strategies/`, and long-running jobs in `workers/`. Integrations and scrapers are under `outer_apis/`; the Flask service is in `inner_api/`. Peewee models and SQLite access are in `database/`, split between accounting and historical data. Put maintenance, backfill, and reporting tools in `scripts/`. Tests mirror these areas in `tests/`.

## Build, Test, and Development Commands

- `python -m venv .venv && source .venv/bin/activate` creates a local environment.
- `pip install -r requirements.txt pytest` installs runtime dependencies and the test runner.
- `cp .env.example .env` creates local configuration; fill in only the credentials needed by your task.
- `python kairos.py` runs one scan. It may contact live services, so prefer tests while developing.
- `python -m pytest` runs the complete suite; `python -m pytest tests/test_market_arb_websocket_scanner.py -q` runs one focused module.
- `docker compose build` builds the Python 3.13 image; `docker compose up <service>` starts a specific service, for example `api`.

## Coding Style & Naming Conventions

Use four-space indentation and standard PEP 8 conventions. Name modules, functions, and variables with `snake_case`, classes with `PascalCase`, and constants with `UPPER_SNAKE_CASE`. Keep API-specific parsing inside its integration package and business rules inside models or strategies. Add concise docstrings or comments for non-obvious market, timing, and persistence decisions. No formatter or linter is currently configured; match nearby code and keep imports explicit.

## Testing Guidelines

Use pytest and `unittest.mock` to isolate network calls, credentials, time, and databases. Add regression tests for bug fixes and name tests `test_<behavior>`. Use fixtures in `tests/conftest.py`; tests must not depend on production databases or live APIs. Run the focused test first, then the full suite before submitting.

## Commit & Pull Request Guidelines

Recent history follows Conventional Commits with scopes, such as `fix(tennis): ...`, `feat(tennis): ...`, and `refactor(tennis): ...`. Keep commits focused and use an imperative summary. Pull requests should explain behavior and risk, list verification commands, link relevant issues, and call out schema, environment, worker-interval, or Docker changes. Include screenshots only for user-visible API or interface changes.

## Security & Configuration

Never commit `.env`, API keys, proxy credentials, private keys, or database files. Document new variables in `.env.example` with safe placeholders. Treat accounting and historical database paths as separate stores, and preserve read-only archive mounts defined in `docker-compose.yml`.
