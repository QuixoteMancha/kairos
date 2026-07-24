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
    python3 -m api.server

Uso en producción (contenedor kairos_api, ver docker-compose.yml):
    python -m api.server
"""

import os

from flask import Flask, jsonify, request

from database.db import init_db, db
from database.accounting.limitarbsignal import LimitArbSignal
from database.accounting.papertrade import PaperTrade
from database.accounting.paperaccount import PaperAccount
from database.accounting.fillverification import FillVerification
from database.accounting.writer import _row_to_dict as _signal_to_dict
from strategies.paper_trading import get_available_equity

import scripts.paper_trading_stats as paper_trading_stats
import scripts.limit_fill_report as limit_fill_report
import scripts.calibration_report as calibration_report

app = Flask(__name__)

DEFAULT_LIMIT = 100
MAX_LIMIT = 500  # fail-closed: /api/operations nunca devuelve una lista sin tope


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


if __name__ == '__main__':
    port = int(os.getenv('API_PORT', '8765'))
    app.run(host='0.0.0.0', port=port, debug=False)
