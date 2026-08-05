"""
Valida la hipótesis completa del usuario sobre el dataset de 66
semifinales/finales reales de `scripts/sf_final_scan.py` (2026-08-02):

    "¿Qué % de underdogs <= 40% en su probabilidad pre-match subieron su
    probabilidad durante el partido en al menos edge + volatilidad_media/2?"

DIFERENCIA CLAVE vs `semifinal_convergence_backtest.py` (que usa
`LimitArbSignal`, la señal REAL que el motor generó en su momento, pero
solo existe para los ~6 partidos que nuestro propio tracking alcanzó a
ver en vivo desde 2026-07-31): este script cubre TODO el universo de 66
partidos que Kalshi todavía expone (ventana ~2 meses), la gran mayoría
de los cuales YA TERMINARON antes de que empezáramos a trackear. Para
esos, no existe una señal `LimitArbSignal` real -- se RECALCULA
`raw_edge`/`avg_volatility` corriendo `TennisFModel`/`TennisVModel`
ahora mismo, usando:
  - precio real PRE-MATCH (primer trade real del trade tape, `price_open`
    de `sf_final_scan_result.json`) como `current_a_price`/
    `current_b_price` -- real, no aproximado.
  - RANKING ACTUAL de hoy (vía `outer_apis.sports.tennis.rankings`), NO
    el ranking al momento histórico del partido -- limitación YA
    documentada en el proyecto (ver memoria
    kairos-tennis-markov-no-style-or-surface-clustering: "usa ranking
    actual no histórico-al-momento"), aceptada como mejor proxy
    disponible, NO oculta.
  - `surface=None` (el dataset de `sf_final_scan.py` no extrae
    rules_secondary/superficie) -- ambos modelos degradan con gracia a
    "superficie desconocida" cuando falta este dato, mismo patrón que el
    resto del código.

Fail-closed por partido: si no se puede recalcular `raw_edge`/
`avg_volatility` (ranking actual no encontrado para alguno de los 2,
precio pre-match real ausente), ese partido se excluye del cálculo, no
se fabrica un valor.

CRITERIOS (mismos que `semifinal_convergence_backtest.py`, aplicados acá
sobre el universo completo):
  1. underdog = la pierna con `price_open` menor.
  2. Se descarta si `price_open del underdog > 0.40`.
  3. `target = price_open_underdog + raw_edge + avg_volatility/2`. Se
     descarta si `target > 0.5`.
  4. "Convergió" (hit) si `price_max` real del underdog (trade tape,
     durante TODO el partido hasta el cierre) >= target."""

from __future__ import annotations

import json
from typing import Any

from models.fairprice.sports.tennis.tennisfmodel import TennisFModel
from models.volatility.sports.tennis.tennisvmodel import TennisVModel
from outer_apis.sports.tennis import rankings as tennis_rankings

_MAX_UNDERDOG_PRICE = 0.40
_MAX_TARGET = 0.50
_SERIES_TOUR = {"KXATPMATCH": "ATP", "KXWTAMATCH": "WTA"}
_RANKING_LOOKUP_DEPTH = 500


def _rank_lookup(tour: str) -> dict[str, int]:
    import re
    import unicodedata

    def _norm(name: str) -> str:
        nfkd = unicodedata.normalize("NFKD", name)
        ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
        return re.sub(r"[^a-z0-9 ]", " ", ascii_str.lower()).strip()

    players = tennis_rankings.get_top_rankings(n=_RANKING_LOOKUP_DEPTH, tour=tour)
    return {_norm(p["nombre"]): p["ranking"] for p in players if p.get("nombre") and p.get("ranking")}


def _lookup_rank(name: str, name_to_rank: dict[str, int]) -> int | None:
    import difflib
    import re
    import unicodedata

    def _norm(n: str) -> str:
        nfkd = unicodedata.normalize("NFKD", n)
        ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
        return re.sub(r"[^a-z0-9 ]", " ", ascii_str.lower()).strip()

    target = _norm(name)
    if target in name_to_rank:
        return name_to_rank[target]
    matches = difflib.get_close_matches(target, list(name_to_rank.keys()), n=1, cutoff=0.85)
    return name_to_rank[matches[0]] if matches else None


def validate_hypothesis(dataset_path: str = "sf_final_scan_result.json") -> dict[str, Any]:
    with open(dataset_path, encoding="utf-8") as f:
        matches = json.load(f)

    fmodel = TennisFModel()
    vmodel = TennisVModel()
    rank_cache: dict[str, dict[str, int]] = {}

    cases = []
    excluded_no_price = 0
    excluded_no_rank = 0
    excluded_underdog_above_40 = 0
    excluded_target_above_50 = 0

    for m in matches:
        price_a = m.get("player_a_price_open")
        price_b = m.get("player_b_price_open")
        if price_a is None or price_b is None:
            excluded_no_price += 1
            continue

        tour = _SERIES_TOUR.get(m["series"])
        if tour not in rank_cache:
            rank_cache[tour] = _rank_lookup(tour)
        rank_a = _lookup_rank(m["player_a"], rank_cache[tour])
        rank_b = _lookup_rank(m["player_b"], rank_cache[tour])
        if rank_a is None or rank_b is None:
            excluded_no_rank += 1
            continue

        market = {
            "current_a_price": price_a, "current_b_price": price_b,
            "player_a_rank": rank_a, "player_b_rank": rank_b,
            "player_a_name": m["player_a"], "player_b_name": m["player_b"],
            "tour": tour, "surface": None, "tournament": m.get("tournament"),
            "best_of": 3,
        }

        fair = fmodel.calculate_fair_price(market)
        volatility = vmodel.calculate_volatility(market)

        if price_a >= price_b:
            fav_side, fav_price, fav_fair = "a", price_a, fair["fair_a_price"]
            dog_side, dog_price = "b", price_b
        else:
            fav_side, fav_price, fav_fair = "b", price_b, fair["fair_b_price"]
            dog_side, dog_price = "a", price_a

        if dog_price > _MAX_UNDERDOG_PRICE:
            excluded_underdog_above_40 += 1
            continue

        raw_edge = abs(fav_price - fav_fair)
        target = dog_price + raw_edge + volatility / 2.0
        if target > _MAX_TARGET:
            excluded_target_above_50 += 1
            continue

        dog_max = m.get(f"player_{dog_side}_price_max")
        if dog_max is None:
            continue

        hit = dog_max >= target
        cases.append({
            "event_ticker": m["event_ticker"], "tournament": m.get("tournament"),
            "round": m.get("round"), "underdog": m[f"player_{dog_side}"],
            "favorite": m[f"player_{fav_side}"], "dog_price_open": dog_price,
            "raw_edge": round(raw_edge, 4), "avg_volatility": round(volatility, 4),
            "target": round(target, 4), "dog_price_max": dog_max, "hit": hit,
        })

    n = len(cases)
    hits = sum(1 for c in cases if c["hit"])

    return {
        "total_matches_in_dataset": len(matches),
        "excluded_no_price_data": excluded_no_price,
        "excluded_no_rank_data": excluded_no_rank,
        "excluded_underdog_above_40pct": excluded_underdog_above_40,
        "excluded_target_above_50pct": excluded_target_above_50,
        "evaluable_cases": n,
        "hits": hits,
        "hit_rate": round(hits / n, 4) if n else None,
        "cases": cases,
        "caveat": (
            "raw_edge/avg_volatility RECALCULADOS con ranking ACTUAL de "
            "hoy, no el histórico al momento de cada partido -- limitación "
            "conocida y documentada, no oculta. NO incluye el gate de "
            "fatiga/edad/lesión de semifinal_convergence_backtest.py "
            "(ese exige LimitArbSignal real, que no existe para la "
            "mayoría de estos 66 partidos)."
        ),
    }


if __name__ == "__main__":
    print(json.dumps(validate_hypothesis(), indent=2, ensure_ascii=False))
