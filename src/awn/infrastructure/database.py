"""SQLAlchemy engine, sessions, and schema lifecycle helpers."""

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    """Declarative base shared by all persistent records."""


class Database:
    """Own the database engine and its session factory without exposing its URL."""

    def __init__(self, url: str, *, echo: bool = False) -> None:
        engine_options: dict[str, object] = {
            "echo": echo,
            "pool_pre_ping": True,
        }
        if url.startswith("sqlite"):
            engine_options["connect_args"] = {"check_same_thread": False}
            if ":memory:" in url:
                engine_options["poolclass"] = StaticPool

        self.engine: Engine = create_engine(url, **engine_options)
        if url.startswith("sqlite"):
            event.listen(
                self.engine,
                "connect",
                lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"),
            )
        self.session_factory = sessionmaker(
            bind=self.engine,
            class_=Session,
            expire_on_commit=False,
            autoflush=False,
        )

    @property
    def dialect_name(self) -> str:
        return self.engine.dialect.name

    def ping(self) -> None:
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    def create_schema(self) -> None:
        """Create tables for isolated tests only; deployed environments use Alembic."""

        from awn.infrastructure.persistence import models  # noqa: F401

        Base.metadata.create_all(self.engine)

    def drop_schema(self) -> None:
        from awn.infrastructure.persistence import models  # noqa: F401

        Base.metadata.drop_all(self.engine)

    def dispose(self) -> None:
        self.engine.dispose()
