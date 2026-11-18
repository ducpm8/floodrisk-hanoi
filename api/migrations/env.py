import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Import all models so Alembic autogenerate can detect them
from api.models import Base  # noqa: F401 — side-effect import registers all models

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    """
    Prefer an explicit URL set by the caller (tests pass one via
    ``alembic_cfg`` so migrations run against DATABASE_TEST_URL, never the
    dev DB). Otherwise fall back to DATABASE_SYNC_URL, then DATABASE_URL
    with asyncpg swapped for psycopg2.
    """
    override = config.get_main_option("sqlalchemy.url")
    if override:
        return override
    url = os.environ.get("DATABASE_SYNC_URL") or os.environ.get("DATABASE_URL", "")
    return url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")


def run_migrations_offline() -> None:
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
