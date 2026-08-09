"""Cola de prioridad + chequeo de frescura para `TennisPlayerSyncStatus`
-- 2026-08-09, pedido explícito del usuario: el engine de scan
(`engine.py`) NUNCA debe fetchear en vivo la data de un jugador
(Flashscore/TennisExplorer) para decidir -- solo debe leer de la base y
verificar si `workers/tennis_match_history_sync_worker.py` ya la
actualizó, opcionalmente pidiéndole prioridad para un jugador nuevo.

Fail-closed por diseño, mismo criterio que el resto del paquete:
`is_player_data_fresh` devuelve False (nunca "listo") ante cualquier
duda -- un jugador nunca sincronizado, un pedido de prioridad todavía
sin procesar, o un error de DB, todos cuentan como "no confiar todavía"."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from database.db import init_db_historical
from database.historical.tennis.models import TennisPlayerSyncStatus


def _now():
    return datetime.now(timezone.utc)


def request_priority_sync(player_name: str) -> None:
    """Llamado por el ENGINE de scan cuando detecta un jugador nuevo
    (partido recién listado en Kalshi) y necesita su `MatchHistory`/
    ranking/stats actualizados antes de decidir. Idempotente: si ya hay
    un pedido pendiente (sin procesar todavía), lo deja igual -- no
    hace falta "reenviar" el pedido, el worker lo va a ver en su
    próximo ciclo de todos modos. Fail-closed: cualquier error de DB se
    traga (nunca tumba el scan real por esto)."""
    try:
        init_db_historical()
        row, created = TennisPlayerSyncStatus.get_or_create(
            player_name=player_name, defaults={"priority_requested_at": _now()},
        )
        if not created and (row.priority_processed_at is None or row.priority_processed_at < (row.priority_requested_at or _now())):
            return  # ya hay un pedido pendiente sin procesar, no hace falta tocar nada
        if not created:
            row.priority_requested_at = _now()
            row.save()
    except Exception:
        pass


def is_player_data_fresh(player_name: str, max_age_hours: float = 24.0) -> bool:
    """True SOLO si: (1) el jugador ya fue sincronizado alguna vez
    (`match_history_updated_at` no es None), (2) esa sincronización no
    es más vieja que `max_age_hours`, Y (3) si hubo un pedido de
    prioridad, ya fue procesado DESPUÉS de pedirse (nunca antes -- eso
    significaría que el pedido actual todavía está pendiente). Fail-
    closed: False ante cualquier duda o error de DB -- el engine debe
    tratar esto como "todavía no, esperar al próximo ciclo", nunca
    decidir con data potencialmente vieja/ausente."""
    try:
        init_db_historical()
        row = TennisPlayerSyncStatus.get_or_none(TennisPlayerSyncStatus.player_name == player_name)
        if row is None or row.match_history_updated_at is None:
            return False
        age = _now() - row.match_history_updated_at.replace(tzinfo=timezone.utc)
        if age > timedelta(hours=max_age_hours):
            return False
        if row.priority_requested_at is not None:
            if row.priority_processed_at is None or row.priority_processed_at < row.priority_requested_at:
                return False
        return True
    except Exception:
        return False


def get_pending_priority_requests() -> list[str]:
    """Llamado por el worker AL EMPEZAR cada ciclo, antes de su barrido
    normal por partido -- estos jugadores se procesan primero. Fail-
    closed: [] si falla la query (el worker sigue con su barrido normal
    igual, no se bloquea)."""
    try:
        init_db_historical()
        rows = TennisPlayerSyncStatus.select().where(
            TennisPlayerSyncStatus.priority_requested_at.is_null(False)
            & (
                TennisPlayerSyncStatus.priority_processed_at.is_null()
                | (TennisPlayerSyncStatus.priority_processed_at < TennisPlayerSyncStatus.priority_requested_at)
            )
        )
        return [r.player_name for r in rows]
    except Exception:
        return []


def mark_player_synced(player_name: str) -> None:
    """Llamado por el worker después de sincronizar de verdad a
    `player_name` (historial + stats) -- marca `match_history_updated_at`/
    `stats_checked_at` a ahora, y si había un pedido de prioridad
    pendiente, lo marca procesado. Fail-closed: error de DB se traga
    (nunca tumba el ciclo del worker por esto)."""
    try:
        init_db_historical()
        now = _now()
        row, _created = TennisPlayerSyncStatus.get_or_create(player_name=player_name)
        row.match_history_updated_at = now
        row.stats_checked_at = now
        if row.priority_requested_at is not None and (
            row.priority_processed_at is None or row.priority_processed_at < row.priority_requested_at
        ):
            row.priority_processed_at = now
        row.save()
    except Exception:
        pass
