from datetime import datetime, timezone

import pytest

from database.db import init_db_historical
from database.historical.system_resources import (
    SystemResourceIncident, SystemResourceSample,
)
from inner_api.server import app


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as value:
        yield value


@pytest.fixture(autouse=True)
def clean_state():
    init_db_historical()
    SystemResourceSample.delete().execute()
    SystemResourceIncident.delete().execute()
    yield
    SystemResourceSample.delete().execute()
    SystemResourceIncident.delete().execute()


def test_system_resources_returns_latest_samples_first(client):
    now = datetime.now(timezone.utc)
    SystemResourceSample.create(
        sampled_at=now, host_available_memory_pct=40.0,
        top_containers_json='[{"container_id": "a", "memory_current": 100}]',
    )
    response = client.get('/api/system/resources')
    assert response.status_code == 200
    payload = response.get_json()
    assert len(payload) == 1
    assert payload[0]['host_available_memory_pct'] == 40.0
    assert payload[0]['top_containers'][0]['container_id'] == 'a'


def test_system_incidents_returns_most_recent_first(client):
    t1 = datetime(2026, 10, 17, 10, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 10, 17, 11, 0, tzinfo=timezone.utc)
    SystemResourceIncident.create(
        occurred_at=t1, trigger_reason='low_host_available_memory:5.00%',
        top_containers_json='[]',
    )
    SystemResourceIncident.create(
        occurred_at=t2, trigger_reason='psi_memory_some_avg60:25.00%',
        top_containers_json='[]',
    )
    response = client.get('/api/system/incidents')
    assert response.status_code == 200
    payload = response.get_json()
    assert len(payload) == 2
    assert payload[0]['trigger_reason'] == 'psi_memory_some_avg60:25.00%'


def test_system_incidents_respects_limit(client):
    for i in range(3):
        SystemResourceIncident.create(
            occurred_at=datetime(2026, 10, 17, i, 0, tzinfo=timezone.utc),
            trigger_reason=f'reason-{i}', top_containers_json='[]',
        )
    response = client.get('/api/system/incidents?limit=1')
    assert response.status_code == 200
    assert len(response.get_json()) == 1
