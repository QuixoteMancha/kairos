from src.scrapers.unibet_scraper import _is_kambi_esports_event


def test_unibet_filter_detects_esports_football():
    ev = {
        'group': 'Cyber Live Arena (2x5 min)',
        'path': [
            {'englishName': 'Football', 'termKey': 'football'},
            {'englishName': '|Esports Football|', 'termKey': 'esports_football'},
        ],
    }
    assert _is_kambi_esports_event(ev, 'football') is True


def test_unibet_filter_keeps_real_football():
    ev = {
        'group': 'Premier League',
        'path': [
            {'englishName': 'Football', 'termKey': 'football'},
            {'englishName': 'England', 'termKey': 'england'},
        ],
    }
    assert _is_kambi_esports_event(ev, 'football') is False


def test_unibet_filter_not_applied_non_football():
    ev = {
        'group': '|Esports Basketball|',
        'path': [
            {'englishName': 'Basketball', 'termKey': 'basketball'},
            {'englishName': '|Esports Basketball|', 'termKey': 'esports_basketball'},
        ],
    }
    assert _is_kambi_esports_event(ev, 'basketball') is False
