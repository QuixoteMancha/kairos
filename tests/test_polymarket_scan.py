import pytest

import src.core.orchestration.polymarket_scan as scan


@pytest.fixture(autouse=True)
def _reset_scan_cache():
    scan._scan_cache = None
    yield
    scan._scan_cache = None


class _DummyClient:
    def __init__(self, pages, slug_markets=None):
        self._pages = pages
        self._slug_markets = slug_markets or []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def get_active_markets_from_events(self, limit_events, offset_events, min_liquidity, max_hours_to_resolution, **kwargs):
        idx = offset_events // 100
        if idx >= len(self._pages):
            return []
        return self._pages[idx]

    def get_market_by_slug(self, slug):
        return None

    def get_markets_by_slugs(self, slugs):
        return list(self._slug_markets)


def _m(question, hours=2.0, spread=0.01, liquidity=2000):
    return {
        'id': question,
        'condition_id': f'cid-{abs(hash(question))}',
        'question': question,
        'event_title': 'Crypto',
        'slug': question.lower().replace(' ', '-'),
        'market_url': 'https://polymarket.com/event/x',
        'yes_price': 0.52,
        'no_price': 0.48,
        'best_bid': 0.51,
        'best_ask': 0.52,
        'spread': spread,
        'minimum_tick_size': 0.01,
        'liquidity': liquidity,
        'volume_24h': 10000,
        'day_change': 0.01,
        'hours_to_resolution': hours,
        'clob_token_ids': [],
    }


def test_scan_prefers_crypto_5m_when_available(monkeypatch):
    pages = [[
        _m('Will Bitcoin be above 120k in 5 min?', hours=0.2),
        _m('Will Ethereum be above 7k by end of week?', hours=24),
    ]]

    monkeypatch.setattr(scan, 'PolymarketClient', lambda: _DummyClient(pages))
    out = scan.scan_polymarket_crypto_5m(limit=10, min_liquidity=0, max_spread=1.0, max_hours_to_resolution=48)

    assert out['count'] == 1
    assert out['fallback_mode'] == 'none'
    assert out['markets'][0]['market_type'] == 'crypto_5m_binary'


def test_scan_with_none_max_hours_uses_config_default(monkeypatch):
    """
    Bug: max_hours_to_resolution=None (p.ej. Flask devuelve None si el query
    param no parsea como float, en vez del default) caía a `or 0.0` → el
    filtro `hours > 0.0` descartaba CUALQUIER mercado real y forzaba modo
    fallback degradado. Debe caer al default de config (0.20h), no a 0.0.
    """
    pages = [[
        _m('Will Bitcoin be above 120k in 5 min?', hours=0.2),
    ]]
    monkeypatch.setattr(scan, 'PolymarketClient', lambda: _DummyClient(pages))
    out = scan.scan_polymarket_crypto_5m(limit=10, min_liquidity=0, max_spread=1.0, max_hours_to_resolution=None)

    assert out['count'] == 1
    assert out['fallback_mode'] == 'none'


def test_scan_discards_stale_5m_markets(monkeypatch):
    pages = [[
        _m('Will Bitcoin be above 120k in 5 min?', hours=-10.0),
        _m('Will Ethereum be above 7k by end of week?', hours=24),
    ]]

    monkeypatch.setattr(scan, 'PolymarketClient', lambda: _DummyClient(pages))
    out = scan.scan_polymarket_crypto_5m(limit=10, min_liquidity=0, max_spread=1.0, max_hours_to_resolution=48)

    assert out['fallback_mode'] != 'none'
    assert all(m['market_type'] != 'crypto_5m_binary' for m in out['markets'])


def test_scan_uses_short_horizon_fallback_when_no_5m(monkeypatch):
    pages = [[
        _m('Will Bitcoin be above 150k by Dec 31?', hours=30),
        _m('Will Ethereum reach 8k this month?', hours=20),
    ]]

    monkeypatch.setattr(scan, 'PolymarketClient', lambda: _DummyClient(pages))
    out = scan.scan_polymarket_crypto_5m(limit=10, min_liquidity=0, max_spread=1.0, max_hours_to_resolution=6)

    assert out['count'] == 2
    assert out['fallback_mode'] == 'crypto_short_horizon'
    assert all(m['market_type'] == 'crypto_short_horizon_binary' for m in out['markets'])


def test_scan_uses_any_horizon_fallback_when_short_horizon_missing(monkeypatch):
    pages = [[
        _m('Will Bitcoin be above 150k by Dec 31?', hours=300),
        _m('Will Ethereum reach 8k this year?', hours=220),
    ]]

    monkeypatch.setattr(scan, 'PolymarketClient', lambda: _DummyClient(pages))
    out = scan.scan_polymarket_crypto_5m(limit=10, min_liquidity=0, max_spread=1.0, max_hours_to_resolution=6)

    assert out['count'] == 2
    assert out['fallback_mode'] == 'crypto_any_horizon'


def test_crypto_detector_avoids_beth_false_positive():
    market = {
        'question': 'Will Beth Davidson be the nominee?',
        'event_title': 'US Elections',
        'slug': 'will-beth-davidson-be-the-democratic-nominee',
    }
    assert scan._is_crypto_market(market) is False


def test_scan_uses_slug_batch_as_primary_source(monkeypatch):
    # El slug batch devuelve un 5m vigente → no se deben tocar tag/general.
    slug_markets = [_m('Bitcoin Up or Down - 5 min', hours=0.05)]
    slug_markets[0]['slug'] = 'btc-updown-5m-123'

    calls = {'events': 0}

    class _Client(_DummyClient):
        def get_active_markets_from_events(self, *a, **k):
            calls['events'] += 1
            return super().get_active_markets_from_events(*a, **k)

    monkeypatch.setattr(scan, 'PolymarketClient', lambda: _Client([], slug_markets=slug_markets))
    out = scan.scan_polymarket_crypto_5m(limit=10, min_liquidity=0, max_spread=1.0, max_hours_to_resolution=6)

    assert out['count'] == 1
    assert out['fallback_mode'] == 'none'
    assert out['markets'][0]['market_type'] == 'crypto_5m_binary'
    # Con slug batch resuelto, NO se debe pegar al endpoint de events.
    assert calls['events'] == 0


def test_scan_result_is_cached_between_calls(monkeypatch):
    slug_markets = [_m('Ethereum Up or Down - 5 min', hours=0.05)]
    slug_markets[0]['slug'] = 'eth-updown-5m-456'

    calls = {'slug': 0}

    class _Client(_DummyClient):
        def get_markets_by_slugs(self, slugs):
            calls['slug'] += 1
            return super().get_markets_by_slugs(slugs)

    monkeypatch.setattr(scan, 'PolymarketClient', lambda: _Client([], slug_markets=slug_markets))
    scan.scan_polymarket_crypto_5m(limit=10, min_liquidity=0, max_spread=1.0, max_hours_to_resolution=6)
    out2 = scan.scan_polymarket_crypto_5m(limit=10, min_liquidity=0, max_spread=1.0, max_hours_to_resolution=6)

    # Segunda llamada dentro del TTL → servida desde cache (1 sola request de slugs).
    assert calls['slug'] == 1
    assert out2.get('from_scan_cache') is True
