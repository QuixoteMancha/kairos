"""Monitorea memoria/swap/PSI del host + top-N contenedores por memoria.

Motivación (incidente 2026-10-17): el host freezó ~30min por agotamiento
de memoria + swap thrashing y nadie pudo determinar después qué
contenedor lo causó -- no había ningún registro histórico de uso de
recursos por contenedor. Este worker llena exactamente ese hueco.

DISEÑO -- enumeración de contenedores via cgroup v2, NO vía socket de
Docker: se itera `/sys/fs/cgroup/system.slice/docker-<id>.scope/` para
leer `memory.current`/`memory.peak`/`cpu.stat`. El cgroup no expone el
nombre del contenedor, así que se registra el id corto (12 chars) del
scope; se cruza con `docker ps -a` en el host.
Se descartó montar `/var/run/docker.sock`: `:ro` no restringe la API
(sigue permitiendo crear contenedores privilegiados, equivale a root en
el host), riesgo desproporcionado para un worker que sólo necesita
rankear por memoria.

Todo acceso a filesystem/Docker está aislado en funciones pequeñas
(`read_host_meminfo`, `read_psi`, `list_container_cgroup_stats`,
`resolve_container_names`) para que los tests las mockeen por completo
-- el sandbox de desarrollo no tiene Docker ni el layout de cgroups de
producción, así que los tests nunca deben tocar filesystem real.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from database.db import init_db_historical
from database.historical.system_resources import (
    SystemResourceIncident, SystemResourceSample,
)
from database.historical.workerstate import (
    mark_cycle_failed, mark_cycle_finished, mark_cycle_started,
    wait_for_startup_schedule,
)

_WORKER = "system_resource_monitor_worker"
_INTERVAL = max(30, int(os.getenv("SYSTEM_RESOURCE_MONITOR_INTERVAL_SECONDS", "45")))
_RETENTION_HOURS = max(1, int(os.getenv("SYSTEM_RESOURCE_SAMPLE_RETENTION_HOURS", "72")))
_TOP_N = max(1, int(os.getenv("SYSTEM_RESOURCE_TOP_N", "10")))

# Umbrales -- ver docstring de `evaluate_incident` para la justificación.
_LOW_MEMORY_PCT_THRESHOLD = float(os.getenv("SYSTEM_RESOURCE_LOW_MEMORY_PCT", "10.0"))
_PSI_MEMORY_STALL_PCT_THRESHOLD = float(os.getenv("SYSTEM_RESOURCE_PSI_MEMORY_PCT", "20.0"))
_CONTAINER_MEM_PROXIMITY_PCT_THRESHOLD = float(os.getenv("SYSTEM_RESOURCE_CONTAINER_PROXIMITY_PCT", "90.0"))

_CGROUP_ROOT = Path(os.getenv("SYSTEM_RESOURCE_CGROUP_ROOT", "/sys/fs/cgroup"))
_PSI_ROOT = Path(os.getenv("SYSTEM_RESOURCE_PSI_ROOT", "/proc/pressure"))
_MEMINFO_PATH = Path(os.getenv("SYSTEM_RESOURCE_MEMINFO_PATH", "/proc/meminfo"))


# ---------------------------------------------------------------------------
# Lectura de host/filesystem -- aisladas para mockear completo en tests.
# ---------------------------------------------------------------------------

def read_host_meminfo() -> dict:
    """Lee `/proc/meminfo`; devuelve {} si no existe (entornos sin /proc)."""
    try:
        text = _MEMINFO_PATH.read_text()
    except OSError:
        return {}
    out: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.split(":")
        if len(parts) != 2:
            continue
        key = parts[0].strip()
        value = parts[1].strip().split()[0] if parts[1].strip() else None
        if value is not None and value.isdigit():
            out[key] = int(value)  # kB
    return out


def read_psi(kind: str) -> dict:
    """Lee `/proc/pressure/<kind>` (cpu/io/memory). Falta en algunos
    kernels/entornos de test -- devuelve {} sin excepcionar."""
    path = _PSI_ROOT / kind
    try:
        text = path.read_text()
    except OSError:
        return {}
    out: dict[str, dict[str, float]] = {}
    for line in text.splitlines():
        tokens = line.split()
        if not tokens:
            continue
        label = tokens[0]
        metrics: dict[str, float] = {}
        for tok in tokens[1:]:
            if "=" not in tok:
                continue
            k, v = tok.split("=", 1)
            try:
                metrics[k] = float(v)
            except ValueError:
                pass
        out[label] = metrics
    return out


def list_container_cgroup_stats() -> list[dict]:
    """Enumera `system.slice/docker-<id>.scope/` bajo cgroup v2 y lee
    memory.current/memory.peak/memory.max/cpu.stat. Devuelve lista de
    dicts crudos; no resuelve nombres (ver `resolve_container_names`)."""
    scope_dir = _CGROUP_ROOT / "system.slice"
    try:
        entries = sorted(scope_dir.iterdir())
    except OSError:
        return []

    results = []
    for entry in entries:
        name = entry.name
        if not (name.startswith("docker-") and name.endswith(".scope")):
            continue
        container_id = name[len("docker-"):-len(".scope")]

        def _read_int(filename: str) -> int | None:
            try:
                raw = (entry / filename).read_text().strip()
            except OSError:
                return None
            if raw == "max":
                return None
            try:
                return int(raw)
            except ValueError:
                return None

        results.append({
            "container_id": container_id,
            "memory_current": _read_int("memory.current"),
            "memory_peak": _read_int("memory.peak"),
            "memory_max": _read_int("memory.max"),
        })
    return results


def resolve_container_names(container_ids: list[str]) -> dict[str, str]:
    """Resuelve id corto -> nombre legible. Sin socket de Docker
    disponible (ver docstring del módulo), el mejor nombre disponible
    sin tocar la red/daemon es el id corto mismo -- suficiente para
    rankear/diagnosticar; un humano puede cruzarlo con `docker ps` a
    mano si lo necesita."""
    return {cid: cid[:12] for cid in container_ids}


# ---------------------------------------------------------------------------
# Cálculo puro -- testeable sin tocar filesystem.
# ---------------------------------------------------------------------------

def compute_host_memory_pct(meminfo: dict) -> tuple[float | None, float | None]:
    """-> (available_memory_pct, swap_used_pct)."""
    total = meminfo.get("MemTotal")
    available = meminfo.get("MemAvailable")
    available_pct = (100.0 * available / total) if total and available is not None else None

    swap_total = meminfo.get("SwapTotal")
    swap_free = meminfo.get("SwapFree")
    swap_used_pct = None
    if swap_total:
        swap_used_pct = 100.0 * (swap_total - (swap_free or 0)) / swap_total
    elif swap_total == 0:
        swap_used_pct = 0.0
    return available_pct, swap_used_pct


def extract_psi_avg60(psi: dict, label: str = "some") -> float | None:
    metrics = psi.get(label)
    if not metrics:
        return None
    return metrics.get("avg60")


def rank_top_containers(stats: list[dict], names: dict[str, str], top_n: int) -> list[dict]:
    ranked = sorted(
        (s for s in stats if s.get("memory_current") is not None),
        key=lambda s: s["memory_current"], reverse=True,
    )[:top_n]
    out = []
    for s in ranked:
        cid = s["container_id"]
        out.append({
            "container_id": cid,
            "name": names.get(cid, cid[:12]),
            "memory_current": s.get("memory_current"),
            "memory_peak": s.get("memory_peak"),
            "memory_max": s.get("memory_max"),
        })
    return out


def evaluate_incident(
    available_memory_pct: float | None,
    psi_memory_some_avg60: float | None,
    psi_memory_full_avg60: float | None,
    container_stats: list[dict],
) -> str | None:
    """Umbrales (ver `.env.example`/docstring de módulo para valores):

    - memoria host disponible < 10%: histéricamente el freeze del
      2026-10-17 arrancó con memoria agotándose antes de que swap
      empezara a thrashear -- 10% da margen de reacción antes de
      llegar a ese punto.
    - PSI memory "some"/"full" avg60 > 20%: PSI mide directamente
      tiempo de stall real (no solo "memoria baja" que puede ser normal
      con cache), 20% en ventana de 60s ya es contención visible para
      procesos reales, no ruido.
    - memory.current de un contenedor >= 90% de su memory.max: margen
      de 10% antes del OOM-kill del cgroup, para que el ranking llegue
      ANTES de que el contenedor muera (y de que arrastre al host si no
      tiene límite bien puesto).

    Devuelve la razón (string) de la primera condición que se cumple,
    o None si nada cruzó umbral."""
    if available_memory_pct is not None and available_memory_pct < _LOW_MEMORY_PCT_THRESHOLD:
        return f"low_host_available_memory:{available_memory_pct:.2f}%"
    if psi_memory_some_avg60 is not None and psi_memory_some_avg60 > _PSI_MEMORY_STALL_PCT_THRESHOLD:
        return f"psi_memory_some_avg60:{psi_memory_some_avg60:.2f}%"
    if psi_memory_full_avg60 is not None and psi_memory_full_avg60 > _PSI_MEMORY_STALL_PCT_THRESHOLD:
        return f"psi_memory_full_avg60:{psi_memory_full_avg60:.2f}%"
    for s in container_stats:
        current = s.get("memory_current")
        limit = s.get("memory_max")
        if current is not None and limit:
            proximity = 100.0 * current / limit
            if proximity >= _CONTAINER_MEM_PROXIMITY_PCT_THRESHOLD:
                return f"container_memory_proximity:{s.get('container_id')}:{proximity:.2f}%"
    return None


# ---------------------------------------------------------------------------
# Ciclo del worker.
# ---------------------------------------------------------------------------

def run_once() -> dict:
    init_db_historical()
    now = datetime.now(timezone.utc)

    meminfo = read_host_meminfo()
    available_pct, swap_used_pct = compute_host_memory_pct(meminfo)

    psi_cpu = read_psi("cpu")
    psi_memory = read_psi("memory")
    psi_io = read_psi("io")
    psi_memory_some = extract_psi_avg60(psi_memory, "some")
    psi_memory_full = extract_psi_avg60(psi_memory, "full")

    container_stats = list_container_cgroup_stats()
    names = resolve_container_names([s["container_id"] for s in container_stats])
    top_containers = rank_top_containers(container_stats, names, _TOP_N)

    SystemResourceSample.create(
        sampled_at=now,
        host_available_memory_pct=available_pct,
        host_swap_used_pct=swap_used_pct,
        psi_cpu_some_avg60=extract_psi_avg60(psi_cpu, "some"),
        psi_memory_some_avg60=psi_memory_some,
        psi_memory_full_avg60=psi_memory_full,
        psi_io_some_avg60=extract_psi_avg60(psi_io, "some"),
        top_containers_json=json.dumps(top_containers),
    )

    cutoff = now.timestamp() - _RETENTION_HOURS * 3600
    cutoff_dt = datetime.fromtimestamp(cutoff, tz=timezone.utc)
    pruned = SystemResourceSample.delete().where(
        SystemResourceSample.sampled_at < cutoff_dt
    ).execute()

    reason = evaluate_incident(available_pct, psi_memory_some, psi_memory_full, container_stats)
    incident_recorded = False
    if reason is not None:
        SystemResourceIncident.create(
            occurred_at=now,
            trigger_reason=reason,
            host_available_memory_pct=available_pct,
            psi_snapshot_json=json.dumps({
                "cpu": psi_cpu, "memory": psi_memory, "io": psi_io,
            }),
            top_containers_json=json.dumps(top_containers),
        )
        incident_recorded = True

    return {
        "available_memory_pct": available_pct,
        "swap_used_pct": swap_used_pct,
        "containers_seen": len(container_stats),
        "pruned_samples": pruned,
        "incident_recorded": incident_recorded,
        "incident_reason": reason,
    }


if __name__ == "__main__":
    init_db_historical()
    wait_for_startup_schedule(_WORKER, _INTERVAL, jitter_max_seconds=20)
    while True:
        try:
            mark_cycle_started(_WORKER)
            report = run_once()
            mark_cycle_finished(_WORKER, report)
            print(json.dumps(report), flush=True)
        except Exception as exc:
            mark_cycle_failed(_WORKER, exc)
            print(f"[{_WORKER}] {exc}", flush=True)
        time.sleep(_INTERVAL)
