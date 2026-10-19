"""Host + per-container resource usage, para diagnosticar freezes de la
mini-pc compartida (incidente 2026-10-17: host congelado ~30min por
memoria/swap, sin registro histórico de qué contenedor lo causó).

Dos tablas:

- `SystemResourceSample`: muestra periódica (cada ~30-60s), retención
  acotada (~48-72h) -- el worker (`workers/system_resource_monitor_worker.py`)
  borra filas viejas cada ciclo. Sirve para ver tendencia reciente, no
  para forense de largo plazo.
- `SystemResourceIncident`: registro PERMANENTE (nunca se purga) de cada
  vez que se cruzó un umbral de memoria/PSI/cgroup -- incluye el ranking
  top-N de contenedores por memoria en ese instante, que es exactamente
  el dato que faltó el 2026-10-17 para saber quién causó el freeze.
"""

from __future__ import annotations

from peewee import CharField, DateTimeField, FloatField, TextField

from database.base_models import HistoricalBaseModel


class SystemResourceSample(HistoricalBaseModel):
    class Meta:
        table_name = "system_resource_sample"
        indexes = ((("sampled_at",), False),)

    sampled_at = DateTimeField(index=True)
    host_available_memory_pct = FloatField(null=True)
    host_swap_used_pct = FloatField(null=True)
    psi_cpu_some_avg60 = FloatField(null=True)
    psi_memory_some_avg60 = FloatField(null=True)
    psi_memory_full_avg60 = FloatField(null=True)
    psi_io_some_avg60 = FloatField(null=True)
    top_containers_json = TextField(null=True)


class SystemResourceIncident(HistoricalBaseModel):
    """Permanente -- nunca se purga, a diferencia de `SystemResourceSample`."""

    class Meta:
        table_name = "system_resource_incident"
        indexes = ((("occurred_at",), False),)

    occurred_at = DateTimeField(index=True)
    trigger_reason = CharField()
    host_available_memory_pct = FloatField(null=True)
    psi_snapshot_json = TextField(null=True)
    top_containers_json = TextField(null=True)
