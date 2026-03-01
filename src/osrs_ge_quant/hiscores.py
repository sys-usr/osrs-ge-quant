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

def fetch_hiscores(rsn: str) -> dict:
    r = requests.get(OSRS_HISCORES_OLD_SCHOOL, params={"player": rsn}, timeout=10)
    r.raise_for_status()
    rows = r.text.strip().split("\n")
    skill_rows = rows[:len(SKILL_ORDER)]
    skills = {}
    for skill_name, line in zip(SKILL_ORDER, skill_rows):
        rank, level, xp = line.split(",")
        skills[skill_name] = {"rank": int(rank), "level": int(level), "xp": int(xp)}
    return skills

def load_player_skills() -> dict:
    """Aggregate main account skills; you can extend to per-account skills."""
    settings = load_settings()
    # Simple: assume first active account is 'main'
    session = get_session()
    main = session.query(Account).filter_by(active=True).order_by(Account.id).first()
    if not main:
        return {}
    skills = fetch_hiscores(main.rsn)
    # map to internal names (Herblore, Construction, etc.)
    return {k: v["level"] for k, v in skills.items()}
