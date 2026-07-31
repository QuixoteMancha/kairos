"""Winrate de un jugador contra un GRUPO de rivales similares al rival
actual -- pedido explícito del usuario (2026-07-31): "para cuando no hay
encuentros previos entre 2 players, se construye una estadística player
vs grupo de rival". El grupo se define como jugadores con:
  1. Estilo de juego similar al rival actual (vector de 4 dimensiones
     real: ace%, DF%, hold%, break%, todos por superficie -- ver
     `outer_apis/sports/tennis/tennisabstract_player_stats.py`).
  2. Ranking similar al rival actual (ventana de ±RANK_WINDOW).
  3. Misma superficie del partido en cuestión.

DECISIÓN DE DISEÑO (2026-07-31, ver memoria
kairos-tennis-style-tagging-investigation-2026-07-31): se investigó usar
el vector de "estilo" real del Match Charting Project (RallyAgg/
ReturnAgg/NetFreq/SnVFreq) pero solo ~62 jugadores ATP tienen suficiente
data cargada en 12 meses -- cobertura demasiado angosta para la mayoría
de los partidos reales de Kalshi (rankings 100+). Se usa en cambio
nuestro propio `career-splits` (ace%/DF%/hold%/break% por superficie),
con MUCHA mejor cobertura real (viene de resultados de partido agregados,
no de charting manual) aunque es una aproximación más burda de "estilo".

El pool de candidatos para armar el grupo de rivales crece
ORGÁNICAMENTE: son todos los jugadores que YA tengan un career-split
guardado para esa superficie (fetched la primera vez que aparecieron en
un mercado real) cruzados con el ranking más reciente ya persistido
(`TennisPlayerRanking`, poblado en cada scan real). No se pre-carga el
circuito completo de una sola vez -- eso violaría el mismo principio de
"solo fetch cuando se necesite" aplicado en todo este pipeline.

Fail-closed en cada paso: sin vector de estilo del rival, sin candidatos
suficientes, o sin muestra suficiente de partidos reales contra el grupo
-> None, nunca se fabrica un winrate."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from database.historical.tennis.writer import get_all_career_splits_for_surface, get_latest_rankings, get_all_surface_matches
from outer_apis.sports.tennis.tennisabstract_player_stats import get_career_win_pct_by_surface

RANK_WINDOW = 40  # candidatos dentro de ±40 posiciones de ranking del rival actual
MAX_PEER_GROUP_SIZE = 15  # tope de vecinos más cercanos por estilo a considerar
MIN_PEER_GROUP_SIZE = 3  # fail-closed: menos candidatos reales que esto -> sin grupo confiable
MIN_MATCHES_VS_PEERS = 3  # fail-closed: menos partidos reales contra el grupo -> sin señal confiable

_STYLE_FIELDS = ("ace_pct", "df_pct", "hold_pct", "break_pct")


def _norm(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9 ]", " ", ascii_str.lower()).strip()


def _style_vector(split: dict[str, Any]) -> tuple[float, ...] | None:
    values = tuple(split.get(f) for f in _STYLE_FIELDS)
    return values if all(v is not None for v in values) else None


def _minmax_normalize(vectors: list[tuple[float, ...]]) -> list[tuple[float, ...]]:
    """Normaliza cada dimensión a [0,1] sobre el pool de candidatos, para
    que ace%/hold% (escalas naturales distintas) pesen comparable en la
    distancia euclidea. Fail-closed defensivo: una dimensión constante
    (min==max) se normaliza a 0.0 para todos, no divide por cero."""
    n_dims = len(vectors[0])
    mins = [min(v[i] for v in vectors) for i in range(n_dims)]
    maxs = [max(v[i] for v in vectors) for i in range(n_dims)]
    ranges = [(maxs[i] - mins[i]) or 1.0 for i in range(n_dims)]
    return [tuple((v[i] - mins[i]) / ranges[i] if maxs[i] != mins[i] else 0.0 for i in range(n_dims)) for v in vectors]


def get_style_peer_group(opponent_name: str, opponent_rank: int | None, surface: str, tour: str) -> list[str] | None:
    """Devuelve hasta `MAX_PEER_GROUP_SIZE` nombres de jugadores
    estilísticamente más parecidos a `opponent_name` en `surface`, dentro
    de una ventana de ranking de ±`RANK_WINDOW`. Fail-closed: None si
    falta el ranking del rival, su vector de estilo, o el pool de
    candidatos reales es menor a `MIN_PEER_GROUP_SIZE`."""
    if opponent_rank is None or not surface or tour not in ("ATP", "WTA"):
        return None

    opponent_split = get_career_win_pct_by_surface(opponent_name, surface)
    if opponent_split is None:
        return None
    opponent_vector = _style_vector(opponent_split)
    if opponent_vector is None:
        return None

    all_splits = get_all_career_splits_for_surface(surface)
    rankings = get_latest_rankings(tour)
    if not all_splits or not rankings:
        return None

    opponent_norm = _norm(opponent_name)
    candidate_names: list[str] = []
    candidate_vectors: list[tuple[float, ...]] = []
    for name, split in all_splits.items():
        if _norm(name) == opponent_norm:
            continue  # el rival no es su propio peer
        rank = rankings.get(name)
        if rank is None or abs(rank - opponent_rank) > RANK_WINDOW:
            continue
        vector = _style_vector(split)
        if vector is None:
            continue
        candidate_names.append(name)
        candidate_vectors.append(vector)

    if len(candidate_names) < MIN_PEER_GROUP_SIZE:
        return None

    normalized = _minmax_normalize(candidate_vectors + [opponent_vector])
    normalized_candidates, normalized_opponent = normalized[:-1], normalized[-1]

    def _distance(v: tuple[float, ...]) -> float:
        return sum((a - b) ** 2 for a, b in zip(v, normalized_opponent)) ** 0.5

    ranked = sorted(zip(candidate_names, normalized_candidates), key=lambda item: _distance(item[1]))
    return [name for name, _ in ranked[:MAX_PEER_GROUP_SIZE]]


def get_winrate_vs_peer_group(
    player_name: str, opponent_name: str, opponent_rank: int | None, surface: str, tour: str,
) -> dict[str, Any] | None:
    """Winrate real de `player_name` contra el grupo de rivales
    estilísticamente similares a `opponent_name` (ver
    `get_style_peer_group`), en `surface`. Pensado para cuando no hay
    encuentros previos reales entre `player_name` y `opponent_name` --
    aproxima "cómo le va a este jugador contra rivales de este perfil"
    en vez de "cómo le va contra ESTE rival puntual" (que sería el H2H
    directo, preferido cuando existe).

    Fail-closed: None si no se pudo armar el grupo de rivales, o si
    `player_name` tiene menos de `MIN_MATCHES_VS_PEERS` partidos reales
    (dentro de 365 días) contra jugadores de ese grupo en esa superficie."""
    peers = get_style_peer_group(opponent_name, opponent_rank, surface, tour)
    if peers is None:
        return None

    peers_norm = {_norm(p) for p in peers}
    matches = get_all_surface_matches(player_name, surface)
    vs_peers = [m for m in matches if _norm(m["opponent"]) in peers_norm]

    if len(vs_peers) < MIN_MATCHES_VS_PEERS:
        return None

    wins = sum(1 for m in vs_peers if m["result"] == "W")
    return {"win_rate": round(wins / len(vs_peers), 4), "n_matches": len(vs_peers), "peer_group": peers}
