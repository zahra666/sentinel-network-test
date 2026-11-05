from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings
from .config import ROOT


class Base(DeclarativeBase):
    pass


def database_url() -> str:
    url = get_settings().database_url
    if url == "sqlite:///./sentinel.db":
        return f"sqlite:///{(ROOT / 'backend' / 'sentinel.db').as_posix()}"
    return url


def build_engine():
    url = database_url()
    return create_engine(url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {}, pool_pre_ping=True)


engine = build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def initialise_database() -> None:
    from . import models  # noqa: F401
    Base.metadata.create_all(engine)
    # The local credential-free demo intentionally avoids a migration runtime.
    # Apply only additive SQLite columns so an existing synthetic demo database
    # keeps its records when the interoperability layer is introduced. Cloud
    # Postgres changes remain explicit Supabase migration work.
    if engine.url.get_backend_name() != "sqlite":
        return
    columns = {item["name"] for item in inspect(engine).get_columns("observations")}
    additions = {
        "original_code": "varchar(96)",
        "coding_system": "varchar(160)",
        "standard_code": "varchar(64)",
        "coding_display": "varchar(240)",
        "terminology_version": "varchar(64)",
        "terminology_status": "varchar(48) not null default 'needs_terminology_review'",
        "clinical_key": "varchar(64)",
        "ucum_unit": "varchar(64)",
        "normalised_value": "float",
        "normalised_ucum_unit": "varchar(64)",
        "result_status": "varchar(32) not null default 'final'",
        "method": "varchar(160)",
        "egfr_equation": "varchar(160)",
        "fhir_id": "varchar(64)",
        "corrects_observation_id": "varchar(36)",
        "provenance": "json not null default '{}'",
    }
    with engine.begin() as connection:
        for name, ddl in additions.items():
            if name not in columns:
                connection.exec_driver_sql(f"alter table observations add column {name} {ddl}")
