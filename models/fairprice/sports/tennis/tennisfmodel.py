# Real fair-price via the tennis Markov engine (models/volatility/sports/
# tennis/tennis_markov.py) instead of the generic Elo-style _SportFModel
# heuristic — tennis is the one sport with a mathematically tractable
# scoring structure (game/set/match derived analytically from p_serve),
# and that same engine already powers TennisVModel's volatility, so this
# reuses it for fair-price too instead of maintaining a second, unrelated
# rating system for the same sport.
#
# CONTEXT (see kairos-tennis-critical-bugs-and-model-risk-2026-07-20
# memory): a rank-bucket-ONLY fair price disagreed with a real liquid
# Kalshi market by ~40 percentage points on a case where the market
# almost certainly priced in recent form/injury history that pure
# ranking doesn't capture (a player ranked 221 who the market still
# treated as a heavy favorite, e.g. a former top-30 player returning from
# injury). Blending in REAL recent-form and head-to-head data (via
# outer_apis/tennis_match_history.py, Flashscore-sourced) doesn't fully close
# that gap — it's still a simple signal, not real injury/surface/context
# modeling — but it's real data instead of nothing, and should narrow the
# rank-only model's blind spot somewhat.

from models.fairprice._common import clamp01
from models.volatility.sports.tennis.tennis_markov import pre_match_win_prob

from .._base import _SportFModel

# Weight given to the recent-form differential (last N matches' win rate)
# on top of the rank-based estimate, when both players' form is available.
# Kept modest, same rationale as _base.py's H2H_WEIGHT: a simple win-rate
# signal is noisy over small samples, shouldn't dominate the rank-based
# estimate.
FORM_WEIGHT = 0.15

# Weight given to head-to-head record, applied AFTER the form blend (so
# the final mix, when everything is available, is
# ~72% rank / 13% form / 15% h2h -- h2h gets a similar modest weight to
# form, but is applied second since a real head-to-head meeting is a
# stronger signal than general recent form when it exists).
H2H_WEIGHT = 0.15
MIN_H2H_MATCHES = 2  # fail-closed: fewer real meetings than this -> not enough signal, skip.

RECENT_FORM_LOOKBACK = 10
H2H_LOOKBACK = 40

# Weight for the set-sequence "resilience" signal (2026-07-30, see
# kairos-tennis-surface-and-set-sequence-model-2026-07-30 memory): smaller
# than FORM_WEIGHT/H2H_WEIGHT because it's a secondary, noisier signal
# derived from a small per-player sample (comeback/closing tendency), not
# a primary rating input. Final mix when everything is available:
# ~65% rank(+surface) / 13% form / 13% h2h / 9% set-sequence resilience.
SET_SEQUENCE_WEIGHT = 0.10
SET_SEQUENCE_LOOKBACK = 30

# Peso para "forma reciente EN ESTA superficie" (2026-07-30, punto 3 del
# pedido explícito del usuario), vía outer_apis/sports/tennis/surface_form.py
# (TennisExplorer, ~6 meses, MIN_MATCHES_ON_SURFACE real). Distinto de
# FORM_WEIGHT (que es forma general, cualquier superficie) -- este es más
# específico y por eso se le da un poco más de peso que al set-sequence,
# pero sigue por debajo de form/h2h generales. Mix final cuando todo está
# disponible: ~58% rank(+surface en p_serve) / 12% form general / 12% h2h
# / 8% set-sequence / 10% forma en esta superficie.
SURFACE_FORM_WEIGHT = 0.10

# Pesos para Tennis Abstract (Jeff Sackmann, 2026-07-31, investigación +
# construcción a pedido explícito del usuario): Elo real POR JUGADOR
# (carrera + por superficie: hElo/cElo/gElo), y Elo de la TEMPORADA en
# curso ("yElo"). Metodológicamente más sólido que el sistema propio de
# buckets de ranking de tennis_markov.py -- por eso se le da el peso más
# alto de todos los blends existentes, no uno "modesto" como form/h2h/
# set-sequence. TENNISABSTRACT_ELO_WEIGHT (carrera+superficie, la fuente
# de mayor calidad y muestra) pesa más que TENNISABSTRACT_YELO_WEIGHT
# (temporada en curso, más ruidoso por ventana más corta pero capta
# forma reciente que el Elo de carrera diluye). Pesos iniciales
# razonados, NO calibrados todavía contra resultados reales -- mismo
# criterio que W_EDGE/W_VOLATILITY en strategies/limit_arb.py.
TENNISABSTRACT_ELO_WEIGHT = 0.30
TENNISABSTRACT_YELO_WEIGHT = 0.15

# Win% de PARTIDO real (no proxy de Elo) por superficie, carrera completa
# -- ver outer_apis/sports/tennis/tennisabstract_player_stats.py. Peso
# menor que TENNISABSTRACT_ELO_WEIGHT a propósito: está correlacionado
# con el Elo por superficie (misma fuente, señal parcialmente redundante),
# pero sigue aportando algo distinto -- es la tasa de victoria de PARTIDO
# real, no una calificación derivada.
TENNISABSTRACT_CAREER_SURFACE_WEIGHT = 0.10

# Winrate contra grupo de rivales de estilo+ranking similar al rival
# actual (2026-07-31, pedido explícito del usuario -- ver
# outer_apis/sports/tennis/style_peer_group.py). Pensado específicamente
# para cuando NO hay H2H directo real disponible (MIN_H2H_MATCHES no
# alcanzado en ninguna fuente) -- en ese caso este blend es la única
# aproximación a "cómo le va este jugador contra un rival de este
# perfil" que tenemos. Peso modesto: es una aproximación de 2 pasos
# (similaridad de estilo + ranking, no el rival real) sobre un pool de
# candidatos que además crece orgánicamente con el tiempo (puede estar
# vacío/chico al principio).
STYLE_PEER_GROUP_WEIGHT = 0.10


class TennisFModel(_SportFModel):
    """Fail-closed: if player_a_rank/player_b_rank aren't present/valid
    positive integers, falls back to the generic _SportFModel heuristic
    (rating-based, itself fail-closed to the market price if no rating
    either) rather than fabricating a probability from missing rank data.
    Form/h2h blending is itself fail-closed and independently optional:
    missing player names, missing/insufficient real match history, or a
    scraper failure all just skip that specific blend step (see
    outer_apis/tennis_match_history.py), never fabricate a signal."""

    def calculate_fair_price(self, market: dict) -> dict:
        rank_a = market.get('player_a_rank')
        rank_b = market.get('player_b_rank')

        if not isinstance(rank_a, int) or isinstance(rank_a, bool) or rank_a <= 0:
            return super().calculate_fair_price(market)
        if not isinstance(rank_b, int) or isinstance(rank_b, bool) or rank_b <= 0:
            return super().calculate_fair_price(market)

        best_of = market.get('best_of', 3)
        if best_of not in (3, 5):
            return super().calculate_fair_price(market)

        tour = market.get('tour')
        surface = market.get('surface')  # None if the tournament wasn't recognized, see kalshi/markets/tennis.py

        try:
            fair_a = pre_match_win_prob(rank_a, rank_b, best_of=best_of, tour=tour, surface=surface)
        except ValueError:
            # defensive: pre_match_win_prob's own validation should never
            # trigger given the checks above, but never let an unexpected
            # exception here silently corrupt an arb decision -- fall back
            # to the generic heuristic instead of raising.
            return super().calculate_fair_price(market)

        fair_a = self._blend_recent_form(fair_a, market)
        fair_a = self._blend_head_to_head(fair_a, market)
        fair_a = self._blend_set_sequence_resilience(fair_a, market)
        fair_a = self._blend_surface_form(fair_a, market)
        fair_a = self._blend_tennisabstract_elo(fair_a, market)
        fair_a = self._blend_tennisabstract_season_form(fair_a, market)
        fair_a = self._blend_tennisabstract_career_surface_winpct(fair_a, market)
        fair_a = self._blend_style_peer_group(fair_a, market)

        fair_a = clamp01(fair_a)
        return {'fair_a_price': fair_a, 'fair_b_price': 1.0 - fair_a}

    @staticmethod
    def _blend_recent_form(fair_a: float, market: dict) -> float:
        name_a = market.get('player_a_name')
        name_b = market.get('player_b_name')
        if not name_a or not name_b:
            return fair_a  # no names -> can't look up real history, skip.

        from outer_apis.sports.tennis.match_history import get_recent_form

        form_a = get_recent_form(name_a, n=RECENT_FORM_LOOKBACK)
        form_b = get_recent_form(name_b, n=RECENT_FORM_LOOKBACK)
        if form_a is None or form_b is None:
            return fair_a  # fail-closed: need both, real data, or skip.

        form_implied_a = clamp01(0.5 + (form_a['win_rate'] - form_b['win_rate']) / 2)
        return (1 - FORM_WEIGHT) * fair_a + FORM_WEIGHT * form_implied_a

    @staticmethod
    def _blend_head_to_head(fair_a: float, market: dict) -> float:
        """2026-07-31: preferido el H2H REAL de Tennis Abstract (rivales
        frecuentes, ver outer_apis/sports/tennis/tennisabstract_player_stats.py)
        cuando el matchup figura ahí -- mejor calidad/muestra que el H2H
        derivado de Flashscore/TennisExplorer (`match_history.get_head_to_head`,
        ventana acotada por lookback). Si el matchup no está en la lista
        de rivales frecuentes de TA (jugadores que casi no se enfrentaron
        no aparecen ahí), cae al camino anterior sin cambios."""
        name_a = market.get('player_a_name')
        name_b = market.get('player_b_name')
        if not name_a or not name_b:
            return fair_a

        from outer_apis.sports.tennis.tennisabstract_player_stats import get_h2h_vs

        ta_h2h = get_h2h_vs(name_a, name_b)
        if ta_h2h is not None and ta_h2h.get('meetings', 0) >= MIN_H2H_MATCHES and ta_h2h.get('win_pct') is not None:
            return (1 - H2H_WEIGHT) * fair_a + H2H_WEIGHT * ta_h2h['win_pct']

        from outer_apis.sports.tennis.match_history import get_head_to_head

        h2h = get_head_to_head(name_a, name_b, lookback=H2H_LOOKBACK)
        if h2h is None or h2h['n_matches'] < MIN_H2H_MATCHES:
            return fair_a  # fail-closed: no data or too few real meetings, skip.

        h2h_implied_a = h2h['a_wins'] / h2h['n_matches']
        return (1 - H2H_WEIGHT) * fair_a + H2H_WEIGHT * h2h_implied_a

    @staticmethod
    def _resilience_index(tendencies: dict) -> float:
        """Promedio simple de las tendencias de secuencia de sets
        disponibles (p_win_set1 siempre presente cuando `tendencies` no es
        None; las 2 condicionales pueden faltar por sub-muestra, ver
        `set_sequence_stats.get_set_tendencies`). Un índice más alto =
        jugador/a que gana más sets en general Y remonta/cierra mejor."""
        values = [v for v in (
            tendencies['p_win_set1'],
            tendencies['p_win_set2_after_losing_set1'],
            tendencies['p_win_set2_after_winning_set1'],
        ) if v is not None]
        return sum(values) / len(values)

    @staticmethod
    def _blend_set_sequence_resilience(fair_a: float, market: dict) -> float:
        """Blend de 2026-07-30 (ver kairos-tennis-surface-and-set-sequence-
        model-2026-07-30 memory): dos jugadoras del mismo ranking pueden
        tener capacidad de remontar/cerrar sets muy distinta -- esto lo
        captura, sin tocar el Markov punto-por-punto (que no modela estado
        de sets previos). Fail-closed: requiere ambos nombres Y ambas
        tendencias con muestra suficiente (ver `_MIN_MATCHES` en
        set_sequence_stats.py), si no, no ajusta nada."""
        name_a = market.get('player_a_name')
        name_b = market.get('player_b_name')
        if not name_a or not name_b:
            return fair_a

        from outer_apis.sports.tennis.set_sequence_stats import get_set_tendencies

        tendencies_a = get_set_tendencies(name_a, n=SET_SEQUENCE_LOOKBACK)
        tendencies_b = get_set_tendencies(name_b, n=SET_SEQUENCE_LOOKBACK)
        if tendencies_a is None or tendencies_b is None:
            return fair_a  # fail-closed: need both, real data, or skip.

        resilience_a = TennisFModel._resilience_index(tendencies_a)
        resilience_b = TennisFModel._resilience_index(tendencies_b)
        resilience_implied_a = clamp01(0.5 + (resilience_a - resilience_b) / 2)
        return (1 - SET_SEQUENCE_WEIGHT) * fair_a + SET_SEQUENCE_WEIGHT * resilience_implied_a

    @staticmethod
    def _blend_surface_form(fair_a: float, market: dict) -> float:
        """Forma reciente (~6 meses) en la superficie ESPECÍFICA de este
        partido -- distinto de `_blend_recent_form` (forma general, todas
        las superficies mezcladas). Fail-closed: requiere `surface`
        conocida (ver outer_apis/scrapers/tennis/tournament_surface.py) Y
        ambos nombres Y muestra suficiente de ambos jugadores en esa
        superficie puntual (ver MIN_MATCHES_ON_SURFACE), si no, no ajusta
        nada."""
        surface = market.get('surface')
        name_a = market.get('player_a_name')
        name_b = market.get('player_b_name')
        if not surface or not name_a or not name_b:
            return fair_a

        from outer_apis.sports.tennis.surface_form import get_recent_form_by_surface

        tour = market.get('tour')
        form_a = get_recent_form_by_surface(name_a, surface, tour=tour)
        form_b = get_recent_form_by_surface(name_b, surface, tour=tour)
        if form_a is None or form_b is None:
            return fair_a  # fail-closed: need both, real data, or skip.

        surface_form_implied_a = clamp01(0.5 + (form_a['win_rate'] - form_b['win_rate']) / 2)
        return (1 - SURFACE_FORM_WEIGHT) * fair_a + SURFACE_FORM_WEIGHT * surface_form_implied_a

    @staticmethod
    def _blend_tennisabstract_elo(fair_a: float, market: dict) -> float:
        """Elo real por jugador (Tennis Abstract/Jeff Sackmann), carrera +
        por superficie (hElo/cElo/gElo, ver
        outer_apis/sports/tennis/tennisabstract_elo.py). Reemplaza
        conceptualmente al sistema propio de buckets de ranking en calidad
        de dato -- por eso tiene el peso más alto de todos los blends.
        Fail-closed: requiere `tour` conocido Y ambos nombres matcheen en
        la tabla de Tennis Abstract, si no, no ajusta nada (nunca fabrica
        un Elo por analogía de ranking)."""
        tour = market.get('tour')
        name_a = market.get('player_a_name')
        name_b = market.get('player_b_name')
        if not tour or not name_a or not name_b:
            return fair_a

        from outer_apis.sports.tennis.tennisabstract_elo import get_elo, elo_implied_win_prob

        surface = market.get('surface')
        elo_a = get_elo(name_a, tour, surface=surface)
        elo_b = get_elo(name_b, tour, surface=surface)
        if elo_a is None or elo_b is None:
            return fair_a  # fail-closed: need both, real data, or skip.

        elo_implied_a = clamp01(elo_implied_win_prob(elo_a, elo_b))
        return (1 - TENNISABSTRACT_ELO_WEIGHT) * fair_a + TENNISABSTRACT_ELO_WEIGHT * elo_implied_a

    @staticmethod
    def _blend_tennisabstract_season_form(fair_a: float, market: dict) -> float:
        """yElo (Elo de la temporada EN CURSO, no de carrera) -- captura
        forma reciente de una manera metodológicamente más sólida que un
        win-rate naive de los últimos N partidos (ver `_blend_recent_form`,
        que sigue existiendo en paralelo porque usa una fuente/ventana
        distinta -- Flashscore, 10 partidos más recientes sin importar
        temporada). Fail-closed: requiere `tour` conocido Y ambos nombres
        matcheen en la tabla de yElo, si no, no ajusta nada."""
        tour = market.get('tour')
        name_a = market.get('player_a_name')
        name_b = market.get('player_b_name')
        if not tour or not name_a or not name_b:
            return fair_a

        from outer_apis.sports.tennis.tennisabstract_elo import get_season_form, elo_implied_win_prob

        form_a = get_season_form(name_a, tour)
        form_b = get_season_form(name_b, tour)
        if form_a is None or form_b is None:
            return fair_a  # fail-closed: need both, real data, or skip.

        yelo_implied_a = clamp01(elo_implied_win_prob(form_a['yelo'], form_b['yelo']))
        return (1 - TENNISABSTRACT_YELO_WEIGHT) * fair_a + TENNISABSTRACT_YELO_WEIGHT * yelo_implied_a

    @staticmethod
    def _blend_tennisabstract_career_surface_winpct(fair_a: float, market: dict) -> float:
        """Win% de PARTIDO real (no Elo) de cada jugador/a en la
        superficie de este partido, sobre su carrera completa (ver
        outer_apis/sports/tennis/tennisabstract_player_stats.py).
        Fail-closed: requiere `surface` conocida Y ambos nombres con datos
        de esa superficie en Tennis Abstract, si no, no ajusta nada."""
        surface = market.get('surface')
        name_a = market.get('player_a_name')
        name_b = market.get('player_b_name')
        if not surface or not name_a or not name_b:
            return fair_a

        from outer_apis.sports.tennis.tennisabstract_player_stats import get_career_win_pct_by_surface

        split_a = get_career_win_pct_by_surface(name_a, surface)
        split_b = get_career_win_pct_by_surface(name_b, surface)
        if split_a is None or split_b is None or split_a.get('win_pct') is None or split_b.get('win_pct') is None:
            return fair_a  # fail-closed: need both, real data, or skip.

        winpct_implied_a = clamp01(0.5 + (split_a['win_pct'] - split_b['win_pct']) / 2)
        return (1 - TENNISABSTRACT_CAREER_SURFACE_WEIGHT) * fair_a + TENNISABSTRACT_CAREER_SURFACE_WEIGHT * winpct_implied_a

    @staticmethod
    def _has_direct_h2h(name_a: str, name_b: str) -> bool:
        """True si existe H2H directo real y suficiente (>= MIN_H2H_MATCHES)
        de CUALQUIERA de las 2 fuentes que usa `_blend_head_to_head`
        (Tennis Abstract rivales frecuentes, o Flashscore/TennisExplorer).
        Usado para decidir si aplica el blend de grupo de rivales por
        estilo -- ese blend está pensado explícitamente para el caso SIN
        historial directo (pedido explícito del usuario), no para sumarse
        encima de un H2H real ya fuerte."""
        from outer_apis.sports.tennis.tennisabstract_player_stats import get_h2h_vs

        ta_h2h = get_h2h_vs(name_a, name_b)
        if ta_h2h is not None and ta_h2h.get('meetings', 0) >= MIN_H2H_MATCHES:
            return True

        from outer_apis.sports.tennis.match_history import get_head_to_head

        h2h = get_head_to_head(name_a, name_b, lookback=H2H_LOOKBACK)
        return h2h is not None and h2h['n_matches'] >= MIN_H2H_MATCHES

    @staticmethod
    def _blend_style_peer_group(fair_a: float, market: dict) -> float:
        """Winrate de cada jugador/a contra el grupo de rivales de
        estilo+ranking similar al RIVAL actual (ver
        outer_apis/sports/tennis/style_peer_group.py) -- SOLO se aplica
        cuando NO hay H2H directo real suficiente entre ambos (pedido
        explícito del usuario: "esto es justamente para cuando no hay
        encuentros previos entre 2 players"). Fail-closed: requiere
        `surface`/`tour`/rankings de ambos Y grupos de rivales armables
        con muestra suficiente para ambos lados, si no, no ajusta nada."""
        surface = market.get('surface')
        tour = market.get('tour')
        name_a = market.get('player_a_name')
        name_b = market.get('player_b_name')
        rank_a = market.get('player_a_rank')
        rank_b = market.get('player_b_rank')
        if not surface or not tour or not name_a or not name_b:
            return fair_a

        if TennisFModel._has_direct_h2h(name_a, name_b):
            return fair_a  # ya hay H2H real, no hace falta el proxy de grupo de rivales.

        from outer_apis.sports.tennis.style_peer_group import get_winrate_vs_peer_group

        stat_a = get_winrate_vs_peer_group(name_a, name_b, rank_b, surface, tour)
        stat_b = get_winrate_vs_peer_group(name_b, name_a, rank_a, surface, tour)
        if stat_a is None or stat_b is None:
            return fair_a  # fail-closed: need both sides, real data, or skip.

        peer_implied_a = clamp01(0.5 + (stat_a['win_rate'] - stat_b['win_rate']) / 2)
        return (1 - STYLE_PEER_GROUP_WEIGHT) * fair_a + STYLE_PEER_GROUP_WEIGHT * peer_implied_a
