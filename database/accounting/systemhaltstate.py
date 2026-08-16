"""Peewee model + helpers para el circuit-breaker global de operaciones
-- 2026-08-11, pedido explícito del usuario: "si efectivamente se
comprueba que no hay fondos, se deben interrumpir todas las
operaciones, para evitar errores por trabajar con datos
desactualizados".

Singleton (1 fila, id=1), mismo patrón que `PaperAccount` -- vive en
`db` (DB_PATH, no `db_historical`), visible por CUALQUIER contenedor
del fleet vía la misma SQLite compartida, sin necesitar un message
broker. `halted=True` significa "el motor de scan NO debe generar
recomendaciones nuevas este ciclo" -- ver `kairos.py::_run_once`."""

from datetime import datetime, timezone

from peewee import BooleanField, CharField, DateTimeField

from database.base_models import BaseModel


class SystemHaltState(BaseModel):
    """Singleton row (id=1). `reason` identifica la causa REAL (ej.
    "brightdata_account_broken") -- permite que `clear_halt` solo
    levante el freno si la causa que lo puso YA se resolvió, sin pisar
    un halt de un motivo distinto que pueda existir a futuro."""

    halted = BooleanField(default=False)
    reason = CharField(null=True)
    halted_at = DateTimeField(null=True)
    cleared_at = DateTimeField(null=True)


def _now():
    return datetime.now(timezone.utc)


def halt_operations(reason: str, *, verbose: bool = True) -> None:
    """Activa el freno global -- fail-closed en el sentido inverso: NO
    excepciona nunca (una escritura de DB rota acá NO debe impedir que
    el resto del sistema siga corriendo con el criterio previo, pero SÍ
    se loguea fuerte porque es una condición grave)."""
    try:
        row, _ = SystemHaltState.get_or_create(id=1)
        row.halted = True
        row.reason = reason
        row.halted_at = _now()
        row.cleared_at = None
        row.save()
        if verbose:
            print(f"[system_halt] OPERACIONES DETENIDAS -- motivo: {reason}")
    except Exception as e:
        if verbose:
            print(f"[system_halt] no se pudo activar el freno (motivo real: {reason}): {e}")


def clear_halt(reason: str, *, verbose: bool = True) -> None:
    """Levanta el freno SOLO si la causa activa coincide con `reason`
    -- no pisa un halt puesto por otro motivo. No-op silencioso si no
    estaba frenado o el motivo no coincide."""
    try:
        row = SystemHaltState.get_or_none(SystemHaltState.id == 1)
        if row is None or not row.halted or row.reason != reason:
            return
        row.halted = False
        row.reason = None
        row.cleared_at = _now()
        row.save()
        if verbose:
            print(f"[system_halt] operaciones reanudadas -- motivo resuelto: {reason}")
    except Exception as e:
        if verbose:
            print(f"[system_halt] no se pudo levantar el freno ({reason}): {e}")


def is_halted() -> tuple[bool, str | None]:
    """Fail-closed hacia el lado SEGURO en caso de error de DB: si no se
    puede determinar el estado, se asume `(False, None)` (NO frenado) --
    decisión deliberada, no una contradicción del resto del proyecto:
    frenar SIN CERTEZA de que hay un problema real detendría todo el
    sistema ante cualquier hiccup transitorio de DB, un costo mayor que
    el riesgo que este mecanismo busca evitar (que además ya tiene su
    propia capa fail-closed: los scrapers mismos devuelven None ante
    datos no confiables, no fabrican nada)."""
    try:
        row = SystemHaltState.get_or_none(SystemHaltState.id == 1)
        if row is None:
            return False, None
        return bool(row.halted), row.reason
    except Exception:
        return False, None
