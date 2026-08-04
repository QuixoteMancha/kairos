"""Tests del limitador thread-safe de requests para tenis (2026-08-04,
pedido explícito del usuario: "el fetch de las stats y los nuevos
matches tanto para flashscore como para tennisexplorer, deben tener
pausas para no overhittear tanto los servers de ambos sitios")."""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

from outer_apis.scrapers.tennis._request_throttle import RequestThrottle


def test_first_call_never_waits():
    throttle = RequestThrottle(min_interval_s=5.0)
    with patch("outer_apis.scrapers.tennis._request_throttle.time.sleep") as mock_sleep:
        throttle.wait()
    mock_sleep.assert_not_called()


def test_second_call_waits_remaining_interval():
    throttle = RequestThrottle(min_interval_s=1.0)
    # wait() llama time.monotonic() 2 veces por invocación (now + al registrar
    # last_call) -- 1ra llamada: t=100.0/100.0 (no espera); 2da: t=100.3 (el
    # "now" que dispara el cálculo de espera), luego t=101.0 (post-sleep real).
    times = iter([100.0, 100.0, 100.3, 101.0])
    with patch("outer_apis.scrapers.tennis._request_throttle.time.monotonic", side_effect=lambda: next(times)), \
         patch("outer_apis.scrapers.tennis._request_throttle.time.sleep") as mock_sleep:
        throttle.wait()  # no espera (primera)
        throttle.wait()  # debe esperar ~0.7s (1.0 - 0.3 transcurrido)
    mock_sleep.assert_called_once()
    waited = mock_sleep.call_args[0][0]
    assert abs(waited - 0.7) < 1e-9


def test_no_wait_if_enough_time_already_passed():
    throttle = RequestThrottle(min_interval_s=0.5)
    times = iter([100.0, 100.0, 105.0, 105.0])  # 5s transcurridos, muy por encima del mínimo
    with patch("outer_apis.scrapers.tennis._request_throttle.time.monotonic", side_effect=lambda: next(times)), \
         patch("outer_apis.scrapers.tennis._request_throttle.time.sleep") as mock_sleep:
        throttle.wait()
        throttle.wait()
    mock_sleep.assert_not_called()


def test_thread_safe_serializes_concurrent_calls():
    """Con min_interval real chico, N threads concurrentes deben tardar
    AL MENOS (N-1)*min_interval en total -- confirma que el lock global
    realmente serializa, no solo cada thread durmiendo por su cuenta
    sin coordinación."""
    throttle = RequestThrottle(min_interval_s=0.05)
    n_threads = 5
    start = time.monotonic()
    threads = [threading.Thread(target=throttle.wait) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.monotonic() - start
    assert elapsed >= (n_threads - 1) * 0.05 * 0.8  # margen de tolerancia real de scheduling
