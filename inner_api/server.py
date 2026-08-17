"""
API Flask de solo lectura de Kairos.

Expone por HTTP el mismo detalle que ya calculan/loggean los módulos
existentes (database/accounting/writer.py, strategies/paper_trading.py,
scripts/*.py) -- este módulo NO reimplementa ninguna query ni cálculo,
solo los envuelve en rutas JSON. Nunca escribe nada (todas las rutas son
GET). No hay ejecución de órdenes reales en ningún lado de este proyecto
(hard red line, ver database/accounting/papertrade.py) -- este servidor
tampoco cambia eso, es puramente de lectura/observabilidad.

Puerto: env var API_PORT, default 8765 (mismo puerto que usaba el
Flask legacy pre-"extreme makeover" -- hay una referencia externa en otro
proyecto del ecosistema (Aegis) que ya apunta a ese puerto, confirmado
libre en la mini-pc).

Manejo de errores: cada vista individual está envuelta en try/except
propio (en vez de un único errorhandler global) porque distintas rutas
necesitan devolver distintos códigos de éxito en el camino feliz (200
vs 404 para /api/operations/<id>) y queríamos que ese 404 conviva sin
ambigüedad con el 500 genérico de "algo se rompió calculando la
respuesta" -- un errorhandler global de Exception no distinguiría fácil
un 404 intencional (que se lanza como abort/return normal, no como
excepción) de un fallo real. Todas las rutas devuelven JSON en cualquier
código de estado, nunca el traceback HTML por defecto de Flask.

Uso local (contra la DB de dev, desde la raíz del repo):
    python3 -m inner_api.server

Uso en producción (contenedor kairos_api, ver docker-compose.yml):
    python -m inner_api.server

`GET /api/portfolio/kalshi` (agregado 2026-07-28): el usuario opera
Kalshi MANUALMENTE fuera de Kairos y pidió poder ver su cuenta real desde
Kairos_Desktop (app GTK separada, otro repo). Esta ruta envuelve
`outer_apis/kalshi_portfolio.py` (auth firmada RSA-PSS contra
/portfolio/balance,/positions,/fills de Kalshi) -- es SOLO LECTURA, igual
que el resto de este servidor, y no agrega ningún camino de escritura de
órdenes (línea roja del proyecto, ver docstring de ese módulo). El
cálculo de curva de equity a partir de los fills se deja del lado del
cliente (Kairos_Desktop): esta ruta devuelve los fills crudos de Kalshi
(ya traen yes_price_dollars/no_price_dollars, fee_cost, created_time) en
vez de un PnL acumulado precalculado, para no duplicar lógica de
contabilidad que Kalshi ya expone y que puede diferir de como Kairos
calcula PnL para sus propias paper trades.
"""

import os
import json
from datetime import datetime, timezone

from flask import Flask, jsonify, request

from database.db import init_db, db
from database.db import init_db_historical, db_historical
from database.historical.workerstate import HistoricalWorkerState, TennisMlExperiment
from database.historical.tennis.models import (
    MatchHistory, MatchStats, MatchWeather, Player, TennisHistoricalOdds,
)
from database.accounting.limitarbsignal import LimitArbSignal
from database.accounting.papertrade import PaperTrade
from database.accounting.paperaccount import PaperAccount
from database.accounting.fillverification import FillVerification
from database.accounting.writer import _row_to_dict as _signal_to_dict
from strategies.paper_trading import get_available_equity

import scripts.reports.paper_trading_stats as paper_trading_stats
import scripts.reports.limit_fill_report as limit_fill_report
import scripts.reports.calibration_report as calibration_report
from outer_apis.kalshi.portfolio import (
    KalshiAuthError,
    KalshiCredentialsError,
    KalshiPortfolioAPI,
)

app = Flask(__name__)

DEFAULT_LIMIT = 100
MAX_LIMIT = 500  # fail-closed: /api/operations nunca devuelve una lista sin tope


@app.route('/api/workers')
def worker_health():
    """Estado persistente de los workers históricos, solo lectura."""
    try:
        init_db_historical()
        now = datetime.now(timezone.utc)
        with db_historical.connection_context():
            rows = list(HistoricalWorkerState.select().order_by(HistoricalWorkerState.worker_name))
        result = []
        for row in rows:
            updated_at = row.updated_at
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            age_seconds = max(0.0, (now - updated_at).total_seconds())
            result.append({
                'worker_name': row.worker_name,
                'cursor_id': row.cursor_id,
                'cycle_started_at': row.cycle_started_at.isoformat() if row.cycle_started_at else None,
                'cycle_finished_at': row.cycle_finished_at.isoformat() if row.cycle_finished_at else None,
                'updated_at': row.updated_at.isoformat(),
                'heartbeat_age_seconds': age_seconds,
                'running_cycle': bool(
                    row.cycle_started_at
                    and (row.cycle_finished_at is None or row.cycle_started_at > row.cycle_finished_at)
                ),
                'last_error': row.last_error,
                'last_result': json.loads(row.last_result_json) if row.last_result_json else None,
            })
        return jsonify(result), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/tennis/data-quality')
def tennis_data_quality():
    """Métricas agregadas para el dashboard externo de Kairos_Desktop."""
    try:
        init_db_historical()
        with db_historical.connection_context():
            total = MatchHistory.select().count()

            def coverage(predicate):
                count = MatchHistory.select().where(predicate).count()
                return {
                    'count': count,
                    'pct': round(100.0 * count / total, 2) if total else 0.0,
                }

            latest_ml = TennisMlExperiment.select().order_by(
                TennisMlExperiment.created_at.desc(), TennisMlExperiment.id.desc(),
            ).first()
            payload = {
                'generated_at': datetime.now(timezone.utc).isoformat(),
                'matches_total': total,
                'players_total': Player.select().count(),
                'historical_odds_total': TennisHistoricalOdds.select().count(),
                'match_stats_total': MatchStats.select().count(),
                'match_weather_total': MatchWeather.select().count(),
                'coverage': {
                    'both_players': coverage(
                        MatchHistory.player_a.is_null(False) & MatchHistory.player_b.is_null(False)
                    ),
                    'winner': coverage(MatchHistory.winner.is_null(False)),
                    'tournament': coverage(MatchHistory.tournament.is_null(False)),
                    'duration': coverage(MatchHistory.duration.is_null(False)),
                    'surface_elo_both': coverage(
                        MatchHistory.player_a_surface_elo.is_null(False)
                        & MatchHistory.player_b_surface_elo.is_null(False)
                    ),
                    'retirement_identified': coverage(
                        MatchHistory.retired & MatchHistory.player_retired.is_null(False)
                    ),
                },
                'latest_ml_experiment': ({
                    'algorithm': latest_ml.algorithm,
                    'dataset_sha256': latest_ml.dataset_sha256,
                    'sample_count': latest_ml.sample_count,
                    'training_through': latest_ml.training_through,
                    'eligible_for_review': latest_ml.eligible_for_review,
                    'promotion_status': latest_ml.promotion_status,
                    'metrics': json.loads(latest_ml.metrics_json),
                    'created_at': latest_ml.created_at.isoformat(),
                } if latest_ml else None),
            }
        return jsonify(payload), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _papertrade_to_dict(t: PaperTrade) -> dict:
    return {
        'id': t.id,
        'signal_id': t.signal_id,
        'market_id': t.market_id,
        'case': t.case,
        'opened_at': t.opened_at.isoformat() if t.opened_at else None,
        'position_size_usd': t.position_size_usd,
        'p_success_proxy_at_open': t.p_success_proxy_at_open,
        'kelly_fraction_used': t.kelly_fraction_used,
        'market_leg_price': t.market_leg_price,
        'market_leg_fee': t.market_leg_fee,
        'status': t.status,
        'closed_at': t.closed_at.isoformat() if t.closed_at else None,
        'close_reason': t.close_reason,
        'realized_pnl_usd': t.realized_pnl_usd,
        'close_market_leg_price': t.close_market_leg_price,
    }


def _fillverification_to_dict(v: FillVerification) -> dict:
    return {
        'id': v.id,
        'signal_id': v.signal_id,
        'market_id': v.market_id,
        'verified_at': v.verified_at.isoformat() if v.verified_at else None,
        'price_touched': v.price_touched,
        'post_touch_volume': v.post_touch_volume,
        'queue_ahead_estimate': v.queue_ahead_estimate,
        'our_order_contracts': v.our_order_contracts,
        'limit_filled_verified': v.limit_filled_verified,
        'fail_reason': v.fail_reason,
    }


def _operation_to_dict(signal: LimitArbSignal, trade: PaperTrade | None,
                        verification: FillVerification | None) -> dict:
    d = _signal_to_dict(signal)
    d['paper_trade'] = _papertrade_to_dict(trade) if trade else None
    d['fill_verification'] = _fillverification_to_dict(verification) if verification else None
    return d


@app.route('/healthz')
def healthz():
    # A propósito no toca la DB -- healthcheck trivial para Docker
    # (ver docker-compose.yml), no debe fallar por un problema de datos,
    # solo si el proceso Flask no está vivo.
    return jsonify({'status': 'ok'}), 200


@app.route('/api/operations')
def list_operations():
    try:
        status = request.args.get('status')
        case = request.args.get('case')
        market_id = request.args.get('market_id')  # filtra por evento/match (ver docstring de módulo)
        try:
            limit = int(request.args.get('limit', DEFAULT_LIMIT))
        except ValueError:
            limit = DEFAULT_LIMIT
        try:
            offset = int(request.args.get('offset', 0))
        except ValueError:
            offset = 0
        limit = max(1, min(limit, MAX_LIMIT))  # fail-closed: nunca > MAX_LIMIT
        offset = max(0, offset)

        init_db()
        with db.connection_context():
            query = LimitArbSignal.select()
            if status:
                query = query.where(LimitArbSignal.monitoring_status == status)
            if case:
                query = query.where(LimitArbSignal.case == case)
            if market_id:
                query = query.where(LimitArbSignal.market_id == market_id)
            query = query.order_by(LimitArbSignal.created_at.desc()).limit(limit).offset(offset)
            signals = list(query)

            signal_ids = [s.id for s in signals]
            trades_by_signal = {t.signal_id: t for t in
                                 PaperTrade.select().where(PaperTrade.signal_id.in_(signal_ids))} if signal_ids else {}
            verifications_by_signal = {v.signal_id: v for v in
                                        FillVerification.select().where(FillVerification.signal_id.in_(signal_ids))} if signal_ids else {}

            out = [_operation_to_dict(s, trades_by_signal.get(s.id), verifications_by_signal.get(s.id))
                   for s in signals]
        return jsonify(out), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/operations/by-event')
def operations_by_event():
    """Paper trades (estrategia LimitArb) agrupados por `market_id` (el
    ticker del evento/match -- ej. un partido de tenis o un juego de
    KXMLBGAME) con PnL agregado. Complementa `/api/operations` (que lista
    operaciones individuales) para responder "¿cómo me fue en este
    evento/match en particular?" sin tener que agregar del lado del
    cliente.

    Solo agrega paper trades ya CERRADOS con `realized_pnl_usd` no nulo
    para wins/losses/total_realized_pnl_usd -- fail-closed: un trade
    abierto o con PnL aún no computable no se cuenta como ganado ni
    perdido, se contabiliza aparte en `n_open`."""
    try:
        limit_events = max(1, min(int(request.args.get('limit', 100)), MAX_LIMIT))

        init_db()
        with db.connection_context():
            signals = list(LimitArbSignal.select().order_by(LimitArbSignal.created_at.desc()))
            signal_ids = [s.id for s in signals]
            trades_by_signal = {t.signal_id: t for t in
                                 PaperTrade.select().where(PaperTrade.signal_id.in_(signal_ids))} if signal_ids else {}

            by_market: dict[str, dict] = {}
            for s in signals:
                trade = trades_by_signal.get(s.id)
                bucket = by_market.setdefault(s.market_id, {
                    'market_id': s.market_id,
                    'n_operations': 0,
                    'n_wins': 0,
                    'n_losses': 0,
                    'n_open': 0,
                    'total_realized_pnl_usd': 0.0,
                    'operations': [],
                })
                bucket['n_operations'] += 1
                bucket['operations'].append(_operation_to_dict(s, trade, None))
                if trade is None or trade.status != 'closed' or trade.realized_pnl_usd is None:
                    bucket['n_open'] += 1
                elif trade.realized_pnl_usd > 0:
                    bucket['n_wins'] += 1
                    bucket['total_realized_pnl_usd'] += trade.realized_pnl_usd
                else:
                    bucket['n_losses'] += 1
                    bucket['total_realized_pnl_usd'] += trade.realized_pnl_usd

        # más eventos primero (por operación más reciente dentro del evento,
        # ya que `signals` viene ordenado desc y armamos los buckets en ese orden)
        out = list(by_market.values())[:limit_events]
        return jsonify(out), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/operations/<int:signal_id>')
def get_operation(signal_id):
    try:
        init_db()
        with db.connection_context():
            signal = LimitArbSignal.get_or_none(LimitArbSignal.id == signal_id)
            if signal is None:
                return jsonify({'error': 'not_found'}), 404
            trade = PaperTrade.get_or_none(PaperTrade.signal_id == signal_id)
            verification = FillVerification.get_or_none(FillVerification.signal_id == signal_id)
            out = _operation_to_dict(signal, trade, verification)
        return jsonify(out), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/account')
def get_account():
    try:
        init_db()
        with db.connection_context():
            account, _ = PaperAccount.get_or_create(id=1, defaults={'bankroll_usd': 200.0})
            open_trades = list(PaperTrade.select().where(PaperTrade.status == 'open'))
            committed_capital = sum(t.position_size_usd for t in open_trades)
            out = {
                'bankroll_usd': account.bankroll_usd,
                'kelly_calibration_factor': account.kelly_calibration_factor,
                'last_recalibrated_at': account.last_recalibrated_at.isoformat() if account.last_recalibrated_at else None,
                'updated_at': account.updated_at.isoformat() if account.updated_at else None,
                'committed_capital_usd': committed_capital,
                'n_open_positions': len(open_trades),
            }
        # get_available_equity() abre/cierra su propia connection_context
        # internamente (strategies/paper_trading.py) -- se llama fuera del
        # `with` de arriba para no anidar contexts sobre la misma conexión.
        out['available_equity_usd'] = get_available_equity()
        return jsonify(out), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/stats/paper-trading')
def stats_paper_trading():
    try:
        return jsonify(paper_trading_stats.compute_report()), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/stats/fill-report')
def stats_fill_report():
    try:
        return jsonify(limit_fill_report.compute_report()), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/stats/calibration')
def stats_calibration():
    try:
        return jsonify(calibration_report.compute_report()), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/portfolio/kalshi')
def portfolio_kalshi():
    """Portfolio REAL de Kalshi (SOLO LECTURA, ver docstring de módulo).
    Query params opcionales: min_ts/max_ts (unix seconds) para acotar el
    rango de fills traído -- sin esto trae hasta DEFAULT_MAX_PAGES páginas
    de historial completo, que puede ser lento/costoso en rate-limit.

    Credenciales ausentes -> 400 (error del operador, corregible sin
    reintentar). 401/403 de Kalshi -> 401 (credenciales cargadas pero
    inválidas/expiradas). Cualquier otro fallo -> 500, igual que el resto
    de las rutas de este servidor."""
    try:
        min_ts = request.args.get('min_ts', type=int)
        max_ts = request.args.get('max_ts', type=int)

        client = KalshiPortfolioAPI()
        balance = client.get_portfolio_balance()
        positions = client.get_portfolio_positions()
        fills = client.get_portfolio_fills(min_ts=min_ts, max_ts=max_ts)

        return jsonify({
            'balance': balance,
            'positions': positions,
            'fills': fills,
        }), 200
    except KalshiCredentialsError as e:
        return jsonify({'error': str(e)}), 400
    except KalshiAuthError as e:
        return jsonify({'error': str(e)}), 401
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _derive_net_pnl_usd(fills: list[dict], position: dict | None, settlement: dict | None) -> float | None:
    """PnL neto en USD para un evento, derivado del FLUJO DE CAJA real de
    sus fills (no de los campos agregados de `settlement` -- ver bug real
    de abajo).

    HISTORIA (misma sesión): la primera versión usaba únicamente
    `settlement.revenue/100 - yes_cost - no_cost - fee`. Verificando
    contra datos reales de producción, esa fórmula daba pérdidas
    fabricadas de -$500+ en mercados donde el usuario en realidad había
    arbitrado con ganancia (comprando yes Y no del mismo mercado --
    estrategia same-market arb). Causa: cuando `yes_count_fp` ==
    `no_count_fp` en el settlement, Kalshi netea esa posición
    internamente y `revenue` da 0 aunque haya lado ganador -- la ganancia
    del arb ya había quedado capturada en el PRECIO de cada compra/venta,
    no en un payout de settlement adicional. La fórmula agregada no podía
    distinguir ese caso sin inventar un número (por eso la primera
    corrección devolvía None ahí, fail-closed pero incompleto).

    FIX: en vez de adivinar a partir de agregados, se suma el flujo de
    caja real de cada fill individual (que YA se está trayendo para el
    detalle de la respuesta, no es una llamada extra a Kalshi): cada
    'buy' resta `precio_del_lado * count_fp`, cada 'sell' suma lo mismo;
    se restan las fees de cada fill. Esto es correcto SIN importar si hubo
    arb en ambos lados o no -- no hace falta distinguir el caso, el cash
    flow ya lo captura todo. Verificado a mano contra un caso real
    (KXWTAMATCH-...-UDV, arb yes+no simultáneo): cálculo manual y este
    código coinciden en -$399.41.

      - Si liquidó (`settlement` presente): cash_flow_de_fills - fees +
        settlement.revenue/100 (lo que se cobró al liquidar por lo que
        haya quedado sin vender, 0 si ya se cerró todo antes).
      - Si todavía está en pie (`settlement` ausente, `position`
        presente): se usa `realized_pnl_dollars` de Kalshi tal cual (no
        cash-flow, porque una posición abierta tiene valor no realizado
        que no está en los fills) -- 0.0 mientras no liquide.
      - Si no hay ninguno de los dos (mercado purgado de ambas listas):
        None -- fail-closed, nunca se fabrica un número.

    Cualquier fill/campo con formato inesperado (ValueError/TypeError/
    KeyError) hace fail-closed toda la cuenta de ese evento (None), no se
    ignora silenciosamente un fill raro y se sigue con un número parcial
    engañoso."""
    try:
        if settlement is not None:
            cash_flow = 0.0
            fees = 0.0
            for f in fills:
                side = f['side']
                price = float(f['yes_price_dollars']) if side == 'yes' else float(f['no_price_dollars'])
                count = float(f['count_fp'])
                sign = 1.0 if f['action'] == 'sell' else -1.0
                cash_flow += sign * price * count
                fees += float(f.get('fee_cost', 0) or 0)
            revenue_usd = float(settlement.get('revenue', 0)) / 100.0
            return cash_flow - fees + revenue_usd
        if position is not None:
            return float(position.get('realized_pnl_dollars', 0) or 0)
        return None
    except (TypeError, ValueError, KeyError):
        return None


@app.route('/api/portfolio/kalshi/by-event')
def portfolio_kalshi_by_event():
    """Operaciones REALES de Kalshi agrupadas por ticker de mercado
    (evento/match), con el resultado de cada una.

    DELIBERADAMENTE no recalcula PnL nosotros mismos, usa 2 fuentes ya
    calculadas por Kalshi (evita el tipo de bug de infra-reporte de ROI ya
    encontrado antes en scanning.py/same_market_arb.py -- auditoría
    2026-07-04):
      - `position`: de GET /portfolio/positions -- solo cubre mercados con
        posición TODAVÍA EN PIE (`position_fp` != 0), con
        `realized_pnl_dollars` (0.000000 mientras no liquide).
      - `settlement`: de GET /portfolio/settlements -- cubre mercados YA
        LIQUIDADOS (Kalshi los saca de `positions` en cuanto la posición
        queda en 0 tras liquidar). Trae `market_result` ('yes'/'no') y los
        campos crudos para derivar PnL (`revenue` en centavos,
        `yes_total_cost_dollars`/`no_total_cost_dollars`, `fee_cost`) --
        también sin recalcular, se devuelve tal cual.

    Un evento puede tener ambos en None si Kalshi ya lo purgó de las dos
    listas (mercado muy viejo) -- fail-closed, nunca se fabrica un
    resultado, se deja `null` explícito en vez de inventar 'ganado' o
    'perdido'.

    `net_pnl_usd` (agregado 2026-07-28, corregido el mismo día -- ver
    docstring de `_derive_net_pnl_usd`): campo calculado de conveniencia
    para no obligar al cliente (Kairos_Desktop) a hacer la cuenta de
    cash-flow él mismo. Se agrega ADEMÁS de (no en reemplazo de)
    `position`/`settlement`/`fills` crudos: el detalle de cada apuesta
    hecha en el evento (`fills`) se mantiene intacto para poder auditar de
    dónde sale ese número -- se calcula precisamente A PARTIR de esos
    fills, así que siempre es reproducible a mano con lo que ya viene en
    la respuesta."""
    try:
        min_ts = request.args.get('min_ts', type=int)
        max_ts = request.args.get('max_ts', type=int)

        client = KalshiPortfolioAPI()
        positions = client.get_portfolio_positions()
        fills = client.get_portfolio_fills(min_ts=min_ts, max_ts=max_ts)
        settlements = client.get_portfolio_settlements(min_ts=min_ts, max_ts=max_ts)

        position_by_ticker = {p['ticker']: p for p in positions.get('market_positions') or []}
        settlement_by_ticker = {s['ticker']: s for s in settlements if s.get('ticker')}

        def _new_bucket(ticker: str) -> dict:
            return {
                'ticker': ticker,
                'position': position_by_ticker.get(ticker),
                'settlement': settlement_by_ticker.get(ticker),
                'fills': [],
            }

        by_ticker: dict[str, dict] = {}
        for f in fills:
            ticker = f.get('ticker') or f.get('market_ticker')
            if not ticker:
                continue
            bucket = by_ticker.setdefault(ticker, _new_bucket(ticker))
            bucket['fills'].append(f)

        # Eventos ya liquidados pero sin fills en el rango pedido (ej. si
        # se filtró por min_ts/max_ts angosto) igual se incluyen, para que
        # el settlement con su resultado no quede huérfano.
        for ticker in settlement_by_ticker:
            if ticker not in by_ticker:
                by_ticker[ticker] = _new_bucket(ticker)

        # net_pnl_usd se calcula en un segundo paso, ya con TODOS los
        # fills de cada ticker acumulados (ver _derive_net_pnl_usd: suma
        # el cash-flow de fills, necesita la lista completa, no fill por
        # fill).
        for bucket in by_ticker.values():
            bucket['net_pnl_usd'] = _derive_net_pnl_usd(bucket['fills'], bucket['position'], bucket['settlement'])

        out = list(by_ticker.values())
        return jsonify(out), 200
    except KalshiCredentialsError as e:
        return jsonify({'error': str(e)}), 400
    except KalshiAuthError as e:
        return jsonify({'error': str(e)}), 401
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    port = int(os.getenv('API_PORT', '8765'))
    app.run(host='0.0.0.0', port=port, debug=False)
