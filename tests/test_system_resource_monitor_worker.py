"""Tests de `workers/system_resource_monitor_worker.py` -- toda lectura
de host/filesystem/cgroup se mockea completo (ver docstring del worker);
ningún test toca `/proc`, `/sys/fs/cgroup` ni un socket Docker real."""

from __future__ import annotations

from unittest.mock import patch

from database.db import init_db_historical
from database.historical.system_resources import (
    SystemResourceIncident, SystemResourceSample,
)
from workers.system_resource_monitor_worker import (
    compute_host_memory_pct,
    evaluate_incident,
    extract_psi_avg60,
    rank_top_containers,
    run_once,
)


def setup_module(module):
    init_db_historical()


def teardown_function(function):
    SystemResourceSample.delete().execute()
    SystemResourceIncident.delete().execute()


def test_compute_host_memory_pct_basic():
    meminfo = {"MemTotal": 1000, "MemAvailable": 100, "SwapTotal": 500, "SwapFree": 400}
    available_pct, swap_used_pct = compute_host_memory_pct(meminfo)
    assert available_pct == 10.0
    assert swap_used_pct == 20.0


def test_compute_host_memory_pct_empty():
    assert compute_host_memory_pct({}) == (None, None)


def test_extract_psi_avg60_missing_label():
    assert extract_psi_avg60({}, "some") is None
    assert extract_psi_avg60({"some": {"avg60": 5.5}}, "some") == 5.5


def test_rank_top_containers_orders_and_truncates():
    stats = [
        {"container_id": "a", "memory_current": 100, "memory_peak": 100, "memory_max": None},
        {"container_id": "b", "memory_current": 300, "memory_peak": 300, "memory_max": None},
        {"container_id": "c", "memory_current": 200, "memory_peak": 200, "memory_max": None},
    ]
    names = {"a": "svc-a", "b": "svc-b", "c": "svc-c"}
    ranked = rank_top_containers(stats, names, top_n=2)
    assert [r["container_id"] for r in ranked] == ["b", "c"]
    assert ranked[0]["name"] == "svc-b"


def test_evaluate_incident_below_thresholds_is_none():
    assert evaluate_incident(50.0, 1.0, 0.0, []) is None


def test_evaluate_incident_low_memory_triggers():
    reason = evaluate_incident(5.0, 1.0, 0.0, [])
    assert reason is not None
    assert "low_host_available_memory" in reason


def test_evaluate_incident_psi_memory_some_triggers():
    reason = evaluate_incident(50.0, 25.0, 0.0, [])
    assert "psi_memory_some_avg60" in reason


def test_evaluate_incident_container_proximity_triggers():
    stats = [{"container_id": "x", "memory_current": 950, "memory_max": 1000}]
    reason = evaluate_incident(50.0, 1.0, 0.0, stats)
    assert "container_memory_proximity" in reason
    assert "x" in reason


def test_run_once_records_sample_no_incident():
    with patch("workers.system_resource_monitor_worker.read_host_meminfo",
               return_value={"MemTotal": 1000, "MemAvailable": 500, "SwapTotal": 0, "SwapFree": 0}), \
         patch("workers.system_resource_monitor_worker.read_psi", return_value={}), \
         patch("workers.system_resource_monitor_worker.list_container_cgroup_stats", return_value=[]), \
         patch("workers.system_resource_monitor_worker.resolve_container_names", return_value={}):
        report = run_once()

    assert report["incident_recorded"] is False
    assert SystemResourceSample.select().count() == 1
    assert SystemResourceIncident.select().count() == 0


def test_run_once_records_incident_with_top_containers():
    container_stats = [
        {"container_id": "aaa111222333", "memory_current": 100, "memory_peak": 100, "memory_max": None},
        {"container_id": "bbb111222333", "memory_current": 900, "memory_peak": 900, "memory_max": None},
    ]
    with patch("workers.system_resource_monitor_worker.read_host_meminfo",
               return_value={"MemTotal": 1000, "MemAvailable": 50, "SwapTotal": 0, "SwapFree": 0}), \
         patch("workers.system_resource_monitor_worker.read_psi", return_value={}), \
         patch("workers.system_resource_monitor_worker.list_container_cgroup_stats",
               return_value=container_stats), \
         patch("workers.system_resource_monitor_worker.resolve_container_names",
               return_value={"aaa111222333": "svc-a", "bbb111222333": "svc-b"}):
        report = run_once()

    assert report["incident_recorded"] is True
    incident = SystemResourceIncident.select().get()
    import json
    top = json.loads(incident.top_containers_json)
    assert top[0]["container_id"] == "bbb111222333"
    assert top[0]["name"] == "svc-b"


def test_run_once_prunes_old_samples():
    from datetime import datetime, timedelta, timezone
    old = datetime.now(timezone.utc) - timedelta(hours=200)
    SystemResourceSample.create(sampled_at=old, top_containers_json="[]")

    with patch("workers.system_resource_monitor_worker.read_host_meminfo", return_value={}), \
         patch("workers.system_resource_monitor_worker.read_psi", return_value={}), \
         patch("workers.system_resource_monitor_worker.list_container_cgroup_stats", return_value=[]), \
         patch("workers.system_resource_monitor_worker.resolve_container_names", return_value={}):
        report = run_once()

    assert report["pruned_samples"] >= 1
    remaining = list(SystemResourceSample.select())
    assert all(s.sampled_at != old for s in remaining)
