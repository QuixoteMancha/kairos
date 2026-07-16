# Propuesta (NO implementada) — Fallback DB→Sofascore→Flashscore para equipos + plantel (multideporte)

Autor: Priya. Estado: PROPUESTA, pendiente de decisión de alcance por Sara. No se tocó código de esta parte.

## 1. Contexto y pregunta del usuario

El usuario pidió explorar si el mismo patrón que se acaba de implementar para
tenis (DB local → Sofascore → Flashscore, para historial de jugador) se puede
extender a **equipos + plantel (roster)** en fútbol/basketball/baseball/hockey.
Importante: hoy Kairos NO opera arb sobre esos deportes de forma general
(el pivote a prediction markets dejó fútbol 3-way y Kalshi football como los
únicos casos activos — ver memoria `football-arb-orphaned-by-design` /
`kairos-football-3way-arb-implemented-verified`). Esta propuesta es
prospectiva, no un blocker de nada en curso.

## 2. ¿Sofascore/Flashscore exponen equipo+plantel?

**Sofascore — sí, de forma análoga a jugador.** Confirmado (no verificado en
vivo esta sesión — ver nota de alcance abajo, pero consistente con la
documentación de terceros y con `search_team_fs`/`_FS_SPORT_ID` ya existente
en `flashscore_scraper.py`, y con `client.py`'s namespace-fix de 2026-07-07
que confirma que Sofascore modela equipos/individuos bajo `/team/{id}/...`):
- `/team/{id}` → metadata de equipo (país, liga, venue).
- `/team/{id}/players` → plantel actual (roster), típicamente con
  posición/edad/nacionalidad por jugador.
- `/team/{id}/events/last/{page}` → historial de partidos del equipo (mismo
  patrón que `get_player_last_matches` para jugadores, paginado).
- Fuentes de terceros (ScraperFC, datafc — consultadas 2026-07-07 vía
  búsqueda web, NO son la doc oficial de Sofascore) confirman que existen
  funciones equivalentes a `squad_data`/`team_stats_data` sobre esta misma
  familia de endpoints. **Supuesto no verificado**: no hice una llamada real
  de solo-lectura contra `/team/{id}/players` esta sesión (para no gastar
  cuota de proxy en algo fuera del alcance pedido — "solo propuesta, no
  implementar"). Antes de construir esto, Vesper/Priya deberían hacer 1-2
  llamadas reales contra un team_id conocido (ej. Real Madrid) para confirmar
  shape exacto, igual que se hizo para `search_player_id`/`get_player_last_
  matches` esta sesión (el "type": "team" vs "player" bug hubiera mordido acá
  también).

**Flashscore — la infraestructura de equipo YA EXISTE parcialmente.**
`src/scrapers/flashscore_scraper.py` ya tiene, desde antes de esta sesión:
- `search_team_fs(name, sport)` (línea 543) — búsqueda de equipo por deporte,
  vía `_FS_SPORT_ID` (fútbol=1, basketball=3, ice_hockey=4, baseball=5, etc.).
- `find_team_match_result(...)` (línea 615) — resultado de UN partido
  puntual de equipo (usado hoy solo por `settlement.py` para resolver bets).
- **NO existe** un equivalente a `get_player_recent_matches_fs` (el que
  acabo de agregar para tenis) para equipos — es decir, no hay función que
  traiga el HISTORIAL completo de partidos de un equipo, solo el resultado
  de un partido puntual dado home/away/fecha.
- **NO existe ningún soporte de plantel/roster en Flashscore** en este
  módulo — Flashscore expone plantel en su sitio pero no está parseado acá;
  habría que agregar un nuevo endpoint+parser (`/team/{slug}/{pid}/squad/` o
  similar, formato no confirmado, requiere investigación con 1 request real).

## 3. Tablas de DB — ¿existe algo ya para esto?

Revisé `src/database/models/domain.py` completo (grep de `class.*Team`):
solo existe **`TeamRating`** (línea 512) — ELO + features agregados por
equipo para deportes no-tenis (ClubElo/nflverse/NHL API), consumido por
`_team_elo_lookup()` en `scanning.py`. Es un rating agregado, **no** un
plantel ni historial de partidos por equipo.

**No existe ninguna tabla** análoga a `RecentMatchHistory` (historial) ni a
`PlayerStats` (stats agregadas) para equipos, y **no existe ninguna tabla**
de plantel/roster (jugadores que pertenecen a un equipo en un momento dado).
Nada que "reactivar" acá — sería from-scratch.

## 4. Propuesta de patrón (sin construir)

Análogo a lo hecho para tenis, con 2 tablas nuevas necesarias:

- **`TeamMatchHistory`** (análoga a `RecentMatchHistory`): team FK, fecha,
  rival, resultado (W/L/D — a diferencia de tenis, fútbol tiene empates),
  goles/puntos a favor y en contra, competición, local/visitante, source
  ("sofascore"|"flashscore").
- **`TeamRoster`** (nueva, sin equivalente hoy): team FK, player_name,
  posición, dorsal, nacionalidad, fecha_snapshot (el plantel cambia por
  fichajes/lesiones — necesita versión temporal, no un simple upsert
  "current", si se quiere auditar composición histórica para settlement de
  mercados tipo "¿jugará X el partido Y?").

Función orquestadora propuesta (mismo esqueleto que
`tennis_data.populate_player_history`):

```
populate_team_history(team_orm, team_name, sport, target=20) -> dict
  1. DB local (TeamMatchHistory) >= target → return source="db", sin red.
  2. Sofascore: search_team_id(team_name, sport) + get_team_last_matches(...)
     (funciones NUEVAS, no existen — construir sobre el mismo client.py,
     respetando el fix de namespace /team/ vs /player/ ya aprendido).
  3. Si Sofascore vacío/bloqueado → Flashscore: NUEVA get_team_recent_
     matches_fs(team_name, sport, n) — extender find_team_match_result con
     la misma lógica que get_player_recent_matches_fs (recorrer TODOS los
     registros de la página de resultados del equipo, no solo 1 partido).
  4. Ninguna fuente → source="none".

populate_team_roster(team_orm, team_name, sport) -> dict
  1. Sofascore: get_team_squad(team_id) (NUEVO).
  2. Si vacío → Flashscore: NUEVO parser de página de plantel (requiere
     investigar el formato pipe-delimitado específico de esa página — no
     asumido, hay que confirmarlo con 1 request real antes de codear).
  3. Persistir versionado por fecha_snapshot (no sobrescribir plantel
     anterior — permite reconstruir "quién estaba en el plantel el día X").
```

## 5. Decisiones que le corresponden a Sara antes de construir esto

1. **Alcance de deportes**: ¿los 4 (fútbol/basketball/baseball/hockey) o
   arrancar con 1 (fútbol, que es el único con arb activo hoy vía Kalshi/
   Polymarket 3-way)?
2. **¿Para qué se usaría el roster?** Sin un consumidor concreto (¿alimentar
   qué estrategia? ¿settlement de mercados de lineup/injury?) esto es
   trabajo especulativo — el mismo principio que ya se aplicó con tenis
   (Sackmann se activó porque alimentaba `PlayerElo`/`PlayerStats` que SÍ
   tienen consumidor real en el motor de dwell-time).
3. **Versionado temporal de plantel** (fichajes/lesiones cambian el roster)
   añade complejidad de schema — confirmar si vale la pena vs. un simple
   snapshot "plantel actual" sin historia.
4. **Autorización de investigación en vivo**: antes de construir el parser
   de Flashscore para plantel, hace falta 1-2 requests reales de solo
   lectura contra la página de squad de un equipo conocido para confirmar
   el formato — mismo criterio de "no scraping agresivo" que rigió esta
   sesión.

No se escribió código para esta parte. Ningún endpoint fue golpeado en vivo
para esta investigación (todo basado en: infraestructura ya existente en el
repo + fuentes de terceros sobre Sofascore, marcadas explícitamente como
supuesto no verificado donde corresponde).
