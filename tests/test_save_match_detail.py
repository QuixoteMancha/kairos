"""Tests de `save_match_detail` -- 2026-08-08, pedido explícito del
usuario: "si el match ya existe en las tablas, no hay que agregarlo
nuevamente, sólo actualizar si se verifica que es el mismo match y los
datos han cambiado" (idempotente por `match_id`, 1 fila por partido
real, NO 2 como `TennisMatchHistory`)."""

from __future__ import annotations

import pytest

from database.db import init_db_historical, db_historical
from database.historical.tennis import MatchHistory, Player, save_match_detail


@pytest.fixture(autouse=True)
def clean_db():
    init_db_historical()
    with db_historical.connection_context():
        MatchHistory.delete().execute()
        Player.delete().execute()
    yield
    with db_historical.connection_context():
        MatchHistory.delete().execute()
        Player.delete().execute()


def _detail(**overrides) -> dict:
    base = {
        "match_id": "2692781",
        "match_date": "2024-08-10",
        "tournament": "Toronto WTA",
        "tour": "WTA",
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
    base.update(overrides)
    return base


def test_creates_new_row_on_first_save():
    status = save_match_detail(_detail())
    assert status == "created"
    row = MatchHistory.get(MatchHistory.match_id == "2692781")
    assert row.player_a.canonical_name == "Anisimova Amanda"
    assert row.player_b.canonical_name == "Sabalenka Aryna"
    assert row.player_a_ranking == 10
    assert row.player_a.birthdate.isoformat() == "2001-08-31"
    assert row.tour == "WTA"


def test_retirement_persists_retired_flag_and_loser_as_player_retired():
    # 2026-08-11, pedido explícito del usuario ("hay que agregar los
    # retiros de los players"): "a" (Anisimova) gana, "b" (Sabalenka)
    # se retiró -- el FK `player_retired` debe apuntar al PERDEDOR.
    status = save_match_detail(_detail(retired=True, retired_side="b"))
    assert status == "created"
    row = MatchHistory.get(MatchHistory.match_id == "2692781")
    assert row.retired is True
    assert row.player_retired.canonical_name == "Sabalenka Aryna"
    assert row.winner.canonical_name == "Anisimova Amanda"


def test_no_retirement_marker_leaves_player_retired_null():
    status = save_match_detail(_detail())
    assert status == "created"
    row = MatchHistory.get(MatchHistory.match_id == "2692781")
    assert row.retired is False
    assert row.player_retired is None


def test_tour_persisted_from_get_match_detail_href_parsing():
    # 2026-08-09, pedido explícito del usuario: "revisar si tennisexplorer
    # muestra el torneo, tour y sexo de los players" -- confirmado, el
    # link del torneo en match-detail YA trae la categoría real, se
    # persiste sin request adicional.
    save_match_detail(_detail(match_id="999888", tour="ATP", player_a_name="Djokovic Novak"))
    row = MatchHistory.get(MatchHistory.match_id == "999888")
    assert row.tour == "ATP"
    assert row.updated_at is None


def test_does_not_duplicate_on_second_save_with_identical_data():
    save_match_detail(_detail())
    status = save_match_detail(_detail())
    assert status == "unchanged"
    assert MatchHistory.select().where(MatchHistory.match_id == "2692781").count() == 1


def test_updates_only_when_a_real_field_changed():
    save_match_detail(_detail())
    status = save_match_detail(_detail(player_a_ranking=8))  # ranking "corregido" por la fuente
    assert status == "updated"
    row = MatchHistory.get(MatchHistory.match_id == "2692781")
    assert row.player_a_ranking == 8
    assert row.updated_at is not None
    assert MatchHistory.select().count() == 1  # sigue siendo 1 sola fila, no se duplicó


def test_invalid_without_match_id():
    assert save_match_detail(_detail(match_id=None)) == "invalid"
    assert MatchHistory.select().count() == 0


def test_invalid_without_player_names():
    assert save_match_detail(_detail(player_a_name=None)) == "invalid"
    assert save_match_detail(_detail(player_b_name=None)) == "invalid"
    assert MatchHistory.select().count() == 0


def test_upgrades_synthetic_recentform_row_instead_of_duplicating():
    # 2026-08-09, pedido explícito del usuario tras auditar un riesgo
    # real: si ya existe una fila "recentform:..." (creada por el
    # prefetch en vivo) para el MISMO partido real (mismo par de
    # jugadores + fecha), save_match_detail debe COMPLETARLA con la
    # identidad real (match_id real + bio), no crear una fila duplicada.
    from datetime import datetime, timezone
    p_a = Player.create(canonical_name="Anisimova Amanda", created_at=datetime.now(timezone.utc))
    p_b = Player.create(canonical_name="Sabalenka Aryna", created_at=datetime.now(timezone.utc))
    MatchHistory.create(
        match_id="recentform:anisimova amanda|sabalenka aryna|2024-08-10",
        player_a=p_a, player_b=p_b,
        match_date="2024-08-10", winner=p_a, source="tennisexplorer",
        fetched_at=datetime.now(timezone.utc),
    )
    status = save_match_detail(_detail())
    assert status == "updated"
    assert MatchHistory.select().count() == 1  # no se duplicó
    row = MatchHistory.get()
    assert row.match_id == "2692781"  # identidad real gana sobre la sintética
    assert row.player_a_ranking == 10  # se completó la bio real


def test_upgrades_synthetic_row_with_sides_swapped():
    # la fila existente tiene a Sabalenka como player_a (la creó SU
    # propio prefetch primero) -- el detail nuevo trae a Anisimova como
    # player_a. Deben espejarse los campos, no mezclar bio de un lado
    # con winner_side del otro.
    from datetime import datetime, timezone
    p_a = Player.create(canonical_name="Sabalenka Aryna", created_at=datetime.now(timezone.utc))
    p_b = Player.create(canonical_name="Anisimova Amanda", created_at=datetime.now(timezone.utc))
    MatchHistory.create(
        match_id="recentform:x",
        player_a=p_a, player_b=p_b,
        match_date="2024-08-10", winner=p_b, source="tennisexplorer",
        fetched_at=datetime.now(timezone.utc),
    )
    status = save_match_detail(_detail())  # detail: player_a=Anisimova, winner_side="a"
    assert status == "updated"
    assert MatchHistory.select().count() == 1
    row = MatchHistory.get()
    assert row.match_id == "2692781"
    # espejado: ahora player_a=Sabalenka (para no romper la fila existente),
    # winner sigue siendo Anisimova (la ganadora real, sea cual sea el lado)
    assert row.player_a.canonical_name == "Sabalenka Aryna"
    assert row.player_b.canonical_name == "Anisimova Amanda"
    assert row.winner.canonical_name == "Anisimova Amanda"
    assert row.player_b_ranking == 10  # ranking de Anisimova (10) ahora del lado b


def test_does_not_search_by_pair_when_match_date_missing():
    # sin match_date no hay forma segura de buscar por par+fecha --
    # debe crear directo, nunca arriesgarse a un match falso.
    status = save_match_detail({
        "match_id": "999", "player_a_name": "Player A", "player_b_name": "Player B",
    })
    assert status == "created"
    assert MatchHistory.select().count() == 1


def test_tolerates_missing_optional_bio_fields():
    minimal = {
        "match_id": "42",
        "player_a_name": "Player A",
        "player_b_name": "Player B",
    }
    status = save_match_detail(minimal)
    assert status == "created"
    row = MatchHistory.get(MatchHistory.match_id == "42")
    assert row.player_a_ranking is None
    assert row.player_a.birthdate is None


def test_populates_canonical_player_fk_alongside_name_fields():
    # 2026-08-10, Fase 1 de la migración a entidad canónica `Player`
    # (dual-write: los campos de texto libre viejos siguen igual, el FK
    # nuevo se llena en paralelo).
    status = save_match_detail(_detail())
    assert status == "created"
    row = MatchHistory.get(MatchHistory.match_id == "2692781")
    assert row.player_a_id is not None
    assert row.player_b_id is not None
    player_a = Player.get_by_id(row.player_a_id)
    assert player_a.canonical_name == "Anisimova Amanda"
    assert player_a.tour == "WTA"


def test_reuses_same_player_across_2_different_matches():
    save_match_detail(_detail(match_id="1", match_date="2024-08-10"))
    save_match_detail(_detail(match_id="2", match_date="2024-08-12"))
    assert Player.select().where(Player.canonical_name == "Anisimova Amanda").count() == 1


def test_recovers_from_match_history_create_race_condition(monkeypatch):
    """2026-08-16, pedido explícito del usuario (revisión de modelos
    previa al worker de backfill con threads): cada partido real aparece
    en la página de LOS 2 jugadores -- 2 threads distintos pueden
    resolver el MISMO `match_id` casi simultáneamente. Antes de este
    fix, el segundo `MatchHistory.create()` chocaba con el índice único
    real de `match_id` y el `except Exception` genérico devolvía
    "error", perdiendo el reporte del partido aunque el OTRO thread ya
    lo hubiera guardado bien un instante antes. Este test fuerza esa
    ventana exacta."""
    MatchHistory.create(match_id="2692781", fetched_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc))

    calls = {"n": 0}
    orig_get_or_none = MatchHistory.get_or_none

    def fake_get_or_none(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return orig_get_or_none(*a, **kw)

    monkeypatch.setattr(MatchHistory, "get_or_none", staticmethod(fake_get_or_none))

    status = save_match_detail(_detail())
    assert status != "error", "BUG: se perdió el partido por la carrera entre threads"
    assert MatchHistory.select().where(MatchHistory.match_id == "2692781").count() == 1
