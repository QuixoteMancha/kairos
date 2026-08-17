from datetime import datetime, timezone

import pytest

from database.db import init_db_historical
from database.historical.workerstate import HistoricalWorkerState
from database.historical.tennis.models import MatchHistory, Player
from inner_api.server import app


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as value:
        yield value


@pytest.fixture(autouse=True)
def clean_state():
    init_db_historical()
    HistoricalWorkerState.delete().execute()
    yield
    HistoricalWorkerState.delete().execute()


def test_worker_health_exposes_heartbeat_and_result(client):
    now = datetime.now(timezone.utc)
    HistoricalWorkerState.create(
        worker_name='worker-a', cursor_id=42,
        cycle_started_at=now, cycle_finished_at=now,
        last_result_json='{"processed": 3}', updated_at=now,
    )
    response = client.get('/api/workers')
    assert response.status_code == 200
    row = response.get_json()[0]
    assert row['worker_name'] == 'worker-a'
    assert row['cursor_id'] == 42
    assert row['last_result'] == {'processed': 3}
    assert row['last_error'] is None


def test_tennis_data_quality_is_exposed_for_desktop(client):
    response = client.get('/api/tennis/data-quality')
    assert response.status_code == 200
    payload = response.get_json()
    assert 'matches_total' in payload
    assert 'coverage' in payload
    assert 'tournament' in payload['coverage']
    assert 'latest_ml_experiment' in payload
