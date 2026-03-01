# src/osrs_ge_quant/config.py

from __future__ import annotations

from pathlib import Path
from functools import lru_cache
from typing import Any, Dict, List, Optional
import yaml


# Roughly:
#   PACKAGE_ROOT = .../site-packages/osrs_ge_quant
#   PROJECT_ROOT = .../src   (when running from source)
PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent


def _candidate_paths(filename: str) -> List[Path]:
    """
    Common search locations for config files.
    """
    return [
        # 1) project-root/config/<file>  (source layout: src/osrs_ge_quant)
        PROJECT_ROOT / "config" / filename,
        # 2) package-internal config (if ever shipped inside wheel)
        PACKAGE_ROOT / "config" / filename,
        # 3) current working dir/config/<file>  (when running CLI from repo root)
        Path.cwd() / "config" / filename,
    ]


def _find_config_file(filename: str) -> Path:
    """
    Strict resolver: raises if not found.
    Used for *required* configs like settings.yaml and accounts.yaml.
    """
    candidates = _candidate_paths(filename)
    for p in candidates:
        if p.is_file():
            return p

    raise FileNotFoundError(
        f"Could not find {filename}. "
        f"Tried: {', '.join(str(c) for c in candidates)}"
    )


def _try_find_config_file(filename: str) -> Optional[Path]:
    """
    Lenient resolver: returns None if not found.
    Used for *optional* configs like strategies.yaml.
    """
    for p in _candidate_paths(filename):
        if p.is_file():
            return p
    return None


def _load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if data is not None else {}


@lru_cache
def load_settings() -> Dict[str, Any]:
    """
    Load settings.yaml as a dict (required).
    """
    path = _find_config_file("settings.yaml")
    return _load_yaml(path)


@lru_cache
def load_accounts_config() -> List[Dict[str, Any]]:
    """
    Load accounts.yaml.

    Accepts either:
      - a list of account dicts
      - or a dict with key "accounts" -> list[dict]
    """
    path = _find_config_file("accounts.yaml")
    data = _load_yaml(path)

    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "accounts" in data:
        accounts = data["accounts"]
        if isinstance(accounts, list):
            return accounts
    return []


@lru_cache
def load_strategies() -> Dict[str, Any]:
    """
    Load strategies.yaml if present.

    This is treated as OPTIONAL:
      - If not found, returns {} instead of raising.
      - This keeps imports (e.g. in strategy.py) from crashing.
    Expected shape (flexible):

      # Example 1:
      flips:
        mean_reversion:
          k_std: 1.0
          position_fraction: 0.05

      # Example 2:
      strategies:
        flips: ...
        processing: ...

    For now we just return whatever dict is in the file and let callers
    interpret it.
    """
    path = _try_find_config_file("strategies.yaml")
    if path is None:
        return {}

    data = _load_yaml(path)
    if isinstance(data, dict):
        return data
    return {}


def data_dir() -> Path:
    """
    Resolve data directory from settings.yaml, falling back to ~/.osrs_ge_quant.

    settings.yaml:
      paths:
        data_dir: "C:/path/to/osrs-data"
    """
    settings = load_settings()
    base = (
        settings.get("paths", {}).get("data_dir")
        if isinstance(settings, dict)
        else None
    )

    if isinstance(base, str) and base.strip():
        return Path(base).expanduser().resolve()

    return (Path.home() / ".osrs_ge_quant").resolve()
