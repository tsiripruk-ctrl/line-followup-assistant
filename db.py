from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker
from config import settings


def normalized_database_url(value: str) -> str:
    """Use psycopg v3 explicitly for PostgreSQL URLs injected by Render."""
    if value.startswith("postgres://"):
        return "postgresql+psycopg://" + value[len("postgres://"):]
    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value[len("postgresql://"):]
    return value


database_url = normalized_database_url(settings.database_url)
connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
engine = create_engine(database_url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def ensure_people_registry_schema() -> None:
    """Non-destructive additive migration for v0.6.5 People Registry fields.

    SQLAlchemy create_all() does not add columns to an existing table, so this
    helper adds only the new nullable/defaulted fields. It is safe to run on
    every startup for both PostgreSQL (Render) and SQLite development.
    """
    inspector = inspect(engine)
    if "people" not in inspector.get_table_names():
        return
    existing = {c["name"] for c in inspector.get_columns("people")}
    statements = []
    if "display_name" not in existing:
        statements.append("ALTER TABLE people ADD COLUMN display_name VARCHAR(255)")
    if "call_name" not in existing:
        statements.append("ALTER TABLE people ADD COLUMN call_name VARCHAR(255)")
    if "role" not in existing:
        statements.append("ALTER TABLE people ADD COLUMN role VARCHAR(32) DEFAULT 'EMPLOYEE'")
    if not statements:
        return
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))
        conn.execute(text("UPDATE people SET role='EMPLOYEE' WHERE role IS NULL OR role=''"))
