# Strategy 1 — Resolución por resultado real (Sofascore/Flashscore) vs. heurística de precio

Vesper, 2026-07-07. Diseño únicamente — no implementado. Contexto: hoy
`_resolve_strategy1_directional` (`src/core/orchestration/paper_trading_cycle.py`
~L235-310) resuelve posiciones `strategy1_awaiting_hedge` sin fill del hedge
por un proxy de precio (>=0.95 gana, <=0.05 pierde, si no `_void_strategy1_position`
reembolsa — commit `91deb94`). El usuario propone reemplazar/complementar ese
proxy con el resultado REAL del partido vía el pipeline ya construido para
tenis (`tennis_data.py`, Sackmann/fallback Flashscore) y equipos
(`team_data.py`, Sofascore→Flashscore), reusando directamente
`flashscore_scraper.find_match_result` (tenis) y
`flashscore_scraper.find_team_match_result` (equipos, ya soporta football/
basketball/baseball/ice_hockey vía `_FS_SPORT_ID`).

## 0. Precondición no negociable: falta el campo `title`

`meta` (guardado en `_run_strategy1_pass`, L550-560) hoy persiste
`external_id`, `platform`, `category`, `resolution_date`, `level_a/b`,
`stake_usd` — **no persiste el título del mercado** (`e.get("title")`), que
es la única fuente de texto con nombres de jugador/equipo. Esto es un
prerequisito de implementación: sin `title` en meta no hay input para el
matcher. Priya deberá añadir `"title": e.get("title")` al dict de meta en
`_run_strategy1_pass` antes de construir lo demás — señalado acá para que no
se descubra a mitad de la ronda de implementación.

## 1. Matching texto→entidad

### 1.1 Extracción de nombres desde el título
Los títulos de Polymarket/Kalshi para eventos deportivos siguen patrones
razonablemente regulares ("Will X win the match vs Y?", "X vs Y — moneyline",
"Will X beat Y on <date>?"). Propongo:

1. Un extractor por categoría (no genérico) con patrones regex explícitos por
   `category` (tenis, fútbol, basketball, baseball) — la misma partición que
   ya existe en `dwell_time/` por categoría. Reusar, si existen, los patrones
   de extracción de nombre que ya haya en `dwell_time/tennis/` o
   `cross_platform_matcher.py` (auditar antes de escribir uno nuevo — no
   duplicar lógica de parsing de título que ya exista y esté probada).
2. Output: `(name_a, name_b)` candidatos, o `None` si el patrón no matchea
   (fail-closed inmediato — no intentar heurísticas de segundo nivel tipo
   "tomar las primeras dos palabras capitalizadas").
3. Normalización de nombre (acentos, orden nombre/apellido, abreviaturas
   tipo "R. Nadal" vs "Rafael Nadal") delegada a la MISMA función de
   normalización que ya usan `tennis_data.py`/`team_data.py` internamente
   para buscar contra Sackmann/Sofascore — no inventar una nueva.

### 1.2 Bridge hacia Sofascore/Flashscore
Con `(name_a, name_b, resolution_date, category)`:

- **Tenis**: `flashscore_scraper.find_match_result(name_a, name_b, match_date, date_window_days=N)`.
  Ya devuelve `None` si no hay match o el partido no finalizó — el fail-closed
  ya existe en la función, no hay que reconstruirlo.
- **Fútbol/basketball/baseball/ice_hockey**: `find_team_match_result(name_a, name_b, match_date, sport, date_window_days=N)`.
  Mismo contrato de `None`.
- **Categoría no soportada** (`_FS_SPORT_ID.get(sport) is None`, o `category`
  no reconocido, p.ej. clima/política/crypto): no intentar matching de
  resultado real en absoluto — ir directo a la heurística de precio. Esto
  cubre el mismo universo de "categorías orphaned" ya documentado
  (`kairos-football-3way-arb-implemented-verified`, clima).

### 1.3 `date_window_days` — la única perilla real de confianza
`find_match_result`/`find_team_match_result` ya devuelven como mucho un
partido (o `None`), pero la ventana de búsqueda (`date_window_days`) es lo
que controla el riesgo de falso positivo cuando `resolution_date` es
aproximada (ej. el mercado resuelve "end of day" pero el partido real fue a
las 14:00 UTC del día anterior por huso horario). Propongo:

- `date_window_days=1` por defecto (la fecha de resolución de un mercado de
  "quién gana el partido de hoy" debería estar a lo sumo a ±1 día del
  partido real).
- Si el matcher interno de Flashscore (`_search_and_find`/`_search_and_find_team`)
  ya desambigua por nombre exacto normalizado dentro de esa ventana, no hace
  falta un score de confianza adicional en esta capa — la ambigüedad ya la
  resuelve (o falla-cierra con `None`) el motor de búsqueda existente. **No
  reimplementar un segundo scorer de confianza por encima** — sería lógica
  duplicada y una superficie nueva de bugs. Si en la implementación real se
  descubre que el motor de Flashscore SÍ devuelve falsos positivos con
  `date_window_days=1` (nombres muy comunes, ej. "J. Smith"), ese es un
  hallazgo para escalar a Vesper en esa ronda, no algo a resolver por
  adelantado acá.
- Regla dura: si `find_match_result`/`find_team_match_result` devuelven
  `None`, NO se reintenta con una ventana más ancha automáticamente dentro
  del mismo ciclo — eso sería relajar el fail-closed para forzar un match.
  Un reintento con ventana más ancha solo se justifica en el fallback de
  timing (§4), no como estrategia de matching en sí.

## 2. Jerarquía de resolución propuesta

**Resultado real tiene prioridad ESTRICTA sobre la heurística de precio
cuando el matching tuvo éxito** — no son complementarios en el sentido de
"promediar" o "usar el que esté más disponible primero". Justificación:

- La heurística de precio es un PROXY (>=0.95/<=0.05 en el último snapshot
  observado) — ya documentado en el código como "NOT a real oracle read".
  Puede estar equivocada en escenarios de baja liquidez, oscilación tardía,
  o snapshot stale justo antes de un vuelco de resultado (ej. remontada en
  el último minuto, walk-off en baseball). El resultado real es la verdad
  del evento, no una aproximación.
- Orden de intento por posición, en cada ciclo donde
  `_resolve_strategy1_directional` se dispara (evento desaparecido del feed
  o timeout de hedge cumplido):
  1. Intentar matching + `find_*_match_result` (resultado real).
  2. Si hay resultado real → resolver directamente como win/loss según el
     lado (YES/NO) que efectivamente ganó — pagar `n * 1.0` en win, `0` en
     loss, igual que hoy pero con `resolved_via="real_result_<source>"` en
     vez de `"price_heuristic_win/loss_unverified"`.
  3. Si NO hay resultado real (matching falló, categoría no soportada, o
     fuente aún no actualizada — ver §4) → caer a la heurística de precio
     existente, sin cambios.
  4. Si tampoco la heurística resuelve (zona ambigua 0.05-0.95, o sin
     `last_leader_price`) → `_void_strategy1_position` (reembolso), sin
     cambios respecto a hoy.

Esto es un fallback en cascada de 3 niveles (real → precio → void), no una
elección entre fuentes igualmente confiables. El resultado real, cuando se
puede obtener con confianza, siempre gana.

## 3. Umbral de confianza — resuelto por diseño, no por score numérico

Dado que `find_match_result`/`find_team_match_result` ya son binarios
(devuelven un resultado concreto o `None`, nunca "posiblemente"), el
"umbral de confianza" en esta capa se reduce a una regla de composición, no
a un score:

- Si el extractor de nombres (§1.1) no puede extraer `(name_a, name_b)` del
  título con el patrón esperado para esa categoría → fail-closed inmediato,
  ni se llama a Flashscore. Nunca usar un extractor "best effort" (ej.
  tomar las dos primeras palabras en mayúscula) — eso es exactamente el
  tipo de "match de baja confianza inventado" que hay que evitar.
- Si `resolution_date` en meta es `None` o no parseable → fail-closed
  inmediato, mismo criterio.
- Si Flashscore devuelve `None` → fail-closed hacia la heurística de precio
  (§2 paso 3), no un reintento con parámetros relajados dentro del mismo
  ciclo.
- No hay escenario en este diseño donde se acepte un match "parcial" o de
  "confianza media" — o el bridge produce un resultado real verificado, o
  no produce nada y se cae al escalón siguiente. Esto es intencional: es
  más barato perder algunos resultados reales recuperables (cae a precio o
  a void) que aceptar un match equivocado que paga o cobra el lado
  incorrecto.

## 4. Timing — partido terminado pero fuente todavía sin actualizar

Problema real: Sofascore/Flashscore pueden tardar minutos u horas en marcar
un evento como finalizado con marcador final, especialmente en deportes/
ligas menores (mismo patrón ya documentado en `find_team_match_result`,
pensado justamente para "ligas menores... cuyo marcador no se capturó en
vivo"). Si `_resolve_strategy1_directional` se dispara apenas se cumple el
timeout de hedge o el evento desaparece del feed, es probable que la fuente
real todavía no tenga el resultado.

Propuesta — un timeout adicional, SEPARADO del timeout de hedge existente
(`_S1_HEDGE_TIMEOUT_HOURS`), específico para la espera de resultado real:

- `_S1_REAL_RESULT_TIMEOUT_HOURS` (valor sugerido a validar por Sara/Priya
  con datos reales de latencia de Sofascore/Flashscore observada en
  `tennis_data.py`/`team_data.py`; como punto de partida razonable, 3-6h
  cubre el caso típico de actualización tardía sin bloquear indefinidamente).
- Mecánica: cuando una posición entra en la rama "necesita resolver
  directional" (evento desaparecido o hedge timeout cumplido), se marca con
  un timestamp `real_result_attempt_started_at` en meta (primera vez que se
  intenta el matching) y NO se resuelve todavía si el matching+Flashscore
  devuelven "sin resultado aún" — se reintenta en el próximo ciclo (mismo
  patrón polling que ya usa el resto de la máquina de estados, no un nuevo
  worker).
- Solo cuando `now - real_result_attempt_started_at > _S1_REAL_RESULT_TIMEOUT_HOURS`
  Y todavía no hay resultado real, se cae definitivamente a la heurística de
  precio (§2 paso 3) usando el `last_leader_price` que ya se venía
  actualizando en meta durante la espera (no un valor stale de hace horas —
  importante: si el feed sigue vivo durante la espera de resultado real, el
  precio heurístico debe seguir refrescándose con cada ciclo, no congelarse
  en el valor del momento en que se disparó el timeout de hedge).
- Si el evento YA desapareció del feed (no hay más snapshots de precio) Y
  se cumple el timeout de resultado real sin resultado, no hay
  `last_leader_price` fresco para caer a heurística — eso ya es exactamente
  el caso `no_price_data` que hoy cae a `_void_strategy1_position`. No hace
  falta un caso especial nuevo: la cascada §2 lo cubre naturalmente.

Este timeout adicional NUNCA debe ser indefinido — es explícitamente acotado
para que la posición eventualmente llegue a un estado terminal (real result
→ price heuristic → void), preservando la garantía ya construida en
`91deb94` de que ninguna posición queda en limbo con capital sin contabilizar.

## 5. Reconciliación con el reembolso existente (commit `91deb94`)

Sin cambios de contrato: `_void_strategy1_position` sigue siendo el fallback
final e intocable. La cascada completa queda:

```
resultado_real (Sofascore/Flashscore, matching exitoso)
    -> resolver win/loss real, resolved_via="real_result_<sofascore|flashscore>"
[fail-closed en cualquier paso: sin title, sin nombres extraíbles,
 sin resolution_date parseable, categoría no soportada, o
 find_*_match_result() devuelve None dentro del timeout de espera]
    v
heurística de precio (>=0.95/<=0.05, con last_leader_price fresco)
    -> resolver win/loss por proxy, resolved_via="price_heuristic_win/loss_unverified"
[fail-closed: zona ambigua 0.05-0.95, o sin last_leader_price]
    v
_void_strategy1_position (reembolso, pnl=0)
    -> resolved_via="no_price_data" | "price_heuristic_ambiguous" | "real_result_timeout_no_price"
```

El único cambio de comportamiento observable para el void: se añade un
tercer valor posible de `resolved_via` para diferenciar "se agotó también
el timeout de resultado real sin fallback de precio disponible" del caso ya
existente `no_price_data`, puramente para trazabilidad en auditoría — no
cambia el mecanismo de reembolso en sí.

## 6. Fuera de alcance de esta ronda (marcado explícito para Priya)

- No decidir en esta ronda el valor exacto de `_S1_REAL_RESULT_TIMEOUT_HOURS`
  ni de `date_window_days` — son parámetros a calibrar con datos reales de
  latencia observada, mismo criterio que otros umbrales del sistema
  (`docs/strategy1-cycle-wiring-parameters-2026-07-06.md`).
- No implementar el extractor de nombres por categoría — eso es la primera
  pieza de código real de la ronda de implementación, y debe auditarse
  contra patrones de título REALES observados en producción (Polymarket/
  Kalshi), no inventados.
- Añadir el campo `title` a meta en `_run_strategy1_pass` (§0) es un
  prerequisito de una línea, pero se deja fuera de este documento porque
  toca código de producción — Priya lo hace al empezar la implementación.
