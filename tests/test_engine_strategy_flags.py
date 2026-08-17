from engine import _enabled


def test_strategy_flags_default_to_disabled(monkeypatch):
    monkeypatch.delenv("ENABLE_LIMIT_ARB", raising=False)
    monkeypatch.delenv("ENABLE_MARKET_ARB", raising=False)
    monkeypatch.delenv("ENABLE_BET_ON_FAVOURITE", raising=False)
    assert _enabled("ENABLE_LIMIT_ARB") is False
    assert _enabled("ENABLE_MARKET_ARB") is False
    assert _enabled("ENABLE_BET_ON_FAVOURITE") is False


def test_strategy_flags_require_explicit_truthy_value(monkeypatch):
    monkeypatch.setenv("ENABLE_LIMIT_ARB", "true")
    monkeypatch.setenv("ENABLE_MARKET_ARB", "0")
    monkeypatch.setenv("ENABLE_BET_ON_FAVOURITE", "yes")
    assert _enabled("ENABLE_LIMIT_ARB") is True
    assert _enabled("ENABLE_MARKET_ARB") is False
    assert _enabled("ENABLE_BET_ON_FAVOURITE") is True
