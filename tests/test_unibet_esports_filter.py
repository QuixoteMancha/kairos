from src.scrapers.unibet_scraper import _is_kambi_esports_event


def test_unibet_filter_detects_esports_football():
    ev = {
        'group': 'Cyber Live Arena (2x5 min)',
        'path': [
            {'englishName': 'Football', 'termKey': 'football'},
            {'englishName': '|Esports Football|', 'termKey': 'esports_football'},
        ],
    }
    assert _is_kambi_esports_event(ev) is True


def test_unibet_filter_keeps_real_football():
    ev = {
        'group': 'Premier League',
        'path': [
            {'englishName': 'Football', 'termKey': 'football'},
            {'englishName': 'England', 'termKey': 'england'},
        ],
    }
    assert _is_kambi_esports_event(ev) is False


def test_unibet_filter_detects_esports_basketball():
    """
    Bug fijado: el filtro estaba restringido a sport=="football" y dejaba
    pasar SIN filtrar eventos virtuales/eSports de otros deportes (Kambi los
    sirve para baloncesto, tenis, etc., no solo fútbol). El filtro de
    settlement (cross_book_settle.py, sin restricción) sí los detectaba
    después, dejando el bet pending en vez de nunca colocarlo.
    """
    ev = {
        'group': '|Esports Basketball|',
        'path': [
            {'englishName': 'Basketball', 'termKey': 'basketball'},
            {'englishName': '|Esports Basketball|', 'termKey': 'esports_basketball'},
        ],
    }
    assert _is_kambi_esports_event(ev) is True


def test_unibet_filter_keeps_real_basketball():
    ev = {
        'group': 'NBA',
        'path': [
            {'englishName': 'Basketball', 'termKey': 'basketball'},
            {'englishName': 'NBA', 'termKey': 'nba'},
        ],
    }
    assert _is_kambi_esports_event(ev) is False
