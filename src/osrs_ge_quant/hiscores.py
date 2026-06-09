# src/osrs_ge_quant/hiscores.py
import requests

from .config import load_settings
from .db import get_session
from .models import Account

OSRS_HISCORES_OLD_SCHOOL = "https://secure.runescape.com/m=hiscore_oldschool/index_lite.ws"

# order per API docs; we only need the main skills :contentReference[oaicite:6]{index=6}
SKILL_ORDER = [
    "Overall","Attack","Defence","Strength","Hitpoints","Ranged","Prayer","Magic",
    "Cooking","Woodcutting","Fletching","Fishing","Firemaking","Crafting","Smithing",
    "Mining","Herblore","Agility","Thieving","Slayer","Farming","Runecraft","Hunter",
    "Construction"
]

import time

_HISCORES_CACHE = {}  # rsn -> (timestamp, skills_dict)
CACHE_DURATION_SECONDS = 3600  # 1 hour

def fetch_hiscores(rsn: str) -> dict:
    if not rsn or not rsn.strip() or rsn.strip() == "N/A":
        return {s: {"rank": -1, "level": 1, "xp": 0} for s in SKILL_ORDER}
        
    now = time.time()
    if rsn in _HISCORES_CACHE:
        ts, cached_skills = _HISCORES_CACHE[rsn]
        if now - ts < CACHE_DURATION_SECONDS:
            return cached_skills

    headers = {
        "User-Agent": "osrs-ge-quant/0.1 (london.thomson.merriman@gmail.com)"
    }
    try:
        r = requests.get(OSRS_HISCORES_OLD_SCHOOL, params={"player": rsn}, headers=headers, timeout=10)
        r.raise_for_status()
        rows = r.text.strip().split("\n")
        skill_rows = rows[:len(SKILL_ORDER)]
        skills = {}
        for skill_name, line in zip(SKILL_ORDER, skill_rows):
            rank, level, xp = line.split(",")
            skills[skill_name] = {"rank": int(rank), "level": int(level), "xp": int(xp)}
        _HISCORES_CACHE[rsn] = (now, skills)
        return skills
    except Exception as e:
        print(f"[Warning] Failed to fetch hiscores for player '{rsn}': {e}. Using fallback/maxed profile.")
        if rsn in _HISCORES_CACHE:
            return _HISCORES_CACHE[rsn][1]
        return {s: {"rank": -1, "level": 99, "xp": 13034431} for s in SKILL_ORDER}

def load_player_skills() -> dict:
    """Aggregate main account skills; you can extend to per-account skills."""
    settings = load_settings()
    session = get_session()
    try:
        main = session.query(Account).filter_by(active=True).order_by(Account.id).first()
        if not main:
            return {s: 1 for s in SKILL_ORDER}
        skills = fetch_hiscores(main.rsn)
        return {k: v["level"] for k, v in skills.items()}
    except Exception as e:
        print(f"[Warning] Error loading player skills: {e}. Defaulting to level 99 fallback.")
        return {s: 99 for s in SKILL_ORDER}
    finally:
        session.close()

def load_all_active_player_skills() -> dict:
    """
    Query and return a dictionary mapping account name -> skill level dict for all active accounts.
    e.g. {"Pimpwurt": {"Herblore": 70, ...}, "BrotherDangr": {"Herblore": 1, ...}}
    """
    session = get_session()
    try:
        accounts = session.query(Account).filter_by(active=True).all()
        result = {}
        for a in accounts:
            skills = fetch_hiscores(a.rsn)
            result[a.name] = {k: v["level"] for k, v in skills.items()}
        return result
    except Exception as e:
        print(f"[Warning] Error loading all active player skills: {e}")
        return {}
    finally:
        session.close()
