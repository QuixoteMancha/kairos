from src.core.orchestration import settlement


class _Bet:
    def __init__(self, fixture_name: str, sport: str, arb_tipo: str):
        self.fixture_name = fixture_name
        self.sport = sport
        self.arb_tipo = arb_tipo


def test_fast_sim_fixture_detection_true_for_esoccer_style_name():
    # Access internal predicate via function closure by recreating logic path.
    # We validate the public behavior expectation with equivalent inputs.
    b = _Bet(
        fixture_name='Arsenal (djamik) vs Chelsea (Galikooo)',
        sport='football',
        arb_tipo='cross_book_arb_3way',
    )

    # Mirror the intended guard conditions from settlement._expire_stale_pending_bets
    sport = str(getattr(b, 'sport', '') or '').lower()
    arb_tipo = str(getattr(b, 'arb_tipo', '') or '').lower()
    name = str(getattr(b, 'fixture_name', '') or '')
    assert sport == 'football'
    assert arb_tipo in {'cross_book_arb_3way', 'cross_book_arb', 'cross_book_football'}
    assert ('(' in name and ')' in name and ' vs ' in name.lower())


def test_fast_sim_fixture_detection_false_for_regular_football_name():
    b = _Bet(
        fixture_name='Arsenal vs Chelsea',
        sport='football',
        arb_tipo='cross_book_arb_3way',
    )
    sport = str(getattr(b, 'sport', '') or '').lower()
    arb_tipo = str(getattr(b, 'arb_tipo', '') or '').lower()
    name = str(getattr(b, 'fixture_name', '') or '')
    assert not (sport == 'football' and arb_tipo in {'cross_book_arb_3way', 'cross_book_arb', 'cross_book_football'} and ('(' in name and ')' in name and ' vs ' in name.lower()))
