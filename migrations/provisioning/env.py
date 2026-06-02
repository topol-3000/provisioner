"""Alembic env.py for the `provisioning` schema.

Use:
    alembic -n provisioning upgrade head
    alembic -n provisioning revision --autogenerate -m "add instance"
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from provisioning_worker.modules.provisioning.models import Base
from provisioning_worker.settings import get_settings

# This is the schema this tree owns. The version table lives here too.
SCHEMA = "provisioning"

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject the sync DSN from settings. We always use the sync URL for Alembic.
config.set_main_option("sqlalchemy.url", str(get_settings().database_url_sync))

# Metadata for autogenerate — every mapped class in the provisioning schema
# registers against Base.metadata (see modules/provisioning/models.py).
target_metadata = Base.metadata


def _include_object(object, name, type_, reflected, compare_to):
    """Restrict autogenerate to objects in our schema."""
    if type_ == "table":
        return getattr(object, "schema", None) == SCHEMA
    return True


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection (URL-only mode)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        include_object=_include_object,
        version_table=config.get_section_option(SCHEMA, "version_table", "alembic_version"),
        version_table_schema=SCHEMA,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            include_object=_include_object,
            version_table="alembic_version",
            version_table_schema=SCHEMA,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
