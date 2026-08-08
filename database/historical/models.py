"""Modelos BASE reutilizables entre deportes -- 2026-08-08, pedido
explícito del usuario: "dejar la base de modelos para sports y clima en
models.py... solo dejar modelos base para la data de sports y clima,
definiendo modelos específicos en un solo file dentro de las carpetas
de deportes".

2026-08-08, corrección tras revisión del usuario ("dejaste un modelo
propio de tennis en models.py"): originalmente este archivo tenía
`Match`/`ProbabilityTick`/`MatchWeather`/`MatchStats`/`MatchStatsSet`,
razonando que su ESTRUCTURA era agnóstica de deporte -- pero en la
realidad su `table_name` es literalmente tenis
(`tennis_scanned_match`, etc.) y HOY ningún otro deporte los usa.
Movidos a `database/historical/tennis/models.py` (donde de verdad
pertenecen ahora mismo).

Este archivo queda intencionalmente SIN modelos concretos por ahora --
no hay ningún concepto de match-tracking/clima que HOY sea real y
compartido entre 2+ deportes (el único candidato, `Match` y su familia,
es 100% tenis en la práctica). Cuando exista un segundo deporte que
realmente necesite la misma estructura, ahí se decide explícitamente
si conviene:
  (a) definir las clases genéricas acá y hacer que cada deporte cree
      su propia tabla concreta (subclase con `table_name` propio), o
  (b) renombrar las tablas reales de tenis a algo sport-agnostic y
      moverlas acá -- ESO SÍ requeriría una migración real de datos
      (rename de tabla + ALTER, sobre tablas con cientos de miles de
      filas reales), nunca a ciegas.

Sin código muerto a propósito: no se deja ningún import/placeholder
sin uso real -- el próximo modelo base genuino se agrega acá cuando
exista, con su propio `from database.base_models import HistoricalBaseModel`."""
