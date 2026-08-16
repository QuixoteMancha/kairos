"""Calcula Elo propio point-in-time + RANKING propio derivado de ese Elo
para TODOS los partidos reales de `MatchHistory` -- 2026-08-11, pedido
explícito del usuario tras descartar depender de una fuente externa de
ranking histórico ATP ("sigamos con el Elo propio en base al historial
de matches" -> "podríamos sacar ambas, Elo y ranking por el historial de
matches").

Ver `models/volatility/sports/tennis/tennis_elo.py` para la fórmula
(Elo estándar, K=32, base=1500) y su docstring para la aclaración
importante: esto NO reproduce el ranking oficial ATP/WTA (sistemas
matemáticamente no relacionados), es una medida de fuerza propia
calculada desde resultados reales.

RANKING PROPIO (`player_a_elo_rank`/`player_b_elo_rank`): posición
(1/2/3/...) ordenando por Elo descendente entre TODOS los jugadores
vistos hasta ese momento en el historial del tour -- honesto: a
diferencia del ranking oficial, esto NO descarta jugadores inactivos
tras 52 semanas sin competir, cualquiera que haya jugado alguna vez
sigue "en la pool" para siempre. Implementación con `bisect` (lista
ordenada de Elo actual, O(log n) para insertar/consultar, O(n) para
remover por shift de lista) -- no optimizada para datasets enormes
(deque/árbol balanceado sería más rápido), pero razonable para el
volumen actual de jugadores reales (cientos, no millones).

MECANISMO: recompute FULL, no incremental -- Elo point-in-time requiere
procesar el historial completo de un jugador EN ORDEN CRONOLÓGICO desde
su primer partido conocido; no se puede calcular una fila aislada sin
saber el Elo de ambos jugadores justo antes de ESE partido. Cada corrida
recalcula TODO desde cero (determinístico, mismo resultado siempre para
el mismo `MatchHistory`) -- evita bugs de "cálculo incremental
parcialmente aplicado" que serían mucho más difíciles de razonar y
depurar. Con el volumen actual de `MatchHistory` (cientos de miles de
filas, no millones) un recompute completo es barato (segundos, todo en
memoria, sin requests de red).

SEPARADO POR TOUR (ATP/WTA nunca se mezclan -- no hay partidos cruzados
reales que permitan calibrar una escala Elo común entre tours). Partidos
sin `tour` resuelto (`None`) se EXCLUYEN del cálculo (fail-closed: no se
puede point-in-time-Elo un partido cuyo tour no se sabe con certeza,
mezclaría escalas).

Fail-closed por fila: un partido sin `winner`/`player_a`/`player_b`
resueltos, o sin `match_date` parseable, se SALTA (no rompe el resto del
cálculo) -- se cuenta en el resumen final para que quede claro cuánto
del historial real no pudo participar."""

from __future__ import annotations

import sys
from bisect import bisect_left, bisect_right, insort
from datetime import date as _date

from database.db import init_db_historical, db_historical
from database.historical.tennis import MatchHistory
from models.volatility.sports.tennis.tennis_elo import DEFAULT_INITIAL_ELO, update_elo


def _parse_match_date(raw: str | None) -> _date | None:
    if not raw:
        return None
    try:
        return _date.fromisoformat(raw)
    except ValueError:
        return None


def _rank_of(sorted_elos: list[float], value: float) -> int:
    """Posición de `value` dentro de `sorted_elos` (lista ascendente),
    ordenando por Elo DESCENDENTE (1 = el Elo más alto). Empates: todos
    los jugadores con el mismo Elo exacto comparten la misma posición
    (no se desempata arbitrariamente)."""
    return len(sorted_elos) - bisect_right(sorted_elos, value) + 1


def _ensure_in_pool(player_id: int, elo: dict[int, float], sorted_elos: list[float]) -> float:
    """Si `player_id` nunca jugó antes en este tour, lo inserta en la
    pool con el Elo default -- necesario para que su PRIMER partido
    también tenga un ranking real (posición entre todos, incluyéndose a
    sí mismo con el valor default)."""
    if player_id not in elo:
        elo[player_id] = DEFAULT_INITIAL_ELO
        insort(sorted_elos, DEFAULT_INITIAL_ELO)
    return elo[player_id]


def _replace_in_pool(player_id: int, new_value: float, elo: dict[int, float], sorted_elos: list[float]) -> None:
    old_value = elo[player_id]
    idx = bisect_left(sorted_elos, old_value)
    del sorted_elos[idx]
    insort(sorted_elos, new_value)
    elo[player_id] = new_value


def run_elo_backfill(tours: tuple[str, ...] = ("ATP", "WTA")) -> dict:
    """Recalcula `player_a_elo`/`player_b_elo` + `player_a_elo_rank`/
    `player_b_elo_rank` para TODO `MatchHistory`, un tour a la vez --
    ver docstring del módulo para el mecanismo completo (recompute
    full, no incremental).

    Returns: {"ATP": {"processed": N, "skipped": M}, "WTA": {...},
    "total_processed": ..., "total_skipped": ...} -- nunca excepciona
    hacia el caller salvo un fallo real de conexión a DB (fail-closed
    por FILA, no por corrida completa)."""
    init_db_historical()
    summary: dict = {}
    total_processed = total_skipped = 0

    for tour in tours:
        processed = skipped = 0
        elo: dict[int, float] = {}  # player_id -> elo actual
        sorted_elos: list[float] = []  # misma pool, ordenada ascendente (para ranking)

        with db_historical.connection_context():
            rows = list(
                MatchHistory
                .select()
                .where(
                    (MatchHistory.tour == tour)
                    & (MatchHistory.player_a.is_null(False))
                    & (MatchHistory.player_b.is_null(False))
                    & (MatchHistory.winner.is_null(False))
                )
                .order_by(MatchHistory.match_date, MatchHistory.id)
            )

        # Una sola transacción por tour -- cientos de miles de filas con
        # un UPDATE individual cada una sería lento (aunque SQLite en
        # WAL no tiene latencia de red, cada commit implícito de fila
        # suelta sí tiene costo real medido). `atomic()` agrupa todo el
        # tour en un solo commit al final.
        with db_historical.connection_context(), db_historical.atomic():
            for row in rows:
                match_date = _parse_match_date(row.match_date)
                if match_date is None:
                    skipped += 1
                    continue

                a_id, b_id, winner_id = row.player_a_id, row.player_b_id, row.winner_id
                if winner_id not in (a_id, b_id):
                    # dato inconsistente real (ganador que no es ninguno
                    # de los 2 jugadores del partido) -- fail-closed, se
                    # salta.
                    skipped += 1
                    continue

                elo_a = _ensure_in_pool(a_id, elo, sorted_elos)
                elo_b = _ensure_in_pool(b_id, elo, sorted_elos)

                row.player_a_elo = elo_a
                row.player_b_elo = elo_b
                row.player_a_elo_rank = _rank_of(sorted_elos, elo_a)
                row.player_b_elo_rank = _rank_of(sorted_elos, elo_b)
                row.save()

                new_elo_a, new_elo_b = update_elo(elo_a, elo_b, a_won=(winner_id == a_id))
                _replace_in_pool(a_id, new_elo_a, elo, sorted_elos)
                _replace_in_pool(b_id, new_elo_b, elo, sorted_elos)
                processed += 1

        summary[tour] = {"processed": processed, "skipped": skipped, "players": len(elo)}
        total_processed += processed
        total_skipped += skipped
        print(f"[compute_self_elo] {tour}: {summary[tour]}")

    summary["total_processed"] = total_processed
    summary["total_skipped"] = total_skipped
    print(f"[compute_self_elo] total: processed={total_processed} skipped={total_skipped}")
    return summary


if __name__ == "__main__":
    run_elo_backfill()
