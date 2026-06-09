# src/osrs_ge_quant/db.py

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from .config import data_dir

# Use project-root-based data directory, not CWD
DB_DIR = data_dir() / "db"
DB_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DB_DIR / "osrs_ge.db"

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"timeout": 30},
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
