"""Guardrails del catálogo PostgreSQL sin requerir un servidor local."""

from database.migrations.catalog import (
    ACCOUNTING_MIGRATIONS,
    HISTORICAL_MIGRATIONS,
    accounting_models,
    historical_models,
)


def test_migration_versions_are_unique_and_ordered():
    for migrations in (ACCOUNTING_MIGRATIONS, HISTORICAL_MIGRATIONS):
        versions = [migration.version for migration in migrations]
        assert versions == sorted(set(versions))
        assert versions[0] == 1


def test_every_active_model_is_in_exactly_one_postgresql_catalog():
    accounting = accounting_models()
    historical = historical_models()
    assert len(accounting) == len(set(accounting))
    assert len(historical) == len(set(historical))
    assert set(accounting).isdisjoint(historical)
    assert all(model._meta.database is not None for model in accounting + historical)
