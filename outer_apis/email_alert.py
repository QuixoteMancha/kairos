"""Alertas por correo -- 2026-08-11, pedido explícito del usuario: "si
no funciona con el proxy o no hay dinero en el proxy, enviarme un
correo a alertas@example.com".

Genérico a propósito (no específico de BrightData) -- vive en
`outer_apis/` raíz, no en `outer_apis/scrapers/`, para que cualquier
alerta futura del sistema (no solo proxy) pueda reusarlo sin acoplarse
al scraping.

SMTP simple (sin dependencias nuevas, `smtplib` de la librería estándar)
-- credenciales vía `.env`:
    ALERT_EMAIL_TO        default "alertas@example.com" (pedido explícito)
    ALERT_EMAIL_FROM      remitente, default = SMTP_USER si no se especifica
    SMTP_HOST             default "smtp.gmail.com"
    SMTP_PORT             default 587 (STARTTLS)
    SMTP_USER             cuenta remitente real
    SMTP_PASSWORD         contraseña de aplicación (Gmail: 2FA + App Password,
                           NUNCA la contraseña normal de la cuenta)

Fail-closed por diseño (mismo criterio del resto del proyecto): cualquier
fallo de envío se loguea, nunca excepciona hacia el caller -- una alerta
rota no debe romper el scraper/worker que la disparó."""

import os
import smtplib
import threading
import time
from email.mime.text import MIMEText

_DEFAULT_TO = "alertas@example.com"
_DEFAULT_SMTP_HOST = "smtp.gmail.com"
_DEFAULT_SMTP_PORT = 587
_TIMEOUT_S = 15

# 2026-08-11: rate-limit real -- sin esto, un proxy roto en un scraper
# que corre cada 60s (ej. tennis_match_tick_worker) mandaría un correo
# por CADA request fallida, potencialmente cientos por hora. Cooldown
# por `reason_key` (no global) -- distintos motivos de alerta no se
# pisan entre sí, pero el MISMO motivo no se re-envía antes del cooldown.
_COOLDOWN_S = 6 * 3600  # 6 horas
_lock = threading.Lock()
_last_sent: dict[str, float] = {}


def _smtp_config() -> dict[str, str]:
    """Fail-closed: {} si falta SMTP_USER/SMTP_PASSWORD -- sin
    credenciales reales no hay forma de enviar nada, mejor que el
    caller sepa explícitamente que está deshabilitado en vez de que
    intente y falle en silencio cada vez."""
    user = os.getenv("SMTP_USER", "")
    password = os.getenv("SMTP_PASSWORD", "")
    if not user or not password:
        return {}
    return {
        "host": os.getenv("SMTP_HOST", _DEFAULT_SMTP_HOST),
        "port": int(os.getenv("SMTP_PORT", str(_DEFAULT_SMTP_PORT))),
        "user": user,
        "password": password,
        "from": os.getenv("ALERT_EMAIL_FROM", "") or user,
        "to": os.getenv("ALERT_EMAIL_TO", "") or _DEFAULT_TO,
    }


def send_email_alert(subject: str, body: str, *, reason_key: str | None = None, verbose: bool = True) -> bool:
    """Envía un correo de alerta real -- fail-closed, `False` ante
    cualquier fallo (SMTP no configurado, error de red/auth, etc.),
    nunca excepciona.

    `reason_key`: identifica el TIPO de alerta para el cooldown (ej.
    "brightdata_no_balance", "brightdata_proxy_down") -- si se omite, se
    usa `subject` tal cual. Dentro de las `_COOLDOWN_S` horas desde el
    último envío exitoso del MISMO `reason_key`, no reenvía (evita
    inundar la bandeja de entrada con la misma alerta repetida)."""
    config = _smtp_config()
    if not config:
        if verbose:
            print("[email_alert] SMTP_USER/SMTP_PASSWORD no configurados -- alerta NO enviada:", subject)
        return False

    key = reason_key or subject
    with _lock:
        last = _last_sent.get(key)
        if last is not None and (time.time() - last) < _COOLDOWN_S:
            if verbose:
                print(f"[email_alert] '{key}' en cooldown, no se reenvía: {subject}")
            return False

        try:
            msg = MIMEText(body)
            msg["Subject"] = f"[Kairos] {subject}"
            msg["From"] = config["from"]
            msg["To"] = config["to"]

            with smtplib.SMTP(config["host"], config["port"], timeout=_TIMEOUT_S) as server:
                server.starttls()
                server.login(config["user"], config["password"])
                server.sendmail(config["from"], [config["to"]], msg.as_string())

            _last_sent[key] = time.time()
            if verbose:
                print(f"[email_alert] enviado a {config['to']}: {subject}")
            return True
        except Exception as e:
            if verbose:
                print(f"[email_alert] fallo al enviar '{subject}': {e}")
            return False
