"""Staff roles: owner (ADMIN_IDS) > manager > support."""
from . import config, db

LEVELS = {"support": 1, "manager": 2, "owner": 3}


def level_of(c, uid):
    if uid in config.ADMIN_IDS:
        return 3
    return LEVELS.get(db.get_staff_role(c, uid) or "", 0)


def staff_ids(c, min_level=1):
    ids = set(config.ADMIN_IDS)
    if min_level <= 2:
        for r in db.list_staff(c):
            if LEVELS.get(r["role"], 0) >= min_level:
                ids.add(r["user_id"])
    return ids
