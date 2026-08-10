"""
Persists real tennis data (already fetched by outer_apis/tennis_*.py) into the
historical DB (database/historical/tennis/ models) — called from those
facades on a FRESH fetch only (not on every in-process cache hit, to
avoid flooding the DB with redundant snapshots of unchanged data).

Fail-closed by design: every function here catches and swallows its own
exceptions (logged, not raised) — persistence is a side effect, a DB
hiccup must never break the caller's actual data path (a scan needing
real-time rankings/calibration/match-history shouldn't fail just because
writing the historical record failed).
"""

from datetime import datetime, timedelta, timezone
from typing import Any

# Ventana por default para get_all_surface_matches -- más amplia que los
# 6 meses de surface_form.py (LOOKBACK_DAYS=183) a propósito: el stat de
# "winrate vs grupo de rivales similares" (style_peer_group.py) necesita
# la mayor muestra real posible, no solo forma reciente.
LOOKBACK_DAYS_DEFAULT = 365.0

from database.db import init_db_historical
from database.historical.tennis.models import (
    TennisPlayerRanking, TennisServeCalibration,
    TennisHeadToHead, TennisAbstractElo,
    TennisAbstractYelo, TennisAbstractCareerSplit, TennisAbstractH2H,
    TennisRecentResult, MatchHistory, Player,
)

# 2026-08-09, pedido explícito del usuario -- "cualquier tabla de matches
# independiente del deporte debe tener la estructura 1 fila por match" --
# `TennisMatchHistory`/`TennisSurfaceMatchHistory` violaban esto (1 fila
# POR JUGADOR por partido, ver docstrings viejos de `save_match_history`/
# `save_surface_match_history` más abajo). Se reemplaza su lectura/
# escritura por `MatchHistory` (ya diseñada 1-fila-por-partido, ver
# `models.py`), sin cambiar la forma externa que consumen los callers
# (`outer_apis/sports/tennis/match_history.py`, `surface_form.py`,
# `style_peer_group.py`) -- misma firma, mismos dicts de salida.
#
# Idempotencia entre los DOS jugadores de un mismo partido real (cada uno
# puede fetchear su propio historial y "ver" el mismo partido): se busca
# una fila existente por (match_date, {A,B} sin importar el orden) antes
# de crear una nueva -- si ya existe (la creó el otro jugador, o ya vino
# de `save_match_detail` con match_id real de TennisExplorer), NUNCA se
# duplica. `match_id` sintético (`recentform:{par ordenado}|{fecha}`)
# usado solo cuando no hay fila previa -- si más tarde llega el detalle
# real del partido (match_id real), `save_match_detail` lo trata como un
# match_id DISTINTO y crearía una segunda fila; se acepta ese caso residual
# (no se resuelve acá, ver memoria de la sesión) porque intentar
# fusionarlos requeriría re-arquitecturar `save_match_detail` también, y
# el problema real que se pedía resolver ahora es la duplicación
# POR-JUGADOR-en-cada-fetch, no cross-fuente."""


def _tokens(name: str) -> list[str]:
    return [t.strip(".") for t in name.replace("-", " ").split() if t.strip(".")]


def _names_confirm_identity(name_a: str, name_b: str) -> bool:
    """Verificación de identidad por APELLIDO + NOMBRE/INICIAL (no solo
    apellido) -- 2026-08-10, FIX DE CAUSA RAÍZ de la triplicación real
    de partidos en `MatchHistory`/`TennisMatchHistoryUnvalidated` (ver
    kairos-matchhistory-triplication-dedup-2026-08-10): antes de este
    fix, `_find_existing_match_history_row` comparaba por IGUALDAD DE
    STRING EXACTA, y el mismo partido real casi siempre llega con
    formato de nombre distinto según quién lo escribió ("Elina
    Svitolina" vs "Svitolina Elina", mayúsculas del rival distintas) --
    nunca matcheaba, generando una fila nueva cada vez.

    Tolerante a las 3 convenciones reales de orden del proyecto
    ("Nombre Apellido" / "Apellido Nombre" / "Apellido Inicial.") sin
    necesitar saber cuál es cada lado -- el apellido se aproxima con el
    TOKEN MÁS LARGO (heurística verificada en vivo). Exige TAMBIÉN
    nombre/inicial coincidente, no solo apellido -- evita colisiones
    reales de personas distintas con el mismo apellido (ver
    kairos-critical-misattribution-bug-2026-08-06: "Casper Ruud" vs
    "Christian Ruud", padre/hijo). Fail-closed: si a algún lado le
    falta el nombre de pila (1 solo token), no se confirma."""
    tokens_a, tokens_b = _tokens(name_a), _tokens(name_b)
    if not tokens_a or not tokens_b:
        return False
    surname_a = max(tokens_a, key=len).lower()
    surname_b = max(tokens_b, key=len).lower()
    if surname_a != surname_b:
        return False
    given_a = [t.lower() for t in tokens_a if t.lower() != surname_a]
    given_b = [t.lower() for t in tokens_b if t.lower() != surname_b]
    if not given_a or not given_b:
        return False
    for ga in given_a:
        for gb in given_b:
            if ga == gb or (len(ga) == 1 and gb.startswith(ga)) or (len(gb) == 1 and ga.startswith(gb)):
                return True
    return False


def _find_existing_match_history_row(name_a: str, name_b: str, match_date: str):
    """2026-08-10, fix de causa raíz -- ya NO filtra por igualdad de
    string exacta en la query SQL (eso era el bug real, ver
    `_names_confirm_identity`). Trae las filas de la MISMA fecha (bien
    acotado, un calendar-day real nunca tiene más que unos pocos cientos
    de partidos en toda la DB) y confirma identidad de ambos lados en
    Python, tolerante a formato de nombre."""
    candidates = MatchHistory.select().where(MatchHistory.match_date == match_date)
    for row in candidates:
        if (
            _names_confirm_identity(name_a, row.player_a_name) and _names_confirm_identity(name_b, row.player_b_name)
        ) or (
            _names_confirm_identity(name_a, row.player_b_name) and _names_confirm_identity(name_b, row.player_a_name)
        ):
            return row
    return None


def _synthetic_recentform_match_id(name_a: str, name_b: str, match_date: str) -> str:
    pair = "|".join(sorted([name_a.strip().lower(), name_b.strip().lower()]))
    return f"recentform:{pair}|{match_date}"


def _resolve_player_id(
    name: str, tour: str | None = None, slug: str | None = None, birthdate=None,
    height_cm: int | None = None, weight_kg: int | None = None, plays: str | None = None,
) -> int | None:
    """Busca-o-crea la fila `Player` canónica para `name` -- 2026-08-10,
    Fase 1 de la migración a entidad canónica de jugador (pedido
    explícito del usuario: "los datos de player deben ir en el modelo
    de player, y simplemente referenciar a los players en los
    matches"). Reusa `_names_confirm_identity` (apellido + nombre/
    inicial, tolerante a formato) para encontrar un `Player` YA
    existente con un formato de nombre distinto -- mismo criterio que
    ya usa `_find_existing_match_history_row`, no igualdad exacta.

    Enriquecimiento PROGRESIVO: si el `Player` existente tiene algún
    campo NULL y esta llamada trae un valor real, se completa (nunca se
    sobreescribe un valor ya confirmado con uno distinto -- eso sería
    tarea de `fix_tour_sex_mismatches`, no de este resolver silencioso
    en el hot path de escritura).

    Fail-closed: `None` si `name` viene vacío -- nunca crea un `Player`
    fantasma. Nunca excepciona hacia el caller (mismo criterio del
    resto de este archivo) -- un fallo acá no debe romper el guardado
    del partido en sí."""
    if not name or not name.strip():
        return None
    try:
        init_db_historical()
        existing = Player.get_or_none(Player.canonical_name == name)
        if existing is None:
            for candidate in Player.select():
                if _names_confirm_identity(name, candidate.canonical_name):
                    existing = candidate
                    break

        now = _now()
        if existing is None:
            player = Player.create(
                canonical_name=name, tour=tour, slug=slug, birthdate=birthdate,
                height_cm=height_cm, weight_kg=weight_kg, plays=plays, created_at=now,
            )
            return player.id

        changed = False
        for field_name, value in (
            ("tour", tour), ("slug", slug), ("birthdate", birthdate),
            ("height_cm", height_cm), ("weight_kg", weight_kg), ("plays", plays),
        ):
            if value is not None and getattr(existing, field_name) is None:
                setattr(existing, field_name, value)
                changed = True
        if changed:
            existing.updated_at = now
            existing.save()
        return existing.id
    except Exception:
        return None

# Campos comparables de `MatchHistory` para detectar cambios reales
# de la fuente en `save_match_detail` -- excluye `match_id` (la clave) y
# los timestamps propios (`fetched_at`/`updated_at`, nunca son "el dato
# cambió", son metadata nuestra).
_MATCH_DETAIL_COMPARABLE_FIELDS = (
    "match_date", "tournament", "tour", "round", "surface", "score", "winner_side",
    "player_a_name", "player_a_slug", "player_a_ranking", "player_a_birthdate",
    "player_a_height_cm", "player_a_weight_kg", "player_a_plays",
    "player_b_name", "player_b_slug", "player_b_ranking", "player_b_birthdate",
    "player_b_height_cm", "player_b_weight_kg", "player_b_plays",
    "player_a_id", "player_b_id",
)


def _now():
    return datetime.now(timezone.utc)


def save_rankings_snapshot(tour: str, players: list[dict[str, Any]]) -> None:
    """One new row per player, per fresh fetch -- deliberately NOT
    deduped: this is a time series (ranking history), each fetch is a
    real new data point. Bounded naturally by the 1h cache TTL in
    outer_apis/tennis_rankings.py (a fresh fetch only happens that often)."""
    if not players:
        return
    try:
        init_db_historical()
        fetched_at = _now()
        rows = [
            {
                "player_name": p["nombre"],
                "tour": tour,
                "ranking": p["ranking"],
                "country": p.get("pais"),
                "fetched_at": fetched_at,
            }
            for p in players
            if p.get("nombre") and p.get("ranking")
        ]
        if rows:
            TennisPlayerRanking.insert_many(rows).execute()
    except Exception:
        pass


def save_serve_calibration_snapshot(tour: str, calibration: dict[tuple[int, int], dict[str, Any]]) -> None:
    """One row per rank bucket, per fresh calibration run -- also a time
    series (is p_serve drifting over time? is n_matches growing?).
    Bounded by the 6h cache TTL in outer_apis/tennis_serve_stats.py."""
    if not calibration:
        return
    try:
        init_db_historical()
        calibrated_at = _now()
        rows = [
            {
                "tour": tour,
                "bucket_min": bucket[0],
                "bucket_max": bucket[1],
                "p_serve": data["p_serve"],
                "n_matches": data["n_matches"],
                "n_players": data["n_players"],
                "calibrated_at": calibrated_at,
            }
            for bucket, data in calibration.items()
        ]
        if rows:
            TennisServeCalibration.insert_many(rows).execute()
    except Exception:
        pass


def _identity_matches_detail_side(known_name: str, detail_name: str) -> bool:
    """`known_name` (nombre que ya conocemos, ej. el que trackea el tick
    worker de Kalshi) confirma contra `detail_name` (nombre nativo de
    TennisExplorer, "Apellido Nombre") -- comparación por SUBCONJUNTO de
    tokens, no igualdad exacta ni orden fijo: tolera que `detail_name`
    traiga un token extra (ej. año de nacimiento entre paréntesis para
    desambiguar homónimos, visto en vivo con "Lee Eunhye (2000)") sin
    dejar de confirmar identidad real."""
    known_tokens = frozenset(known_name.lower().split())
    detail_tokens = frozenset(detail_name.lower().split())
    return bool(known_tokens) and known_tokens.issubset(detail_tokens)


def save_match_history(player_name: str, matches: list[dict[str, Any]]) -> None:
    """1 fila por PARTIDO real en `MatchHistory` (no 1 por jugador --
    ver nota de migración arriba). Deduped vía `_find_existing_match_history_row`
    contra (match_date, {player_name, opponent} en cualquier orden) --
    un partido real no cambia una vez jugado, y si el otro jugador (o
    `save_match_detail`) ya lo registró, no se duplica.

    2026-08-09, pedido explícito del usuario: "save_match_history debe
    ser modificada usando la lógica del script que estamos corriendo
    que está normalizando y filleando masivamente... sacar los nombres
    completos" -- antes esta función SIEMPRE persistía el rival tal
    cual aparece en la tabla de resultados propia del jugador (casi
    siempre ABREVIADO, ej. "Sabalenka A.", confirmado en producción:
    95% de las filas `source="tennisexplorer"` tienen al menos un lado
    abreviado). Ahora, si la fila trae un `match_id` real (gratis del
    mismo HTML, ver `get_player_recent_matches`), se resuelve identidad
    completa de AMBOS lados vía `get_match_detail` + `save_match_detail`
    -- MISMO mecanismo que ya usa el worker de backfill
    (`validate_unresolved_via_live_fetch.py::process_player`), aplicado
    en vivo al momento de escribir en vez de esperar a que el backfill
    lo alcance después. Fail-closed y con fallback real: si no hay
    `match_id`, la resolución falla (red/parseo), o la identidad no
    confirma contra el jugador que ya conocemos, se cae al comportamiento
    anterior (fila con el rival abreviado) -- nunca se pierde el
    partido por un fallo de la resolución más rica."""
    if not matches:
        return
    try:
        init_db_historical()
        fetched_at = _now()
        for m in matches:
            opponent = m.get("oponente")
            match_date = m.get("fecha")
            score = m.get("score")
            result = m.get("resultado")
            if not (opponent and match_date and result):
                continue
            if _find_existing_match_history_row(player_name, opponent, match_date) is not None:
                continue

            match_id = m.get("match_id")
            if match_id and _resolve_and_save_via_match_detail(player_name, match_id):
                continue  # identidad completa ya resuelta y persistida

            winner_side = "a" if result == "W" else "b"
            synthetic_id = _synthetic_recentform_match_id(player_name, opponent, match_date)
            try:
                MatchHistory.create(
                    match_id=synthetic_id,
                    source=m.get("source", "tennisexplorer"),
                    match_date=match_date,
                    score=score,
                    winner_side=winner_side,
                    player_a_name=player_name,
                    player_b_name=opponent,
                    fetched_at=fetched_at,
                )
            except Exception:
                pass  # carrera real: el otro jugador/save_match_detail lo creó justo antes -- no duplicar
    except Exception:
        pass


def _resolve_and_save_via_match_detail(known_player_name: str, match_id: str) -> bool:
    """Intenta resolver identidad completa (`get_match_detail`) y
    persistir (`save_match_detail`) -- `True` solo si de verdad se
    guardó algo Y la identidad confirma contra `known_player_name`
    (nunca confiar ciegamente en el `match_id` sin re-chequear, mismo
    criterio que `process_player`). Import perezoso del scraper (única
    excepción documentada a la separación fetch/persist de este
    archivo, ver docstring de `save_match_history`) -- fail-closed:
    `False` ante cualquier fallo de red/parseo/identidad, nunca
    excepciona hacia el caller.

    2026-08-09, pedido explícito del usuario: "esta función también
    podría verificar el tour y el tournament y por supuesto el level"
    -- `tour`/`tournament` ya vienen de `get_match_detail` (parseados
    del link real del torneo, sin request extra); `level` NO viene de
    TennisExplorer (nunca expone la categoría del torneo en su HTML) --
    se resuelve acá mismo, en el momento, vía
    `resolve_level_for_tournament` (lookup puntual contra los catálogos
    `AtpTournamentLevel`/`WtaTournamentLevel` ya poblados, sin red
    extra), en vez de esperar a que lo alcance el backfill offline
    (`backfill_matchhistory_level_from_sackmann.py::run_backfill`, que
    sigue corriendo igual como red de seguridad para lo que esta
    resolución en vivo no llegue a cubrir)."""
    try:
        from outer_apis.scrapers.tennis.tennisexplorer_player_scraper import get_match_detail
        from scripts.tennis.backfill_matchhistory_level_from_sackmann import resolve_level_for_tournament

        detail = get_match_detail(match_id)
        if detail is None:
            return False
        if not (
            _identity_matches_detail_side(known_player_name, detail["player_a_name"])
            or _identity_matches_detail_side(known_player_name, detail["player_b_name"])
        ):
            return False
        status = save_match_detail(detail)
        if status not in ("created", "updated", "unchanged"):
            return False

        match_date = detail.get("match_date")
        year = int(match_date[:4]) if match_date and match_date[:4].isdigit() else None
        level, tour_bonus = resolve_level_for_tournament(detail.get("tournament"), year, detail.get("tour"))
        if level is not None or tour_bonus is not None:
            row = MatchHistory.get_or_none(MatchHistory.match_id == match_id)
            if row is not None:
                fields = {}
                if level is not None and row.level is None:
                    fields["level"] = level
                if tour_bonus is not None and row.tour is None:
                    fields["tour"] = tour_bonus
                if fields:
                    MatchHistory.update(**fields).where(MatchHistory.id == row.id).execute()
        return True
    except Exception:
        return False


def _hint_matches_match_text(hint: str, match_text: str) -> bool:
    """`True` si algún token real (>1 char) de `hint` aparece como
    palabra completa en `match_text` -- 2026-08-09, bug real encontrado
    en producción: `expected_opponent_hint` viene en el orden que use el
    caller (ej. Kalshi: "Nombre Apellido"), pero `match_text` viene en
    el orden nativo de TennisExplorer ("Apellido Inicial.", ej.
    "Alexandrova E. - Svitolina E.") -- una comparación de SUBSTRING
    directa (el nombre completo del hint dentro del string crudo)
    NUNCA matcheaba por el orden invertido, tumbando el 100% de las
    validaciones reales (verificado en vivo: 8/8 fallos en el primer
    ciclo del hilo de enriquecimiento, incluyendo un caso real conocido,
    Alexandrova-Svitolina). Comparación por tokens, no por orden."""
    hint_tokens = {t.lower() for t in hint.replace("-", " ").split() if len(t) > 1}
    text_tokens = {t.strip(".,").lower() for t in match_text.replace("-", " ").split()}
    return bool(hint_tokens & text_tokens)


def save_next_match(player_name: str, expected_opponent_hint: str | None = None) -> bool:
    """Resuelve+persiste el partido PROGRAMADO (aún no jugado) de la
    sección "Next match" del perfil de `player_name` -- 2026-08-09,
    pedido explícito del usuario: "puede que no aparezca [el nombre
    completo] porque el match aún no habría corrido cuando aparezca en
    Kalshi y el worker lo detecte... TennisExplorer en la página de
    player tiene un campo que dice next match, ese match tiene que ser
    validado". Reusa el MISMO mecanismo que ya resuelve partidos
    jugados (`_resolve_and_save_via_match_detail` -- `get_match_detail`
    ya tolera partidos sin score, ver el fix de
    `_MATCH_DETAIL_HEADER_RE`), así que `tour`/`tournament`/`level` se
    completan igual que para un partido ya jugado.

    VALIDACIÓN (pedido explícito, "ese match tiene que ser validado"):
    `expected_opponent_hint` -- si el caller ya conoce el nombre (o
    apellido) del rival por otra vía (ej. el mercado de Kalshi que
    disparó esta consulta), se exige que aparezca en el texto crudo del
    partido (`match_text`, ej. "Alexandrova E. - Svitolina E.") ANTES
    de resolver nada -- evita persistir el próximo partido de
    `player_name` cuando en realidad es uno DISTINTO al que el caller
    esperaba (ej. el jugador ya jugó y tiene un partido siguiente
    distinto para cuando el worker vuelve a consultar). Sin hint
    (`None`), se confía en que `player_name` + la identidad real de
    `get_match_detail` ya son suficiente verificación (mismo criterio
    fail-closed de `_resolve_and_save_via_match_detail`).

    Fail-closed: `False` si no hay próximo partido, la validación del
    hint falla, o la resolución/persistencia falla por cualquier
    motivo."""
    try:
        from outer_apis.scrapers.tennis.tennisexplorer_player_scraper import get_next_match

        next_match = get_next_match(player_name)
        if next_match is None or not next_match.get("match_id"):
            return False
        if expected_opponent_hint and not _hint_matches_match_text(
            expected_opponent_hint, next_match.get("match_text") or "",
        ):
            return False
        return _resolve_and_save_via_match_detail(player_name, next_match["match_id"])
    except Exception:
        return False


def has_real_match_detail_between(player_a_name: str, player_b_name: str) -> bool:
    """`True` si YA existe una fila `MatchHistory` con identidad real
    (`match_id` NO sintético, ej. no `recentform:...`) para ESTE par de
    jugadores (cualquier fecha, cualquier orden de lados) -- 2026-08-09,
    pedido explícito del usuario: separar el guardado de ticks de Kalshi
    (rápido, síncrono) del "fill" del partido con datos de TennisExplorer
    (async, con delay real) EN EL MISMO WORKER -- este chequeo es la
    parte barata/sin red que decide si vale la pena pagar el costo de
    red de `save_next_match`, evita re-fetchear en cada ciclo un partido
    que ya se resolvió.

    Sin red, solo lectura de DB (índice por nombres) -- seguro de llamar
    en cada ciclo del loop de enriquecimiento sin generar tráfico extra."""
    init_db_historical()
    row = MatchHistory.get_or_none(
        ((MatchHistory.player_a_name == player_a_name) & (MatchHistory.player_b_name == player_b_name))
        | ((MatchHistory.player_a_name == player_b_name) & (MatchHistory.player_b_name == player_a_name))
    )
    return row is not None and not row.match_id.startswith("recentform:") and not row.match_id.startswith("tmldb:")


def _swap_match_detail_sides(fields: dict[str, Any]) -> dict[str, Any]:
    """Invierte player_a_*/player_b_*/winner_side de un dict de campos
    de `save_match_detail` -- usado cuando la fila EXISTENTE en
    `MatchHistory` (encontrada por par de jugadores + fecha, no por
    match_id) tiene los lados en el orden opuesto al que trae el
    `detail` recién resuelto."""
    swapped = dict(fields)
    for suffix in ("name", "slug", "ranking", "birthdate", "height_cm", "weight_kg", "plays"):
        a_key, b_key = f"player_a_{suffix}", f"player_b_{suffix}"
        swapped[a_key], swapped[b_key] = fields[b_key], fields[a_key]
    if fields.get("winner_side") in ("a", "b"):
        swapped["winner_side"] = "b" if fields["winner_side"] == "a" else "a"
    return swapped


def save_match_detail(detail: dict[str, Any]) -> str:
    """Idempotente por `match_id` -- 2026-08-08, pedido explícito del
    usuario: "si el match ya existe en las tablas, no hay que agregarlo
    nuevamente, sólo actualizar si se verifica que es el mismo match y
    los datos han cambiado". 1 fila por partido REAL (a diferencia de
    `save_match_history`, que guarda 2 filas por partido, una por
    jugador).

    `detail` es el dict que devuelve `get_match_detail` (fechas ya en
    ISO string). Fail-closed: si falta `match_id`/`player_a_name`/
    `player_b_name` (los únicos campos NOT NULL del modelo), no escribe
    nada y devuelve "invalid" -- nunca fabrica un partido a medias.

    Returns: "created" | "updated" | "unchanged" | "invalid" | "error"
    -- para que el caller (harvesting en el backfill) pueda reportar un
    resumen real de qué pasó, no solo "terminó sin excepción"."""
    match_id = detail.get("match_id")
    if not match_id or not detail.get("player_a_name") or not detail.get("player_b_name"):
        return "invalid"
    try:
        init_db_historical()
        now = _now()

        def _parse_date_field(raw):
            if not raw:
                return None
            from datetime import date as _d
            try:
                return _d.fromisoformat(raw)
            except ValueError:
                return None

        fields = {
            "match_date": detail.get("match_date"),
            "tournament": detail.get("tournament"),
            # 2026-08-09, pedido explícito del usuario -- "revisar si
            # tennisexplorer muestra el torneo, tour y sexo de los
            # players": confirmado, el link del torneo en match-detail
            # YA trae la categoría real (atp-men/wta-women/itf-*), sin
            # request adicional -- ver `_tour_from_tournament_href`.
            "tour": detail.get("tour"),
            "round": detail.get("round"),
            "surface": detail.get("surface"),
            "score": detail.get("score"),
            "winner_side": detail.get("winner_side"),
            "player_a_name": detail["player_a_name"],
            "player_a_slug": detail.get("player_a_slug"),
            "player_a_ranking": detail.get("player_a_ranking"),
            "player_a_birthdate": _parse_date_field(detail.get("player_a_birthdate")),
            "player_a_height_cm": detail.get("player_a_height_cm"),
            "player_a_weight_kg": detail.get("player_a_weight_kg"),
            "player_a_plays": detail.get("player_a_plays"),
            "player_b_name": detail["player_b_name"],
            "player_b_slug": detail.get("player_b_slug"),
            "player_b_ranking": detail.get("player_b_ranking"),
            "player_b_birthdate": _parse_date_field(detail.get("player_b_birthdate")),
            "player_b_height_cm": detail.get("player_b_height_cm"),
            "player_b_weight_kg": detail.get("player_b_weight_kg"),
            "player_b_plays": detail.get("player_b_plays"),
        }

        existing = MatchHistory.get_or_none(MatchHistory.match_id == match_id)
        upgraded_identity = False
        if existing is None:
            # 2026-08-09, pedido explícito del usuario tras auditar un
            # riesgo real: antes de crear una fila NUEVA, chequear si el
            # MISMO partido real ya existe bajo un match_id sintético
            # (ej. "recentform:...", creado por el prefetch en vivo de
            # `save_match_history`/`save_surface_match_history` ANTES de
            # que este partido se resolviera vía match-detail real) --
            # evita duplicar el mismo partido con 2 match_id distintos.
            # Auditado en producción (2026-08-09): 0 duplicados reales
            # encontrados en 448K+ filas hasta ahora, pero el riesgo
            # existía en el código -- mismo criterio de prevención que
            # ya usa `save_match_history` (`_find_existing_match_history_row`).
            match_date = fields.get("match_date")
            if match_date:
                candidate = _find_existing_match_history_row(
                    fields["player_a_name"], fields["player_b_name"], match_date,
                )
                if candidate is not None:
                    existing = candidate
                    upgraded_identity = existing.match_id != match_id
                    if upgraded_identity:
                        existing.match_id = match_id  # identidad real gana sobre la sintética
                    if existing.player_a_name != fields["player_a_name"]:
                        # la fila existente tiene player_a/player_b en el
                        # orden INVERSO (la creó el prefetch del OTRO
                        # jugador primero) -- espejar los campos nuevos
                        # para no mezclar bio de un lado con winner_side/
                        # ranking del otro.
                        fields = _swap_match_detail_sides(fields)

        # 2026-08-10, Fase 1 de la migración a entidad canónica `Player`
        # -- resuelve-o-crea el `Player` de cada lado DESPUÉS del swap
        # de arriba (si aplicó), para que `player_a_id`/`player_b_id`
        # queden del lado correcto. Dual-write: NO reemplaza los campos
        # de texto libre (`player_a_name`, etc.), solo agrega el FK en
        # paralelo -- ver docstring de `Player`.
        fields["player_a_id"] = _resolve_player_id(
            fields["player_a_name"], tour=fields.get("tour"), slug=fields.get("player_a_slug"),
            birthdate=fields.get("player_a_birthdate"), height_cm=fields.get("player_a_height_cm"),
            weight_kg=fields.get("player_a_weight_kg"), plays=fields.get("player_a_plays"),
        )
        fields["player_b_id"] = _resolve_player_id(
            fields["player_b_name"], tour=fields.get("tour"), slug=fields.get("player_b_slug"),
            birthdate=fields.get("player_b_birthdate"), height_cm=fields.get("player_b_height_cm"),
            weight_kg=fields.get("player_b_weight_kg"), plays=fields.get("player_b_plays"),
        )

        if existing is None:
            MatchHistory.create(match_id=match_id, fetched_at=now, **fields)
            return "created"

        changed = upgraded_identity
        for f in _MATCH_DETAIL_COMPARABLE_FIELDS:
            if getattr(existing, f) != fields[f]:
                setattr(existing, f, fields[f])
                changed = True
        if changed:
            existing.updated_at = now
            existing.save()
            return "updated"
        return "unchanged"
    except Exception:
        return "error"


def _last_midnight_utc():
    """Borde de calendario UTC más reciente (00:00:00 GMT de HOY).

    2026-07-31, cambio de diseño explícito del usuario: los partidos de
    tenis se resuelven "de un día para otro" (nunca hay torneos de
    top-100 jugándose a las 00:00 GMT), así que el chequeo de frescura de
    `get_recent_match_history` pasó de una ventana rodante de horas
    (`now - fetched_at < N horas`, la fila se volvía stale a una hora
    distinta cada día según cuándo se hizo el último fetch) a un borde
    fijo de calendario: "¿se fetcheó HOY (desde las 00:00 GMT), sí o no?".
    Esto evita el problema real encontrado el 2026-07-30 con
    BrightData caído: con una ventana rodante de 1h, cada intento fallido
    de refresh mantenía la fila "stale" indefinidamente, generando un
    reintento contra Flashscore en CADA llamada sin ninguna posibilidad de
    éxito. Con el borde de calendario, si ya se intentó/logró hoy no se
    reintenta hasta mañana a las 00:00 GMT -- como máximo 1 intento
    (exitoso o no) por jugador por día natural."""
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def get_recent_match_history(player_name: str, max_age_hours: float | None = None, limit: int = 40) -> list[dict[str, Any]] | None:
    """Lee el historial de partidos ya persistido para `player_name` si
    ya se fetcheó hoy (desde las 00:00 GMT más reciente, ver
    `_last_midnight_utc` -- reemplaza la ventana rodante de horas usada
    antes del 2026-07-31) -- None si no hay filas, o si la fila más
    reciente (`fetched_at`) es de ANTES del borde de hoy (fuerza un fetch
    en vivo en ese caso, como máximo una vez por día natural UTC).

    `max_age_hours`: parámetro legacy, ignorado si es None (default) --
    se mantiene por compatibilidad de firma, pero el chequeo real ya no es
    una ventana rodante. Si se pasa un valor explícito, se respeta como
    ventana rodante ADICIONAL (más estricta que el borde diario) para
    casos que necesiten frescura más agresiva que "una vez al día" -- hoy
    ningún caller la usa así, pero no se rompe la firma existente.

    Reemplaza el cache en memoria que existía en
    outer_apis/tennis_match_history.py (`_recent_matches_cache`): ese
    diccionario vivía solo en RAM del proceso y se vaciaba por completo en
    cada reinicio del contenedor, disparando un refetch masivo vía
    BrightData de TODOS los jugadores activos de una sola vez -- causa
    real investigada tras un pico de tráfico de proxy (ver memoria
    kairos-tennis-brightdata-db-cache-2026-07-28). La DB es la única
    fuente de verdad ahora: sobrevive a cualquier reinicio, y en SQLite
    con WAL + índice por `player_name` esta consulta es de milisegundos
    (nada comparable a los ~5s de un fetch real vía proxy), así que no
    hace falta una capa de memoria intermedia.

    Devuelve la MISMA forma que
    outer_apis.scrapers.tennis.flashscore_scraper.get_player_recent_matches
    ({"fecha", "oponente", "resultado", "score", "source"}, más reciente
    primero), para que el caller no tenga que distinguir si el dato vino
    de la DB o de un fetch en vivo.

    ADEMÁS (2026-07-31, segundo pedido explícito del usuario -- "antes de
    hacer fetch de todo se debe verificar si hay un nuevo match con
    respecto a la fecha del último match guardado en base"): incluso si
    ya pasó el borde de medianoche desde el último fetch, si el partido
    más reciente YA GUARDADO en DB (`match_date`, no `fetched_at`) es de
    HOY, no hace falta un fetch en vivo -- lógicamente no puede existir un
    partido "más nuevo" del que ya tenemos, sin importar cuándo se hizo el
    último chequeo. Esto es un segundo camino a la frescura, independiente
    del borde diario: cualquiera de los dos (chequeado hoy, O ya tenemos
    el resultado de hoy) alcanza para servir desde DB.

    Fail-closed: cualquier error de DB devuelve None (fuerza fetch en vivo,
    nunca sirve una lista vacía fabricada como si fuera "sin historial")."""
    try:
        init_db_historical()
        today_str = _last_midnight_utc().date().isoformat()
        player_filter = (MatchHistory.player_a_name == player_name) | (MatchHistory.player_b_name == player_name)

        latest_match = (
            MatchHistory
            .select(MatchHistory.match_date)
            .where(player_filter)
            .order_by(MatchHistory.match_date.desc())
            .limit(1)
            .first()
        )
        if latest_match is None:
            return None
        already_have_todays_match = latest_match.match_date >= today_str

        latest = (
            MatchHistory
            .select(MatchHistory.fetched_at)
            .where(player_filter)
            .order_by(MatchHistory.fetched_at.desc())
            .limit(1)
            .first()
        )
        if latest is None:
            return None

        fetched_at = latest.fetched_at
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)

        checked_today = fetched_at >= _last_midnight_utc()
        if not checked_today and not already_have_todays_match:
            return None  # ni se chequeó hoy, ni ya tenemos el resultado de hoy -- fuerza fetch en vivo
        if max_age_hours is not None and (_now() - fetched_at).total_seconds() / 3600.0 > max_age_hours:
            return None  # ventana rodante adicional explícita, más estricta que el borde diario

        rows = (
            MatchHistory
            .select()
            .where(player_filter)
            .order_by(MatchHistory.match_date.desc(), MatchHistory.id.desc())
            .limit(limit)
        )
        return [_match_history_row_to_player_dict(r, player_name) for r in rows]
    except Exception:
        return None


def _match_history_row_to_player_dict(r, player_name: str) -> dict[str, Any]:
    """Reconstruye la forma legacy per-jugador ({"fecha","oponente",
    "resultado","score","source"}) desde una fila de `MatchHistory`
    (1 por partido real) -- mantiene la firma externa que consumen
    `outer_apis/sports/tennis/match_history.py` y compañía sin que
    tengan que saber que la fuente ahora es 1-fila-por-partido."""
    def _resultado(my_side: str) -> str | None:
        if r.winner_side is None:
            return None  # no fabricar W/L cuando no se sabe con certeza quién ganó
        return "W" if r.winner_side == my_side else "L"

    if r.player_a_name == player_name:
        opponent, resultado = r.player_b_name, _resultado("a")
    else:
        opponent, resultado = r.player_a_name, _resultado("b")
    return {"fecha": r.match_date, "oponente": opponent, "resultado": resultado, "score": r.score, "source": r.source}


def get_any_match_history(player_name: str, limit: int = 40) -> list[dict[str, Any]] | None:
    """Lee lo que haya en `MatchHistory` para `player_name` (como
    player_a O player_b) SIN ningún chequeo de frescura -- a diferencia
    de `get_recent_match_history`, esto sirve datos VIEJOS a propósito.

    Usado exclusivamente cuando un jugador no está autorizado a un fetch
    en vivo hoy (ver `outer_apis/sports/tennis/match_history.py::
    set_fetch_allowlist`, pedido explícito del usuario 2026-08-01: solo
    hacer fetch de Flashscore para partidos NUEVOS) -- en ese caso,
    preferimos servir la última forma/H2H conocida (aunque tenga días) a
    no tener nada, mientras seguimos siendo honestos en el código sobre
    que puede estar desactualizada.

    Fail-closed: None si no hay NINGUNA fila para este jugador (nunca
    hubo un fetch exitoso) -- ahí sí el llamador no tiene nada que servir."""
    try:
        init_db_historical()
        rows = (
            MatchHistory
            .select()
            .where((MatchHistory.player_a_name == player_name) | (MatchHistory.player_b_name == player_name))
            .order_by(MatchHistory.match_date.desc(), MatchHistory.id.desc())
            .limit(limit)
        )
        rows = list(rows)
        if not rows:
            return None
        return [_match_history_row_to_player_dict(r, player_name) for r in rows]
    except Exception:
        return None


def save_head_to_head_snapshot(player_a: str, player_b: str, h2h: dict[str, Any]) -> None:
    """One new row per fresh h2h computation -- a snapshot (like
    calibration), since the underlying record can genuinely change as new
    matches get played. Bounded by the 1h cache TTL of the underlying
    match-history fetch in outer_apis/tennis_match_history.py."""
    if not h2h:
        return
    try:
        init_db_historical()
        TennisHeadToHead.create(
            player_a_name=player_a,
            player_b_name=player_b,
            a_wins=h2h["a_wins"],
            b_wins=h2h["b_wins"],
            n_matches=h2h["n_matches"],
            computed_at=_now(),
        )
    except Exception:
        pass


def save_surface_match_history(player_name: str, matches: list[dict[str, Any]]) -> None:
    """Persiste historial de TennisExplorer (con superficie real por
    partido, ver outer_apis/scrapers/tennis/tennisexplorer_scraper.py).
    1 fila por PARTIDO real en `MatchHistory` (2026-08-09, mismo
    tratamiento que `save_match_history` -- ver nota de migración
    arriba). Dedup vía `_find_existing_match_history_row` (match_date +
    par de jugadores en cualquier orden) -- si el partido ya existe
    (creado por el otro jugador, por `save_match_history`, o por
    `save_match_detail`), solo completa `surface`/`tournament` si esos
    campos venían vacíos (nunca pisa un dato real ya confirmado)."""
    if not matches:
        return
    try:
        init_db_historical()
        fetched_at = _now()
        for m in matches:
            opponent = m.get("opponent")
            match_date = m.get("match_date")
            surface = m.get("surface")
            if not opponent or not match_date or not surface:
                continue
            existing = _find_existing_match_history_row(player_name, opponent, match_date)
            if existing is not None:
                changed = False
                if not existing.surface:
                    existing.surface, changed = surface, True
                if not existing.tournament and m.get("tournament"):
                    existing.tournament, changed = m.get("tournament"), True
                if changed:
                    existing.updated_at = fetched_at
                    existing.save()
                continue
            result = m.get("result")
            winner_side = ("a" if result == "W" else "b") if result in ("W", "L") else None
            match_id = _synthetic_recentform_match_id(player_name, opponent, match_date)
            try:
                MatchHistory.create(
                    match_id=match_id,
                    source="tennisexplorer",
                    match_date=match_date,
                    surface=surface,
                    tournament=m.get("tournament"),
                    score=m.get("score"),
                    winner_side=winner_side,
                    player_a_name=player_name,
                    player_b_name=opponent,
                    fetched_at=fetched_at,
                )
            except Exception:
                pass  # carrera real -- el otro jugador/save_match_detail lo creó justo antes
    except Exception:
        pass


def get_recent_surface_match_history(
    player_name: str, max_age_hours: float = 24.0, lookback_days: int = 183,
) -> list[dict[str, Any]] | None:
    """DB-first: partidos reales de `player_name` con superficie, dentro
    de los últimos `lookback_days` (~6 meses por default, ver
    outer_apis/sports/tennis/surface_form.py), SOLO si hay al menos una
    fila fetcheada dentro de `max_age_hours` (evita re-scrapear
    TennisExplorer en cada llamada del mismo scan, mismo patrón DB-first
    que get_recent_match_history para tenis/Flashscore).

    Fail-closed: None si no hay ninguna fila para el jugador, o si la más
    reciente está más vieja que `max_age_hours` (el llamador debe
    refetchear en vivo en ese caso) -- nunca sirve una ventana vacía
    fingiendo que es una respuesta válida de "sin partidos"."""
    from datetime import datetime, timedelta, timezone

    try:
        init_db_historical()
        player_filter = (
            (MatchHistory.player_a_name == player_name) | (MatchHistory.player_b_name == player_name)
        ) & MatchHistory.surface.is_null(False)

        latest = (
            MatchHistory.select()
            .where(player_filter)
            .order_by(MatchHistory.fetched_at.desc())
            .first()
        )
        if latest is None:
            return None

        fetched_at = latest.fetched_at
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - fetched_at) > timedelta(hours=max_age_hours):
            return None

        cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).date().isoformat()
        rows = (
            MatchHistory.select()
            .where(player_filter & (MatchHistory.match_date >= cutoff))
            .order_by(MatchHistory.match_date.desc())
        )
        result = []
        for r in rows:
            d = _match_history_row_to_player_dict(r, player_name)
            result.append({
                "opponent": d["oponente"], "match_date": d["fecha"],
                "surface": r.surface, "result": d["resultado"], "score": d["score"],
            })
        return result
    except Exception:
        return None


_TA_ELO_FRESHNESS_HOURS = 24 * 4  # 4 días -- el sitio se actualiza "semanalmente(ish)", ver docstring


def save_tennisabstract_elo_snapshot(tour: str, rows: list[dict[str, Any]]) -> None:
    """Snapshot semanal, una fila nueva por fetch fresco (mismo criterio
    que save_rankings_snapshot) -- no upsert, es una serie temporal."""
    if not rows:
        return
    try:
        init_db_historical()
        fetched_at = _now()
        to_insert = [
            {
                "player_name": r["player"], "tour": tour, "elo": r["elo"],
                "h_elo": r.get("h_elo"), "c_elo": r.get("c_elo"), "g_elo": r.get("g_elo"),
                "atp_rank": r.get("atp_rank"), "fetched_at": fetched_at,
            }
            for r in rows if r.get("player") and r.get("elo") is not None
        ]
        if to_insert:
            TennisAbstractElo.insert_many(to_insert).execute()
    except Exception:
        pass


def get_current_tennisabstract_elo(tour: str, max_age_hours: float = _TA_ELO_FRESHNESS_HOURS) -> dict[str, dict[str, Any]] | None:
    """DB-first: {player_name: {"elo","h_elo","c_elo","g_elo","atp_rank"}}
    de la fila MÁS RECIENTE por jugador, solo si el fetch más reciente de
    la tabla completa está dentro de `max_age_hours`. Fail-closed: None si
    no hay filas o están stale (fuerza fetch en vivo)."""
    try:
        init_db_historical()
        latest = (
            TennisAbstractElo
            .select(TennisAbstractElo.fetched_at)
            .where(TennisAbstractElo.tour == tour)
            .order_by(TennisAbstractElo.fetched_at.desc())
            .limit(1)
            .first()
        )
        if latest is None:
            return None
        fetched_at = latest.fetched_at
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        if (_now() - fetched_at).total_seconds() / 3600.0 > max_age_hours:
            return None

        rows = (
            TennisAbstractElo
            .select()
            .where((TennisAbstractElo.tour == tour) & (TennisAbstractElo.fetched_at == latest.fetched_at))
        )
        return {
            r.player_name: {"elo": r.elo, "h_elo": r.h_elo, "c_elo": r.c_elo, "g_elo": r.g_elo, "atp_rank": r.atp_rank}
            for r in rows
        }
    except Exception:
        return None


def save_tennisabstract_yelo_snapshot(tour: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    try:
        init_db_historical()
        fetched_at = _now()
        to_insert = [
            {
                "player_name": r["player"], "tour": tour, "yelo": r["yelo"],
                "wins": r.get("wins"), "losses": r.get("losses"), "fetched_at": fetched_at,
            }
            for r in rows if r.get("player") and r.get("yelo") is not None
        ]
        if to_insert:
            TennisAbstractYelo.insert_many(to_insert).execute()
    except Exception:
        pass


def get_current_tennisabstract_yelo(tour: str, max_age_hours: float = _TA_ELO_FRESHNESS_HOURS) -> dict[str, dict[str, Any]] | None:
    """DB-first: {player_name: {"yelo","wins","losses"}}, mismo patrón que
    get_current_tennisabstract_elo."""
    try:
        init_db_historical()
        latest = (
            TennisAbstractYelo
            .select(TennisAbstractYelo.fetched_at)
            .where(TennisAbstractYelo.tour == tour)
            .order_by(TennisAbstractYelo.fetched_at.desc())
            .limit(1)
            .first()
        )
        if latest is None:
            return None
        fetched_at = latest.fetched_at
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        if (_now() - fetched_at).total_seconds() / 3600.0 > max_age_hours:
            return None

        rows = (
            TennisAbstractYelo
            .select()
            .where((TennisAbstractYelo.tour == tour) & (TennisAbstractYelo.fetched_at == latest.fetched_at))
        )
        return {r.player_name: {"yelo": r.yelo, "wins": r.wins, "losses": r.losses} for r in rows}
    except Exception:
        return None


from database.historical.tennis.models import (
    TennisAbstractCareerSplit, TennisAbstractH2H, TennisRecentResult,
)

_TA_PLAYER_STATS_FRESHNESS_HOURS = 24 * 7  # 1 semana -- career splits/H2H cambian muy lento partido a partido


def save_tennisabstract_career_splits(player_name: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    try:
        init_db_historical()
        fetched_at = _now()
        to_insert = [
            {
                "player_name": player_name, "surface": r["surface"],
                "matches": r.get("matches"), "wins": r.get("wins"), "losses": r.get("losses"),
                "win_pct": r.get("win_pct"), "hold_pct": r.get("hold_pct"),
                "break_pct": r.get("break_pct"), "ace_pct": r.get("ace_pct"), "df_pct": r.get("df_pct"),
                "fetched_at": fetched_at,
            }
            for r in rows if r.get("surface")
        ]
        if to_insert:
            TennisAbstractCareerSplit.insert_many(to_insert).execute()
    except Exception:
        pass


def get_current_career_splits(player_name: str, max_age_hours: float = _TA_PLAYER_STATS_FRESHNESS_HOURS) -> list[dict[str, Any]] | None:
    """DB-first, frescura POR JUGADOR (2026-07-31, pedido explícito del
    usuario: "solo cuando se necesiten actualizar los datos de algún
    player" -- cada jugador tiene su propio reloj de frescura, no uno
    global para toda la tabla). Fail-closed: None si no hay filas para
    ESTE jugador o están stale."""
    try:
        init_db_historical()
        latest = (
            TennisAbstractCareerSplit
            .select(TennisAbstractCareerSplit.fetched_at)
            .where(TennisAbstractCareerSplit.player_name == player_name)
            .order_by(TennisAbstractCareerSplit.fetched_at.desc())
            .limit(1)
            .first()
        )
        if latest is None:
            return None
        fetched_at = latest.fetched_at
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        if (_now() - fetched_at).total_seconds() / 3600.0 > max_age_hours:
            return None

        rows = (
            TennisAbstractCareerSplit
            .select()
            .where((TennisAbstractCareerSplit.player_name == player_name)
                   & (TennisAbstractCareerSplit.fetched_at == latest.fetched_at))
        )
        return [
            {
                "surface": r.surface, "matches": r.matches, "wins": r.wins, "losses": r.losses,
                "win_pct": r.win_pct, "hold_pct": r.hold_pct, "break_pct": r.break_pct,
                "ace_pct": r.ace_pct, "df_pct": r.df_pct,
            }
            for r in rows
        ]
    except Exception:
        return None


def save_tennisabstract_h2h(player_name: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    try:
        init_db_historical()
        fetched_at = _now()
        to_insert = [
            {
                "player_name": player_name, "opponent_name": r["opponent"], "meetings": r["meetings"],
                "wins": r.get("wins"), "losses": r.get("losses"), "win_pct": r.get("win_pct"),
                "fetched_at": fetched_at,
            }
            for r in rows if r.get("opponent") and r.get("meetings") is not None
        ]
        if to_insert:
            TennisAbstractH2H.insert_many(to_insert).execute()
    except Exception:
        pass


def get_current_tennisabstract_h2h(player_name: str, max_age_hours: float = _TA_PLAYER_STATS_FRESHNESS_HOURS) -> list[dict[str, Any]] | None:
    """DB-first, frescura POR JUGADOR -- mismo criterio que get_current_career_splits."""
    try:
        init_db_historical()
        latest = (
            TennisAbstractH2H
            .select(TennisAbstractH2H.fetched_at)
            .where(TennisAbstractH2H.player_name == player_name)
            .order_by(TennisAbstractH2H.fetched_at.desc())
            .limit(1)
            .first()
        )
        if latest is None:
            return None
        fetched_at = latest.fetched_at
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        if (_now() - fetched_at).total_seconds() / 3600.0 > max_age_hours:
            return None

        rows = (
            TennisAbstractH2H
            .select()
            .where((TennisAbstractH2H.player_name == player_name)
                   & (TennisAbstractH2H.fetched_at == latest.fetched_at))
        )
        return [
            {"opponent": r.opponent_name, "meetings": r.meetings, "wins": r.wins,
             "losses": r.losses, "win_pct": r.win_pct}
            for r in rows
        ]
    except Exception:
        return None


def get_all_career_splits_for_surface(surface: str, max_age_days: float = 30.0) -> dict[str, dict[str, Any]]:
    """Bulk read: {player_name: {surface, matches, wins, losses, win_pct,
    hold_pct, break_pct, ace_pct, df_pct}} para TODOS los jugadores que
    tengan una fila de `TennisAbstractCareerSplit` en `surface` -- la fila
    MÁS RECIENTE por jugador si hay más de una (nunca mezcla snapshots
    viejos de un jugador con nuevos de otro).

    Usado para armar el "pool" de candidatos de
    outer_apis/sports/tennis/style_peer_group.py -- este pool crece
    orgánicamente con el tiempo a medida que más jugadores aparecen en
    escaneos reales (cada uno se fetchea la primera vez que aparece en un
    mercado real, nunca se pre-carga todo el circuito de una vez).

    `max_age_days`: filas más viejas que esto se ignoran (evita usar el
    perfil de estilo de un jugador cuyo career-split no se refrescó en
    mucho tiempo, aunque siga siendo razonablemente estable dato a dato).

    Fail-closed: {} si no hay filas o falla la query -- nunca fabrica un
    pool de candidatos."""
    try:
        init_db_historical()
        cutoff = _now() - timedelta(days=max_age_days)
        rows = (
            TennisAbstractCareerSplit
            .select()
            .where((TennisAbstractCareerSplit.surface == surface)
                   & (TennisAbstractCareerSplit.fetched_at >= cutoff))
            .order_by(TennisAbstractCareerSplit.fetched_at.desc())
        )
        result: dict[str, dict[str, Any]] = {}
        for r in rows:
            if r.player_name in result:
                continue  # ya se guardó la fila más reciente de este jugador (orden desc)
            result[r.player_name] = {
                "surface": r.surface, "matches": r.matches, "wins": r.wins, "losses": r.losses,
                "win_pct": r.win_pct, "hold_pct": r.hold_pct, "break_pct": r.break_pct,
                "ace_pct": r.ace_pct, "df_pct": r.df_pct,
            }
        return result
    except Exception:
        return {}


def get_latest_rankings(tour: str, max_age_days: float = 14.0) -> dict[str, int]:
    """Bulk read: {player_name: ranking} para el snapshot MÁS RECIENTE de
    `TennisPlayerRanking` de ese tour (todas las filas de un mismo fetch
    comparten el mismo `fetched_at`, ver save_rankings_snapshot -- alcanza
    con el máximo global de la tabla para ese tour, no hace falta agrupar
    por jugador). Esta tabla YA se puebla en cada scan real (ver
    outer_apis/sports/tennis/rankings.py -> outer_apis/kalshi/markets/tennis.py),
    esto es lectura pura, sin fetch nuevo.

    Fail-closed: {} si no hay filas, el snapshot más reciente es más
    viejo que `max_age_days`, o falla la query."""
    try:
        init_db_historical()
        latest = (
            TennisPlayerRanking
            .select(TennisPlayerRanking.fetched_at)
            .where(TennisPlayerRanking.tour == tour)
            .order_by(TennisPlayerRanking.fetched_at.desc())
            .limit(1)
            .first()
        )
        if latest is None:
            return {}
        fetched_at = latest.fetched_at
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        if (_now() - fetched_at).days > max_age_days:
            return {}

        rows = (
            TennisPlayerRanking
            .select()
            .where((TennisPlayerRanking.tour == tour) & (TennisPlayerRanking.fetched_at == latest.fetched_at))
        )
        return {r.player_name: r.ranking for r in rows}
    except Exception:
        return {}


def get_all_surface_matches(player_name: str, surface: str, lookback_days: float = LOOKBACK_DAYS_DEFAULT) -> list[dict[str, Any]]:
    """Lectura RAW de `MatchHistory` (2026-08-09, migrado de
    `TennisSurfaceMatchHistory` -- ver nota de migración arriba) para
    `player_name` en `surface`, dentro de `lookback_days` -- a diferencia
    de `get_recent_surface_match_history`, NO tiene gate de frescura de
    `fetched_at` (esto es un stat derivado secundario, usar lo que ya
    esté en DB sin forzar/bloquear por un chequeo de "se refrescó hoy").

    Fail-closed: [] si no hay filas o falla la query."""
    try:
        init_db_historical()
        cutoff = (_now() - timedelta(days=lookback_days)).date().isoformat()
        rows = (
            MatchHistory
            .select()
            .where(
                ((MatchHistory.player_a_name == player_name) | (MatchHistory.player_b_name == player_name))
                & (MatchHistory.surface == surface)
                & (MatchHistory.match_date >= cutoff)
            )
        )
        result = []
        for r in rows:
            d = _match_history_row_to_player_dict(r, player_name)
            result.append({"opponent": d["oponente"], "match_date": d["fecha"], "result": d["resultado"]})
        return result
    except Exception:
        return []


_TA_RECENT_RESULTS_FRESHNESS_HOURS = 24  # 1 día -- señal de lesión debe estar razonablemente al día, no 1 semana


def save_recent_results(player_name: str, rows: list[dict[str, Any]]) -> None:
    """Dedupe por (player_name, match_date, opponent_name) -- un partido
    real no cambia una vez jugado, evita duplicar en cada refresh."""
    if not rows:
        return
    try:
        init_db_historical()
        fetched_at = _now()
        for r in rows:
            date = r.get("date")
            opponent = r.get("opponent")
            if not date or not opponent:
                continue
            exists = TennisRecentResult.select().where(
                (TennisRecentResult.player_name == player_name)
                & (TennisRecentResult.match_date == date)
                & (TennisRecentResult.opponent_name == opponent)
            ).exists()
            if exists:
                continue
            TennisRecentResult.create(
                player_name=player_name, match_date=date, tournament=r.get("tournament"),
                round=r.get("round"), opponent_name=opponent, subject_won=r.get("subject_won", False),
                retired_or_walkover=r.get("retired_or_walkover", False), fetched_at=fetched_at,
            )
    except Exception:
        pass


def get_recent_results(player_name: str, max_age_hours: float = _TA_RECENT_RESULTS_FRESHNESS_HOURS) -> list[dict[str, Any]] | None:
    """DB-first, frescura POR JUGADOR. Fail-closed: None si no hay filas
    para este jugador o la más reciente está stale (fuerza fetch en vivo)."""
    try:
        init_db_historical()
        latest = (
            TennisRecentResult
            .select(TennisRecentResult.fetched_at)
            .where(TennisRecentResult.player_name == player_name)
            .order_by(TennisRecentResult.fetched_at.desc())
            .limit(1)
            .first()
        )
        if latest is None:
            return None
        fetched_at = latest.fetched_at
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        if (_now() - fetched_at).total_seconds() / 3600.0 > max_age_hours:
            return None

        rows = (
            TennisRecentResult
            .select()
            .where(TennisRecentResult.player_name == player_name)
            .order_by(TennisRecentResult.match_date.desc())
        )
        return [
            {
                "date": r.match_date, "tournament": r.tournament, "round": r.round,
                "opponent": r.opponent_name,
                "subject_won": r.subject_won, "retired_or_walkover": r.retired_or_walkover,
            }
            for r in rows
        ]
    except Exception:
        return None
