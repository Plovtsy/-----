"""Isolated PostgreSQL databases for integration tests."""
import os
import uuid
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from kontur.infrastructure.database import Base, Database


@contextmanager
def temporary_database():
    admin_url = os.environ.get(
        "KONTUR_TEST_DATABASE_URL",
        "postgresql+psycopg://kontur:kontur@127.0.0.1:55432/kontur",
    )
    parsed = make_url(admin_url)
    if parsed.get_backend_name() != "postgresql":
        raise ValueError("Тестам нужен PostgreSQL")
    name = "kontur_test_" + uuid.uuid4().hex
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    storage = None
    created = False
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
        created = True
        url = parsed.set(database=name).render_as_string(hide_password=False)
        storage = Database(url)
        Base.metadata.create_all(storage.engine)
        yield storage
    finally:
        if storage is not None:
            storage.engine.dispose()
        if created:
            with admin.connect() as connection:
                connection.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin.dispose()
