# src/osrs_ge_quant/db.py

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from .config import data_dir


def resolve_db_path() -> Path:
    """
    Resolve SQLite DB location.

    Priority:
      1) OSRS_GE_DB_PATH env var
      2) settings.yaml paths.data_dir + /db/osrs_ge.db
    """
    env_path = os.getenv("OSRS_GE_DB_PATH", "").strip()
    if env_path:
        return Path(env_path).expanduser().resolve()

    db_dir = data_dir() / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir / "osrs_ge.db"


DB_PATH = resolve_db_path()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    echo=False,
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    future=True,
)

Base = declarative_base()


def get_session():
    return SessionLocal()


def init_db():
    """
    Create tables if they don't exist.
    Called from CLI `init-db`.
    """
    from . import models  # make sure models are imported so Base knows them

    Base.metadata.create_all(bind=engine)
