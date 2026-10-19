"""Invariantes del stack docker-compose (incidente 2026-10-18: imágenes
mezcladas por servicio dejaron 11 workers en crash-loop tras un reboot por
migraciones desconocidas, y sin topes de memoria un worker puede congelar
el host compartido)."""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

_COMPOSE = Path(__file__).resolve().parents[1] / "docker-compose.yml"


def _app_services() -> dict:
    services = yaml.safe_load(_COMPOSE.read_text())["services"]
    return {name: svc for name, svc in services.items() if str(svc.get("image", "")).startswith("kairos:")}


def test_all_app_services_share_the_single_image_tag():
    images = {name: svc["image"] for name, svc in _app_services().items()}
    assert images
    assert set(images.values()) == {"kairos:${KAIROS_IMAGE_TAG:?KAIROS_IMAGE_TAG must be set}"}, images


def test_long_running_app_services_have_memory_limit():
    missing = [
        name for name, svc in _app_services().items()
        if svc.get("restart") == "unless-stopped" and not svc.get("mem_limit")
    ]
    assert missing == []
