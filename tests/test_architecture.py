#!/usr/bin/env python3
"""
Tests de arquitectura — valida la estructura POST-refactor (Grok + limpieza).

Invariantes verificados:
  1. Los módulos clave de la nueva estructura importan limpio.
  2. El core (orchestration + sports + math) NO importa la capa web
     (engine autónomo; web es solo visor).
  3. Cada deporte de ENABLED_SPORTS expone la API mínima esperada.
  4. El dispatch de sports/__init__ es consistente con cada módulo de deporte.
  5. Los helpers de DB reconstruidos tras el split están exportados.
"""
from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


# ── 1. Imports de la nueva estructura ─────────────────────────────────────────

NEW_STRUCTURE_MODULES = [
    "src.core.config",
    "src.core.models",
    "src.database.database",
    "src.core.orchestration.engine",
    "src.scrapers.forgotten.legacy_orchestration_main",
    "src.core.orchestration.scanning",
    "src.core.orchestration.scan_settlement",
    "src.core.orchestration.live_monitor",
    "src.core.orchestration.workers",
    "src.core.math.simulation",
    "src.core.math.simulation_engine",
    "src.core.math.arbitrage",
    "src.core.math.elo",
    "src.core.math.hedge",
    "src.core.math.backtest",
    "src.core.sports",
    "src.web.create_app",
]


@pytest.mark.parametrize("module_name", NEW_STRUCTURE_MODULES)
def test_new_structure_imports(module_name):
    """Cada módulo de la nueva estructura debe importar sin errores."""
    assert importlib.import_module(module_name) is not None


# ── 2. Autonomía del core: no debe importar src.web ───────────────────────────

CORE_DIRS = [
    ROOT / "src" / "core",
    ROOT / "src" / "database",
]


def _iter_py_files(dirs):
    for d in dirs:
        for f in d.rglob("*.py"):
            if "__pycache__" in f.parts:
                continue
            yield f


def _imports_web(path: Path) -> bool:
    """True si el archivo importa src.web.* (acoplamiento prohibido en core)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "src.web" or node.module.startswith("src.web."):
                return True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "src.web" or alias.name.startswith("src.web."):
                    return True
    return False


def test_core_does_not_import_web():
    """El core autónomo no debe acoplarse a la capa web (solo el revés)."""
    offenders = [str(f.relative_to(ROOT)) for f in _iter_py_files(CORE_DIRS) if _imports_web(f)]
    assert offenders == [], f"Core importa src.web (acoplamiento prohibido): {offenders}"


# ── 3. API mínima por deporte ─────────────────────────────────────────────────

SPORT_MODULES = [
    "tennis", "football", "basketball",
    "baseball", "ice_hockey", "american_football",
]

REQUIRED_SPORT_FUNCS = [
    "get_live_stat_danger",
    "get_supported_live_stat_leg_tipos",
]


@pytest.mark.parametrize("sport", SPORT_MODULES)
def test_sport_module_exposes_live_api(sport):
    """Cada módulo de deporte expone la API de live-hedge usada por el monitor."""
    mod = importlib.import_module(f"src.core.sports.{sport}")
    for fn in REQUIRED_SPORT_FUNCS:
        assert hasattr(mod, fn), f"{sport} no expone {fn}()"
        assert callable(getattr(mod, fn))


# ── 4. Consistencia del dispatch de sports/__init__ ───────────────────────────

def test_sport_registry_complete():
    """El registro de configs cubre exactamente los 6 deportes habilitados."""
    from src.core.sports import all_sports, get_sport_config

    sports = all_sports()
    assert len(sports) == 6
    for sp in sports:
        cfg = get_sport_config(sp)
        assert cfg.market_tipos, f"{sp} sin market_tipos"
        assert cfg.stake_slug


@pytest.mark.parametrize("sport", SPORT_MODULES)
def test_dispatch_matches_sport_module(sport):
    """El dispatcher de sports/__init__ devuelve lo mismo que el módulo del deporte."""
    from src.core.sports import get_supported_live_stat_leg_tipos as dispatch_get

    mod = importlib.import_module(f"src.core.sports.{sport}")
    assert dispatch_get(sport) == mod.get_supported_live_stat_leg_tipos()


@pytest.mark.parametrize("sport", SPORT_MODULES)
def test_supports_live_stat_trigger_consistency(sport):
    """supports_live_stat_trigger es True sii el leg_tipo está en el set soportado."""
    from src.core.sports import (
        get_supported_live_stat_leg_tipos as dispatch_get,
        supports_live_stat_trigger,
    )

    supported = dispatch_get(sport)
    for tipo in supported:
        assert supports_live_stat_trigger(sport, tipo) is True
    assert supports_live_stat_trigger(sport, "tipo_inexistente_xyz") is False


def test_restored_db_helpers_are_exported():
    """Los helpers reconstruidos tras el split deben estar disponibles."""
    from src.database import database as dbm

    for name in (
        "get_or_create_player",
        "get_or_create_tournament",
        "save_match_stats",
        "save_h2h_record",
        "save_recent_matches",
    ):
        assert hasattr(dbm, name), f"database.py no exporta {name}"
        assert callable(getattr(dbm, name))
