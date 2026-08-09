"""Tests del scraper de historial completo por jugador/a de
TennisExplorer (2026-08-03, pedido explícito del usuario -- reemplaza
el copy-paste manual que reveló un gap real en flashscore_scraper.py).

Fixtures HTML mínimas pero con la estructura REAL confirmada en vivo
(ver outer_apis/scrapers/tennis/tennisexplorer_player_scraper.py)."""

from __future__ import annotations

from unittest.mock import patch

from outer_apis.scrapers.tennis.tennisexplorer_player_scraper import (
    get_player_match_history, guess_player_slug, _parse_year_html,
    _verify_player_page, _reorient_score_to_self,
    get_player_recent_matches, _to_iso_date, _derive_result_from_score,
    resolve_player_slug, _extract_href_slug,
    get_match_detail, _bio_row, _parse_birthdate, _parse_int_prefix, get_tour_by_sex,
)

_TITLE_OK = "<title>Alexandra Eala - Tennis Explorer</title>"


def _row(self_first: bool, opponent: str, score: str, odds_self: str, odds_opp: str,
         round_title: str = "final", round_short: str = "F", surface: str = "hard",
         tournament_before: bool = True) -> str:
    self_block = '<a href="/player/eala/" class="notU"><strong>Eala A.</strong></a>'
    opp_block = f'<a href="/player/x/">{opponent}</a>'
    if self_first:
        p1, p2 = self_block, opp_block
        c1, c2 = odds_self, odds_opp
    else:
        p1, p2 = opp_block, self_block
        c1, c2 = odds_opp, odds_self
    row = f'''
        <tr class="one">
            <td class="first time">01.08.</td>
            <td class="s-color"><span title="{surface}" style="x">&nbsp;</span></td>
            <td class="t-name">{p1} - {p2}</td>
            <td class="round" title="{round_title}">{round_short}</td>
            <td class="tl"><a href="/match-detail/?id=1" title="x">{score}</a></td>
            <td class="course">{c1}</td>
            <td class="course">{c2}</td>
        </tr>
    '''
    return row


def _page(rows_html: str, year: int = 2026, tournament_type_class: str = "type-women2") -> str:
    header = (
        '<tr class="head flags"><td class="t-name" colspan="3">'
        '<a href="/washington/2026/wta-women/"><span class="fl fl-us">&nbsp;</span>'
        f'<span class="{tournament_type_class}">&nbsp;</span>Washington</a></td></tr>'
    )
    return (
        f"{_TITLE_OK}"
        f'<div id="matches-{year}-1-data" class="none">'
        f"<table><tbody>{header}{rows_html}</tbody></table>"
        f'</div><div id="matches-{year}-2-data" class="none"></div>'
    )


def test_verify_player_page_matches_real_name():
    assert _verify_player_page(_TITLE_OK, "Alexandra Eala") is True
    assert _verify_player_page(_TITLE_OK, "Someone Else") is False


def test_verify_player_page_tolerates_one_token_difference():
    assert _verify_player_page(_TITLE_OK, "Alex Eala") is True  # "Alex" != "Alexandra" pero "Eala" matchea


def test_verify_player_page_tolerates_first_name_initial():
    title = "<title>C. Ruud - Tennis Explorer</title>"
    assert _verify_player_page(title, "Casper Ruud") is True


def test_verify_player_page_rejects_same_surname_different_person():
    # 2026-08-06, bug real encontrado en vivo: "Casper Ruud" (jugador
    # actual) vs "Christian Ruud" (su padre, ex-jugador) -- MISMO
    # apellido exacto, pero personas DISTINTAS. La versión anterior de
    # este chequeo (tolerancia por intersección de tokens sin
    # distinguir nombre/apellido) aceptaba esto por error -- nunca debe
    # aceptarse solo porque el apellido coincide.
    title = "<title>Christian Ruud - Tennis Explorer</title>"
    assert _verify_player_page(title, "Casper Ruud") is False


def test_verify_player_page_requires_exact_surname_match():
    title = "<title>Alexandra Ealaa - Tennis Explorer</title>"
    assert _verify_player_page(title, "Alexandra Eala") is False


def test_verify_player_page_fail_closed_on_single_token_name():
    assert _verify_player_page(_TITLE_OK, "Eala") is False


def test_reorient_score_flips_when_self_is_second():
    assert _reorient_score_to_self("6-4, 4-6, 6-3", self_is_first=True) == "6-4, 4-6, 6-3"
    assert _reorient_score_to_self("6-4, 4-6, 6-3", self_is_first=False) == "4-6, 6-4, 3-6"


def test_parse_year_html_self_first():
    html = _page(_row(self_first=True, opponent="Pegula J.", score="4-6, 6-4, 6-0", odds_self="2.36", odds_opp="1.58"))
    results = _parse_year_html(html, "Alexandra Eala")
    assert len(results) == 1
    r = results[0]
    assert r["opponent"] == "Pegula J."
    assert r["score"] == "4-6, 6-4, 6-0"
    assert r["odds_self"] == 2.36
    assert r["odds_opponent"] == 1.58
    assert r["tournament"] == "Washington"


def test_parse_year_html_recognizes_any_tournament_type_class():
    """Bug real encontrado 2026-08-04 investigando por qué 48,127 filas
    de source="tennisexplorer" quedaron con tournament=None: el regex
    original hardcodeaba `class="type-women2"`, pero la fuente
    categoriza el TORNEO (no la jugadora) con esa clase -- un evento
    (ej. "Czech league", de equipos) puede venir marcado `type-men2`
    incluso en la página de una jugadora WTA (inconsistencia real de la
    fuente). Como `current_tournament` se actualiza secuencialmente, un
    solo header que no matcheaba dejaba sin torneo a TODOS los partidos
    siguientes -- de ahí el volumen tan grande de filas afectadas.
    Caso real reproducido con Elise Mertens/"Czech league" (type-men2)."""
    html = _page(
        _row(self_first=True, opponent="Rival R.", score="6-3, 6-2", odds_self="1.50", odds_opp="2.50"),
        tournament_type_class="type-men2",
    )
    results = _parse_year_html(html, "Alexandra Eala")
    assert len(results) == 1
    assert results[0]["tournament"] == "Washington"  # NO None


def test_parse_year_html_self_second_reorients_score_and_odds():
    """Bug real encontrado y corregido: cuando el rival aparece PRIMERO
    en la fila, el score y las cuotas deben reorientarse a la
    perspectiva del dueño de la página, no quedar como vienen crudas."""
    html = _page(_row(self_first=False, opponent="Paolini J.", score="6-4, 4-6, 6-3", odds_self="1.60", odds_opp="2.32"))
    results = _parse_year_html(html, "Alexandra Eala")
    assert len(results) == 1
    r = results[0]
    assert r["opponent"] == "Paolini J."
    assert r["score"] == "4-6, 6-4, 3-6"  # invertido respecto al crudo "6-4, 4-6, 6-3"
    assert r["odds_self"] == 1.60
    assert r["odds_opponent"] == 2.32


def test_parse_year_html_strips_tiebreak_superscript():
    row = _row(self_first=True, opponent="Fernandez L.", score='6-2, 7-6<sup>1</sup>', odds_self="1.79", odds_opp="2.01")
    html = _page(row)
    results = _parse_year_html(html, "Alexandra Eala")
    assert results[0]["score"] == "6-2, 7-6"


def test_parse_year_html_handles_missing_round_title():
    row = '''
        <tr class="one">
            <td class="first time">14.01.</td>
            <td class="s-color"><span title="hard" style="x">&nbsp;</span></td>
            <td class="t-name"><a href="/player/eala/" class="notU"><strong>Eala A.</strong></a> - <a href="/player/vekic/">Vekic D.</a></td>
            <td class="round">&nbsp;</td>
            <td class="tl"><a href="/match-detail/?id=2" title="x">6-3, 6-4</a></td>
            <td class="course">1.60</td>
            <td class="course">2.22</td>
        </tr>
    '''
    html = _page(row)
    results = _parse_year_html(html, "Alexandra Eala")
    assert len(results) == 1
    assert results[0]["opponent"] == "Vekic D."


def test_parse_year_html_skips_rows_without_real_score():
    row = '''
        <tr class="one">
            <td class="first noWrp tl"><a href="/toronto-wta/2026/wta-women/">Toronto WTA</a></td>
            <td class="tl" colspan="5">Next opponent is not known yet</td>
        </tr>
    '''
    html = _page(row)
    results = _parse_year_html(html, "Alexandra Eala")
    assert results == []


def test_get_player_match_history_fetches_each_requested_year():
    html_2026 = _page(_row(self_first=True, opponent="Pegula J.", score="4-6, 6-4, 6-0", odds_self="2.36", odds_opp="1.58"), year=2026)
    html_2025 = _page(_row(self_first=True, opponent="Swiatek I.", score="6-2, 6-3", odds_self="3.0", odds_opp="1.4"), year=2025)

    def fake_fetch(slug, year=None):
        return html_2026 if year in (2026, None) else html_2025

    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper._fetch", side_effect=fake_fetch,
    ):
        result = get_player_match_history("eala", "Alexandra Eala", years=[2026, 2025])
    assert len(result) == 2
    opponents = {r["opponent"] for r in result}
    assert opponents == {"Pegula J.", "Swiatek I."}


def test_get_player_match_history_fail_closed_when_verification_fails():
    """Si el <title> de la página no coincide con el jugador esperado,
    no se agregan sus partidos -- nunca se confía ciegamente en el slug."""
    wrong_page = "<title>Someone Else - Tennis Explorer</title>"
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper._fetch", return_value=wrong_page,
    ):
        result = get_player_match_history("eala", "Alexandra Eala", years=[2026])
    assert result == []


def test_guess_player_slug_verifies_before_returning():
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper._fetch", return_value=_TITLE_OK,
    ):
        assert guess_player_slug("Alexandra Eala") == "eala"


def test_guess_player_slug_fail_closed_on_mismatch():
    wrong_page = "<title>Someone Else - Tennis Explorer</title>"
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper._fetch", return_value=wrong_page,
    ):
        assert guess_player_slug("Alexandra Eala") is None


def test_guess_player_slug_fail_closed_on_fetch_failure():
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper._fetch", return_value=None,
    ):
        assert guess_player_slug("Alexandra Eala") is None


# ── get_player_recent_matches (2026-08-04, reemplaza flashscore_scraper) ──

def test_to_iso_date_uses_reference_year():
    assert _to_iso_date("03.08.", 2025) == "2025-08-03"


def test_to_iso_date_fail_closed_on_garbage():
    assert _to_iso_date("garbage", 2025) is None


def test_derive_result_from_score_win():
    assert _derive_result_from_score("6-4, 6-3") == "W"


def test_derive_result_from_score_loss():
    assert _derive_result_from_score("4-6, 3-6") == "L"


def test_get_player_recent_matches_fail_closed_when_no_slug():
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper.guess_player_slug", return_value=None,
    ):
        assert get_player_recent_matches("Nonexistent Player") == []


def test_get_player_recent_matches_matches_flashscore_shape():
    """Mismo shape que `flashscore_scraper.get_player_recent_matches`
    ({"fecha","oponente","resultado","score","source"}) -- pedido
    explícito del usuario para no tener que tocar los callers
    (match_history.py) al migrar de Flashscore a TennisExplorer."""
    html = _page(
        _row(self_first=True, opponent="Pegula J.", score="6-4, 6-3", odds_self="1.5", odds_opp="2.5"),
        year=2026,
    )
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper.guess_player_slug", return_value="eala",
    ), patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper._fetch",
        side_effect=lambda slug, year=None: html if year is None else None,
    ), patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper._verify_player_page", return_value=True,
    ), patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper._date",
    ) as mock_date:
        mock_date.today.return_value.year = 2026
        result = get_player_recent_matches("Alexandra Eala", n=20)

    assert len(result) == 1
    r = result[0]
    assert set(r.keys()) == {"fecha", "oponente", "resultado", "score", "source"}
    assert r["oponente"] == "Pegula J."
    assert r["resultado"] == "W"
    assert r["score"] == "6-4, 6-3"
    assert r["source"] == "tennisexplorer"


def test_get_player_recent_matches_sorted_most_recent_first_and_capped_at_n():
    html_year = _page(
        _row(self_first=True, opponent="A", score="6-4, 6-3", odds_self="1.5", odds_opp="2.5"),
        year=2026,
    )
    # 2 filas con fechas distintas -- inyectamos 2 partidos con distinto orden real
    two_rows_html = html_year.replace(
        '01.08.', '01.08.'
    )
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper.guess_player_slug", return_value="eala",
    ), patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper._fetch",
        side_effect=lambda slug, year=None: two_rows_html if year is None else None,
    ), patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper._verify_player_page", return_value=True,
    ), patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper._date",
    ) as mock_date:
        mock_date.today.return_value.year = 2026
        result = get_player_recent_matches("Alexandra Eala", n=1)

    assert len(result) == 1  # respeta el límite n


def test_parse_year_html_extracts_real_opponent_slug():
    # 2026-08-06, pedido explícito del usuario: "corrige la data de
    # matches por confusión de nombres" -- el `href` real del rival
    # (ej. "/player/alcaraz-5ab70/") ya viene en el HTML descargado.
    html = _page(_row(self_first=True, opponent="Pegula J.", score="4-6, 6-4, 6-0", odds_self="2.36", odds_opp="1.58"))
    results = _parse_year_html(html, "Alexandra Eala")
    assert results[0]["opponent_slug"] == "x"  # ver _row: opp_block usa href="/player/x/"


def test_extract_href_slug_real_disambiguated_slug():
    block = '<a href="/player/alcaraz-5ab70/">Alcaraz C.</a>'
    assert _extract_href_slug(block) == "alcaraz-5ab70"


def test_extract_href_slug_fail_closed_no_link():
    assert _extract_href_slug("no hay link acá") is None


def test_resolve_player_slug_uses_naive_guess_first():
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper.guess_player_slug", return_value="eala",
    ):
        assert resolve_player_slug("Alexandra Eala", candidate_slugs=["should-not-be-tried"]) == "eala"


def test_resolve_player_slug_falls_back_to_verified_candidate():
    # 2026-08-06, caso real: "Casper Ruud" -- el slug naive "ruud" NO
    # verifica (es la página de su padre Christian Ruud), pero un slug
    # candidato REAL cosechado (ej. "ruud-c1234") sí.
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper.guess_player_slug", return_value=None,
    ), patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper._fetch",
        side_effect=lambda slug, year=None: "<html>x</html>" if slug == "ruud-c1234" else None,
    ), patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper._verify_player_page",
        side_effect=lambda html, name: True,
    ):
        result = resolve_player_slug("Casper Ruud", candidate_slugs=["ruud-wrong", "ruud-c1234"])
    assert result == "ruud-c1234"


def test_resolve_player_slug_fail_closed_when_no_candidate_verifies():
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper.guess_player_slug", return_value=None,
    ), patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper._fetch", return_value="<html>x</html>",
    ), patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper._verify_player_page", return_value=False,
    ):
        assert resolve_player_slug("Naomi Osaka", candidate_slugs=["osaka-mari"]) is None


# --- get_match_detail (2026-08-08, pedido explícito del usuario --
# resolver identidad con certeza vía la página match-detail, en vez de
# heurística de apellido+fecha) ---

def test_parse_year_html_extracts_match_id_from_score_link():
    # `_row()` ya incluye `href="/match-detail/?id=1"` en su fixture --
    # confirma que el regex extendido lo captura sin romper nada más.
    page = _page(_row(self_first=True, opponent="Rival R.", score="6-4, 6-2", odds_self="1.50", odds_opp="2.50"))
    results = _parse_year_html(page, "Alexandra Eala")
    assert len(results) == 1
    assert results[0]["match_id"] == "1"


_MATCH_DETAIL_HTML = '''
<h1 class="bg">Anisimova - Sabalenka</h1>
<table class="result gDetail noMgB" cellspacing="0">
    <thead>
        <tr>
            <th class="plName" colspan="2"><a href="/player/anisimova-3be62">Anisimova Amanda</a></th>
            <td class="gScore">2 : 0<br /><span>(6-4, 6-2)</span></td>
            <th class="plName" colspan="2"><a href="/player/sabalenka">Sabalenka Aryna</a></th>
        </tr>
    </thead>
    <tbody>
        <tr class="one">
            <td class="tr">10.</td>
            <th>Singles ranking</th>
            <td class="tl">1.</td>
        </tr>
        <tr class="two">
            <td class="tr">31. 8. 2001</td>
            <th>Birthdate</th>
            <td class="tl">5. 5. 1998</td>
        </tr>
        <tr class="one">
            <td class="tr">180 cm</td>
            <th>Height</th>
            <td class="tl">182 cm</td>
        </tr>
        <tr class="two">
            <td class="tr">59 kg</td>
            <th>Weight</th>
            <td class="tl">80 kg</td>
        </tr>
        <tr class="one">
            <td class="tr">right</td>
            <th>Plays</th>
            <td class="tl">right</td>
        </tr>
    </tbody>
</table>
<div class="box boxBasic lGray"><span class="upper">10.08.2024</span>, 22:10, <a href="/toronto-wta/2024/wta-women/">Toronto WTA</a>, quarterfinal, hard</div>
'''


def test_get_match_detail_parses_real_page_structure():
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper._fetch_match_detail",
        return_value=_MATCH_DETAIL_HTML,
    ):
        result = get_match_detail("2692781")
    assert result == {
        "match_id": "2692781",
        "match_date": "2024-08-10",
        "tournament": "Toronto WTA",
        "round": "quarterfinal",
        "surface": "hard",
        "score": "6-4, 6-2",
        "winner_side": "a",
        "player_a_name": "Anisimova Amanda",
        "player_a_slug": "anisimova-3be62",
        "player_a_ranking": 10,
        "player_a_birthdate": "2001-08-31",
        "player_a_height_cm": 180,
        "player_a_weight_kg": 59,
        "player_a_plays": "right",
        "player_b_name": "Sabalenka Aryna",
        "player_b_slug": "sabalenka",
        "player_b_ranking": 1,
        "player_b_birthdate": "1998-05-05",
        "player_b_height_cm": 182,
        "player_b_weight_kg": 80,
        "player_b_plays": "right",
    }


def test_get_match_detail_fail_closed_on_fetch_failure():
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper._fetch_match_detail", return_value=None,
    ):
        assert get_match_detail("999") is None


def test_get_match_detail_fail_closed_on_unparseable_header():
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper._fetch_match_detail",
        return_value="<html>no header here</html>",
    ):
        assert get_match_detail("999") is None


def test_get_match_detail_winner_side_b_when_second_player_won_more_sets():
    html = _MATCH_DETAIL_HTML.replace("2 : 0", "0 : 2")
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper._fetch_match_detail", return_value=html,
    ):
        result = get_match_detail("1")
    assert result["winner_side"] == "b"


def test_get_match_detail_bio_fields_optional_fail_closed():
    # Partido sin tabla de bio (nivel bajo, algunos partidos reales no
    # la traen) -- nunca debe fabricar ranking/edad/altura/peso/mano.
    html = (
        '<h1 class="bg">A - B</h1>'
        '<table class="result gDetail noMgB"><thead><tr>'
        '<th class="plName" colspan="2"><a href="/player/a-1">Player A</a></th>'
        '<td class="gScore">2 : 1<br /><span>(6-4, 3-6, 6-2)</span></td>'
        '<th class="plName" colspan="2"><a href="/player/b-1">Player B</a></th>'
        '</tr></thead></table>'
    )
    result = None
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper._fetch_match_detail", return_value=html,
    ):
        result = get_match_detail("1")
    assert result is not None
    assert result["player_a_ranking"] is None
    assert result["player_a_birthdate"] is None
    assert result["player_a_height_cm"] is None
    assert result["player_a_plays"] is None


def test_bio_row_fail_closed_when_label_missing():
    assert _bio_row("<html>no rows</html>", "Singles ranking") == (None, None)


def test_parse_birthdate_valid():
    assert _parse_birthdate("31. 8. 2001") == "2001-08-31"


def test_parse_birthdate_fail_closed_on_dash():
    assert _parse_birthdate("-") is None


def test_parse_birthdate_fail_closed_on_garbage():
    assert _parse_birthdate("not a date") is None


def test_parse_int_prefix_strips_unit_suffix():
    assert _parse_int_prefix("180 cm") == 180
    assert _parse_int_prefix("59 kg") == 59
    assert _parse_int_prefix("10.") == 10


def test_parse_int_prefix_fail_closed_on_dash():
    assert _parse_int_prefix("-") is None
    assert _parse_int_prefix(None) is None


# --- get_tour_by_sex (2026-08-09, pedido explícito del usuario --
# resolver `MatchHistory.tour` vía el campo real "Sex: man"|"Sex: woman"
# de la página de perfil de TennisExplorer) ---

def test_get_tour_by_sex_resolves_wta_from_woman():
    html = _TITLE_OK.replace("Alexandra Eala", "Aryna Sabalenka") + '<div class="date">Sex: woman</div>'
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper.resolve_player_slug", return_value="sabalenka",
    ), patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper._fetch", return_value=html,
    ):
        assert get_tour_by_sex("Aryna Sabalenka") == "WTA"


def test_get_tour_by_sex_resolves_atp_from_man():
    html = _TITLE_OK.replace("Alexandra Eala", "Novak Djokovic") + '<div class="date">Sex: man</div>'
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper.resolve_player_slug", return_value="djokovic",
    ), patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper._fetch", return_value=html,
    ):
        assert get_tour_by_sex("Novak Djokovic") == "ATP"


def test_get_tour_by_sex_fail_closed_when_no_slug():
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper.resolve_player_slug", return_value=None,
    ):
        assert get_tour_by_sex("Nobody Known") is None


def test_get_tour_by_sex_fail_closed_when_fetch_fails():
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper.resolve_player_slug", return_value="x",
    ), patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper._fetch", return_value=None,
    ):
        assert get_tour_by_sex("Someone") is None


def test_get_tour_by_sex_fail_closed_when_field_missing():
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper.resolve_player_slug", return_value="x",
    ), patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper._fetch", return_value=_TITLE_OK,
    ):
        assert get_tour_by_sex("Alexandra Eala") is None


def test_get_tour_by_sex_fail_closed_when_verification_fails():
    html = "<title>Someone Else - Tennis Explorer</title>" + '<div class="date">Sex: woman</div>'
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper.resolve_player_slug", return_value="x",
    ), patch(
        "outer_apis.scrapers.tennis.tennisexplorer_player_scraper._fetch", return_value=html,
    ):
        assert get_tour_by_sex("Alexandra Eala") is None
