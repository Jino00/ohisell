from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
#
# ★disable_existing_loggers=False 필수(D-NAO-47, 2026-07-17 발견): fileConfig의 기본값은
# True이고, 그러면 **이미 만들어진 모든 로거의 .disabled를 True로 바꾼다**. 그 순간부터 그
# 프로세스의 app.* 로거는 전부 벙어리가 된다.
# 실제로 물린 곳: pytest는 전 테스트를 한 프로세스에서 돌리는데, test_migration_one_running_
# index.py가 이 env.py를 타면 그 뒤 파일들의 로그 단언이 전부 실패한다(원인이 안 보여서
# "왜 격리로는 통과하는데 전체로는 실패하지"로 몇 시간 태우기 좋은 종류).
# prod는 무사했다 — alembic이 앱과 별도 프로세스로 돌고(app/main.py는 인프로세스 마이그레이션
# 안 함) pm2 로그도 정상 확인함(원칙22). 그래도 앞으로 누가 부팅 시 마이그레이션을 붙이면
# 그때부터 prod 로깅이 통째로 죽으므로 근본을 고친다.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

from app.models import (  # noqa: F401
    Channel, Product, Order, Settlement, Inventory,
    ProductMaster, ProductChannelMapping, AdCost, ProfitReport,
    SyncLog, OAuthToken, SchedulerState,
)
from app.database import Base

target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
