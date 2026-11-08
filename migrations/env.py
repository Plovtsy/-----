import os
from logging.config import fileConfig
from alembic import context
from sqlalchemy import engine_from_config, pool
from kontur.infrastructure.database import Base
config = context.config
target_metadata = Base.metadata
url = os.environ.get('DATABASE_URL', config.get_main_option('sqlalchemy.url'))
if context.is_offline_mode():
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction(): context.run_migrations()
else:
    engine = engine_from_config({'sqlalchemy.url':url}, prefix='sqlalchemy.', poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction(): context.run_migrations()
