"""CLI de migración: ``python -m database.migrate``."""

from database.db import init_db, init_db_historical


def main() -> None:
    init_db()
    init_db_historical()
    print("Migraciones PostgreSQL aplicadas y esquema validado")


if __name__ == "__main__":
    main()
