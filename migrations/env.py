import logging
from contextlib import contextmanager
from logging.config import fileConfig

from flask import current_app
from sqlalchemy import event

from alembic import context


@contextmanager
def _chaves_estrangeiras_desligadas(engine):
    """Corre as migrações com `PRAGMA foreign_keys` DESLIGADO (só no SQLite).

    ⚠️ **Porquê**: o painel passou a ligar as chaves estrangeiras em cada
    ligação (ver `set_sqlite_pragma`), e o `flask db upgrade` usa o MESMO
    engine. No SQLite, alterar uma tabela é copiá-la para uma nova e trocar os
    nomes — com as chaves ligadas, esse baile dispara violações (ou, pior,
    apagamentos em cascata) sobre um estado que só é intermédio. É a
    recomendação do próprio Alembic para este dialeto.

    ⚠️ **E porquê assim, e não um `PRAGMA` na ligação já aberta**: no SQLite
    esse comando é **ignorado em silêncio dentro de uma transação**, e o
    SQLAlchemy 2.0 abre uma mal se executa a primeira instrução. Ficava tudo na
    mesma — e a transação aberta à frente do Alembic fazia o `upgrade` inteiro
    ser desfeito no fim, sem erro nenhum: a base de dados ficava no esquema
    inicial com a `alembic_version` VAZIA. Num contentor, isso é um painel que
    não arranca. Por isso mexe-se onde a ligação NASCE.
    """
    if engine.dialect.name != 'sqlite':
        yield
        return

    def _desligar(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute('PRAGMA foreign_keys=OFF')
        finally:
            cursor.close()

    # O listener do painel já está registado e liga-as; este corre a seguir e
    # desliga-as. O `dispose()` obriga a que as ligações do pool sejam
    # deitadas fora e refeitas — sem ele, as que já estão abertas mantinham o
    # valor antigo, porque o evento 'connect' só corre em ligações novas.
    event.listen(engine, 'connect', _desligar)
    engine.dispose()
    try:
        yield
    finally:
        event.remove(engine, 'connect', _desligar)
        # E outra vez à saída: o que fica no pool tem de voltar a ser o que o
        # resto do painel espera.
        engine.dispose()

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
fileConfig(config.config_file_name)
logger = logging.getLogger('alembic.env')


def get_engine():
    try:
        # this works with Flask-SQLAlchemy<3 and Alchemical
        return current_app.extensions['migrate'].db.get_engine()
    except (TypeError, AttributeError):
        # this works with Flask-SQLAlchemy>=3
        return current_app.extensions['migrate'].db.engine


def get_engine_url():
    try:
        return get_engine().url.render_as_string(hide_password=False).replace(
            '%', '%%')
    except AttributeError:
        return str(get_engine().url).replace('%', '%%')


# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
config.set_main_option('sqlalchemy.url', get_engine_url())
target_db = current_app.extensions['migrate'].db

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def get_metadata():
    if hasattr(target_db, 'metadatas'):
        return target_db.metadatas[None]
    return target_db.metadata


def run_migrations_offline():
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
        url=url, target_metadata=get_metadata(), literal_binds=True
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    # this callback is used to prevent an auto-migration from being generated
    # when there are no changes to the schema
    # reference: http://alembic.zzzcomputing.com/en/latest/cookbook.html
    def process_revision_directives(context, revision, directives):
        if getattr(config.cmd_opts, 'autogenerate', False):
            script = directives[0]
            if script.upgrade_ops.is_empty():
                directives[:] = []
                logger.info('No changes in schema detected.')

    conf_args = current_app.extensions['migrate'].configure_args
    if conf_args.get("process_revision_directives") is None:
        conf_args["process_revision_directives"] = process_revision_directives

    connectable = get_engine()

    with _chaves_estrangeiras_desligadas(connectable):
        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=get_metadata(),
                **conf_args
            )

            with context.begin_transaction():
                context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
