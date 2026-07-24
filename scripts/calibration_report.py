"""
Informe de recalibración SUGERIDA para W_EDGE/W_VOLATILITY (ver
strategies/limit_arb.py) -- la regresión lineal que determina
`price_adjustment = W_EDGE*raw_edge + W_VOLATILITY*(avg_volatility/2)`.

POR QUÉ ES SOLO UN INFORME, NO UN AUTO-AJUSTE (a diferencia de
kelly_calibration_factor en strategies/paper_trading.py, que sí se
autoaplica cada ciclo del monitor): W_EDGE/W_VOLATILITY determinan
DIRECTAMENTE dónde se coloca el precio de la orden límite -- estructuran
el riesgo de toda la estrategia, no solo escalan el tamaño de una
posición ya acotada (Kelly ya tiene topes de seguridad: medio-Kelly, 10%
máx de equity). Un cambio automático de estos pesos en caliente, basado
en pocos datos, podría mover la estrategia entera hacia un comportamiento
no revisado. Por eso este informe SUGIERE valores nuevos y los deja para
que un humano decida aplicarlos manualmente en strategies/limit_arb.py.

CÓMO SE CALCULA
----------------
Para cada PaperTrade clasificable (mismo criterio que
recalibrate_kelly_factor en strategies/paper_trading.py: status='closed',
close_reason != 'market_gone_unresolved', realized_pnl_usd no es None),
se junta con su LimitArbSignal (raw_edge, avg_volatility) y se arma:

  y_i  = realized_pnl_usd_i / position_size_usd_i   (retorno normalizado, puede ser negativo)
  x1_i = raw_edge_i
  x2_i = avg_volatility_i / 2.0

Se ajusta una regresión lineal SIN intercepto (mismo shape que la fórmula
actual, que tampoco tiene término constante) resolviendo las ecuaciones
normales 2x2 a mano (sin depender de numpy/scipy, que no están en
requirements.txt -- el sistema es 2x2, no justifica la dependencia):

  [S11 S12] [a]   [Sy1]
  [S12 S22] [b] = [Sy2]

  S11=sum(x1^2), S12=sum(x1*x2), S22=sum(x2^2), Sy1=sum(x1*y), Sy2=sum(x2*y)
  a = (Sy1*S22 - Sy2*S12) / det
  b = (S11*Sy2 - S12*Sy1) / det
  det = S11*S22 - S12^2

Fail-closed: si det es casi cero (features colineales -- en la práctica
podría pasar si `raw_edge`/`avg_volatility` casi siempre se mueven juntos
en el subconjunto de datos disponible), o si `n < MIN_SAMPLES_FOR_REPORT`,
NO se ofrece una recomendación -- se informa por qué, nunca se fabrica un
número.

MIN_SAMPLES_FOR_REPORT = 50 (más alto que el umbral de Kelly, 30) porque
esto es una regresión de 2 parámetros, no una tasa de éxito de 1
parámetro -- necesita más datos para no sobreajustar a ruido.

Los coeficientes (a, b) ajustados NO están garantizados a ser positivos
ni a sumar 1 -- si el fit da un coeficiente negativo, eso es información
real (ese término empíricamente anti-correlaciona con el retorno
realizado en esta muestra) y se reporta tal cual, sin forzarlo a un rango
"razonable". La normalización a W_EDGE'+W_VOLATILITY'=1 (mismo convenio
que los pesos actuales) solo tiene sentido si a+b>0 -- si no, se reporta
la anomalía explícitamente en vez de dividir por un número cercano a
cero.

Uso:
    python3 -m scripts.calibration_report
"""

import sys

from database.db import init_db, db
from database.accounting.papertrade import PaperTrade
from database.accounting.limitarbsignal import LimitArbSignal
from strategies.limit_arb import W_EDGE, W_VOLATILITY

MIN_SAMPLES_FOR_REPORT = 50
MIN_SAMPLES_PER_CASE = 20


def _classifiable_rows():
    """Devuelve lista de dicts {raw_edge, avg_volatility, y, case} para
    cada PaperTrade clasificable con su LimitArbSignal asociada. Salta
    silenciosamente filas sin LimitArbSignal encontrada (no debería
    pasar en datos sanos, pero fail-closed: nunca inventar raw_edge)."""
    init_db()
    with db.connection_context():
        trades = list(PaperTrade
                       .select()
                       .where((PaperTrade.status == 'closed') &
                              (PaperTrade.close_reason != 'market_gone_unresolved') &
                              (PaperTrade.realized_pnl_usd.is_null(False))))
        signals_by_id = {s.id: s for s in LimitArbSignal.select()}

    rows = []
    for t in trades:
        sig = signals_by_id.get(t.signal_id)
        if sig is None or sig.raw_edge is None or sig.avg_volatility is None:
            continue
        if t.position_size_usd is None or t.position_size_usd == 0:
            continue
        rows.append({
            'raw_edge': sig.raw_edge,
            'avg_volatility': sig.avg_volatility,
            'y': t.realized_pnl_usd / t.position_size_usd,
            'case': sig.case,
        })
    return rows


def _fit_2d_no_intercept(rows):
    """Resuelve la regresión 2x2 sin intercepto descrita en el docstring
    del módulo. Devuelve None si no se puede ajustar (det casi cero)."""
    n = len(rows)
    if n == 0:
        return None

    x1 = [r['raw_edge'] for r in rows]
    x2 = [r['avg_volatility'] / 2.0 for r in rows]
    y = [r['y'] for r in rows]

    S11 = sum(a * a for a in x1)
    S12 = sum(a * b for a, b in zip(x1, x2))
    S22 = sum(b * b for b in x2)
    Sy1 = sum(a * c for a, c in zip(x1, y))
    Sy2 = sum(b * c for b, c in zip(x2, y))

    det = S11 * S22 - S12 * S12
    if abs(det) < 1e-12:
        return None

    a_coef = (Sy1 * S22 - Sy2 * S12) / det
    b_coef = (S11 * Sy2 - S12 * Sy1) / det

    # R^2 -- qué tanto explica el fit (honesto: con pocas muestras suele
    # ser bajo, no se oculta).
    y_mean = sum(y) / n
    ss_tot = sum((v - y_mean) ** 2 for v in y)
    y_pred = [a_coef * a + b_coef * b for a, b in zip(x1, x2)]
    ss_res = sum((yi - pi) ** 2 for yi, pi in zip(y, y_pred))
    r_squared = (1 - ss_res / ss_tot) if ss_tot > 0 else None

    return {'a': a_coef, 'b': b_coef, 'n': n, 'r_squared': r_squared}


def compute_report() -> dict:
    """Envuelve _classifiable_rows()/_fit_2d_no_intercept() y devuelve un
    dict puro con el mismo resultado que hoy se imprime -- reusado tanto
    por main() como por api/server.py (GET /api/stats/calibration)."""
    rows = _classifiable_rows()
    n = len(rows)

    report = {
        'w_edge_current': W_EDGE,
        'w_volatility_current': W_VOLATILITY,
        'n_classifiable': n,
        'min_samples_required': MIN_SAMPLES_FOR_REPORT,
        'ready': False,
        'reason': None,
        'fit': None,
        'suggestion': None,
        'anomaly': False,
        'case_breakdown': [],
    }

    if n < MIN_SAMPLES_FOR_REPORT:
        report['reason'] = 'insufficient_samples'
        return report

    fit = _fit_2d_no_intercept(rows)
    if fit is None:
        report['reason'] = 'singular_matrix'
        return report

    a, b = fit['a'], fit['b']
    report['ready'] = True
    report['fit'] = {'a': a, 'b': b, 'r_squared': fit['r_squared']}

    if a + b > 0:
        w_edge_suggested = a / (a + b)
        w_vol_suggested = b / (a + b)
        report['suggestion'] = {'w_edge': w_edge_suggested, 'w_volatility': w_vol_suggested}
    else:
        report['anomaly'] = True

    for case in ('favorite_overvalued', 'favorite_undervalued'):
        case_rows = [r for r in rows if r['case'] == case]
        entry = {'case': case, 'n': len(case_rows)}
        if len(case_rows) < MIN_SAMPLES_PER_CASE:
            entry['status'] = 'insufficient'
        else:
            case_fit = _fit_2d_no_intercept(case_rows)
            if case_fit is None:
                entry['status'] = 'singular_matrix'
            else:
                ca, cb = case_fit['a'], case_fit['b']
                if ca + cb > 0:
                    entry['status'] = 'ok'
                    entry['w_edge'] = ca / (ca + cb)
                    entry['w_volatility'] = cb / (ca + cb)
                    entry['r_squared'] = case_fit['r_squared']
                else:
                    entry['status'] = 'anomaly'
        report['case_breakdown'].append(entry)

    return report


def main():
    r = compute_report()
    n = r['n_classifiable']

    print("=" * 72)
    print("KAIROS -- INFORME DE RECALIBRACIÓN SUGERIDA (W_EDGE / W_VOLATILITY)")
    print("Este informe NO modifica nada -- es una recomendación para revisión manual.")
    print("=" * 72)
    print()
    print(f"W_EDGE actual:       {r['w_edge_current']}")
    print(f"W_VOLATILITY actual: {r['w_volatility_current']}")
    print(f"Muestras clasificables disponibles: {n} (mínimo requerido: {r['min_samples_required']})")
    print()

    if r['reason'] == 'insufficient_samples':
        print(f"Todavía NO hay suficientes datos limpios para una recalibración con algo")
        print(f"de confianza estadística (faltan {r['min_samples_required'] - n} muestras más).")
        print("Los pesos actuales se mantienen sin cambios.")
        print("=" * 72)
        return

    if r['reason'] == 'singular_matrix':
        print("No se pudo ajustar la regresión (raw_edge y avg_volatility están")
        print("demasiado correlacionados en esta muestra -- matriz casi singular).")
        print("Los pesos actuales se mantienen sin cambios.")
        print("=" * 72)
        return

    fit = r['fit']
    a, b = fit['a'], fit['b']
    print(f"Coeficientes ajustados (sin normalizar): a(edge)={a:.4f}  b(volatility)={b:.4f}")
    print(f"R^2 del ajuste: {fit['r_squared']:.4f}" if fit['r_squared'] is not None else "R^2: N/A")

    print()
    if r['suggestion']:
        print(f"SUGERENCIA (normalizada a sumar 1, mismo convenio que hoy):")
        print(f"  W_EDGE'       = {r['suggestion']['w_edge']:.4f}  (actual: {r['w_edge_current']})")
        print(f"  W_VOLATILITY' = {r['suggestion']['w_volatility']:.4f}  (actual: {r['w_volatility_current']})")
    else:
        print("ANOMALÍA: a+b <= 0 -- no se puede normalizar a una combinación convexa")
        print("razonable. Esto sugiere que, en esta muestra, ni raw_edge ni")
        print("avg_volatility explican positivamente el retorno realizado -- revisar")
        print("con cautela antes de tocar los pesos actuales.")

    print()
    print("-" * 72)
    print("DESGLOSE POR CASO (solo si hay >= %d muestras en ese caso)" % MIN_SAMPLES_PER_CASE)
    print("-" * 72)
    for entry in r['case_breakdown']:
        case, cn = entry['case'], entry['n']
        if entry['status'] == 'insufficient':
            print(f"  {case:<24} n={cn:<4} (insuficiente, mínimo {MIN_SAMPLES_PER_CASE})")
        elif entry['status'] == 'singular_matrix':
            print(f"  {case:<24} n={cn:<4} (no se pudo ajustar, matriz singular)")
        elif entry['status'] == 'ok':
            print(f"  {case:<24} n={cn:<4} W_EDGE'={entry['w_edge']:.4f} "
                  f"W_VOLATILITY'={entry['w_volatility']:.4f} R^2={entry['r_squared']:.4f}")
        else:
            print(f"  {case:<24} n={cn:<4} anomalía (a+b<=0)")

    print()
    print("=" * 72)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Error generando el informe: {e}", file=sys.stderr)
        sys.exit(1)
