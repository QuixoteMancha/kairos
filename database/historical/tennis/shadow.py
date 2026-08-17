"""Persistencia y settlement simulado de BetOnFavourite, sin órdenes reales."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from database.db import db_historical, init_db_historical
from database.historical.tennis.models import (
    FavouriteShadowDecision, Match, ProbabilityTick,
)


STRATEGY_VERSION = "bet_on_favourite_v1"


def record_shadow_decision(proposal: dict[str, Any]) -> FavouriteShadowDecision | None:
    """Guarda la propuesta sólo si existe un tick real para ese mercado/lado."""
    try:
        init_db_historical()
        match = Match.get_or_none(Match.market_id == proposal.get("market_id"))
        if match is None or match.winner is not None:
            return None
        side = proposal.get("fav_side")
        if side not in {"a", "b"}:
            return None
        price_field = (
            ProbabilityTick.market_price_a if side == "a"
            else ProbabilityTick.market_price_b
        )
        tick = (
            ProbabilityTick.select()
            .where((ProbabilityTick.match == match) & price_field.is_null(False))
            .order_by(ProbabilityTick.observed_at.desc(), ProbabilityTick.id.desc())
            .first()
        )
        if tick is None:
            return None
        entry = tick.market_price_a if side == "a" else tick.market_price_b
        row, _ = FavouriteShadowDecision.get_or_create(
            probability_tick=tick,
            strategy_version=STRATEGY_VERSION,
            defaults={
                "match": match,
                "decision_at": datetime.now(timezone.utc),
                "favourite_side": side,
                "ticker": proposal["ticker"],
                "favourite_name": proposal["fav_name"],
                "underdog_name": proposal["dog_name"],
                "entry_market_probability": float(entry),
                "model_probability": float(proposal["model_probability"]),
                "model_probability_before_injury": proposal.get("model_probability_before_injury"),
                "model_edge": float(proposal["model_edge"]),
                "proposed_stake_usd": float(proposal["stake_usd"]),
                "features_json": json.dumps(proposal, default=str, sort_keys=True),
            },
        )
        return row
    except Exception:
        return None


def settle_shadow_decisions(limit: int = 200) -> dict[str, int]:
    """Liquida señales abiertas con ganador y último tick reales."""
    init_db_historical()
    rows = list(
        FavouriteShadowDecision.select(FavouriteShadowDecision, Match)
        .join(Match)
        .where(
            (FavouriteShadowDecision.status == "open")
            & Match.winner.is_null(False)
        )
        .limit(limit)
    )
    settled = errors = 0
    for row in rows:
        try:
            match = row.match
            closing_tick = (
                ProbabilityTick.select()
                .where(ProbabilityTick.match == match)
                .order_by(ProbabilityTick.observed_at.desc(), ProbabilityTick.id.desc())
                .first()
            )
            closing = None
            if closing_tick is not None:
                closing = (
                    closing_tick.market_price_a if row.favourite_side == "a"
                    else closing_tick.market_price_b
                )
            won = match.winner == row.favourite_side
            entry = float(row.entry_market_probability)
            stake = float(row.proposed_stake_usd)
            pnl = stake * ((1.0 / entry) - 1.0) if won else -stake
            with db_historical.atomic():
                row.status = "won" if won else "lost"
                row.settled_at = match.settled_at or datetime.now(timezone.utc)
                row.closing_market_probability = closing
                row.clv = float(closing) - entry if closing is not None else None
                row.simulated_pnl_usd = round(pnl, 4)
                row.save()
            settled += 1
        except Exception:
            errors += 1
    return {"eligible": len(rows), "settled": settled, "errors": errors}
