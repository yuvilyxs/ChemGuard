from datetime import datetime, timezone


MAX_SEARCH_HISTORY = 20
MAX_RECENT_COMPOUNDS = 12


def _utc_timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def add_search_entry(history, query, results, error=None):
    entry = {
        "query": query.strip(),
        "searched_at": _utc_timestamp(),
        "result_count": len(results),
        "results": list(results),
        "error": error,
    }
    return [entry, *history][:MAX_SEARCH_HISTORY]


def add_recent_compound(recent, compound, query):
    cid = compound.get("cid")
    if not isinstance(cid, int) or isinstance(cid, bool) or cid < 1:
        raise ValueError("A valid PubChem compound is required for recent history.")

    entry = {
        "compound": compound,
        "query": query.strip(),
        "accessed_at": _utc_timestamp(),
    }
    remaining = [
        item for item in recent if item["compound"].get("cid") != cid
    ]
    return [entry, *remaining][:MAX_RECENT_COMPOUNDS]
