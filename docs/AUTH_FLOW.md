# Flujo de Autenticación en Kairos (2026 - actualizado)

## Estado actual

TennisStats ha sido completamente eliminado del sistema (fuente ya no utilizada).

Autenticación / acceso actual:

- **Books (Stake y cross-book)**: Cookies de sesión (extraídas vía `extract_cookies.py` o login) almacenadas en tabla `sessioncookie` o usadas directamente con curl_cffi.
- **Sofascore, Flashscore y similares**: BrightData proxies residenciales rotativos + curl_cffi (impersonate Chrome). No requieren login persistente para la mayoría de endpoints de resultados y stats.
- **LLM**: Solo API keys.

Ver `README.md` > Autenticación para variables de entorno actuales (BRIGHTDATA_PROXY recomendado, STAKE_* si aplica).

El pipeline de scraping, player stats y settlement funcionan sin TennisStats (Sofascore + Flashscore + feeds de books son las fuentes primarias multi-deporte).

Cualquier código, comentario, doc o referencia legacy a TennisStats ha sido purgado.

## Notas para proxies / anti-detección

- Usar sesión por request con rotación.
- Impersonate headers consistentes.
- Rate limiting y jitter en orchestration/scanning.

