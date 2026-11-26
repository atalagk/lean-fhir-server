"""Engine, table definitions (SPEC 4.3) and SQLite pragmas (SPEC 5.5)."""

from pathlib import Path

from sqlalchemy import (
    JSON,
    Column,
    Engine,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    event,
)
from sqlalchemy.engine import make_url

metadata = MetaData()

resource = Table(
    "resource",
    metadata,
    Column("resource_type", String(32), primary_key=True),
    Column("id", String(64), primary_key=True),
    Column("version_id", Integer, nullable=False),
    Column("last_updated", String(24), nullable=False),
    Column("content", JSON, nullable=False),
    Index("ix_resource_type_updated", "resource_type", "last_updated"),
)

search_index = Table(
    "search_index",
    metadata,
    Column("resource_type", String(32), nullable=False),
    Column("resource_id", String(64), nullable=False),
    Column("param", String(64), nullable=False),
    Column("value_string", Text),
    Column("value_exact", Text),
    Column("token_system", Text),
    Column("token_code", Text),
    Column("date_start", String(24)),
    Column("date_end", String(24)),
    Column("reference", Text),
    ForeignKeyConstraint(
        ["resource_type", "resource_id"],
        ["resource.resource_type", "resource.id"],
        ondelete="CASCADE",
    ),
    Index("ix_si_token", "resource_type", "param", "token_code"),
    Index("ix_si_string", "resource_type", "param", "value_string"),
    Index("ix_si_reference", "resource_type", "param", "reference"),
    Index("ix_si_date", "resource_type", "param", "date_start"),
    Index("ix_si_inbound", "reference"),
    Index("ix_si_owner", "resource_type", "resource_id"),
)


def is_sqlite(url: str) -> bool:
    return make_url(url).get_backend_name() == "sqlite"


def _sqlite_pragmas(dbapi_conn, _record) -> None:  # type: ignore[no-untyped-def]
    cur = dbapi_conn.cursor()
    for pragma in (
        "journal_mode=WAL",
        "foreign_keys=ON",
        "busy_timeout=5000",
        "synchronous=NORMAL",
    ):
        cur.execute(f"PRAGMA {pragma}")
    cur.close()


def make_engine(url: str) -> Engine:
    if is_sqlite(url):
        db_path = make_url(url).database
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url)
        event.listen(engine, "connect", _sqlite_pragmas)
    else:
        engine = create_engine(url, pool_pre_ping=True)
    return engine


def init_db(engine: Engine) -> None:
    metadata.create_all(engine)
