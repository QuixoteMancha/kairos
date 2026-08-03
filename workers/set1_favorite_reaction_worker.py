"""Integración real de `strategies/set1_favorite_reaction.py` con el
score en vivo (`outer_apis/scrapers/tennis/tennisexplorer_livescore_scraper.py`)
-- 2026-08-02, pedido explícito del usuario: "integración con la
estrategia set_1_favorite_reaction".

Antes de este módulo, "el favorito ganó el set 1" era una condición que
el usuario confirmaba a mano (caso real: Pegula vs Eala). Acá se
detecta sola: para cada partido de Kalshi todavía abierto, busca el
score real en vivo, determina el favorito por RANKING ACTUAL (mismo
criterio usado en la sesión para Pegula/Eala -- no por precio de
mercado, ver caveat de `favorite_by_historical_rank_backtest.py`), y si
el set 1 ya terminó, guarda la recomendación de `recommend_set1_entry`
-- idempotente por `market_id`, nunca se recalcula un partido ya
registrado.

FUSIONADO 2026-08-02 (pedido explícito del usuario, "que los 2 scripts
o funciones estén funcionando en un mismo contenedor... sino quedará el
sistema como nave espacial con tantos módulos separados en contenedores
diferentes"): este módulo YA NO corre como contenedor/worker propio --
`workers/tennis_match_tick_worker.py` llama a `check_and_save_set1_signal`
por cada partido abierto dentro de SU MISMO ciclo de 60s, en su propio
try/except (si TennisExplorer falla, nunca bloquea/degrada el tick de
precio real de Kalshi, que es más crítico). Se eligió fusionar en el
tick worker (no al revés) porque ambos ya iteran exactamente la misma
lista de partidos abiertos con la misma cadencia -- evita una consulta
duplicada a `get_open_scanned_matches()` y un contenedor entero para
una tarea liviana.

SOLO REGISTRO -- ninguna orden se coloca, línea roja del proyecto sigue
vigente. El usuario sigue siendo quien decide operar o no la
recomendación."""

from __future__ import annotations

from database.accounting.set1entrysignal import Set1EntrySignal
from outer_apis.kalshi.markets.tennis import _build_name_to_rank, _lookup_rank
from outer_apis.scrapers.tennis.tennisexplorer_livescore_scraper import get_live_matches, get_set1_winner
from strategies.set1_favorite_reaction import recommend_set1_entry


def _find_live_match(player_a: str, player_b: str, tour: str, live_cache: dict) -> dict | None:
    if tour not in live_cache:
        live_cache[tour] = get_live_matches(tour)
    for m in live_cache[tour]:
        if {m["player_a"], m["player_b"]} == {player_a, player_b}:
            return m
    return None


def check_and_save_set1_signal(scanned: dict, rank_cache: dict, live_cache: dict) -> str:
    """Procesa UN partido abierto (mismo shape de dict que devuelve
    `get_open_scanned_matches`) -- `rank_cache`/`live_cache` se pasan
    desde afuera para compartirse entre partidos del MISMO ciclo (evita
    pedir el ranking/score en vivo repetido por cada match del tour).

    Returns: 'processed' | 'skipped' -- nunca lanza (el caller decide
    si envolver en try/except; ver `tennis_match_tick_worker.py` que sí
    lo hace, con aislamiento explícito del tick de precio real)."""
    market_id = scanned["market_id"]
    if Set1EntrySignal.select().where(Set1EntrySignal.market_id == market_id).exists():
        return "skipped"

    tour = scanned["tour"]
    name_a, name_b = scanned["player_a_name"], scanned["player_b_name"]

    live_match = _find_live_match(name_a, name_b, tour, live_cache)
    if live_match is None:
        return "skipped"  # partido todavía no arrancó, o no está en la fuente de score en vivo

    set1_winner_side = get_set1_winner(name_a, name_b, tour)
    if set1_winner_side is None:
        return "skipped"  # set 1 todavía en curso -- fail-closed, no se adivina

    if tour not in rank_cache:
        rank_cache[tour] = _build_name_to_rank(tour)
    rank_a = _lookup_rank(name_a, rank_cache[tour])
    rank_b = _lookup_rank(name_b, rank_cache[tour])
    if rank_a is None or rank_b is None:
        return "skipped"  # sin ranking real de alguno de los 2 -- fail-closed

    favorite_is_a = rank_a < rank_b
    favorite_name = name_a if favorite_is_a else name_b
    underdog_name = name_b if favorite_is_a else name_a
    favorite_rank = min(rank_a, rank_b)
    underdog_rank = max(rank_a, rank_b)
    rank_gap = underdog_rank - favorite_rank

    favorite_side = "a" if favorite_is_a else "b"
    favorite_won_set1 = (set1_winner_side == favorite_side)

    recommendation = recommend_set1_entry(favorite_won_set1, rank_gap)

    Set1EntrySignal.create(
        market_id=market_id, favorite_name=favorite_name, underdog_name=underdog_name,
        favorite_rank=favorite_rank, underdog_rank=underdog_rank, rank_gap=rank_gap,
        favorite_won_set1=favorite_won_set1, size_pct=recommendation["size_pct"],
        favorite_win_rate_basis=recommendation.get("favorite_win_rate_basis"),
    )
    print(f"[set1_reaction] {market_id}: {recommendation}")
    return "processed"
