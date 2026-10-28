
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
import app.models
from app.core.config import settings
from app.db.session import Base
from app.models.supplier import Supplier
from app.models.purchase_order import PurchaseOrder, PurchaseOrderItem
from app.models.supplier_invoice import SupplierInvoice
from app.models.audit_log import AuditLog

# Import models here as we create them so Alembic can discover them.
# from app.models.user import User

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option(
    "sqlalchemy.url",
    settings.database_url.replace("%", "%%"),
)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = settings.database_url

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
