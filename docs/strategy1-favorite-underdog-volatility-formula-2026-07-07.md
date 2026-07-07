# Strategy 1 universal — caso "favorito claro": fórmula de volatilidad y nivel de entrada

Autora: Vesper (auditora cuantitativa). Encargo: Sara. 2026-07-07.

Alcance: SOLO diseño matemático, sin código. Archivos leídos:
`src/core/strategies/universal_limit_arb.py`, `docs/strategy1-cycle-wiring-parameters-2026-07-06.md`,
`docs/universal-limit-order-strategy-math-audit-2026-07-05.md`, `src/core/math/volatility.py`,
`src/core/math/elo.py`, `src/database/models/domain.py` (`TeamRating`).

Todos los inputs concretos (ELO real, win-rate real por superficie, win-rate real vs. grupo de
rivales, tamaños de muestra) quedan como **parámetros simbólicos** — Priya confirma en paralelo
qué de esto está vivo hoy. No inventé ningún valor numérico de ELO/ranking.

---

## 0. Encuadre: qué NO cambia respecto al mecanismo ya construido

El caso "favorito claro" reusa exactamente la misma matemática de arb ya auditada en
`docs/universal-limit-order-strategy-math-audit-2026-07-05.md` (N1=N2=N mandatorio,
`compute_l2_max`/`propose_hedge_order` como ceiling de seguridad). Lo único nuevo es **dónde**
colocar el nivel inicial del favorito — hoy `propose_initial_orders()` usa un offset fijo
(`EPSILON_INITIAL = 0.03`) simétrico para ambos lados, diseñado para el caso 50/50. Para el
caso favorito-claro el offset del favorito debe ser **hacia abajo** y derivado de volatilidad
específica de categoría, no un `epsilon` fijo. Este documento resuelve ese único nuevo grado de
libertad.

---

## 1. De (ELO, win-rate superficie, win-rate vs. grupo similar) a una volatilidad estimable σ

### 1.1 Por qué NO uso esos 3 inputs directamente como "volatilidad" — hacen dos trabajos distintos

Los 3 inputs, tal como los propone el enunciado, en realidad alimentan dos cosas matemáticamente
separadas y no deben mezclarse sin decir cuál alimenta cuál:

1. Una **probabilidad de referencia** p̂ del favorito (para chequear consistencia contra el
   precio de mercado p0 — esto NO es lo pedido acá, pero lo dejo anotado porque es un
   subproducto natural y útil).
2. Una **dispersión esperada del camino de precio** durante el evento (lo que sí se pide) — esto
   depende de cuán "reñido"/errático es el patrón de juego, no solo de quién es más probable que
   gane.

Formalmente:

```
p̂ = w_elo · p_elo + w_surf · p_surf + w_grp · p_grp
```

donde:
- `p_elo = EloCalculator.expected_score(elo_fav, elo_dog)` (ya existe, `src/core/math/elo.py:47`,
  fórmula logística estándar 1/(1+10^((Rb-Ra)/400))).
- `p_surf` = win-rate histórico del favorito en la superficie del evento.
- `p_grp` = win-rate histórico del favorito vs. rivales del mismo bucket de fuerza que el
  underdog (reusar `_ranking_bucket()`, `src/core/math/volatility.py:83`, ya construida para
  agrupar por top10/top20/top50/top100/rest).
- `w_elo + w_surf + w_grp = 1`, pesos simbólicos — calibrarlos (ej. regresión logística contra
  cierres reales histpricos) es trabajo de datos, no matemático puro; dejo esto para Priya/Nora
  con datos reales, no invento pesos.

`p̂` sirve como validación de que el precio de mercado p0 no está muy desalineado del modelo
(si `|p̂ - p0|` es grande, es señal de que el mercado puede seguir moviéndose ANTES de que arranque
el evento — un caso distinto, no cubierto aquí). Para la volatilidad IN-MATCH que se pide, uso un
enfoque distinto (§1.2), y anoto explícitamente: **no hay que usar var(p̂) ni la dispersión entre
p_elo/p_surf/p_grp como proxy de volatilidad in-match — eso mide desacuerdo entre features, no
cuánto se mueve el precio durante el partido.** Confundir ambas cosas sería el tipo de error
silencioso que se pidió auditar (un σ que "parece" razonable pero no mide lo que dice medir).

### 1.2 Modelo de la volatilidad in-match: barrera reflejada sobre un proceso logit-martingala

El precio de un mercado eficiente es, por construcción, una martingala que converge a {0,1} en
la resolución. Trabajo en escala logit (`x = ln(p/(1-p))`) porque ahí el proceso es
aproximadamente un movimiento browniano sin deriva fuerte hasta el desenlace (evita los problemas
de escala de la binomial cerca de los bordes 0/1).

Defino:

```
σ = volatilidad logit del PARTIDO COMPLETO (desde el inicio hasta la resolución),
    específica de categoría y de este matchup particular.
```

**Fórmula propuesta (heurística, justificada, NO inventando valores concretos):**

```
σ = σ_base(categoria) · g(ELO_gap, WR_superficie, WR_grupo)
```

- `σ_base(categoria)`: constante calibrada empíricamente por categoría (tenis ATP ≠ WTA ≠
  fútbol) a partir de datos históricos reales de cuánto se mueve el precio/logit de un evento
  entre el kickoff y la resolución. **Esto NO puede fijarse hoy sin datos reales** — es
  exactamente el tipo de parámetro que Sara debe pedirle a Priya que confirme si hay histórico
  suficiente para estimarlo (ver §4, fail-closed).
- `g(...)`: multiplicador adimensional que ajusta σ_base según qué tan "reñido"/errático es
  ESTE matchup particular respecto al partido promedio de la categoría. Propongo reusar el
  patrón que YA existe en `src/core/math/volatility.py` para tenis (dispersión de
  `games_total` agrupada por superficie × ranking-bucket del rival, `compute_player_volatility`,
  líneas 1-70), pero reinterpretado como proxy de volatilidad de PRECIO, no de conteo de juegos:

  ```
  g = clamp( std_games_total(superficie, bucket_rival) / VOL_STD_NEUTRAL[tour] , g_min, g_max )
  ```

  Justificación estadística: más varianza en el marcador (más quiebres de servicio, sets
  reñidos 7-6/7-5 en vez de 6-1/6-2) implica más oscilación del precio implícito durante el
  partido — un jugador que gana consistentemente 6-2/6-1 (baja std de juegos) también tiene un
  camino de precio más monótono (menos volatilidad); un jugador parejo con su rival (alta std)
  tiene un camino de precio más errático. Esta es la MISMA justificación estadística que ya usa
  `adjust_p_both_for_volatility()` (línea ~150 del mismo archivo) para penalizar `p_both`, solo
  que acá se usa para escalar σ en vez de ajustar una probabilidad de rango. `VOL_STD_NEUTRAL`
  ya existe como constante calibrada por tour (`_VOL_STD_NEUTRAL=2.5` ATP,
  `_VOL_STD_NEUTRAL_WTA=3.5`, `src/core/math/volatility.py:44-45`) — reusable tal cual como
  denominador de normalización.
  `g_min`/`g_max` (ej. algo como [0.5, 2.0]) son un clamp defensivo simbólico, no un valor que
  fije yo aquí — evita que un matchup con muestra rara (n muy chico) dispare un multiplicador
  absurdo; debe fijarse junto con `σ_base` cuando haya datos reales.

- **Nota importante — evitar doble conteo del ELO gap:** el ELO gap NO debería ser un segundo
  factor multiplicativo separado de `g`, porque `std_games_total` ya se agrupa
  condicionalmente por `bucket_rival` (que codifica indirectamente la brecha de fuerza: un
  favorito goleando a un rival de bucket "rest" ya tiene, dentro de ese grupo, su propia std
  histórica típicamente baja). Meter el ELO gap otra vez como factor multiplicativo
  independiente doble-contaría la misma señal. El ELO gap entra en `p̂` (§1.1), no en `g`.

### 1.3 Fail-closed si σ no es calculable — ver §4 (no me adelanto acá para no repetir).

---

## 2. Regla de nivel de entrada del favorito, usando σ

### 2.1 Por qué "reflexión de barrera" y no percentiles ad-hoc

Con `x0 = logit(p0_fav)` el logit del precio actual del favorito, y σ el logit-volatility del
partido completo (§1.2), modelo el camino de precio como una difusión sin deriva fuerte (la
propiedad de martingala del precio eficiente respalda esto: no hay una tendencia direccional
conocida distinta de cero antes del evento). Para un movimiento browniano sin deriva, la
probabilidad de que el MÍNIMO del camino toque un nivel `x0 - m·σ` en algún momento antes de la
resolución (no solo al final) es el resultado clásico de reflexión:

```
P(tocar nivel) ≈ 2·Φ(-m)
```

donde `Φ` es la CDF normal estándar y `m` es la distancia al nivel medida en unidades de σ.

Esto es preferible a "elegir un percentil arbitrario" porque da una **probabilidad de fill
explícita y auditable** en función de m — exactamente lo que le faltaba al parámetro `k` de
`propose_hedge_order` (interpolación lineal sin probabilidad asociada, señalado como heurística
sin anclaje estadístico en la auditoría anterior). Acá si Sara pide "¿qué probabilidad de fill
tiene este nivel?", la respuesta es un número derivado de la fórmula, no una intuición.

Traducción de vuelta a precio: nivel propuesto para el favorito

```
L_fav = sigmoid(x0 - m·σ) = p0_fav bajado en δ(m,σ) puntos de probabilidad,
   δ(m,σ) = p0_fav - sigmoid(logit(p0_fav) - m·σ)
```

(`δ` no es lineal en `m·σ` cerca de los extremos 0/1 — es la forma correcta, no una resta lineal
ingenua de puntos porcentuales, que fue precisamente el bug del buffer fijo "+8pp" señalado en
la auditoría previa).

### 2.2 Trade-off explícito de `m` (igual formato que el análisis de `k` en la ronda anterior)

```
m pequeño (ej. 0.5)  → P(tocar) = 2Φ(-0.5) ≈ 61.7%  → alta chance de fill, PERO δ chico
                        → margen de arb resultante pequeño (ver §3), poco colchón para fees/slippage.

m ≈ 1.0               → P(tocar) = 2Φ(-1.0) ≈ 31.7%  → punto medio razonable: "un movimiento de
                        1 desviación estándar" es, por definición estadística, el tipo de
                        movimiento MÁS FRECUENTE dentro de la categoría "movimiento no trivial"
                        (no ruido de microestructura, pero tampoco un evento de cola).

m grande (ej. 1.645)   → P(tocar) = 2Φ(-1.645) ≈ 10%   → bajo, pero si llena, δ es grande →
                        mayor margen de arb combinado.

m ≥ ~2                 → P(tocar) < ~4.6%              → esperar un movimiento de 2+ sigmas es
                        pedir, en la práctica, que el favorito pierda ventaja significativa antes
                        de la resolución — para un favorito CLARO (por definición del caso que
                        estamos tratando, fuera de [0.40,0.60]) esto puede no llegar a ocurrir
                        nunca dentro de la ventana del evento. Fill probability tan baja que el
                        "candidato" probablemente termine igual que el caso 1 legacy: solo la
                        pata líder resuelve, sin cobertura (riesgo direccional puro, documentado
                        en §4 del audit anterior).
```

**Recomendación (para que Sara decida el valor final, no lo fijo yo como default):** la petición
del usuario ("banda de volatilidad baja, movimiento típico/frecuente, maximizar fill sin
sacrificar tanto margen") mapea naturalmente a **m ∈ [0.5, 1.0]** — es exactamente la banda que
en una distribución normal concentra la mayoría de la masa de movimientos "no triviales pero
comunes", con probabilidades de fill honestas de 30-62%. Fuera de esa banda hay que aceptar
explícitamente el trade-off (menos fill, más margen, o viceversa) — no hay un punto "correcto"
sin que Sara fije cuánto fill-rate está dispuesta a sacrificar por margen adicional, igual que
con `k` en el paso 3.

---

## 3. Condición de arb: ¿el margen simétrico funciona? — NO, y esto es un hallazgo, no una confirmación

El enunciado pregunta si `L_fav + L_dog < 1` se cumple automáticamente con un margen simétrico
para el underdog. **La respuesta es NO — hay que derivarlo, no asumirlo.**

Dado que favorito y underdog son complementarios exactos en un mercado único (`p0_dog = 1 -
p0_fav`, ya verificado en la ronda anterior), en el INSTANTE en que el favorito cruza su nivel
`L_fav = p0_fav - δ`, el precio del underdog en ese mismo instante es, por complementariedad,
exactamente:

```
p_dog(instante_cruce) = 1 - L_fav = 1 - p0_fav + δ = p0_dog + δ
```

Es decir: el underdog SUBE exactamente δ el mismo instante — esto confirma la parte que el
enunciado ya daba por sentada (ambas patas cruzan juntas por construcción). El punto que el
enunciado NO deriva es qué pasa con el margen del underdog:

Si la orden resting del underdog está puesta en `L_dog = p0_dog + margen_dog`, para que esa
orden YA esté cruzada (fillable) en el instante en que el favorito toca `L_fav`, necesitamos:

```
p_dog(instante_cruce) ≥ L_dog
p0_dog + δ ≥ p0_dog + margen_dog
δ ≥ margen_dog
```

Y la condición de arb real:

```
L_fav + L_dog < 1
(p0_fav - δ) + (p0_dog + margen_dog) < 1
1 - δ + margen_dog < 1
margen_dog < δ
```

**Conclusión matemática: un margen SIMÉTRICO (margen_dog = δ) da exactamente `L_fav + L_dog = 1`,
es decir ROI = 0 — NO es un arb, es el punto de equilibrio exacto.** Cualquier margen_dog ≥ δ
produce una "arb" con ROI ≤ 0 si ambas patas llenan simultáneamente en ese cruce. La regla
correcta NO es simetría — es:

```
margen_dog < δ,   estrictamente,   con margen_dog fijado por un piso INDEPENDIENTE
                  (cobertura de fees de ambas patas vía platform_fees.py + slippage
                  esperado de book, no por "igual que el otro lado").
```

El margen de arb resultante, si ambas patas cruzan en simultáneo con esta condición, es
`ROI = (δ - margen_dog) / (L_fav + L_dog)` (misma forma funcional que `compute_l2_max`, con
`δ` jugando el rol que `L1` jugaba en el paso 3 del mecanismo original). Recomiendo, para
mantener consistencia con el resto del sistema y evitar reinventar una segunda fórmula de
seguridad: fijar `margen_dog` en función de un `r_target` explícito de la misma forma que
`compute_l2_max`, en vez de dejarlo como un número suelto — eso es una decisión de Sara sobre
cómo cablear el parámetro, la matemática de arriba es lo que hay que respetar sin importar cómo
se implemente.

**Severidad de este hallazgo: alto si se implementa la versión "simétrica" tal como la planteó
el usuario en el enunciado** — produciría candidatos que, en el mejor caso de ambas patas
cruzando, dan ROI≈0 o negativo, disfrazados de "arb de favorito claro". Mismo patrón de bug que
el buffer fijo "+8pp" señalado en la auditoría del 2026-07-05: parece razonable a simple vista,
falla el chequeo aritmético explícito.

---

## 4. Fail-closed: sin datos de volatilidad, NO hay nivel

Si para una categoría/evento particular no hay:
- ELO del jugador/equipo con muestra suficiente (mismo umbral conceptual que
  `_VOL_MIN_MATCHES = 5` ya usado en `volatility.py:59`, o el que Priya confirme viable), o
- win-rate por superficie con muestra suficiente, o
- `std_games_total`/proxy de dispersión para el bucket (superficie × rival) correspondiente,

... entonces `σ` **no debe calcularse con un fallback silencioso** (ej. "usar σ_base solo, sin
ajuste de `g`", o peor, "usar un σ genérico de la categoría entera"). El evento debe marcarse
como **candidato NO admitido para el caso favorito-claro** — no avanza a `observing` bajo esta
rama del mecanismo (puede seguir siendo evaluado bajo el caso 50/50 legacy si su precio cae
dentro de esa banda, pero eso es una condición distinta, no un fallback de este cálculo).

Este es exactamente el mismo patrón que `compute_l2_max()` ya implementa (retorna `None` en vez
de un default cuando no hay ceiling viable) — mantener la misma disciplina acá es consistencia
de diseño, no una sugerencia nueva.

---

## 5. Generalización breve a fútbol

Mismo armazón, dos piezas cambian:

1. **`p_elo`**: usar `TeamRating.elo` (`src/database/models/domain.py:527`, ya poblado por
   ClubElo para fútbol) en el mismo `EloCalculator.expected_score()`. `p_surf`/`p_grp` no
   aplican igual (no hay "superficie" en fútbol) — reemplazar por win-rate local/visitante y
   win-rate vs. rivales de rating ELO similar (mismo concepto de bucket, distinto eje: bucket de
   ELO del rival en vez de ranking ATP/WTA).
2. **El proxy de dispersión `g`**: en tenis usé `std_games_total` (existente). En fútbol, el
   modelo Dixon-Coles ya estima `λ_home`, `λ_away` (goles esperados) — la dispersión natural
   análoga es la varianza de goles totales esperados, que para una Poisson es simplemente
   `λ_home + λ_away` (varianza = media en Poisson). Partidos de alto λ combinado (equipos
   ofensivos) generan más saltos discretos de probabilidad (cada gol mueve el precio
   abruptamente) → mayor volatilidad de precio in-match que partidos de bajo λ (0-0 probable,
   camino de precio más plano). Propuesta:

   ```
   g_football = clamp( (λ_home + λ_away) / λ_neutral , g_min, g_max )
   ```

   con `λ_neutral` calibrado empíricamente (promedio de goles totales esperados de la liga/
   categoría), simbólico igual que en tenis. El marco de reflexión de barrera (§2.1) y la
   condición de arb (§3) son **sport-agnostic** — no cambian; solo cambia qué alimenta `σ`.
   Mismo fail-closed de §4 aplica si `TeamRating` no tiene muestra (`n_games`) suficiente para
   el equipo/matchup.

---

## Resumen para Sara (≤250 palabras)

**Fórmula de volatilidad (σ, escala logit, horizonte = partido completo):**
`σ = σ_base(categoría) · g(bucket)`, con `g = clamp(std_games_total(superficie×bucket_rival) /
VOL_STD_NEUTRAL[tour], g_min, g_max)` reusando la infraestructura ya existente en
`src/core/math/volatility.py`. `σ_base` y los clamps NO están calibrados con datos reales —
dependen de lo que Priya confirme disponible; dejarlos simbólicos, fail-closed si faltan (§4).
El ELO gap alimenta la probabilidad de referencia `p̂` (chequeo de consistencia con el precio de
mercado), NO un segundo factor de `g` — meterlo dos veces doble-contaría la señal ya condicionada
en el bucket.

**Nivel de entrada:** modelar el precio como martingala (logit-Brownian sin deriva) y usar la
fórmula clásica de reflexión de barrera: `P(tocar nivel a m·σ) ≈ 2Φ(-m)`. Recomiendo `m ∈ [0.5,
1.0]` (fill probability 31.7%-61.7%, la "banda de movimiento típico") como punto de partida,
decisión final de Sara — mismo formato de trade-off explícito que el parámetro `k` ya usado en
`propose_hedge_order`.

**Hallazgo importante — margen NO debe ser simétrico:** derivé que un margen del underdog
IGUAL al del favorito (`margen_dog = δ`) da `L_fav + L_dog = 1` exactamente, es decir **ROI=0,
no un arb**. La condición correcta es `margen_dog < δ` estrictamente, con `margen_dog` fijado
por un piso de fees/slippage independiente, no por simetría. Implementar la versión simétrica tal
como se planteó produciría falsos positivos de "arb de favorito claro" con ROI≈0 o negativo —
severidad alta si se traslada tal cual a producción.

**Fútbol generaliza** reemplazando el proxy de dispersión por `(λ_home+λ_away)/λ_neutral`
(varianza Poisson de goles) — el resto del armazón (barrera reflejada, condición de arb) es
sport-agnostic.
