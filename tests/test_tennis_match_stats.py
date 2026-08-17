"""Tests de estadísticas reales de partido vía Flashscore (2026-08-01,
pedido explícito del usuario: "cuando se detecte que un partido terminó
se debe hacer fetch de las stats... para identificar el match usar
coincidencia de nombres + fecha + torneo"). Alcance: SOLO tenis."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis.models import MatchStats as TennisMatchStats
from database.historical.tennis.matchstats import has_match_stats, save_match_stats, get_match_stats
from outer_apis.scrapers.tennis.flashscore_scraper import (
    _extract_tournament_name, get_match_statistics, find_match_id, _parse_record,
    _parse_score_sets, _score_sets_match,
)


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        TennisMatchStats.delete().execute()
    yield
    with db_historical.connection_context():
        TennisMatchStats.delete().execute()


# ── database/historical/tennis/matchstats.py ────────────────────────────

def test_legacy_market_stats_are_not_persisted_without_match_history():
    stats = {"match": {"Aces": ["3", "0"], "Double Faults": ["5", "2"]}}
    save_match_stats("KXATPMATCH-X", "fs123", stats)
    assert has_match_stats("KXATPMATCH-X") is False
    assert get_match_stats("KXATPMATCH-X") is None


def test_save_match_stats_skips_empty():
    save_match_stats("KXATPMATCH-EMPTY", "fs999", {})
    assert has_match_stats("KXATPMATCH-EMPTY") is False


def test_get_match_stats_none_when_missing():
    assert get_match_stats("KXATPMATCH-NOPE") is None


def test_has_match_stats_false_on_db_failure():
    with patch(
        "database.historical.tennis.matchstats.TennisMatchStats.select",
        side_effect=Exception("boom"),
    ):
        assert has_match_stats("anything") is False


# ── flashscore_scraper: extracción de torneo ────────────────────────────

def test_extract_tournament_name_with_country():
    za = "ATP - SINGLES: Barcelona (Spain), clay¬ZEE÷rZvXbpeD¬ZB÷3473162"
    assert _extract_tournament_name(za) == "Barcelona"


def test_extract_tournament_name_without_country():
    za = "WTA - SINGLES: Washington, hard¬ZEE÷abc"
    assert _extract_tournament_name(za) == "Washington"


def test_extract_tournament_name_fail_closed_on_unrecognized_format():
    assert _extract_tournament_name("garbage without colon") is None


# ── flashscore_scraper: get_match_statistics (parser del feed real) ────

_REAL_STATS_FEED = (
    "SE÷Match¬~SF÷Service¬~SG÷Aces¬SH÷3¬SI÷0¬~SG÷Double Faults¬SH÷5¬SI÷2¬~"
    "SG÷1st serve percentage¬SH÷58%¬SI÷51%¬~SF÷Points¬~SG÷Winners¬SH÷20¬SI÷13¬~"
    "SE÷Set 1¬~SF÷Service¬~SG÷Aces¬SH÷1¬SI÷0¬~A1÷¬~"
)


def _make_response(text, status=200):
    class _FakeResp:
        pass
    r = _FakeResp()
    r.status_code = status
    r.text = text
    return r


def test_get_match_statistics_parses_sections_and_values():
    with patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.brightdata_proxy.fetch_with_ip_block_fallback",
        return_value=_make_response(_REAL_STATS_FEED),
    ):
        stats = get_match_statistics("fNdO7zfc")

    assert stats["match"]["Aces"] == ("3", "0")
    assert stats["match"]["Double Faults"] == ("5", "2")
    assert stats["match"]["1st serve percentage"] == ("58%", "51%")
    assert stats["match"]["Winners"] == ("20", "13")
    assert stats["set_1"]["Aces"] == ("1", "0")


def test_get_match_statistics_fail_closed_on_empty_response():
    with patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.brightdata_proxy.fetch_with_ip_block_fallback",
        return_value=_make_response("0"),
    ):
        assert get_match_statistics("nostats") is None


def test_get_match_statistics_fail_closed_on_request_failure():
    with patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.brightdata_proxy.fetch_with_ip_block_fallback",
        return_value=None,
    ):
        assert get_match_statistics("whatever") is None


def test_get_match_statistics_fail_closed_on_http_error():
    with patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.brightdata_proxy.fetch_with_ip_block_fallback",
        return_value=_make_response("", status=500),
    ):
        assert get_match_statistics("whatever") is None


# ── flashscore_scraper: _parse_score_sets / _score_sets_match ───────────

def test_parse_score_sets_plain():
    assert _parse_score_sets("6-4, 6-2") == [(6, 4), (6, 2)]


def test_parse_score_sets_tiebreak_sup_after_dash():
    assert _parse_score_sets("6-4, 6-4, 7-6<sup>4</sup>") == [(6, 4), (6, 4), (7, 6)]


def test_parse_score_sets_tiebreak_sup_before_dash():
    # 2026-08-12, bug real: el tag puede caer ANTES del guión también
    # (el lado que ganó games perdió el tie-break) -- antes se
    # descartaba el chunk entero en silencio.
    assert _parse_score_sets("6-3, 6<sup>6</sup>-7, 7-5") == [(6, 3), (6, 7), (7, 5)]


def test_parse_score_sets_multiple_tiebreaks_mixed_position():
    assert _parse_score_sets("7-6<sup>3</sup>, 6<sup>5</sup>-7, 7-6<sup>2</sup>") == [
        (7, 6), (6, 7), (7, 6),
    ]


def test_parse_score_sets_retired_suffix():
    assert _parse_score_sets("7-5, 2-6, 6-4 ret.") == [(7, 5), (2, 6), (6, 4)]


def test_parse_score_sets_fail_closed_on_empty():
    assert _parse_score_sets(None) is None
    assert _parse_score_sets("") is None


def test_score_sets_match_direct_orientation():
    assert _score_sets_match([(6, 4), (6, 2)], [(6, 4), (6, 2)]) is True


def test_score_sets_match_mirrored_orientation():
    assert _score_sets_match([(6, 4), (6, 2)], [(4, 6), (2, 6)]) is True


def test_score_sets_match_false_on_different_result():
    assert _score_sets_match([(6, 4), (6, 2)], [(3, 6), (2, 6)]) is False


def test_score_sets_match_false_on_different_set_count():
    assert _score_sets_match([(6, 4), (6, 2)], [(6, 4)]) is False


# ── flashscore_scraper: find_match_id ───────────────────────────────────

def _fake_results_html(*records_raw: str) -> str:
    """Simula el shape mínimo que `_parse_all_records_with_tournament`
    espera: un bloque ~ZA÷ seguido de records ~AA÷."""
    return "~ZA÷ATP - SINGLES: Montreal (Canada), hard¬ZEE÷x" + "".join(
        "~AA÷" + r for r in records_raw
    )


def _raw_record(match_id, ts, p1, p2, winner=1, sets1=2, sets2=0, set_scores=""):
    return (
        f"{match_id}¬AD÷{ts}¬AB÷3¬FH÷{p1}¬FK÷{p2}¬AS÷{winner}¬AG÷{sets1}¬AH÷{sets2}{set_scores}"
    )


def _set_scores(*pairs: tuple[int, int]) -> str:
    """Construye el sufijo BA/BB.../BI/BJ (games por set, hasta 5 sets)
    -- 2026-08-11, pedido explícito del usuario ("añade un filtro más
    para validar el match"), tests del nuevo filtro de marcador real."""
    codes = [("BA", "BB"), ("BC", "BD"), ("BE", "BF"), ("BG", "BH"), ("BI", "BJ")]
    return "".join(f"¬{c1}÷{g1}¬{c2}÷{g2}" for (c1, c2), (g1, g2) in zip(codes, pairs))


def test_find_match_id_matches_both_names_and_date():
    import time
    ts = int(time.mktime((2026, 8, 1, 12, 0, 0, 0, 0, 0)))
    html = _fake_results_html(
        _raw_record("abc123", ts, "Medjedovic H.", "Cerundolo J.M."),
    )
    with patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.search_player",
        return_value=("pid1", "slug1"),
    ), patch(
        "outer_apis.scrapers.tennis.flashscore_scraper._fetch_results_page",
        return_value=html,
    ):
        match_id = find_match_id(
            "Hamad Medjedovic", "Juan Manuel Cerundolo", "2026-08-01", "Montreal",
        )
    assert match_id == "abc123"


def test_find_match_id_fail_closed_when_names_dont_match():
    import time
    ts = int(time.mktime((2026, 8, 1, 12, 0, 0, 0, 0, 0)))
    html = _fake_results_html(
        _raw_record("abc123", ts, "Someone Else A.", "Someone Else B."),
    )
    with patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.search_player",
        return_value=("pid1", "slug1"),
    ), patch(
        "outer_apis.scrapers.tennis.flashscore_scraper._fetch_results_page",
        return_value=html,
    ):
        match_id = find_match_id(
            "Hamad Medjedovic", "Juan Manuel Cerundolo", "2026-08-01", "Montreal",
        )
    assert match_id is None


def test_find_match_id_fail_closed_when_date_too_far():
    import time
    ts = int(time.mktime((2026, 1, 1, 12, 0, 0, 0, 0, 0)))  # muy lejos de la fecha buscada
    html = _fake_results_html(
        _raw_record("abc123", ts, "Medjedovic H.", "Cerundolo J.M."),
    )
    with patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.search_player",
        return_value=("pid1", "slug1"),
    ), patch(
        "outer_apis.scrapers.tennis.flashscore_scraper._fetch_results_page",
        return_value=html,
    ):
        match_id = find_match_id(
            "Hamad Medjedovic", "Juan Manuel Cerundolo", "2026-08-01", "Montreal",
        )
    assert match_id is None


def test_find_match_id_fail_closed_when_player_not_found():
    with patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.search_player",
        return_value=None,
    ):
        assert find_match_id("Nobody", "Nobody Else", "2026-08-01") is None


def test_find_match_id_falls_back_to_player_b_when_a_not_found():
    import time
    ts = int(time.mktime((2026, 8, 1, 12, 0, 0, 0, 0, 0)))
    html = _fake_results_html(
        _raw_record("abc123", ts, "Medjedovic H.", "Cerundolo J.M."),
    )

    def _search(name):
        return ("pid2", "slug2") if name == "Juan Manuel Cerundolo" else None

    with patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.search_player", side_effect=_search,
    ), patch(
        "outer_apis.scrapers.tennis.flashscore_scraper._fetch_results_page",
        return_value=html,
    ):
        match_id = find_match_id(
            "Hamad Medjedovic", "Juan Manuel Cerundolo", "2026-08-01",
        )
    assert match_id == "abc123"


def test_find_match_id_accepts_candidate_when_score_matches():
    import time
    ts = int(time.mktime((2026, 8, 1, 12, 0, 0, 0, 0, 0)))
    html = _fake_results_html(
        _raw_record("abc123", ts, "Medjedovic H.", "Cerundolo J.M.", set_scores=_set_scores((6, 4), (6, 2))),
    )
    with patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.search_player",
        return_value=("pid1", "slug1"),
    ), patch(
        "outer_apis.scrapers.tennis.flashscore_scraper._fetch_results_page",
        return_value=html,
    ):
        match_id = find_match_id(
            "Hamad Medjedovic", "Juan Manuel Cerundolo", "2026-08-01", "Montreal",
            expected_score="6-4, 6-2",
        )
    assert match_id == "abc123"


def test_find_match_id_accepts_candidate_when_tiebreak_sup_tag_before_dash():
    # 2026-08-12, bug real encontrado en auditoría de verificación: el
    # tag <sup> de tie-break de TennisExplorer puede caer ANTES del
    # guión ("6<sup>6</sup>-7", el lado que ganó el set en games perdió
    # el tie-break) y no solo después ("7-6<sup>4</sup>") -- el regex
    # viejo de `_parse_score_sets` solo toleraba el segundo caso,
    # descartaba el set entero en el primero, y `_score_sets_match`
    # rechazaba partidos REALES por longitud de sets distinta. Caso real:
    # Djokovic vs Tommy Paul, 2026-06-26, "6-3, 6<sup>6</sup>-7, 7-5".
    import time
    ts = int(time.mktime((2026, 6, 26, 12, 0, 0, 0, 0, 0)))
    html = _fake_results_html(
        _raw_record(
            "abc123", ts, "Djokovic N.", "Paul T.",
            set_scores=_set_scores((6, 3), (6, 7), (7, 5)),
        ),
    )
    with patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.search_player",
        return_value=("pid1", "slug1"),
    ), patch(
        "outer_apis.scrapers.tennis.flashscore_scraper._fetch_results_page",
        return_value=html,
    ):
        match_id = find_match_id(
            "Novak Djokovic", "Tommy Paul", "2026-06-26", None,
            expected_score="6-3, 6<sup>6</sup>-7, 7-5",
        )
    assert match_id == "abc123"


def test_find_match_id_rejects_candidate_when_score_does_not_match():
    # 2026-08-11, pedido explícito del usuario ("añade un filtro más
    # para validar el match") -- caso real que motivó esto: mismos 2
    # jugadores + fecha cercana, pero es OTRO partido (marcador distinto,
    # ej. round-robin/colisión de fecha). Antes se hubiera aceptado
    # igual (solo nombre+fecha); ahora se rechaza.
    import time
    ts = int(time.mktime((2026, 8, 1, 12, 0, 0, 0, 0, 0)))
    html = _fake_results_html(
        _raw_record("abc123", ts, "Medjedovic H.", "Cerundolo J.M.", set_scores=_set_scores((3, 6), (2, 6))),
    )
    with patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.search_player",
        return_value=("pid1", "slug1"),
    ), patch(
        "outer_apis.scrapers.tennis.flashscore_scraper._fetch_results_page",
        return_value=html,
    ):
        match_id = find_match_id(
            "Hamad Medjedovic", "Juan Manuel Cerundolo", "2026-08-01", "Montreal",
            expected_score="6-4, 6-2",
        )
    assert match_id is None


def test_find_match_id_score_filter_skipped_when_not_provided():
    # comportamiento previo intacto si el caller no pasa expected_score.
    import time
    ts = int(time.mktime((2026, 8, 1, 12, 0, 0, 0, 0, 0)))
    html = _fake_results_html(
        _raw_record("abc123", ts, "Medjedovic H.", "Cerundolo J.M."),
    )
    with patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.search_player",
        return_value=("pid1", "slug1"),
    ), patch(
        "outer_apis.scrapers.tennis.flashscore_scraper._fetch_results_page",
        return_value=html,
    ):
        match_id = find_match_id(
            "Hamad Medjedovic", "Juan Manuel Cerundolo", "2026-08-01", "Montreal",
        )
    assert match_id == "abc123"


# ── proxy pausado por default (2026-08-04, pedido explícito del usuario) ──

def test_proxy_fetch_disabled_by_default():
    """"El proxy ya no se usará hasta nuevo aviso para fetchs" -- pedido
    explícito del usuario. `PROXY_FETCH_ENABLED` debe ser False por
    default (env var `FLASHSCORE_PROXY_FETCH_ENABLED` sin configurar)."""
    from outer_apis.scrapers.tennis.flashscore_scraper import PROXY_FETCH_ENABLED
    assert PROXY_FETCH_ENABLED is False


def test_get_match_statistics_goes_direct_and_does_not_force_proxy():
    """2026-08-16, pedido explícito del usuario ("el gate de estadísticas
    sin proxy"): el gate duro `PROXY_FETCH_ENABLED` se removió de
    `get_match_statistics`. Ahora usa el mismo mecanismo que el resto del
    módulo -- request DIRECTA primero, y el proxy solo entra si hay una
    señal real de bloqueo (eso lo cubre `fetch_with_ip_block_fallback`,
    testeado aparte). Este test verifica que ya NO corta en seco y que no
    fuerza el proxy: con el gate viejo, esto devolvía None sin pedir nada.

    Motivo real: `tennis_match_stats` tenía 0 filas en producción pese a
    tener el pipeline completo ya wireado -- esta función cortaba en la
    primera línea, y el endpoint responde 200 sin proxy (verificado en
    vivo contra match_id reales)."""
    with patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.brightdata_proxy.fetch_with_ip_block_fallback",
        return_value=_make_response(_REAL_STATS_FEED),
    ) as direct, patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.brightdata_proxy.request_via_proxy_with_ip_retry",
    ) as forced_proxy:
        result = get_match_statistics("whatever")

    direct.assert_called_once()
    forced_proxy.assert_not_called()
    assert result is not None and result["match"]["Aces"] == ("3", "0")


# ── `_fetch_results_page`: fetch directo + fallback a proxy (2026-08-12) ──
# Alineado al mismo mecanismo que `search_player`/`get_match_duration`
# (`fetch_with_ip_block_fallback`) -- pedido explícito del usuario ("se
# puede arreglar con lo que usa la otra función"). Ya NO depende de
# `PROXY_FETCH_ENABLED` (test de gate duro reemplazado por estos 3).

_FAKE_FEED_PAGE = "SA÷2¬~ZA÷ATP - SINGLES: Test (Country), hard¬ZEE÷x¬~AA÷mockrecord¬AD÷1700000000¬AB÷3¬~"


def test_fetch_results_page_uses_direct_request_when_not_blocked():
    from unittest.mock import MagicMock
    from outer_apis.scrapers.tennis.flashscore_scraper import _fetch_results_page

    resp = MagicMock(status_code=200, text=_FAKE_FEED_PAGE)
    with patch("requests.get", return_value=resp) as mock_get, patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.flashscore_throttle.wait",
    ), patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.brightdata_proxy.request_via_proxy_with_ip_retry",
    ) as mock_proxy:
        # max_pages=1: acá se testea el mecanismo directo/fallback de UNA
        # página, no la paginación en sí (ver tests dedicados más abajo,
        # "_fetch_results_page: paginación real").
        result = _fetch_results_page("pid1", "slug1", max_pages=1)

    mock_get.assert_called_once()
    mock_proxy.assert_not_called()  # sin señal de bloqueo, nunca cae a proxy
    assert result == _FAKE_FEED_PAGE


def test_fetch_results_page_falls_back_to_proxy_on_ip_block_signal():
    from unittest.mock import MagicMock
    from outer_apis.scrapers.tennis.flashscore_scraper import _fetch_results_page

    blocked_resp = MagicMock(status_code=403, text="")
    proxied_resp = MagicMock(status_code=200, text=_FAKE_FEED_PAGE)
    with patch("requests.get", return_value=blocked_resp), patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.flashscore_throttle.wait",
    ), patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.brightdata_proxy.request_via_proxy_with_ip_retry",
        return_value=proxied_resp,
    ) as mock_proxy:
        result = _fetch_results_page("pid1", "slug1", max_pages=1)

    mock_proxy.assert_called_once()
    assert result == _FAKE_FEED_PAGE


def test_fetch_results_page_fails_closed_when_both_paths_fail():
    import requests

    from outer_apis.scrapers.tennis.flashscore_scraper import _fetch_results_page

    with patch("requests.get", side_effect=requests.ConnectionError("network down")), patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.flashscore_throttle.wait",
    ), patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.brightdata_proxy.request_via_proxy_with_ip_retry",
        return_value=None,
    ):
        assert _fetch_results_page("pid1", "slug1", max_pages=1) is None


# ── `_fetch_results_page`: paginación real (2026-08-12) ─────────────────
# Descubierto capturando tráfico real de red con Playwright: la
# paginación real de Flashscore es `pr_2_167_{pid}_{page}_-4_en_1_s`,
# `page` es un contador simple. Estos tests verifican la ORQUESTACIÓN de
# la paginación (cuántas páginas pide, cuándo para), no el contenido real.

def test_fetch_results_page_paginates_until_empty_page():
    from unittest.mock import MagicMock
    from outer_apis.scrapers.tennis.flashscore_scraper import _fetch_results_page

    responses = [
        MagicMock(status_code=200, text=_FAKE_FEED_PAGE),
        MagicMock(status_code=200, text=_FAKE_FEED_PAGE),
        MagicMock(status_code=200, text=""),  # fin real de la carrera
    ]
    with patch("requests.get", side_effect=responses) as mock_get, patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.flashscore_throttle.wait",
    ):
        result = _fetch_results_page("pid1", "slug1")

    assert mock_get.call_count == 3
    assert result == _FAKE_FEED_PAGE + _FAKE_FEED_PAGE


def test_fetch_results_page_stops_early_once_target_date_covered():
    from unittest.mock import MagicMock
    from outer_apis.scrapers.tennis.flashscore_scraper import _fetch_results_page

    # página 0: partidos de 2026 (no cubre el target 2020-01-01 todavía)
    page0 = "SA÷2¬~ZA÷ATP - SINGLES: Test¬ZEE÷x¬~AA÷m0¬AD÷1767225600¬AB÷3¬~"  # ~2026
    # página 1: partidos de 2019 (ya PASÓ el target -> corte temprano)
    page1 = "SA÷2¬~ZA÷ATP - SINGLES: Test¬ZEE÷x¬~AA÷m1¬AD÷1546300800¬AB÷3¬~"  # ~2019
    with patch(
        "requests.get",
        side_effect=[
            MagicMock(status_code=200, text=page0),
            MagicMock(status_code=200, text=page1),
        ],
    ) as mock_get, patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.flashscore_throttle.wait",
    ):
        result = _fetch_results_page("pid1", "slug1", target_date="2020-01-01")

    assert mock_get.call_count == 2  # se detiene apenas la página 1 ya pasó el target
    assert result == page0 + page1


def test_fetch_results_page_respects_max_pages_cap():
    from unittest.mock import MagicMock
    from outer_apis.scrapers.tennis.flashscore_scraper import _fetch_results_page

    with patch(
        "requests.get", return_value=MagicMock(status_code=200, text=_FAKE_FEED_PAGE),
    ) as mock_get, patch(
        "outer_apis.scrapers.tennis.flashscore_scraper.flashscore_throttle.wait",
    ):
        result = _fetch_results_page("pid1", "slug1", max_pages=3)

    assert mock_get.call_count == 3  # nunca pide más de max_pages, aunque sigan viniendo datos
    assert result == _FAKE_FEED_PAGE * 3
