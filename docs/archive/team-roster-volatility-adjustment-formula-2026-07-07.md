# Ajuste de σ por composición de plantel (equipos: fútbol/basketball/baseball/hockey)

Autora: Vesper (auditora cuantitativa). Encargo: Sara. 2026-07-07.

Alcance: SOLO diseño matemático, sin código. Archivos leídos:
`docs/strategy1-favorite-underdog-volatility-formula-2026-07-07.md`,
`docs/strategy1-unified-volatility-formula-2026-07-07.md`,
`docs/multisport-team-roster-stats-proposal-2026-07-07.md` (propuesta de Priya, NO implementada),
`src/database/models/domain.py` (`TeamRating`, línea 512), `src/core/math/volatility.py`
(`get_volatility`, `adjust_p_both_for_volatility`), `src/core/strategies/volatility_entry.py`
(`propose_unified_initial_levels`, docstring de invariante de no-pérdida-garantizada).

**Nota de estado de dependencias**: `TeamMatchHistory`/`TeamRoster` y
`populate_team_history`/`populate_team_roster` son trabajo de Priya, EN PARALELO, posiblemente
no commiteado todavía. Este diseño trata sus campos como **parámetros simbólicos** — no asumo
que existan hoy, y la sección 5 (fail-closed) es explícita sobre qué pasa si no existen.

---

## 0. Encuadre: dónde entra esto en la cadena ya construida

La cadena ya diseñada para tenis es:

```
σ = σ_base(categoría) · g(bucket)          [doc favorito-claro, §1.2]
σ_fase = σ · h(fase)                        [doc unificado, §2.2]
propose_unified_initial_levels(sigma=σ_fase, phase=..., ...)   [ya construido, fail-closed en σ=None]
```

Este documento agrega un **tercer multiplicador**, `f(Δ_plantel)`, que se aplica ANTES de que
`σ` llegue a `propose_unified_initial_levels` — nunca dentro de esa función, nunca tocando la
garantía algebraica ya probada. Para deportes de equipo la cadena queda:

```
σ_base_equipo = análogo de σ_base(categoría) para el deporte/equipo — a calibrar con
                TeamMatchHistory (dispersión histórica de goles/puntos, análoga a std_games_total)

σ_ajustado = σ_base_equipo · f(Δ_plantel)     ← ESTE DOCUMENTO

σ_fase = σ_ajustado · h(fase)                  [multiplicador de fase, ya diseñado, sin cambios]
```

`f(Δ_plantel)` es un multiplicador MÁS en la misma cadena que `g(bucket)` y `h(fase)` — no
reemplaza a ninguno, no requiere ningún cambio en `propose_unified_initial_levels` (que sigue
recibiendo un único `sigma: float | None` ya compuesto). Esto es deliberado: la función que
tiene la garantía probada no necesita saber de dónde salió `sigma`, solo que sea `None` cuando
no hay dato confiable — igual disciplina que ya rige `g`/`h`.

---

## 1. Definición de "jugador clave" — criticidad

### 1.1 Qué NO uso como criticidad (y por qué)

No uso "es titular sí/no" como única señal (binario, pierde información: un titular que juega
90' cada partido no es igual de crítico que un titular que rota a menudo), ni el nombre del
jugador ni ninguna medida cualitativa/reputacional (no rastreable, invita a hardcodear "Messi es
crítico" a mano — exactamente el tipo de parámetro fantasma que se pidió auditar en otros
contextos de este proyecto).

### 1.2 Inputs que SÍ necesito que existan (fail-closed si no)

De `TeamRoster`/`TeamMatchHistory` (esquema propuesto por Priya, aún no construido), necesito
por jugador, sobre una ventana reciente (ej. últimos N partidos del equipo, N a calibrar por
deporte — ver §3):

- `minutos_jugados` (fútbol/hockey) o `apariciones_como_titular` (basketball/baseball) por
  partido reciente — **directamente de `TeamMatchHistory` si esa tabla registra participación
  individual, o de una tabla de participación por partido que hoy NO está en la propuesta de
  Priya** (su esquema actual es plantel snapshot + historial de RESULTADOS de equipo, no
  minutos individuales por partido — esto es un gap que señalo explícitamente, no lo asumo
  resuelto).
- `posición` (de `TeamRoster`).
- `fecha_snapshot` más reciente anterior al partido en cuestión, para saber quién estaba
  disponible ANTES de la ausencia que se quiere modelar (comparar snapshot pre-lesión vs.
  snapshot del día del partido).

**Fail-closed explícito**: si no existe una fuente de minutos/apariciones por jugador y por
partido — es decir, si lo único disponible es el roster estático sin participación histórica —
**no hay manera honesta de calcular criticidad**, y este ajuste no debe aplicarse (ver §5). No
propongo inventar una criticidad default (ej. "todo titular pesa 1.0") porque eso sería
precisamente fabricar una señal que no está midiendo lo que dice medir — el mismo error que
Strategy 1 evitó al no usar `var(p̂)` como proxy de volatilidad in-match.

### 1.3 Fórmula de criticidad

Para un jugador `i` del equipo, sobre la ventana de los últimos `N` partidos disputados por el
equipo (N a definir por deporte, §3):

```
participación_i = (partidos donde i jugó ≥ umbral_min_participación) / N
```

donde `umbral_min_participación` es un piso de minutos/apariciones para contar como "jugó" (evita
que un jugador que entró 2 minutos en garbage time cuente igual que un titular de 90'; el umbral
concreto es un parámetro de calibración por deporte, no lo fijo aquí — símbolo `θ_part(deporte)`).

```
peso_posicional_i = peso fijo por rol posicional-crítico (ver §3, difiere por deporte)

criticidad_i = participación_i · peso_posicional_i,   criticidad_i ∈ [0, peso_posicional_i]
```

Justificación: `participación_i` mide "qué tan irremplazable es este jugador en la rotación
REAL reciente del equipo" (dato empírico, no opinión) — un jugador con 95% de participación es
un titular indiscutido; uno con 40% es rotación, su ausencia mueve mucho menos el "equipo
esperado" porque el equipo YA absorbe su ausencia regularmente. `peso_posicional_i` corrige el
sesgo de que no todas las posiciones son intercambiables en su efecto sobre el resultado (§3).

### 1.4 Agregación a nivel de "bajas del partido" — Δ_plantel

Dado el conjunto `A` de jugadores ausentes confirmados para el partido (de una fuente de
lineup/injury — fuera de alcance de este documento de dónde sale esa confirmación, es un dato de
entrada, símbolo):

```
Δ_plantel = Σ_{i ∈ A} criticidad_i
```

Suma simple (no producto, no máximo) porque el efecto de múltiples bajas sobre la fuerza
esperada del equipo es aproximadamente aditivo a primer orden — perder 2 titulares de alta
participación es peor que perder 1, de forma razonablemente lineal en criticidad agregada, antes
de aplicar el techo de saturación (§2). No modelo interacciones de segundo orden entre bajas
(ej. "el suplente del arquero también es malo") — sería sobreingeniería sin datos que la
respalden; señalo la simplificación explícitamente, no la escondo.

---

## 2. Fórmula del ajuste — `f(Δ_plantel)`

### 2.1 Forma funcional propuesta

```
σ_ajustado = σ_base_equipo · f(Δ_plantel)

f(Δ_plantel) = 1 + (f_max − 1) · (1 − e^(−k · Δ_plantel))
```

Es una saturación exponencial (no lineal sin techo), con:

- `f(0) = 1` — sin bajas de jugadores clave, ningún ajuste (`σ_ajustado = σ_base_equipo`,
  consistente con que `σ_base_equipo` ya está calibrado sobre partidos con plantel "típico"
  disponible).
- `f` creciente y estrictamente monótona en `Δ_plantel` — más criticidad ausente, más
  volatilidad, sin excepción.
- `f_max` (símbolo, a calibrar — NO lo fijo yo) — techo de saturación: **un equipo no puede
  tener volatilidad infinita solo por bajas**, porque incluso el peor escenario de bajas sigue
  siendo un partido del mismo deporte contra un rival dado, acotado en última instancia por la
  volatilidad máxima observada empíricamente en la categoría (ej. el partido más "loco"
  históricamente registrado en `TeamMatchHistory` para esa liga/deporte). Sin este techo, una
  extrapolación fuera de muestra (`Δ_plantel` muy alto, ej. un equipo con medio plantel de baja
  por lesiones masivas — evento real pero raro) podría producir un `σ_ajustado` absurdo que
  `propose_unified_initial_levels` tomaría como válido sin poder distinguir "extrapolación
  alocada" de "señal real".
- `k` (símbolo, a calibrar) — controla qué tan rápido se acerca `f` a su techo; con
  `k` chico, hace falta mucha criticidad agregada ausente para acercarse a `f_max`; con `k`
  grande, una sola baja de alta criticidad ya empuja `f` cerca del techo.

### 2.2 Por qué exponencial-saturado y no lineal sin techo

Un modelo lineal (`f = 1 + c·Δ_plantel`) no tiene justificación de por qué el efecto marginal de
la enésima baja crítica sería igual al de la primera — en la práctica, el efecto marginal de
bajas adicionales sobre la volatilidad ESPERADA del resultado es decreciente una vez que el
equipo ya está "muy debilitado": pasar de plantel completo a faltar el arquero titular es un
salto grande de incertidumbre; pasar de faltar arquero+capitán a faltar arquero+capitán+un
suplente más agrega incertidumbre pero cada vez menos marginal, porque el mercado y el modelo ya
incorporaron que este partido es atípico. La saturación también actúa como salvaguarda
estadística: sin `TeamMatchHistory` suficiente en el régimen de "múltiples bajas simultáneas"
(evento raro por definición — la mayoría de los partidos históricos de calibración tienen
plantel completo o casi), extrapolar linealmente sin techo es exactamente el tipo de "sobreajuste
fuera de muestra" que se pidió auditar en otros contextos de Kairos.

`k` y `f_max` **no los fijo con valores numéricos concretos** — mismo criterio que `σ_base`,
`g_min/g_max`, `h(fase)` en los docs anteriores: son parámetros de calibración empírica que
requieren `TeamMatchHistory` real, poblada, con suficiente muestra de partidos con bajas
confirmadas vs. resultado observado. Sin esa muestra, cualquier valor que yo proponga sería un
número inventado disfrazado de calibración.

---

## 3. Generalización a los 4 deportes — qué difiere y qué no

**Propuesta: una sola forma funcional `f`, un solo mecanismo de `criticidad_i`, con el
`peso_posicional` y `θ_part` (umbral mínimo de participación) parametrizados POR DEPORTE.** No
propongo 4 funciones `f` distintas — no hay justificación estadística para que la FORMA de
saturación difiera por deporte (el argumento de §2.2 — efecto marginal decreciente, techo de
extrapolación — aplica igual a los 4). Lo que sí difiere estructuralmente es CUÁNTO pesa una
posición dada, y por qué:

| Deporte | Rol desproporcionadamente crítico | Por qué (`peso_posicional` alto) | Nota sobre tamaño de plantel en cancha |
|---|---|---|---|
| Fútbol | Arquero titular | Único jugador cuyo reemplazo cambia la varianza de goles concedidos de forma discreta y grande (arquero suplente con historial muy distinto de paradas/errores) — no es "un jugador de campo menos", es un cambio de régimen en la defensa. | 11 en cancha → cada jugador de campo pesa ~1/10-1/11 del "equipo esperado"; el arquero es la excepción a esa regla pareja. |
| Hockey (ice_hockey) | Golero titular (arquero) | Mismo argumento que fútbol, incluso más marcado — en hockey el golero es estadísticamente el factor individual más predictivo de resultado por partido en la literatura de hockey analytics. | Roster rotativo (líneas), pero el golero no rota dentro del partido salvo pulled goalie — análogo al arquero de fútbol. |
| Basketball | Ninguna posición única domina tanto como arquero/golero, PERO cada ausencia pesa proporcionalmente más | Con 5 titulares simultáneos en cancha (vs. 11 en fútbol), la fracción del "equipo esperado" que representa cada titular es mecánicamente mayor (~1/5 vs ~1/11) — el efecto no viene de una posición mágica sino de la aritmética del tamaño de plantel activo. Un base titular de alta participación (`criticidad` alta por posesión de balón/asistencias) puede acercarse al peso de un arquero en términos relativos, pero por participación fraccional, no por rol único. | 5 en cancha simultáneo — `peso_posicional` debería ser más uniforme entre posiciones que en fútbol/hockey, salvo ajuste fino por rol de manejo de balón (base/point guard) si hay dato para justificarlo. |
| Baseball | Pitcher abridor DEL DÍA (no el plantel completo de pitchers) | Es casi determinante para ESE partido específico — pero esto es distinto conceptualmente a "falta un jugador clave del plantel": es "¿quién es el abridor confirmado hoy?", una variable de lineup diario, no de plantel de temporada. Requiere un input adicional que `TeamRoster`/`TeamMatchHistory` (snapshot de plantel) NO captura por diseño — hace falta el lineup confirmado del día (probable starting pitcher), una fuente de datos distinta y de menor horizonte temporal que el roster snapshot. | No hay "11 o 5 en cancha simultáneo" reduciendo el peso relativo — el pitcher abridor domina la varianza de UN partido de forma casi total mientras está en el montículo, análogo conceptualmente al arquero/golero pero con el matiz de que es una decisión diaria, no una ausencia por lesión/rotación de temporada. |

**Conclusión de generalización**: un parámetro de "peso por posición crítica" (`peso_posicional`,
por deporte y posición) SÍ alcanza para unificar la fórmula — no hace falta una `f` distinta por
deporte. La única salvedad real es baseball: el "jugador clave ausente" relevante para
volatilidad del partido de HOY no es información de `TeamRoster` (plantel de temporada) sino de
lineup diario confirmado (abridor probable) — **si Priya no construye una fuente de lineup
diario separada del roster snapshot, el ajuste de baseball específicamente debe ir fail-closed
por defecto** (§5), no aproximarse con el roster de temporada, porque "el abridor de hoy" cambia
partido a partido y el roster snapshot no lo captura con la granularidad necesaria.

`N` (ventana de partidos recientes para `participación_i`, §1.3) y `θ_part` también deberían
diferir por deporte por razón práctica, no matemática: una temporada de basketball/baseball tiene
muchos más partidos por semana que fútbol/hockey, así que una ventana de "últimos N partidos" que
sea comparable en tiempo de calendario (ej. últimas 4-6 semanas) implica un `N` numéricamente
distinto por deporte — símbolo, no un valor que fije aquí.

---

## 4. Reconciliación con la garantía de "nunca pérdida garantizada"

Confirmado, sin reabrir la derivación algebraica ya probada (doc unificado, §3):

- `f(Δ_plantel)` es un multiplicador MÁS que compone `σ_ajustado = σ_base_equipo · f(Δ_plantel)`,
  que luego entra a `σ_fase = σ_ajustado · h(fase)`, que luego entra como el único argumento
  `sigma` de `propose_unified_initial_levels(sigma=σ_fase, ...)`. Esa función no sabe ni le
  importa si `sigma` viene de tenis (`g(bucket)`) o de un deporte de equipo con ajuste de
  plantel (`f(Δ_plantel)`) — recibe un `float | None` y aplica exactamente la misma lógica ya
  auditada.
- La garantía probada (`docs/strategy1-unified-volatility-formula-2026-07-07.md` §3.2, y
  reconfirmada en el docstring de `propose_unified_initial_levels`) es que `L_líder = p0_líder −
  δ(m, σ_fase)` con `δ > 0` **para cualquier valor de `σ_fase` que la función reciba** — la
  demostración no asume ningún rango o calibración particular de σ, solo que sea un número
  positivo válido. Un `σ_fase` mal calibrado por un `f(Δ_plantel)` impreciso (ej. porque
  `k`/`f_max` todavía no tienen calibración empírica real) puede producir:
  - Un nivel de entrada peor ubicado (demasiado conservador o agresivo respecto al verdadero
    riesgo del partido) → **ineficiencia operacional** (peor fill-rate o peor margen de ROI del
    caso "ambas patas llenan").
  - **NUNCA** puede convertir el escenario "solo llena el líder" en una pérdida garantizada,
    porque esa propiedad depende solo de que `δ > 0`, y `δ > 0` se sostiene para cualquier
    `σ_fase > 0` por la forma de la fórmula de reflexión de barrera (`sigmoid` es estrictamente
    monótona), no por el valor específico de σ.
- Por lo tanto: este modelo es estrictamente un input adicional a la ESTIMACIÓN de σ, no una
  modificación de la fórmula de nivel de entrada ni de la condición de admisión ya probada. Si
  `Δ_plantel` no es calculable (§5), el comportamiento correcto es "no aplicar este ajuste
  particular, usar `σ_base_equipo` sin modificar" — que sigue siendo un `σ` válido > 0, así que
  la garantía se sostiene igual, simplemente con una estimación menos informada (peor eficiencia,
  nunca peor seguridad).

---

## 5. Fail-closed — criterio explícito

Dos escenarios posibles, y cuál corresponde a cada uno (siguiendo el mismo criterio ya fijado en
`propose_unified_initial_levels`: fail-closed devuelve `None`/no-candidato, nunca un default
inventado):

1. **No hay datos de participación por jugador y partido** (falta la fuente de minutos/apariciones
   que §1.2 señala como gap respecto a la propuesta actual de Priya) → **no aplicar el ajuste,
   usar `σ_base_equipo` sin modificar**. Esto es correcto porque `σ_base_equipo` en sí mismo sigue
   siendo un σ válido (calibrado sobre el histórico general del equipo, plantel típico
   incluido) — degradar a "sin ajuste de plantel" es estrictamente más conservador que inventar
   una criticidad, y no rompe la garantía de §4.
2. **Hay datos de participación, pero no hay confirmación de QUIÉN está ausente para ESTE
   partido específico** (falta el input de lineup/injury confirmado — un dato de evento, no de
   plantel histórico) → **el evento no califica como candidato bajo este ajuste específico**
   (`Δ_plantel` indefinido, no `Δ_plantel = 0` por default) — mismo tratamiento que caso 1: usar
   `σ_base_equipo` sin ajuste, no rechazar el evento entero de Strategy 1 (el ajuste de plantel es
   un refinamiento opcional de σ, no una condición de admisión obligatoria).
3. **Caso especial baseball** (§3): si no existe una fuente de lineup diario confirmado
   (abridor probable) separada del roster snapshot de temporada, el ajuste de plantel para
   baseball específicamente **no debe aproximarse usando el roster snapshot** — cae directamente
   en el caso 1 (sin ajuste), nunca usando "titular de rotación de pitchers" como proxy del
   abridor de hoy, porque esa aproximación mediría algo distinto de lo que dice medir (mismo
   principio ya aplicado en el doc de tenis: no confundir una señal disponible con la señal que
   realmente se necesita).

En todos los casos: **jamás un default de criticidad "razonable" inventado** (ej. "asumir
criticidad 0.5 si no hay dato") — eso sería fabricar una señal indistinguible de ruido y
presentarla como ajuste informado, exactamente el tipo de error silencioso que este rol existe
para prevenir.
