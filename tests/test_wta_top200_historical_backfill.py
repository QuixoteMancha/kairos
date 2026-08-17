"""Tests del backfill masivo top-200 WTA (2026-08-03, pedido explícito
del usuario). Mockea red (rankings + fetch de TennisExplorer) y usa la
DB histórica real (sqlite en memoria/test) -- nunca golpea la red en
tests.

2026-08-09, pedido explícito del usuario ("modifica las importaciones
para que sólo se utilice la tabla de matchhistory"): `TennisMatchHistory`
fue borrada -- `backfill_player` ya NO escribe ahí, solo escribe
`TennisHistoricalOdds` (sigue existiendo) y cosecha `MatchHistory` (vía
`get_match_detail`/`save_match_detail`, ya cubierto por
`test_save_match_detail.py`). Los tests que verificaban inserción/
reparación de filas de `TennisMatchHistory` se removieron -- ya no
aplica, ese comportamiento no existe más."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis import TennisHistoricalOdds, MatchHistory
from scripts.tennis.wta_top200_historical_backfill import (
    backfill_player, run_backfill, run_backfill_from_wta_roster, _to_iso_date, _run_backfill_for_names,
    run_country_catalog_backfill, _catalog_display_name,
)
from database.historical.tennis import PlayerRankingHistory, Player


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        TennisHistoricalOdds.delete().execute()
    yield
    with db_historical.connection_context():
        TennisHistoricalOdds.delete().execute()


def _fake_html(marker: str) -> str:
    return f"<title>Test Player - Tennis Explorer</title>{marker}"


def _make_player(name: str):
    from datetime import datetime, timezone
    existing = Player.get_or_none(Player.canonical_name == name)
    if existing is not None:
        return existing
    return Player.create(canonical_name=name, created_at=datetime.now(timezone.utc))


def test_to_iso_date_uses_reference_year():
    assert _to_iso_date("03.08.", 2025) == "2025-08-03"


def test_to_iso_date_fail_closed_on_garbage():
    assert _to_iso_date("garbage", 2025) is None


def test_backfill_player_skips_when_no_slug():
    with patch(
        "scripts.tennis.wta_top200_historical_backfill.resolve_player_slug", return_value=None,
    ):
        result = backfill_player("Nonexistent Player")
    assert result["status"] == "skipped_no_slug"
    assert result["odds_inserted"] == 0


def test_backfill_player_inserts_odds():
    fake_match = {
        "date": "03.08.", "tournament": "Washington", "surface": "hard",
        "opponent": "Pegula J.", "round": "F", "round_full": "final",
        "score": "4-6, 6-4, 6-0", "odds_self": 2.36, "odds_opponent": 1.58, "match_id": None,
    }

    def fake_fetch(slug, year=None):
        return f"year={year}"  # marca el año en el html devuelto, para el side_effect de abajo

    def fake_parse(html, name):
        # solo un año real trae este partido -- los otros 10 vienen vacíos
        # (fail-closed, como pasaría de verdad con años sin cobertura).
        return [fake_match] if html == "year=2026" else []

    with patch(
        "scripts.tennis.wta_top200_historical_backfill.resolve_player_slug", return_value="testplayer",
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill._fetch", side_effect=fake_fetch,
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill._parse_year_html", side_effect=fake_parse,
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill.time.sleep",
    ):
        result = backfill_player("Test Player")

    assert result["status"] == "ok"
    assert result["odds_inserted"] == 1

    with db_historical.connection_context():
        odds_row = TennisHistoricalOdds.get(TennisHistoricalOdds.player_name == "Test Player")
        assert odds_row.odds_self == 2.36
        assert odds_row.odds_opponent == 1.58


def test_backfill_player_idempotent_on_second_run():
    fake_match = {
        "date": "03.08.", "tournament": "Washington", "surface": "hard",
        "opponent": "Pegula J.", "round": "F", "round_full": "final",
        "score": "4-6, 6-4, 6-0", "odds_self": 2.36, "odds_opponent": 1.58, "match_id": None,
    }
    with patch(
        "scripts.tennis.wta_top200_historical_backfill.resolve_player_slug", return_value="testplayer",
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill._fetch", return_value="<html>x</html>",
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill._parse_year_html", return_value=[fake_match],
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill.time.sleep",
    ):
        first = backfill_player("Test Player")
        second = backfill_player("Test Player")

    # mismo partido reportado en LOS 11 años pedidos -> "03.08." combinado
    # con cada año da 11 fechas ISO reales DISTINTAS (2016-08-03 ...
    # 2026-08-03) -- no colapsan entre sí, cada una es una cuota real
    # nueva. Lo que sí debe colapsar es correr la función 2 VECES.
    assert first["odds_inserted"] == 11
    assert second["odds_inserted"] == 0


def test_backfill_player_skips_match_without_valid_date():
    fake_match = {
        "date": "garbage", "tournament": "X", "surface": "hard",
        "opponent": "Y", "round": "F", "round_full": "final",
        "score": "6-4, 6-3", "odds_self": 1.5, "odds_opponent": 2.5, "match_id": None,
    }
    with patch(
        "scripts.tennis.wta_top200_historical_backfill.resolve_player_slug", return_value="testplayer",
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill._fetch", return_value="<html>x</html>",
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill._parse_year_html", return_value=[fake_match],
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill.time.sleep",
    ):
        result = backfill_player("Test Player")
    assert result["odds_inserted"] == 0


def test_backfill_player_no_odds_row_when_both_odds_none():
    fake_match = {
        "date": "03.08.", "tournament": "X", "surface": "hard",
        "opponent": "Y", "round": "F", "round_full": "final",
        "score": "6-4, 6-3", "odds_self": None, "odds_opponent": None, "match_id": None,
    }
    with patch(
        "scripts.tennis.wta_top200_historical_backfill.resolve_player_slug", return_value="testplayer",
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill._fetch", return_value="<html>x</html>",
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill._parse_year_html", return_value=[fake_match],
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill.time.sleep",
    ):
        result = backfill_player("Test Player")
    assert result["odds_inserted"] == 0


def test_run_backfill_paces_between_players_and_summarizes():
    fake_players = [
        {"nombre": "Player A", "ranking": 1, "ranking_type": "WTA", "pais": "USA"},
        {"nombre": "Player B", "ranking": 2, "ranking_type": "WTA", "pais": "USA"},
    ]
    with patch(
        "scripts.tennis.wta_top200_historical_backfill.get_top_rankings", return_value=fake_players,
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill.backfill_player",
        return_value={"player": "x", "status": "ok", "odds_inserted": 1},
    ) as mock_bp, patch(
        "scripts.tennis.wta_top200_historical_backfill.time.sleep",
    ) as mock_sleep:
        summary = run_backfill(n_players=2)

    assert mock_bp.call_count == 2
    assert mock_sleep.call_count == 2  # una pausa entre jugadoras por cada jugadora procesada
    assert len(summary) == 2


def test_run_backfill_uses_requested_tour_for_rankings():
    fake_players = [{"nombre": "Player A", "ranking": 1, "ranking_type": "ATP", "pais": "ESP"}]
    with patch(
        "scripts.tennis.wta_top200_historical_backfill.get_top_rankings", return_value=fake_players,
    ) as mock_rankings, patch(
        "scripts.tennis.wta_top200_historical_backfill.backfill_player",
        return_value={"player": "x", "status": "ok", "odds_inserted": 0},
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill.time.sleep",
    ):
        run_backfill(n_players=1, tour="ATP")
    mock_rankings.assert_called_once_with(n=1, tour="ATP")


def test_run_backfill_from_wta_roster_uses_distinct_names_from_ranking_table():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    PlayerRankingHistory.delete().execute()
    Player.delete().execute()
    player_a = Player.create(canonical_name="Player A", created_at=now)
    player_b = Player.create(canonical_name="Player B", created_at=now)
    PlayerRankingHistory.create(player=player_a, ranked_at="2020-01-01", singles_ranking=5, fetched_at=now)
    PlayerRankingHistory.create(player=player_a, ranked_at="2021-01-01", singles_ranking=3, fetched_at=now)
    PlayerRankingHistory.create(player=player_b, ranked_at="2020-01-01", singles_ranking=50, fetched_at=now)

    with patch(
        "scripts.tennis.wta_top200_historical_backfill.backfill_player",
        return_value={"player": "x", "status": "ok", "odds_inserted": 0},
    ) as mock_bp, patch("scripts.tennis.wta_top200_historical_backfill.time.sleep"):
        summary = run_backfill_from_wta_roster()

    # 2 jugadoras DISTINTAS (Player A aparece 2 veces en la tabla, 1 sola vez en el roster)
    assert mock_bp.call_count == 2
    assert len(summary) == 2


def test_run_backfill_from_atp_roster_uses_match_history_atp_names():
    from datetime import datetime, timezone
    from scripts.tennis.wta_top200_historical_backfill import run_backfill_from_atp_roster

    now = datetime.now(timezone.utc)
    MatchHistory.delete().execute()
    Player.delete().execute()
    pa, pb = _make_player("Player A"), _make_player("Player B")
    pc, pd = _make_player("Player C"), _make_player("Player D")
    MatchHistory.create(
        match_id="test:1", source="tennisexplorer", tour="ATP",
        player_a=pa, player_b=pb, fetched_at=now,
    )
    # WTA -- no debe entrar en el roster ATP.
    MatchHistory.create(
        match_id="test:2", source="tennisexplorer", tour="WTA",
        player_a=pc, player_b=pd, fetched_at=now,
    )

    with patch(
        "scripts.tennis.wta_top200_historical_backfill.backfill_player",
        return_value={"player": "x", "status": "ok", "odds_inserted": 0},
    ) as mock_bp, patch("scripts.tennis.wta_top200_historical_backfill.time.sleep"):
        run_backfill_from_atp_roster()

    called_names = {c.args[0] for c in mock_bp.call_args_list}
    assert called_names == {"Player A", "Player B"}


def test_run_backfill_for_missing_odds_opponents_targets_unfetched_player():
    from datetime import datetime, timezone
    from scripts.tennis.wta_top200_historical_backfill import run_backfill_for_missing_odds_opponents

    now = datetime.now(timezone.utc)
    MatchHistory.delete().execute()
    TennisHistoricalOdds.delete().execute()
    Player.delete().execute()
    p_self, p_rival = _make_player("Known Self"), _make_player("Rival Real")

    MatchHistory.create(
        match_id="test:1", source="tennisexplorer", tour="ATP",
        player_a=p_self, player_b=p_rival, fetched_at=now,
    )
    # "Known Self" YA tiene su propia página backfilleada (aparece como
    # `player_name` real en TennisHistoricalOdds) -- "Rival Real" no.
    TennisHistoricalOdds.create(
        player_name="Known Self", opponent_name="Rival R.", match_date="2016-06-01",
        odds_self=1.5, odds_opponent=2.5, source="tennisexplorer", fetched_at=now,
    )

    with patch(
        "scripts.tennis.wta_top200_historical_backfill.backfill_player",
        return_value={"player": "x", "status": "ok", "odds_inserted": 0},
    ) as mock_bp, patch("scripts.tennis.wta_top200_historical_backfill.time.sleep"):
        run_backfill_for_missing_odds_opponents()

    called_names = {c.args[0] for c in mock_bp.call_args_list}
    assert called_names == {"Rival Real"}


def test_run_backfill_for_missing_odds_opponents_skips_players_already_fetched():
    from datetime import datetime, timezone
    from scripts.tennis.wta_top200_historical_backfill import run_backfill_for_missing_odds_opponents

    now = datetime.now(timezone.utc)
    MatchHistory.delete().execute()
    TennisHistoricalOdds.delete().execute()

    MatchHistory.create(
        match_id="test:1", source="tennisexplorer", tour="ATP",
        player_a_name="Known Self", player_b_name="Also Known", fetched_at=now,
    )
    TennisHistoricalOdds.create(
        player_name="Known Self", opponent_name="Also K.", match_date="2016-06-01",
        odds_self=1.5, odds_opponent=2.5, source="tennisexplorer", fetched_at=now,
    )
    TennisHistoricalOdds.create(
        player_name="Also Known", opponent_name="Known S.", match_date="2016-06-01",
        odds_self=2.5, odds_opponent=1.5, source="tennisexplorer", fetched_at=now,
    )

    with patch(
        "scripts.tennis.wta_top200_historical_backfill.backfill_player",
    ) as mock_bp, patch("scripts.tennis.wta_top200_historical_backfill.time.sleep"):
        run_backfill_for_missing_odds_opponents()

    mock_bp.assert_not_called()


def test_run_backfill_for_names_sequential_by_default():
    with patch(
        "scripts.tennis.wta_top200_historical_backfill.backfill_player",
        return_value={"player": "x", "status": "ok", "odds_inserted": 0},
    ) as mock_bp, patch("scripts.tennis.wta_top200_historical_backfill.time.sleep"):
        summary = _run_backfill_for_names(["A", "B", "C"], tag="test")
    assert mock_bp.call_count == 3
    assert len(summary) == 3


def test_run_backfill_for_names_with_thread_pool():
    with patch(
        "scripts.tennis.wta_top200_historical_backfill.backfill_player",
        return_value={"player": "x", "status": "ok", "odds_inserted": 1},
    ) as mock_bp, patch("scripts.tennis.wta_top200_historical_backfill.time.sleep"):
        summary = _run_backfill_for_names(["A", "B", "C", "D"], tag="test", max_workers=4)
    assert mock_bp.call_count == 4
    assert len(summary) == 4
    assert sum(r["odds_inserted"] for r in summary) == 4


def test_backfill_player_resolves_match_detail_via_shared_writer_helper():
    # 2026-08-10, pedido explícito del usuario: "con todo lo que
    # tenemos actualmente, dedup y fill" -- backfill_player debe usar
    # _resolve_and_save_via_match_detail (writer.py), NO get_match_detail/
    # save_match_detail sueltos, para heredar gratis el fix de causa raíz
    # de la triplicación + el fill de level/tour.
    fake_match = {
        "date": "03.08.", "tournament": "Washington", "surface": "hard",
        "opponent": "Pegula J.", "round": "F", "round_full": "final",
        "score": "4-6, 6-4, 6-0", "odds_self": 2.36, "odds_opponent": 1.58, "match_id": "999",
    }
    with patch(
        "scripts.tennis.wta_top200_historical_backfill.resolve_player_slug", return_value="testplayer",
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill._fetch",
        side_effect=lambda slug, year=None: fake_match and (f"year={year}" if year == 2026 else None),
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill._parse_year_html",
        side_effect=lambda html, name: [fake_match] if html == "year=2026" else [],
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill._resolve_and_save_via_match_detail", return_value=True,
    ) as mock_resolve, patch(
        "scripts.tennis.wta_top200_historical_backfill.time.sleep",
    ):
        result = backfill_player("Test Player")

    mock_resolve.assert_called_once_with("Test Player", "999")
    assert result["match_details_resolved"] == 1
    assert result["match_details_error"] == 0


def test_backfill_player_reenriches_incomplete_match_already_in_history():
    from datetime import datetime, timezone

    MatchHistory.delete().execute()
    MatchHistory.create(
        match_id="999", player_a_name="A", player_b_name="B", match_date="2026-08-03",
        source="tennisexplorer", fetched_at=datetime.now(timezone.utc),
    )
    fake_match = {
        "date": "03.08.", "tournament": "Washington", "surface": "hard",
        "opponent": "Pegula J.", "round": "F", "round_full": "final",
        "score": "4-6, 6-4, 6-0", "odds_self": None, "odds_opponent": None, "match_id": "999",
    }
    with patch(
        "scripts.tennis.wta_top200_historical_backfill.resolve_player_slug", return_value="testplayer",
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill._fetch",
        side_effect=lambda slug, year=None: "year=2026" if year == 2026 else None,
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill._parse_year_html",
        side_effect=lambda html, name: [fake_match] if html == "year=2026" else [],
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill._resolve_and_save_via_match_detail",
    ) as mock_resolve, patch(
        "scripts.tennis.wta_top200_historical_backfill.time.sleep",
    ):
        backfill_player("Test Player")

    mock_resolve.assert_called_once_with("Test Player", "999")


def test_cli_top_n_reads_explicit_flag():
    import sys
    from scripts.tennis.wta_top200_historical_backfill import _cli_top_n

    old_argv = sys.argv
    try:
        sys.argv = ["prog", "--top-n", "2000"]
        assert _cli_top_n() == 2000
    finally:
        sys.argv = old_argv


def test_cli_top_n_falls_back_to_positional_int():
    import sys
    from scripts.tennis.wta_top200_historical_backfill import _cli_top_n

    old_argv = sys.argv
    try:
        sys.argv = ["prog", "500"]
        assert _cli_top_n() == 500
    finally:
        sys.argv = old_argv


def test_cli_top_n_default_when_nothing_passed():
    import sys
    from scripts.tennis.wta_top200_historical_backfill import _cli_top_n

    old_argv = sys.argv
    try:
        sys.argv = ["prog"]
        assert _cli_top_n() == 200
    finally:
        sys.argv = old_argv


def test_cli_top_n_ignores_subcommand_keywords():
    import sys
    from scripts.tennis.wta_top200_historical_backfill import _cli_top_n

    old_argv = sys.argv
    try:
        sys.argv = ["prog", "full_roster"]
        assert _cli_top_n() == 200
    finally:
        sys.argv = old_argv


def test_te_native_to_nombre_apellido_reorders_correctly():
    from scripts.tennis.wta_top200_historical_backfill import _te_native_to_nombre_apellido

    assert _te_native_to_nombre_apellido("Svitolina Elina") == "Elina Svitolina"
    assert _te_native_to_nombre_apellido("Bouzas Maneiro Jessica") == "Jessica Bouzas Maneiro"
    assert _te_native_to_nombre_apellido("Sabalenka") == "Sabalenka"  # 1 solo token, fail-closed


def test_backfill_player_stores_full_normalized_opponent_name_when_resolved():
    # 2026-08-10, pedido explícito del usuario: "que integre lo de los
    # nombres que estén normalizados como nombre y apellido" --
    # TennisHistoricalOdds.opponent_name debe guardar el nombre COMPLETO
    # real en "Nombre Apellido" (no el rival abreviado "Pegula J.")
    # cuando el match-detail resuelve identidad.
    from datetime import datetime, timezone

    MatchHistory.delete().execute()
    TennisHistoricalOdds.delete().execute()
    Player.delete().execute()
    fake_match = {
        "date": "03.08.", "tournament": "Washington", "surface": "hard",
        "opponent": "Pegula J.", "round": "F", "round_full": "final",
        "score": "4-6, 6-4, 6-0", "odds_self": 2.36, "odds_opponent": 1.58, "match_id": "999",
    }

    def fake_resolve(player_name, match_id):
        p_a, p_b = _make_player("Test Player"), _make_player("Jessica Pegula")
        MatchHistory.create(
            match_id=match_id, player_a=p_a, player_b=p_b,
            match_date="2026-08-03", source="tennisexplorer", fetched_at=datetime.now(timezone.utc),
        )
        return True

    with patch(
        "scripts.tennis.wta_top200_historical_backfill.resolve_player_slug", return_value="testplayer",
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill._fetch",
        side_effect=lambda slug, year=None: "year=2026" if year == 2026 else None,
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill._parse_year_html",
        side_effect=lambda html, name: [fake_match] if html == "year=2026" else [],
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill._resolve_and_save_via_match_detail",
        side_effect=fake_resolve,
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill.time.sleep",
    ):
        result = backfill_player("Test Player")

    assert result["odds_inserted"] == 1
    odds_row = TennisHistoricalOdds.get(TennisHistoricalOdds.player_name == "Test Player")
    assert odds_row.opponent_name == "Jessica Pegula"  # completo, "Nombre Apellido" -- no "Pegula J."


def test_backfill_player_falls_back_to_abbreviated_opponent_when_not_resolved():
    from datetime import datetime, timezone

    MatchHistory.delete().execute()
    TennisHistoricalOdds.delete().execute()
    fake_match = {
        "date": "03.08.", "tournament": "Washington", "surface": "hard",
        "opponent": "Pegula J.", "round": "F", "round_full": "final",
        "score": "4-6, 6-4, 6-0", "odds_self": 2.36, "odds_opponent": 1.58, "match_id": "999",
    }
    with patch(
        "scripts.tennis.wta_top200_historical_backfill.resolve_player_slug", return_value="testplayer",
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill._fetch",
        side_effect=lambda slug, year=None: "year=2026" if year == 2026 else None,
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill._parse_year_html",
        side_effect=lambda html, name: [fake_match] if html == "year=2026" else [],
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill._resolve_and_save_via_match_detail", return_value=False,
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill.time.sleep",
    ):
        backfill_player("Test Player")

    odds_row = TennisHistoricalOdds.get(TennisHistoricalOdds.player_name == "Test Player")
    assert odds_row.opponent_name == "Pegula J."  # fallback abreviado, resolución falló
def test_catalog_display_name_normalizes_native_catalog_format():
    assert _catalog_display_name("Auger Aliassime, Felix") == "Felix Auger Aliassime"
    assert _catalog_display_name("Damm, Martin (2003)") == "Martin Damm"


def test_country_catalog_backfill_uses_country_pages_and_prefetched_player_html():
    discovered = {
        "country_url": "https://www.tennisexplorer.com/list-players/?country=chile",
        "player_url": "https://www.tennisexplorer.com/player/garin/",
        "slug": "garin",
        "player_name": "Garin, Cristian",
        "html": "<html>prefetched</html>",
    }
    with patch(
        "scripts.tennis.wta_top200_historical_backfill.init_db_historical",
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill.get_all_player_country_links",
        return_value=[discovered["country_url"]],
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill.iter_all_player_pages",
        return_value=iter([discovered]),
    ) as iter_pages, patch(
        "scripts.tennis.wta_top200_historical_backfill.backfill_player",
        return_value={
            "status": "ok", "matches_found": 12,
            "match_details_resolved": 4, "odds_inserted": 3,
        },
    ) as backfill, patch(
        "scripts.tennis.wta_top200_historical_backfill.time.sleep",
    ):
        result = run_country_catalog_backfill(years=[2026], max_players=1)

    iter_pages.assert_called_once_with([discovered["country_url"]], 2026)
    backfill.assert_called_once_with(
        "Cristian Garin", years=[2026], known_slug="garin",
        prefetched_pages={2026: "<html>prefetched</html>"},
    )
    assert result == {
        "countries": 1, "players_processed": 1, "players_ok": 1,
        "players_skipped": 0, "matches_found": 12,
        "match_details_resolved": 4, "odds_inserted": 3,
    }


def test_country_catalog_backfill_threaded_processes_all_discovered_players():
    """2026-08-16, pedido explícito del usuario ("un worker que tenga
    threads para backfillear los players que corra constantemente") --
    `max_workers>1` debe procesar TODOS los jugadores descubiertos (en
    lotes de tamaño `max_workers`, ver docstring de la función), no solo
    el primer lote, y reportar el mismo resumen agregado que el modo
    secuencial."""
    discovered_players = [
        {
            "country_url": "https://www.tennisexplorer.com/list-players/?country=chile",
            "player_url": f"https://www.tennisexplorer.com/player/p{i}/",
            "slug": f"p{i}",
            "player_name": f"Player{i}, Test",
            "html": f"<html>{i}</html>",
        }
        for i in range(5)
    ]
    with patch(
        "scripts.tennis.wta_top200_historical_backfill.init_db_historical",
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill.get_all_player_country_links",
        return_value=["https://www.tennisexplorer.com/list-players/?country=chile"],
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill.iter_all_player_pages",
        return_value=iter(discovered_players),
    ), patch(
        "scripts.tennis.wta_top200_historical_backfill.backfill_player",
        return_value={
            "status": "ok", "matches_found": 1,
            "match_details_resolved": 1, "odds_inserted": 1,
        },
    ) as backfill, patch(
        "scripts.tennis.wta_top200_historical_backfill.time.sleep",
    ):
        result = run_country_catalog_backfill(years=[2026], max_workers=2)

    assert backfill.call_count == 5
    assert result["players_processed"] == 5
    assert result["players_ok"] == 5
    assert result["matches_found"] == 5
