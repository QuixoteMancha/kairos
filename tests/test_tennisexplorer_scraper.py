from unittest.mock import patch

from outer_apis.scrapers.tennis.tennisexplorer_scraper import (
    get_all_player_country_links,
    iter_all_player_pages,
)


def test_get_all_player_country_links_extracts_only_countries_and_deduplicates():
    html = """
        <a href="/list-players/?country=chile">Chile</a>
        <a href="https://www.tennisexplorer.com/list-players/?country=usa">USA</a>
        <a href="/list-players/?country=chile">Chile again</a>
        <a href="/player/garin/">Not a country</a>
    """
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_scraper._fetch_list_page",
        return_value=html,
    ):
        assert get_all_player_country_links() == [
            "https://www.tennisexplorer.com/list-players/?country=chile",
            "https://www.tennisexplorer.com/list-players/?country=usa",
        ]


def test_iter_all_player_pages_follows_pagination_and_fetches_each_player_once():
    first_page = """
        <a href="/player/garin/">Garin, Cristian</a>
        <a href="?country=chile&page=2">2</a>
    """
    second_page = """
        <a href="/player/garin/">Garin, Cristian</a>
        <a href="/player/tabilo/">Tabilo, Alejandro</a>
    """

    def fetch_list(url):
        return second_page if "page=2" in url else first_page

    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_scraper._fetch_list_page",
        side_effect=fetch_list,
    ), patch(
        "outer_apis.scrapers.tennis.tennisexplorer_scraper._fetch_player_page",
        side_effect=lambda slug, year: f"{slug}-{year}",
    ) as fetch_player:
        results = list(iter_all_player_pages(["?country=chile"], 2026))

    assert [(item["slug"], item["html"]) for item in results] == [
        ("garin", "garin-2026"),
        ("tabilo", "tabilo-2026"),
    ]
    assert fetch_player.call_count == 2


def test_iter_all_player_pages_skips_failed_pages():
    country_html = '<a href="/player/garin/">Garin, Cristian</a>'
    with patch(
        "outer_apis.scrapers.tennis.tennisexplorer_scraper._fetch_list_page",
        return_value=country_html,
    ), patch(
        "outer_apis.scrapers.tennis.tennisexplorer_scraper._fetch_player_page",
        return_value=None,
    ):
        assert list(iter_all_player_pages(["?country=chile"], 2026)) == []
